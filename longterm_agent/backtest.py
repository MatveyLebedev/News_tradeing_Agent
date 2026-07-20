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
from longterm_agent.tools.pit import daily_bar_cutoff
from longterm_agent import backtest_metrics as M

_DDL = Path(__file__).parent / "create_backtest_runs_table.sql"
MIN_N = 30  # below this the report prints an ESTIMATE banner instead of a verdict
# Grace period past a row's expected maturity before its missing outcome is reported as
# STALE-UNGRADED. The graders skip rows whose price series never reaches the exit date
# (halt, delisting, a ticker the fetcher dropped) — silently, which is exactly how a
# worst-case outcome disappears from a book instead of scoring in it.
STALE_GRACE_DAYS = 14


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
                       ADD COLUMN IF NOT EXISTS exit_days INTEGER,
                       ADD COLUMN IF NOT EXISTS regime_context JSONB,
                       ADD COLUMN IF NOT EXISTS entry_date DATE,
                       ADD COLUMN IF NOT EXISTS entry_px REAL,
                       ADD COLUMN IF NOT EXISTS fill_convention VARCHAR(12),
                       ADD COLUMN IF NOT EXISTS fwd_return_t2 REAL,
                       ADD COLUMN IF NOT EXISTS benchmark_return_t2 REAL,
                       ADD COLUMN IF NOT EXISTS excess_t2 REAL,
                       ADD COLUMN IF NOT EXISTS gate_trace JSONB,
                       ADD COLUMN IF NOT EXISTS beta REAL,
                       ADD COLUMN IF NOT EXISTS excess_beta REAL""")
    conn.commit()
    conn.close()


def _weekly(start, end, step):
    d, out = start, []
    while d <= end:
        out.append(d)
        d += timedelta(days=step)
    return out


def _regime(cur, as_of: date, lookback=60, band=0.03):
    """Market regime at as_of from SPY's trailing return: bull / bear / flat.

    The window ends at the last COMPLETED session, not the as_of day itself — that day's
    close prints hours after a midnight/pre-open cohort stamp. Label only (it drives the
    report's regime split, never a decision), but a split keyed on a same-day close is
    still attributing outcomes to a regime the decision could not observe.
    """
    end = daily_bar_cutoff(as_of)
    r = _ret(cur, "SPY", end - timedelta(days=lookback), lookback)
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
                     direction, conviction, traded, shift_flag, shift_score, signal, gate_trace)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (backtest_id, ticker, as_of, horizon_days) DO UPDATE SET
                    provenance=EXCLUDED.provenance, agent_version=EXCLUDED.agent_version,
                    direction=EXCLUDED.direction, conviction=EXCLUDED.conviction,
                    traded=EXCLUDED.traded, shift_flag=EXCLUDED.shift_flag,
                    shift_score=EXCLUDED.shift_score, signal=EXCLUDED.signal,
                    gate_trace=EXCLUDED.gate_trace
            """, [backtest_id, Json(prov), cfg.AGENT_VERSION, ticker.upper(), as_of.date(), h,
                  direction, sig.get("conviction"), traded, bool(shift.get("has_shifted")),
                  shift.get("shift_score"), Json(filtered or sig or {}),
                  Json(final.get("gate_trace") or {})])
    conn.commit()
    conn.close()
    print(f"  ran {as_of.date()} {ticker}: {direction} conv={sig.get('conviction')} traded={traded}")


# --------------------------------------------------------------------------- grade
def grade(cfg, backtest_id, asof_today=None, benchmark="SPY", fill="t1", dual_book=False):
    """Fill realized outcome + regime for matured rows — BOTH books: hold-to-horizon
    (fwd_return/excess, back-compat) and stopped (Phase 5.5 exit discipline). Rows graded
    before 5.5 get their stopped columns backfilled idempotently; hold columns are never
    overwritten. `benchmark` is the comparison index (B3: SPY or a sector ETF).

    fill="d0" fills PRE-OPEN cohorts at the as_of day's own open (open_d0 — legal only
    when the run used the PIT feature cutoff, i.e. any post-2026-07-19 sweep).
    dual_book additionally fills the diagnostic next-bar book (fwd/benchmark/excess_t2)
    — the paired day-1-value study; hold-only by design (no stopped leg)."""
    from longterm_agent.exit_rules import (FILL_CONVENTION, FILL_CONVENTION_D0,
                                           entry_bar, stopped_returns, t1_return)
    from longterm_agent.beta import beta_adjusted_excess, estimate_beta
    from longterm_agent.tools.regime_tool import regime_context_from_conn
    d0 = fill == "d0"
    convention = FILL_CONVENTION_D0 if d0 else FILL_CONVENTION
    _ensure_table(cfg)
    today = datetime.strptime(asof_today, "%Y-%m-%d").date() if asof_today else date.today()
    conn = _conn(cfg)
    cur = conn.cursor()
    cur.execute("""SELECT id, ticker, as_of, horizon_days, direction, outcome_filled_at,
                          regime_context IS NULL, beta IS NULL
                   FROM backtest_runs
                   WHERE backtest_id=%s AND (outcome_filled_at IS NULL
                          OR (exit_reason IS NULL AND traded AND direction IN ('LONG','SHORT'))
                          OR regime_context IS NULL
                          OR (beta IS NULL AND direction IN ('LONG','SHORT')))
                   ORDER BY as_of""", [backtest_id])
    rows = cur.fetchall()
    graded = backfilled = stamped = betas = 0
    for rid, ticker, as_of, horizon, direction, already, no_regime, no_beta in rows:
        # Phase 14 — regime stamp is a property of as_of (not of maturity): fill it for any
        # row missing one, incl. idempotent backfill of rows graded before the stamp existed.
        # Left NULL while macro_series is empty so a later pass can still fill it.
        if no_regime:
            ctx = regime_context_from_conn(conn, as_of)
            if ctx.get("available"):
                cur.execute("UPDATE backtest_runs SET regime_context=%s WHERE id=%s",
                            [Json(ctx), rid])
                stamped += 1
        eb = entry_bar(cur, ticker, as_of, include_asof_day=d0)
        if eb is None:
            continue  # entry bar not in stock_prices_daily yet; retry next run
        if eb[0] + timedelta(days=horizon) > today:
            continue  # horizon not matured yet (anchored at the open-entry bar)
        tr = t1_return(cur, ticker, as_of, horizon, include_asof_day=d0)
        br = t1_return(cur, benchmark, as_of, horizon, include_asof_day=d0)
        if tr is None:
            continue  # missing prices; retry next run
        fwd = tr["ret"]
        bench = br["ret"] if br else None
        # diagnostic next-bar book (dual_book): same decision, entry one bar later —
        # the paired delta prices day-1 of the move
        if dual_book:
            t2 = t1_return(cur, ticker, as_of, horizon, nth=2, include_asof_day=d0)
            b2 = t1_return(cur, benchmark, as_of, horizon, nth=2, include_asof_day=d0)
            if t2 is not None:
                ex2 = trade_excess(direction, t2["ret"], b2["ret"] if b2 else None)
                cur.execute("""UPDATE backtest_runs SET fwd_return_t2=%s,
                               benchmark_return_t2=%s, excess_t2=%s WHERE id=%s""",
                            [round(t2["ret"], 4),
                             round(b2["ret"], 4) if b2 else None,
                             round(ex2, 4) if ex2 is not None else None, rid])
        # beta-adjusted book (P3): did the stock beat what its own market sensitivity
        # predicted, or was the excess just factor exposure? Reporting only; idempotent
        # backfill for rows graded before the column existed. Beta is estimated from bars
        # strictly BEFORE the entry, so it is knowable at decision time.
        if no_beta and direction in ("LONG", "SHORT"):
            bta = estimate_beta(cur, ticker, eb[0], benchmark)
            if bta is not None:
                ex_b = beta_adjusted_excess(direction, fwd, bench, bta)
                cur.execute("""UPDATE backtest_runs SET beta=%s, excess_beta=%s WHERE id=%s""",
                            [bta, round(ex_b, 4) if ex_b is not None else None, rid])
                betas += 1
        # stopped book (traded directional rows only — the exit rule is a trade property)
        stopped = None
        if direction in ("LONG", "SHORT"):
            stopped = stopped_returns(cur, ticker, as_of, direction, horizon,
                                      benchmark=benchmark, stop_loss=cfg.STOP_LOSS_PCT,
                                      take_profit=cfg.TAKE_PROFIT_PCT,
                                      include_asof_day=d0)
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
                           regime=%s, outcome_filled_at=%s,
                           entry_date=%s, entry_px=%s, fill_convention=%s WHERE id=%s""",
                        [round(fwd, 4), round(bench, 4) if bench is not None else None,
                         round(ex, 4) if ex is not None else None, _regime(cur, as_of), today,
                         eb[0], eb[1], convention, rid])
            graded += 1
        else:
            backfilled += 1
    conn.commit()
    conn.close()
    print(f"graded {graded} matured rows (today={today})"
          + (f"; stopped-book backfilled on {backfilled} already-graded rows" if backfilled else "")
          + (f"; regime_context stamped on {stamped} rows" if stamped else "")
          + (f"; beta estimated on {betas} rows" if betas else ""))


# --------------------------------------------------------------------------- report
def _pct(x):
    return "  n/a" if x is None else f"{x:+.2%}"


def _ci(lo, hi, pct=True):
    if lo is None or hi is None:
        return "[ n/a ]"
    f = (lambda v: f"{v:+.2%}") if pct else (lambda v: f"{v:.2f}")
    return f"[{f(lo)}, {f(hi)}]"


def report(cfg, backtest_id, asof_today=None):
    today = datetime.strptime(asof_today, "%Y-%m-%d").date() if asof_today else date.today()
    _ensure_table(cfg)  # --report-only must work standalone on a book graded by older code
    conn = _conn(cfg)
    cur = conn.cursor()
    cur.execute("""SELECT horizon_days, ticker, as_of, direction, conviction, traded,
                          shift_flag, regime, excess, excess_stopped, exit_reason, regime_context,
                          beta, excess_beta
                   FROM backtest_runs WHERE backtest_id=%s ORDER BY horizon_days, as_of""",
                [backtest_id])
    rows = [dict(zip(("horizon", "ticker", "as_of", "direction", "conviction", "traded",
                      "shift_flag", "regime", "excess", "excess_stopped", "exit_reason",
                      "regime_context", "beta", "excess_beta"),
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
        # A trade whose prices never arrive is dropped by the graders forever. Left silent,
        # that is survivorship in the worst direction: a halt or delisting mid-horizon is
        # the outcome most likely to be missing AND most likely to be catastrophic.
        stale = [r for r in traded if r["excess"] is None
                 and r["as_of"] + timedelta(days=h + STALE_GRACE_DAYS) < today]
        if stale:
            print(f"  ⚠ STALE-UNGRADED: {len(stale)} traded row(s) >{STALE_GRACE_DAYS}d past "
                  f"maturity with no outcome (price series never reached the exit — halt / "
                  f"delisting / missing bars). NOT in the numbers below: "
                  f"{sorted({r['ticker'] for r in stale})}")
        if not graded:
            print("  no graded trades yet (horizons not matured / prices missing).")
            continue
        gate = "" if len(graded) >= MIN_N else f"  ⚠ ESTIMATE (n={len(graded)} < {MIN_N}; harness check, not a verdict)"
        d = M.distribution(ex)
        wr = M.win_rate(ex)
        wr_ci = M.bootstrap_ci(ex, M.win_rate)
        mean_ci = M.bootstrap_ci(ex, lambda s: M.distribution(s)["mean"])
        # Trades inside one cohort share a market week, and weekly cohorts overlap at these
        # horizons — so the per-trade CI is too narrow. The cohort-block CI resamples whole
        # weeks; when the two disagree, the block one is the honest width.
        cohort_blocks = [[r["excess"] for r in graded if r["as_of"] == c]
                         for c in sorted({r["as_of"] for r in graded})]
        mean_ci_blk = M.bootstrap_ci_blocks(cohort_blocks,
                                            lambda s: M.distribution(s)["mean"])
        print(f"  win rate     : {wr:.0%}   CI {_ci(*wr_ci)}{gate}")
        print(f"  mean excess  : {_pct(d['mean'])}   CI {_ci(*mean_ci)}")
        print(f"               : cohort-block CI {_ci(*mean_ci_blk)} "
              f"({len(cohort_blocks)} cohorts — corrects for within-week correlation)")
        print(f"  median / IQR : {_pct(d['median'])}   [{_pct(d['p25'])}, {_pct(d['p75'])}]   "
              f"min {_pct(d['min'])}  max {_pct(d['max'])}")
        # portfolio view: per-cohort mean excess → Sharpe/IR + drawdown of the equity curve
        series = [M.distribution(b)["mean"] for b in cohort_blocks]
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

        # P3 — the SAME trades scored against a beta-matched benchmark: how much of the
        # excess was stock-picking rather than factor exposure? (reporting only)
        betad = [r for r in graded if r["excess_beta"] is not None]
        if betad:
            exb = [r["excess_beta"] for r in betad]
            db = M.distribution(exb)
            b_ci = M.bootstrap_ci(exb, lambda s: M.distribution(s)["mean"])
            betas = [r["beta"] for r in betad if r["beta"] is not None]
            print(f"  beta-adj book: win {M.win_rate(exb):.0%}  mean {_pct(db['mean'])} "
                  f"CI {_ci(*b_ci)}  (n={len(exb)}, median beta "
                  f"{M.distribution(betas)['median']:.2f})")

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
        _regime_context_splits(graded)
        _protocol_checks(graded, cal)
    print("\n" + "=" * 74)


def _vix_band(ctx):
    v = (ctx or {}).get("vix")
    if v is None:
        return None
    return "vix<20" if v < 20 else ("vix20-30" if v <= 30 else "vix>30")


def _hy_band(ctx):
    p = (ctx or {}).get("hy_oas_pctile_5y")
    if p is None:
        return None
    return "hy<p50" if p < 50 else ("hy_p50-80" if p <= 80 else "hy>p80")


def _regime_context_splits(graded):
    """Phase 14 — descriptive splits by the stamped macro regime (VIX / HY-OAS bands).
    A single populated band is itself the finding: the sample spans ONE regime, so
    regime-conditional conclusions are not yet supported (and nothing gates on this)."""
    for label, fn in (("vix band", _vix_band), ("hy band", _hy_band)):
        buckets = {}
        for r in graded:
            b = fn(r.get("regime_context"))
            if b is not None:
                buckets.setdefault(b, []).append(r["excess"])
        if not buckets:
            continue
        line = [f"{b}: n={len(sub)} mean {_pct(M.distribution(sub)['mean'])}"
                for b, sub in sorted(buckets.items())]
        note = "  (single band — one-regime sample)" if len(buckets) == 1 else ""
        print(f"  by {label:9s}: " + "   ".join(line) + note)


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
    ap.add_argument("--trace", action="store_true", help="enable LangSmith tracing")
    ap.add_argument("--at", default=None, metavar="HH:MM",
                    help="time-of-day for every cohort as_of (e.g. 06:30 for pre-open "
                         "Monday cohorts; default midnight)")
    ap.add_argument("--fill", choices=["t1", "d0"], default="t1",
                    help="fill convention for grading: t1 = open of the bar AFTER the "
                         "as_of day (legacy grids); d0 = the as_of day's own open "
                         "(pre-open cohorts under the PIT feature cutoff)")
    ap.add_argument("--dual-book", action="store_true",
                    help="also grade the diagnostic next-bar book (fwd/excess_t2) — "
                         "paired day-1-value study")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    if args.trace:
        cfg.enable_tracing()
    if not args.report_only:
        if args.tickers and args.start and args.end:
            cohorts = _weekly(datetime.strptime(args.start, "%Y-%m-%d"),
                              datetime.strptime(args.end, "%Y-%m-%d"), args.step)
            if args.at:
                hh, mm = map(int, args.at.split(":"))
                cohorts = [c + timedelta(hours=hh, minutes=mm) for c in cohorts]
            run_cohorts(cfg, args.tickers, cohorts, args.horizons, args.backtest_id,
                        args.force, args.workers)
        if not args.no_grade:
            grade(cfg, args.backtest_id, args.asof_today, args.benchmark,
                  fill=args.fill, dual_book=args.dual_book)
    report(cfg, args.backtest_id, args.asof_today)


if __name__ == "__main__":
    main()
