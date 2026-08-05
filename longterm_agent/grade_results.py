"""
Grade matured longterm_agent_results rows: fill fwd_return_4w + benchmark_return_4w once the
4-week horizon has elapsed. This is what gives the Telegram bot a trade win-rate. Mirrors
grade_shifts.py (which grades the shift_library); both read stock_prices_daily and use SPY
as the benchmark. Decisions used data ≤ as_of; only the *outcome* is measured later.

Phase 5.5 additions, run on the same daily cadence:
  - the STOPPED book (structural stop-loss exit, exit_rules.py) is graded alongside the
    hold-to-horizon columns for every matured trade — live data validates the rule forward;
  - open traded rows whose current position return has breached the stop trigger a
    one-time Telegram alert (exit_alert_at marks sent).

    python -m longterm_agent.grade_results
"""
import argparse
from datetime import datetime, timedelta, date
from math import isfinite
from types import SimpleNamespace

import psycopg2
import pytz
from psycopg2.extras import Json

from longterm_agent import oplog
from longterm_agent.config import LongTermAgentConfig
from longterm_agent.grade_shifts import _has_data_through  # shared grading guard
from longterm_agent.exit_rules import (FILL_CONVENTION, FILL_CONVENTION_MID, entry_bar,
                                       fetch_price_path, resolve_entry, simulate_exit,
                                       stopped_returns, t1_return)
from longterm_agent.beta import beta_adjusted_excess, estimate_beta
from longterm_agent.sector_map import SECTOR_ETF, SECTOR_OF
from longterm_agent.targets import (classify_fundamentals_realized, classify_shift_outcome,
                                    worst_position_return)

ET = pytz.timezone("America/New_York")
BENCHMARK = "SPY"
HORIZON_DAYS = 28  # 4 weeks (the primary checkpoint)
# Phase 11 multi-target horizons (2w/12w ride alongside the primary 4w columns)
EXTRA_HORIZONS = {"2w": 14, "12w": 84}
FUND_REALIZED_LAG_DAYS = 120  # next quarter must have FILED before the target is gradable


def _ensure_exit_columns(cur):
    cur.execute("""ALTER TABLE longterm_agent_results
                   ADD COLUMN IF NOT EXISTS fwd_return_stopped REAL,
                   ADD COLUMN IF NOT EXISTS benchmark_return_stopped REAL,
                   ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(12),
                   ADD COLUMN IF NOT EXISTS exit_days INTEGER,
                   ADD COLUMN IF NOT EXISTS exit_alert_at DATE,
                   ADD COLUMN IF NOT EXISTS entry_date DATE,
                   ADD COLUMN IF NOT EXISTS entry_px REAL,
                   ADD COLUMN IF NOT EXISTS fill_convention VARCHAR(12),
                   ADD COLUMN IF NOT EXISTS beta REAL,
                   ADD COLUMN IF NOT EXISTS excess_beta REAL""")
    # Phase 11 — per-node target set (multi-horizon, sector-relative, adverse excursion,
    # realized fundamentals + shift persistence in aux_targets JSONB)
    cur.execute("""ALTER TABLE longterm_agent_results
                   ADD COLUMN IF NOT EXISTS fwd_return_2w REAL,
                   ADD COLUMN IF NOT EXISTS benchmark_return_2w REAL,
                   ADD COLUMN IF NOT EXISTS fwd_return_12w REAL,
                   ADD COLUMN IF NOT EXISTS benchmark_return_12w REAL,
                   ADD COLUMN IF NOT EXISTS sector_benchmark VARCHAR(8),
                   ADD COLUMN IF NOT EXISTS sector_benchmark_return_4w REAL,
                   ADD COLUMN IF NOT EXISTS worst_position_return_4w REAL,
                   ADD COLUMN IF NOT EXISTS aux_targets JSONB""")


def _finite_ret(x):
    """The return as a plain float, or None if it is missing or not a real number.

    `round(nan, 4)` returns nan and raises nothing, so an unguarded grade turns a defective
    price bar into a NaN "outcome" that then passes every `IS NOT NULL` filter downstream —
    which is how 21 shift_library rows were graded against the poisoned 2026-07-24 closes
    and went on to poison the analog medians. An outcome that is not a number is not an
    outcome: leave the row ungraded and let a later run retry it.
    """
    if x is None:
        return None
    v = x["ret"] if isinstance(x, dict) else x
    return v if v is not None and isfinite(v) else None


def _decision_moment(run_timestamp):
    """`run_timestamp` as a NAIVE ET wall clock — the moment the signal existed.

    Stamped NOW() when the row is persisted, i.e. AFTER the pipeline finished, so it needs
    no added "agent working time": the runtime is already inside it. Minute bars are stored
    naive ET (docs: timezone conventions), so the tz has to come off before they can be
    compared. Returns None when the column is empty (rows written before it existed).
    """
    if run_timestamp is None:
        return None
    if run_timestamp.tzinfo is not None:
        run_timestamp = run_timestamp.astimezone(ET).replace(tzinfo=None)
    return run_timestamp


def _fill_extra_horizons(cur, rid, ticker, as_of, entry_date, today, max_dates, missing,
                         entry=None, bench_entry=None):
    """Fill 2w/12w return pairs that are matured, data-complete, and still NULL."""
    filled = 0
    for tag, days in EXTRA_HORIZONS.items():
        if tag not in missing:
            continue
        target = entry_date + timedelta(days=days)
        if target > today:
            continue
        if not (_has_data_through(cur, ticker, target, max_dates)
                and _has_data_through(cur, BENCHMARK, target, max_dates)):
            continue
        r = _finite_ret(t1_return(cur, ticker, as_of, days, entry=entry))
        b = _finite_ret(t1_return(cur, BENCHMARK, as_of, days, entry=bench_entry))
        if r is None or b is None:
            continue
        cur.execute(f"""UPDATE longterm_agent_results
                        SET fwd_return_{tag}=%s, benchmark_return_{tag}=%s WHERE id=%s""",
                    [round(r, 4), round(b, 4), rid])
        filled += 1
    return filled


def _post_shift_daily_means(neo, db_config, ticker, as_of, days):
    """Post-as_of daily mean AFFECTS sentiment (graph labels ±1/0) — the detection-v2
    estimator measured FORWARD, so the persistence target grades what detection saw."""
    from longterm_agent.tools.news_corpus_tool import SENTIMENT_NUM, _affects_news_ids
    labels = _affects_news_ids(ticker, neo, 0.5)
    if not labels:
        return []
    conn = psycopg2.connect(**db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, published FROM news
                           WHERE id = ANY(%s) AND published > %s AND published <= %s""",
                        [list(labels), as_of, as_of + timedelta(days=days)])
            rows = cur.fetchall()
    finally:
        conn.close()
    by_day = {}
    for nid, pub in rows:
        s = SENTIMENT_NUM.get(labels.get(nid))
        if s is not None:
            by_day.setdefault(pub.date(), []).append(s)
    return [sum(v) / len(v) for _, v in sorted(by_day.items())]


def scan_open_breaches(cur, cfg, today) -> int:
    """One-time Telegram alert for open trades whose position return breached the stop.
    Best-effort: alerting must never block grading."""
    cur.execute("""
        SELECT id, ticker, as_of, verdict, run_timestamp FROM longterm_agent_results
        WHERE agent_version=%s AND traded AND verdict IN ('LONG','SHORT')
          AND outcome_filled_at IS NULL AND exit_alert_at IS NULL
    """, [cfg.AGENT_VERSION])
    open_rows = cur.fetchall()
    if not open_rows:
        return 0
    try:
        from core.notify import TelegramNotifier
        notifier = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    except Exception:
        notifier = None
    alerted = 0
    for rid, ticker, as_of, verdict, run_ts in open_rows:
        # The stop is measured FROM THE FILL, so the scanner must resolve the entry the
        # same way the grader does. Using the daily convention on a mid-session trade would
        # measure the stop from a price the position never paid.
        re_ = resolve_entry(cur, ticker, as_of, _decision_moment(run_ts))
        if re_ is None:
            continue  # position opens at the next session's open — nothing to scan yet
        fill_conv, e_date, e_px = re_
        days_open = min((today - e_date).days, HORIZON_DAYS)
        if days_open <= 0:
            continue
        path = [(d, c) for d, c in fetch_price_path(
            cur, ticker, as_of, days_open,
            include_asof_day=(fill_conv == FILL_CONVENTION_MID)) if d <= today]
        sim = simulate_exit(path, e_date, e_px, verdict, days_open,
                            stop_loss=cfg.STOP_LOSS_PCT, take_profit=cfg.TAKE_PROFIT_PCT)
        if not sim or sim["reason"] != "stop":
            continue
        sign = 1 if verdict == "LONG" else -1
        pos = sign * (sim["exit_px"] - sim["entry_px"]) / sim["entry_px"]
        msg = (f"⛔ STOP BREACH {ticker} {verdict} (as_of {as_of}): position {pos:+.1%} "
               f"through the {cfg.STOP_LOSS_PCT:.0%} stop on {sim['exit_date']} — "
               f"exit per Phase 5.5 discipline")
        print(f"  {msg}")
        if notifier and notifier.enabled:  # property, not a method
            try:
                notifier.send(msg)
            except Exception as e:
                print(f"  ⚠ stop-breach alert failed: {e}")
        cur.execute("UPDATE longterm_agent_results SET exit_alert_at=%s WHERE id=%s",
                    [today, rid])
        alerted += 1
    return alerted


def main():
    ap = argparse.ArgumentParser(description="Grade matured longterm_agent_results rows (4w vs SPY)")
    ap.add_argument("--asof-today", default=None, help="override 'today' (YYYY-MM-DD) for testing")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    today = datetime.strptime(args.asof_today, "%Y-%m-%d").date() if args.asof_today else date.today()

    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    _ensure_exit_columns(cur)
    cur.execute("""
        SELECT id, ticker, as_of, verdict, outcome_filled_at,
               fwd_return_2w IS NULL, fwd_return_12w IS NULL,
               sector_benchmark, sector_benchmark_return_4w IS NULL,
               worst_position_return_4w IS NULL, aux_targets,
               background_shift->>'direction', fundamentals_summary->>'verdict',
               run_timestamp
        FROM longterm_agent_results
        WHERE agent_version=%s AND verdict IN ('LONG','SHORT')
          AND (outcome_filled_at IS NULL OR (traded AND exit_reason IS NULL)
               OR fwd_return_2w IS NULL OR fwd_return_12w IS NULL
               OR sector_benchmark IS NULL OR worst_position_return_4w IS NULL
               OR aux_targets IS NULL OR beta IS NULL
               OR NOT (aux_targets ?& array['shift_outcome','fundamentals_realized']))
        ORDER BY as_of
    """, [cfg.AGENT_VERSION])
    rows = cur.fetchall()
    print(f"{len(rows)} rows to grade/backfill; today={today}")

    # Phase 11 shared handles — best-effort: multi-target grading must never block the
    # primary 4w book (a missing graph just leaves aux_targets partial until the next run).
    dbm = SimpleNamespace(db_config=cfg.get_db_config())
    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"  ⚠ Neo4j unavailable — shift persistence targets deferred ({e})")

    graded, pending, backfilled, extras = 0, 0, 0, 0
    mid_pending_bench = 0
    max_dates = {}
    for (rid, ticker, as_of, verdict, already, no_2w, no_12w, sector_bm, no_sec_ret,
         no_worst, aux, shift_dir, fund_verdict, run_ts) in rows:
        decision = _decision_moment(run_ts)
        re_ = resolve_entry(cur, ticker, as_of, decision)
        if re_ is None:
            pending += 1  # entry session hasn't landed / no minute print yet
            continue
        fill_conv, e_date, e_px = re_
        # Both legs must be priced under the SAME convention: a stock filled at 12:02
        # against a benchmark filled at 09:30 measures the two over different windows and
        # books the gap as skill. SPY has no minute table until the fetcher fix ships, so
        # in-session rows stay PENDING rather than grade wrong — and the count says so.
        entry = bench_entry = None
        if fill_conv == FILL_CONVENTION_MID:
            bre = resolve_entry(cur, BENCHMARK, as_of, decision)
            if bre is None or bre[0] != FILL_CONVENTION_MID:
                mid_pending_bench += 1
                pending += 1
                continue
            entry, bench_entry = (e_date, e_px), (bre[1], bre[2])
        # --- Phase 11 multi-horizon fills (each matures on its own clock) --------------
        missing = {t for t, flag in (("2w", no_2w), ("12w", no_12w)) if flag}
        extras += _fill_extra_horizons(cur, rid, ticker, as_of, e_date, today, max_dates,
                                       missing, entry=entry, bench_entry=bench_entry)

        target = e_date + timedelta(days=HORIZON_DAYS)
        if target > today:
            continue  # 4w horizon not matured yet
        # pending_data guard (Phase 2): both series must actually reach the exit date,
        # and the benchmark is REQUIRED — the trade's score IS excess-vs-SPY, so a row
        # graded without it could never be scored. Ungraded rows retry next run.
        if not (_has_data_through(cur, ticker, target, max_dates)
                and _has_data_through(cur, BENCHMARK, target, max_dates)):
            pending += 1
            continue
        r = _finite_ret(t1_return(cur, ticker, as_of, HORIZON_DAYS, entry=entry))
        b = _finite_ret(t1_return(cur, BENCHMARK, as_of, HORIZON_DAYS, entry=bench_entry))
        if r is None or b is None:
            pending += 1
            continue
        # stopped book (Phase 5.5) — same trade under the structural exit rule
        stopped = stopped_returns(cur, ticker, as_of, verdict, HORIZON_DAYS,
                                  benchmark=BENCHMARK, stop_loss=cfg.STOP_LOSS_PCT,
                                  take_profit=cfg.TAKE_PROFIT_PCT,
                                  entry=entry, bench_entry=bench_entry)
        s_fwd = _finite_ret(stopped["fwd"]) if stopped else None
        s_bench = _finite_ret(stopped["bench"]) if stopped else None
        if stopped and s_fwd is not None and s_bench is not None:
            cur.execute("""UPDATE longterm_agent_results
                           SET fwd_return_stopped=%s, benchmark_return_stopped=%s,
                               exit_reason=%s, exit_days=%s WHERE id=%s""",
                        [round(s_fwd, 4), round(s_bench, 4),
                         stopped["reason"], stopped["days"], rid])
        # P3 — beta-adjusted book: the same trade against a beta-matched benchmark, so
        # "picked the stock" is separable from "picked the factor". Reporting only.
        bta = _finite_ret(estimate_beta(cur, ticker, e_date, BENCHMARK))
        if bta is not None:
            ex_b = _finite_ret(beta_adjusted_excess(verdict, r, b, bta))
            cur.execute("""UPDATE longterm_agent_results SET beta=%s, excess_beta=%s
                           WHERE id=%s""",
                        [bta, round(ex_b, 4) if ex_b is not None else None, rid])
        if already is None:
            cur.execute("""
                UPDATE longterm_agent_results
                SET fwd_return_4w=%s, benchmark_return_4w=%s, outcome_filled_at=%s,
                    entry_date=%s, entry_px=%s, fill_convention=%s WHERE id=%s
            """, [round(r, 4), round(b, 4), today, e_date, e_px, fill_conv, rid])
            graded += 1
        else:
            backfilled += 1

        # --- Phase 11 targets at the 4w checkpoint --------------------------------------
        # sector-relative benchmark ("right about the stock, or just long the sector?")
        if sector_bm is None:
            sector_bm = SECTOR_ETF.get(SECTOR_OF.get(ticker.upper(), ""), "NONE")
            cur.execute("UPDATE longterm_agent_results SET sector_benchmark=%s WHERE id=%s",
                        [sector_bm, rid])
        if no_sec_ret and sector_bm and sector_bm != "NONE":
            sbr = _finite_ret(t1_return(cur, sector_bm, as_of, HORIZON_DAYS))
            if sbr is not None:
                cur.execute("""UPDATE longterm_agent_results
                               SET sector_benchmark_return_4w=%s WHERE id=%s""",
                            [round(sbr, 4), rid])
                extras += 1
        # max adverse excursion over the horizon (exit-discipline / sizing target)
        if no_worst:
            h_path = [(d, c) for d, c in fetch_price_path(cur, ticker, as_of, HORIZON_DAYS)
                      if d <= e_date + timedelta(days=HORIZON_DAYS)]
            worst = worst_position_return(h_path, verdict, e_px)
            if worst is not None:
                cur.execute("""UPDATE longterm_agent_results
                               SET worst_position_return_4w=%s WHERE id=%s""", [worst, rid])
                extras += 1
        # aux targets: shift persistence (graph) + realized fundamentals (next filing)
        aux = dict(aux or {})
        changed = False
        if "shift_outcome" not in aux and shift_dir and neo is not None:
            try:
                means = _post_shift_daily_means(neo, cfg.get_db_config(), ticker,
                                                datetime.combine(as_of, datetime.min.time()),
                                                HORIZON_DAYS)
                aux["shift_outcome"] = classify_shift_outcome(shift_dir, means)
                changed = True
            except Exception as e:
                print(f"  ⚠ shift_outcome {ticker} {as_of}: {e}")
        if ("fundamentals_realized" not in aux
                and as_of + timedelta(days=FUND_REALIZED_LAG_DAYS) <= today):
            try:
                from longterm_agent.tools.fundamentals_tool import read_fundamentals
                before = read_fundamentals(dbm, ticker,
                                           datetime.combine(as_of, datetime.min.time()))
                after = read_fundamentals(
                    dbm, ticker, datetime.combine(as_of + timedelta(days=FUND_REALIZED_LAG_DAYS),
                                                  datetime.min.time()))
                real = classify_fundamentals_realized(before or {}, after or {})
                real["verdict_at_signal"] = fund_verdict
                aux["fundamentals_realized"] = real
                changed = True
            except Exception as e:
                print(f"  ⚠ fundamentals_realized {ticker} {as_of}: {e}")
        if changed:
            cur.execute("UPDATE longterm_agent_results SET aux_targets=%s WHERE id=%s",
                        [Json(aux), rid])
            extras += 1

    # Grading commits BEFORE the best-effort breach scan. The scan used to run inside the
    # same uncommitted transaction, so anything it raised discarded the whole run's grades
    # and the next run re-did — and re-lost — identical work (2026-08-05: a TypeError in the
    # alert path left the book ungraded every day while only the error reached Telegram).
    conn.commit()
    try:
        alerted = scan_open_breaches(cur, cfg, today)
        conn.commit()
    except Exception as e:
        conn.rollback()
        alerted = 0
        oplog.error("BREACH_SCAN_ERROR", f"{type(e).__name__}: {e}")
        print(f"  ⚠ stop-breach scan failed (grading already committed): {e}")
    conn.close()
    if neo is not None:
        try:
            neo.close()
        except Exception:
            pass
    print(f"Graded {graded} rows (4w horizon matured); {pending} pending_data "
          f"(price series not caught up); stopped-book backfilled {backfilled}; "
          f"multi-target fills {extras}; stop-breach alerts {alerted}.")
    if mid_pending_bench:
        # Loud on purpose: without ETF minute bars an in-session book cannot be graded at
        # all, and a silent zero would read as "no in-session trades" instead of
        # "every in-session trade is stuck". Fix = ship the price_fetcher ETF change.
        print(f"  ⚠ {mid_pending_bench} in-session ({FILL_CONVENTION_MID}) row(s) held back: "
              f"no {BENCHMARK} MINUTE bar for the fill moment. Both legs must share a "
              f"convention, so these stay ungraded until the ETF minute fetch is deployed.")
    return graded


if __name__ == "__main__":
    main()
