"""
Walk-forward backtest harness (Backtest Plan B1).

Runs the FULL pipeline across a universe × weekly cohorts, writes one row per
(backtest_id, ticker, as_of, horizon) to the dedicated `backtest_runs` table — namespaced by
backtest_id so a backtest NEVER pollutes the live longterm_agent_results / forward stats — then
grades matured rows vs SPY and prints a versioned report: conviction calibration, excess
distribution, Sharpe/IR, drawdown, regime/direction/shift splits, and bootstrap CIs, with a
min-n gate so a tiny, correlated, single-regime sample can't masquerade as a verdict.

Reproducible: enable the B0 LLM cache and run with reflection off.

    LT_LLM_CACHE=true LT_MAX_REFLECTION=0 python -m longterm_agent.backtest \
        --backtest-id exp1 --tickers NVDA AAPL MSFT JPM --start 2026-03-01 --end 2026-05-01 \
        --horizons 14 28 84
    python -m longterm_agent.backtest --backtest-id exp1 --report-only   # re-print the report
"""
import sys
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from longterm_agent.config import LongTermAgentConfig
from longterm_agent.grade_shifts import _ret
from longterm_agent.stats import trade_excess
from longterm_agent import backtest_metrics as M

_DDL = Path(__file__).parent / "create_backtest_runs_table.sql"
MIN_N = 30  # below this the report prints an ESTIMATE banner instead of a verdict


# --------------------------------------------------------------------------- db helpers
def _conn(cfg):
    return psycopg2.connect(**cfg.get_db_config())


def _ensure_table(cfg):
    conn = _conn(cfg)
    with conn.cursor() as cur:
        cur.execute(_DDL.read_text())
    conn.commit()
    conn.close()


def _weekly(start, end, step):
    d, out = start, []
    while d <= end:
        out.append(d)
        d += timedelta(days=step)
    return out


def _regime(cur, as_of: date, lookback=60, band=0.03):
    """Market regime at as_of from SPY's trailing return: bull / bear / flat."""
    r = _ret(cur, "SPY", as_of - timedelta(days=lookback), lookback)
    if r is None:
        return None
    return "bull" if r > band else ("bear" if r < -band else "flat")


# --------------------------------------------------------------------------- run
def run_cohorts(cfg, tickers, cohorts, horizons, backtest_id, force=False):
    from longterm_agent.agent.graph import create_longterm_graph
    from longterm_agent.llm_cache import maybe_enable_llm_cache
    from news_agent.database.db_manager import DBManager

    _ensure_table(cfg)
    maybe_enable_llm_cache()  # LT_LLM_CACHE=true → reproducible + cheap re-runs (B0)
    prov = cfg.provenance()
    print(f"backtest_id={backtest_id}  provenance={prov}")

    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=5)
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e})")
    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    llm_reduce = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=8000)
    graph = create_longterm_graph(llm_map, llm_reduce, cfg, db, neo)

    done = set()
    if not force:
        conn = _conn(cfg)
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT ticker, as_of FROM backtest_runs
                           WHERE backtest_id=%s AND signal IS NOT NULL""", [backtest_id])
            done = {(t, d) for t, d in cur.fetchall()}
        conn.close()

    n_run = 0
    for as_of in cohorts:
        for tk in tickers:
            if (tk.upper(), as_of.date()) in done:
                print(f"  skip {as_of.date()} {tk} (already recorded)")
                continue
            try:
                final = graph.invoke({"ticker": tk.upper(), "as_of": as_of,
                                      "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND,
                                      "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": []})
            except Exception as e:
                print(f"  {as_of.date()} {tk}: ERROR {e}")
                continue
            _write_rows(cfg, backtest_id, prov, tk, as_of, horizons, final)
            n_run += 1
    if neo:
        neo.close()
    print(f"ran {n_run} (ticker, cohort) pipelines")


def _write_rows(cfg, backtest_id, prov, ticker, as_of, horizons, final):
    """One row per horizon (the decision is shared; only the measurement window differs)."""
    sig = final.get("signal") or {}
    filtered = final.get("filtered_signal")
    no_data = final.get("no_data", False)
    shift = final.get("background_shift") or {}
    direction = "NO_DATA" if no_data else (sig.get("direction") or "NEUTRAL")
    traded = bool(filtered) and filtered.get("direction") in ("LONG", "SHORT")

    conn = _conn(cfg)
    with conn.cursor() as cur:
        for h in horizons:
            cur.execute("""
                INSERT INTO backtest_runs
                    (backtest_id, provenance, agent_version, ticker, as_of, horizon_days,
                     direction, conviction, traded, shift_flag, shift_score, signal)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (backtest_id, ticker, as_of, horizon_days) DO UPDATE SET
                    provenance=EXCLUDED.provenance, agent_version=EXCLUDED.agent_version,
                    direction=EXCLUDED.direction, conviction=EXCLUDED.conviction,
                    traded=EXCLUDED.traded, shift_flag=EXCLUDED.shift_flag,
                    shift_score=EXCLUDED.shift_score, signal=EXCLUDED.signal
            """, [backtest_id, Json(prov), cfg.AGENT_VERSION, ticker.upper(), as_of.date(), h,
                  direction, sig.get("conviction"), traded, bool(shift.get("has_shifted")),
                  shift.get("shift_score"), Json(filtered or sig or {})])
    conn.commit()
    conn.close()
    print(f"  ran {as_of.date()} {ticker}: {direction} conv={sig.get('conviction')} traded={traded}")


# --------------------------------------------------------------------------- grade
def grade(cfg, backtest_id, asof_today=None):
    today = datetime.strptime(asof_today, "%Y-%m-%d").date() if asof_today else date.today()
    conn = _conn(cfg)
    cur = conn.cursor()
    cur.execute("""SELECT id, ticker, as_of, horizon_days, direction FROM backtest_runs
                   WHERE backtest_id=%s AND outcome_filled_at IS NULL ORDER BY as_of""",
                [backtest_id])
    rows = cur.fetchall()
    graded = 0
    for rid, ticker, as_of, horizon, direction in rows:
        if as_of + timedelta(days=horizon) > today:
            continue  # horizon not matured yet
        fwd = _ret(cur, ticker, as_of, horizon)
        bench = _ret(cur, "SPY", as_of, horizon)
        if fwd is None:
            continue  # missing prices; retry next run
        ex = trade_excess(direction, fwd, bench) if direction in ("LONG", "SHORT") else None
        cur.execute("""UPDATE backtest_runs SET fwd_return=%s, benchmark_return=%s, excess=%s,
                       regime=%s, outcome_filled_at=%s WHERE id=%s""",
                    [round(fwd, 4), round(bench, 4) if bench is not None else None,
                     round(ex, 4) if ex is not None else None, _regime(cur, as_of), today, rid])
        graded += 1
    conn.commit()
    conn.close()
    print(f"graded {graded} matured rows (today={today})")


# --------------------------------------------------------------------------- report
def _pct(x):
    return "  n/a" if x is None else f"{x:+.2%}"


def _ci(lo, hi, pct=True):
    if lo is None or hi is None:
        return "[ n/a ]"
    f = (lambda v: f"{v:+.2%}") if pct else (lambda v: f"{v:.2f}")
    return f"[{f(lo)}, {f(hi)}]"


def report(cfg, backtest_id):
    conn = _conn(cfg)
    cur = conn.cursor()
    cur.execute("""SELECT horizon_days, ticker, as_of, direction, conviction, traded,
                          shift_flag, regime, excess
                   FROM backtest_runs WHERE backtest_id=%s ORDER BY horizon_days, as_of""",
                [backtest_id])
    rows = [dict(zip(("horizon", "ticker", "as_of", "direction", "conviction", "traded",
                      "shift_flag", "regime", "excess"), r)) for r in cur.fetchall()]
    cur.execute("SELECT provenance FROM backtest_runs WHERE backtest_id=%s LIMIT 1", [backtest_id])
    prov = (cur.fetchone() or [None])[0]
    conn.close()

    print("\n" + "=" * 74)
    print(f"BACKTEST REPORT  ·  backtest_id={backtest_id}")
    print("=" * 74)
    if not rows:
        print("no rows — run the harness first.")
        return
    horizons = sorted({r["horizon"] for r in rows})
    cohorts = sorted({r["as_of"] for r in rows})
    tickers = sorted({r["ticker"] for r in rows})
    print(f"provenance: {prov}")
    print(f"universe={len(tickers)}  cohorts={len(cohorts)} ({cohorts[0]}..{cohorts[-1]})  "
          f"horizons={horizons}")

    for h in horizons:
        hr = [r for r in rows if r["horizon"] == h]
        traded = [r for r in hr if r["traded"] and r["direction"] in ("LONG", "SHORT")]
        graded = [r for r in traded if r["excess"] is not None]
        ex = [r["excess"] for r in graded]
        print("\n" + "-" * 74)
        print(f"HORIZON {h//7}w ({h}d):  signals={len(hr)}  traded={len(traded)}  graded={len(graded)}")
        if not graded:
            print("  no graded trades yet (horizons not matured / prices missing).")
            continue
        gate = "" if len(graded) >= MIN_N else f"  ⚠ ESTIMATE (n={len(graded)} < {MIN_N}; harness check, not a verdict)"
        d = M.distribution(ex)
        wr = M.win_rate(ex)
        wr_ci = M.bootstrap_ci(ex, M.win_rate)
        mean_ci = M.bootstrap_ci(ex, lambda s: M.distribution(s)["mean"])
        print(f"  win rate     : {wr:.0%}   CI {_ci(*wr_ci)}{gate}")
        print(f"  mean excess  : {_pct(d['mean'])}   CI {_ci(*mean_ci)}")
        print(f"  median / IQR : {_pct(d['median'])}   [{_pct(d['p25'])}, {_pct(d['p75'])}]   "
              f"min {_pct(d['min'])}  max {_pct(d['max'])}")
        # portfolio view: per-cohort mean excess → Sharpe/IR + drawdown of the equity curve
        series = [M.distribution([r["excess"] for r in graded if r["as_of"] == c])["mean"]
                  for c in sorted({r["as_of"] for r in graded})]
        print(f"  Sharpe/IR    : {('  n/a' if M.sharpe(series) is None else f'{M.sharpe(series):.2f}')} "
              f"(per-cohort)   max drawdown {_pct(M.max_drawdown(series))}")

        print("  conviction calibration (is conviction predictive?):")
        for b in M.calibration_curve(graded):
            if b["n"]:
                print(f"    conv {b['lo']:.2f}-{b['hi']:.2f}: n={b['n']:2d}  "
                      f"win {b['win_rate']:.0%}  mean {_pct(b['mean_excess'])}")
        # splits
        for label, key, vals in (("direction", "direction", ("LONG", "SHORT")),
                                 ("shift", "shift_flag", (True, False))):
            line = []
            for v in vals:
                sub = [r["excess"] for r in graded if r[key] == v]
                if sub:
                    line.append(f"{v}: n={len(sub)} win {M.win_rate(sub):.0%} mean {_pct(M.distribution(sub)['mean'])}")
            if line:
                print(f"  by {label:9s}: " + "   ".join(line))
        regimes = sorted({r["regime"] for r in graded if r["regime"]})
        if regimes:
            line = []
            for v in regimes:
                sub = [r["excess"] for r in graded if r["regime"] == v]
                line.append(f"{v}: n={len(sub)} mean {_pct(M.distribution(sub)['mean'])}")
            print(f"  by regime   : " + "   ".join(line))
    print("\n" + "=" * 74)


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="Walk-forward backtest harness (B1)")
    ap.add_argument("--backtest-id", required=True)
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--horizons", nargs="+", type=int, default=[28])
    ap.add_argument("--force", action="store_true", help="re-run cohorts already recorded")
    ap.add_argument("--report-only", action="store_true", help="skip run+grade, just print")
    ap.add_argument("--no-grade", action="store_true", help="skip grading")
    ap.add_argument("--asof-today", help="override 'today' for grading (YYYY-MM-DD)")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    if not args.report_only:
        if args.tickers and args.start and args.end:
            cohorts = _weekly(datetime.strptime(args.start, "%Y-%m-%d"),
                              datetime.strptime(args.end, "%Y-%m-%d"), args.step)
            run_cohorts(cfg, args.tickers, cohorts, args.horizons, args.backtest_id, args.force)
        if not args.no_grade:
            grade(cfg, args.backtest_id, args.asof_today)
    report(cfg, args.backtest_id)


if __name__ == "__main__":
    main()
