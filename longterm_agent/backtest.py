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
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, date
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

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
        # Phase 5.5 — the stopped book rides alongside the hold-to-horizon columns so the
        # two exit policies stay comparable on every graded trade (forward validation).
        cur.execute("""ALTER TABLE backtest_runs
                       ADD COLUMN IF NOT EXISTS fwd_return_stopped REAL,
                       ADD COLUMN IF NOT EXISTS benchmark_return_stopped REAL,
                       ADD COLUMN IF NOT EXISTS excess_stopped REAL,
                       ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(12),
                       ADD COLUMN IF NOT EXISTS exit_days INTEGER""")
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
# Per-worker isolated (db, neo, graph). The Neo4j driver's reconnect-on-failure path
# closes the shared driver, which would break sibling threads mid-query — so each worker
# thread builds and reuses its OWN context instead of sharing one graph. Parallelism is
# safe for correctness: the B0 cache makes every cohort independent and content-addressed,
# so recorded signals do not depend on execution order.
_tls = threading.local()
_ctx_lock = threading.Lock()
_all_ctxs = []


def _thread_ctx(cfg):
    ctx = getattr(_tls, "ctx", None)
    if ctx is None:
        from longterm_agent.agent.graph import create_longterm_graph
        from news_agent.database.db_manager import DBManager
        db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=3)
        neo = None
        try:
            from graph_db.neo4j_manager import Neo4jManager
            neo = Neo4jManager()
        except Exception as e:
            print(f"⚠ Neo4j unavailable in worker ({e})")
        llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
        llm_reduce = cfg.make_llm(model=cfg.LLM_MODEL, max_tokens=8000)
        graph = create_longterm_graph(llm_map, llm_reduce, cfg, db, neo)
        ctx = _tls.ctx = {"db": db, "neo": neo, "graph": graph}
        with _ctx_lock:
            _all_ctxs.append(ctx)
    return ctx


def _run_one(cfg, backtest_id, prov, horizons, tk, as_of):
    ctx = _thread_ctx(cfg)
    try:
        final = ctx["graph"].invoke({"ticker": tk.upper(), "as_of": as_of,
                                     "lookback_days": cfg.LOOKBACK_DAYS_BACKGROUND,
                                     "shift_window_days": cfg.SHIFT_WINDOW_DAYS, "messages": []})
        _write_rows(cfg, backtest_id, prov, tk, as_of, horizons, final)
        return True
    except Exception as e:
        # One bad (ticker, cohort) must never abort the sweep — log and move on.
        print(f"  {as_of.date()} {tk}: ERROR {type(e).__name__}: {e}")
        return False


def run_cohorts(cfg, tickers, cohorts, horizons, backtest_id, force=False, workers=1):
    from longterm_agent.llm_cache import maybe_enable_llm_cache

    _ensure_table(cfg)
    maybe_enable_llm_cache()  # LT_LLM_CACHE=true → reproducible + cheap re-runs (B0). Global
    # cache, set once here in the main thread; all workers share it (Postgres-backed, safe).
    prov = cfg.provenance()
    print(f"backtest_id={backtest_id}  workers={workers}  provenance={prov}")

    done = set()
    if not force:
        conn = _conn(cfg)
        with conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT ticker, as_of FROM backtest_runs
                           WHERE backtest_id=%s AND signal IS NOT NULL""", [backtest_id])
            done = {(t, d) for t, d in cur.fetchall()}
        conn.close()

    tasks = [(tk, as_of) for as_of in cohorts for tk in tickers
             if (tk.upper(), as_of.date()) not in done]
    skipped = len(cohorts) * len(tickers) - len(tasks)
    if skipped:
        print(f"  skipping {skipped} already-recorded (ticker, cohort) pairs")

    n_run = 0
    if workers <= 1:
        for tk, as_of in tasks:
            n_run += _run_one(cfg, backtest_id, prov, horizons, tk, as_of)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_run_one, cfg, backtest_id, prov, horizons, tk, as_of)
                    for tk, as_of in tasks]
            for f in futs:
                try:
                    n_run += bool(f.result())
                except Exception as e:  # _run_one swallows its own errors; belt-and-suspenders
                    print(f"  worker error: {type(e).__name__}: {e}")

    for ctx in _all_ctxs:  # tidy up the per-thread Neo4j drivers
        if ctx.get("neo"):
            try:
                ctx["neo"].close()
            except Exception:
                pass
    _all_ctxs.clear()
    print(f"ran {n_run}/{len(tasks)} (ticker, cohort) pipelines")


def _write_rows(cfg, backtest_id, prov, ticker, as_of, horizons, final):
    """One row per horizon (the decision is shared; only the measurement window differs)."""
    sig = final.get("signal") or {}
    filtered = final.get("filtered_signal")
    no_data = final.get("no_data", False)
    shift = final.get("background_shift") or {}
    direction = "NO_DATA" if no_data else (sig.get("direction") or "NEUTRAL")
    traded = bool(filtered) and filtered.get("direction") in ("LONG", "SHORT")
    if direction not in ("LONG", "SHORT", "NEUTRAL", "NO_DATA"):
        print(f"  ⚠ {ticker} {as_of.date()}: unexpected direction {direction!r} "
              f"(len {len(str(direction))}) — coercing to NEUTRAL")
        direction = "NEUTRAL"

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
def grade(cfg, backtest_id, asof_today=None, benchmark="SPY"):
    """Fill realized outcome + regime for matured rows — BOTH books: hold-to-horizon
    (fwd_return/excess, back-compat) and stopped (Phase 5.5 exit discipline). Rows graded
    before 5.5 get their stopped columns backfilled idempotently; hold columns are never
    overwritten. `benchmark` is the comparison index (B3: SPY or a sector ETF)."""
    from longterm_agent.exit_rules import stopped_returns
    _ensure_table(cfg)
    today = datetime.strptime(asof_today, "%Y-%m-%d").date() if asof_today else date.today()
    conn = _conn(cfg)
    cur = conn.cursor()
    cur.execute("""SELECT id, ticker, as_of, horizon_days, direction, outcome_filled_at
                   FROM backtest_runs
                   WHERE backtest_id=%s AND (outcome_filled_at IS NULL
                          OR (exit_reason IS NULL AND traded AND direction IN ('LONG','SHORT')))
                   ORDER BY as_of""", [backtest_id])
    rows = cur.fetchall()
    graded = backfilled = 0
    for rid, ticker, as_of, horizon, direction, already in rows:
        if as_of + timedelta(days=horizon) > today:
            continue  # horizon not matured yet
        fwd = _ret(cur, ticker, as_of, horizon)
        bench = _ret(cur, benchmark, as_of, horizon)
        if fwd is None:
            continue  # missing prices; retry next run
        # stopped book (traded directional rows only — the exit rule is a trade property)
        stopped = None
        if direction in ("LONG", "SHORT"):
            stopped = stopped_returns(cur, ticker, as_of, direction, horizon,
                                      benchmark=benchmark, stop_loss=cfg.STOP_LOSS_PCT,
                                      take_profit=cfg.TAKE_PROFIT_PCT)
        if stopped:
            ex_st = trade_excess(direction, stopped["fwd"], stopped["bench"])
            cur.execute("""UPDATE backtest_runs SET fwd_return_stopped=%s,
                           benchmark_return_stopped=%s, excess_stopped=%s,
                           exit_reason=%s, exit_days=%s WHERE id=%s""",
                        [round(stopped["fwd"], 4), round(stopped["bench"], 4),
                         round(ex_st, 4) if ex_st is not None else None,
                         stopped["reason"], stopped["days"], rid])
        if already is None:
            ex = trade_excess(direction, fwd, bench) if direction in ("LONG", "SHORT") else None
            cur.execute("""UPDATE backtest_runs SET fwd_return=%s, benchmark_return=%s, excess=%s,
                           regime=%s, outcome_filled_at=%s WHERE id=%s""",
                        [round(fwd, 4), round(bench, 4) if bench is not None else None,
                         round(ex, 4) if ex is not None else None, _regime(cur, as_of), today, rid])
            graded += 1
        else:
            backfilled += 1
    conn.commit()
    conn.close()
    print(f"graded {graded} matured rows (today={today})"
          + (f"; stopped-book backfilled on {backfilled} already-graded rows" if backfilled else ""))


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
                          shift_flag, regime, excess, excess_stopped, exit_reason
                   FROM backtest_runs WHERE backtest_id=%s ORDER BY horizon_days, as_of""",
                [backtest_id])
    rows = [dict(zip(("horizon", "ticker", "as_of", "direction", "conviction", "traded",
                      "shift_flag", "regime", "excess", "excess_stopped", "exit_reason"),
                     r)) for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT provenance::text FROM backtest_runs WHERE backtest_id=%s""",
                [backtest_id])
    prov_stamps = [r[0] for r in cur.fetchall()]
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
    print(f"provenance: {prov_stamps[0] if prov_stamps else None}")
    print(f"universe={len(tickers)}  cohorts={len(cohorts)} ({cohorts[0]}..{cohorts[-1]})  "
          f"horizons={horizons}")
    # B3 — out-of-sample discipline: the config must be FROZEN across a walk-forward sweep. More
    # than one provenance stamp means knobs/model/code changed mid-protocol → results are not OOS.
    if len(prov_stamps) > 1:
        print(f"  ⚠ OOS DISCIPLINE VIOLATED: {len(prov_stamps)} distinct provenance stamps under "
              f"this backtest_id — config was not frozen; re-run under a fresh --backtest-id.")
    else:
        print("  ✓ config frozen (single provenance stamp) — OOS discipline holds")

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

        # Phase 5.5 — the SAME trades under the stop rule (both books graded per trade)
        stopped = [r for r in graded if r["excess_stopped"] is not None]
        if stopped:
            exs = [r["excess_stopped"] for r in stopped]
            ds = M.distribution(exs)
            s_ci = M.bootstrap_ci(exs, lambda s: M.distribution(s)["mean"])
            n_stop = sum(1 for r in stopped if r["exit_reason"] == "stop")
            n_tp = sum(1 for r in stopped if r["exit_reason"] == "take_profit")
            print(f"  stopped book : win {M.win_rate(exs):.0%}  mean {_pct(ds['mean'])} "
                  f"CI {_ci(*s_ci)}  min {_pct(ds['min'])}  "
                  f"(exits: {n_stop} stop / {n_tp} tp / {len(stopped) - n_stop - n_tp} horizon)")

        cal = M.calibration_curve(graded)
        print("  conviction calibration (is conviction predictive?):")
        for b in cal:
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
            print("  by regime   : " + "   ".join(line))
        _protocol_checks(graded, cal)
    print("\n" + "=" * 74)


def _protocol_checks(graded, cal):
    """B3 gates on a horizon's graded trades: conviction-calibration monotonicity (is conviction
    trustworthy as a sizing input?) and multiple-testing FDR control across per-ticker tests."""
    print("  B3 protocol checks:")
    mono = M.is_monotonic([b["win_rate"] for b in cal if b["n"]])
    verdict = {True: "YES — monotonic", False: "NO — non-monotonic (do not size by conviction)",
               None: "insufficient buckets"}[mono]
    print(f"    calibration monotonic (sizing-trustworthy): {verdict}")
    # multiple-testing: per-ticker H0 = mean excess ≤ 0; BH-control the false-discovery rate.
    by_tk = {}
    for r in graded:
        by_tk.setdefault(r["ticker"], []).append(r["excess"])
    testable = {tk: ex for tk, ex in by_tk.items() if len(ex) >= 3}
    if not testable:
        print("    multiple-testing: no ticker has ≥3 graded trades yet — not testable")
        return
    names = sorted(testable)
    pvals = [M.bootstrap_pvalue(testable[tk], side="greater") for tk in names]
    reject, crit = M.benjamini_hochberg(pvals, alpha=0.05)
    survivors = [names[i] for i in range(len(names)) if reject[i]]
    print(f"    multiple-testing (per-ticker H0: mean excess ≤ 0, {len(names)} tested): "
          f"{len(survivors)} survive BH FDR 5%"
          + (f" — {survivors}" if survivors else " (none — no per-ticker edge survives correction)"))


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="Walk-forward backtest harness (B1)")
    ap.add_argument("--backtest-id", required=True)
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--step", type=int, default=7)
    ap.add_argument("--horizons", nargs="+", type=int, default=[28])
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel worker threads (each gets its own db/neo/graph). "
                         "B0 cache keeps results order-independent, so this is reproducible.")
    ap.add_argument("--force", action="store_true", help="re-run cohorts already recorded")
    ap.add_argument("--report-only", action="store_true", help="skip run+grade, just print")
    ap.add_argument("--no-grade", action="store_true", help="skip grading")
    ap.add_argument("--benchmark", default="SPY", help="benchmark ticker (B3: SPY or a sector ETF)")
    ap.add_argument("--asof-today", help="override 'today' for grading (YYYY-MM-DD)")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    if not args.report_only:
        if args.tickers and args.start and args.end:
            cohorts = _weekly(datetime.strptime(args.start, "%Y-%m-%d"),
                              datetime.strptime(args.end, "%Y-%m-%d"), args.step)
            run_cohorts(cfg, args.tickers, cohorts, args.horizons, args.backtest_id,
                        args.force, args.workers)
        if not args.no_grade:
            grade(cfg, args.backtest_id, args.asof_today, args.benchmark)
    report(cfg, args.backtest_id)


if __name__ == "__main__":
    main()
