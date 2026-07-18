"""
Statistics for the long-term agent (Phase 6) — the data behind the Telegram stats bot.

Two layers:
  * pure functions (compute_trade_stats) — unit-testable, no DB.
  * DB readers (signals_summary, trades_summary, errors_summary, recent_*) — used by the
    bot and the `python -m longterm_agent.stats` CLI.

Vocabulary:
  * signal = any directional verdict the agent emitted (LONG/SHORT), gate or no gate.
  * trade  = a signal that PASSED the risk gate (traded=TRUE) — the actionable subset.
  * a trade is "graded" once its 4-week horizon matured (outcome_filled_at set).
  * win    = strategy *excess* return vs SPY > 0 (LONG: stock−SPY; SHORT: SPY−stock).
"""
import statistics
from datetime import date
from typing import List, Dict, Any

import psycopg2

from longterm_agent.config import LongTermAgentConfig


def _conn():
    return psycopg2.connect(**LongTermAgentConfig.get_db_config())


def _version():
    return LongTermAgentConfig.AGENT_VERSION


# --- pure math (unit-tested) ------------------------------------------------
def trade_excess(direction: str, fwd_4w, bench_4w):
    """Strategy excess return vs benchmark for one trade, or None if not gradable.

    LONG profits when the stock beats SPY; SHORT profits when it lags SPY.
    """
    if fwd_4w is None or bench_4w is None:
        return None
    raw = fwd_4w - bench_4w
    if direction == "LONG":
        return raw
    if direction == "SHORT":
        return -raw
    return None


def compute_trade_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: dicts with keys verdict, fwd_return_4w, benchmark_return_4w.
    Returns win rate + mean/median excess over the *graded* subset."""
    excesses = []
    for r in rows:
        e = trade_excess(r.get("verdict"), r.get("fwd_return_4w"), r.get("benchmark_return_4w"))
        if e is not None:
            excesses.append(e)
    n = len(excesses)
    if n == 0:
        return {"graded": 0, "win_rate": None, "wins": 0,
                "mean_excess": None, "median_excess": None}
    wins = sum(1 for e in excesses if e > 0)
    return {
        "graded": n,
        "wins": wins,
        "win_rate": wins / n,
        "mean_excess": statistics.fmean(excesses),
        "median_excess": statistics.median(excesses),
    }


# --- DB readers -------------------------------------------------------------
def signals_summary(days: int = 30) -> Dict[str, Any]:
    """Verdict counts + run health over the last `days` of as_of cohorts."""
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT verdict, COUNT(*),
                   COUNT(*) FILTER (WHERE traded),
                   AVG(conviction)
            FROM longterm_agent_results
            WHERE agent_version = %s AND as_of >= (CURRENT_DATE - %s::int)
            GROUP BY verdict
        """, [_version(), days])
        by_verdict = {}
        total = traded = 0
        conv_acc = []
        for verdict, cnt, traded_cnt, avg_conv in cur.fetchall():
            by_verdict[verdict or "?"] = {"count": cnt, "traded": traded_cnt or 0}
            total += cnt
            traded += traded_cnt or 0
            if avg_conv is not None:
                conv_acc.append((avg_conv, cnt))
        cur.execute("""SELECT MAX(as_of), MAX(run_timestamp) FROM longterm_agent_results
                       WHERE agent_version=%s""", [_version()])
        last_as_of, last_run = cur.fetchone()
    conn.close()
    directional = sum(by_verdict.get(d, {}).get("count", 0) for d in ("LONG", "SHORT"))
    return {
        "days": days, "total_runs": total, "directional_signals": directional,
        "trades": traded, "by_verdict": by_verdict,
        "last_as_of": last_as_of, "last_run": last_run,
    }


def trades_summary(days: int = 365) -> Dict[str, Any]:
    """Outcome stats for actionable trades (traded=TRUE) within the window."""
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT verdict, fwd_return_4w, benchmark_return_4w, outcome_filled_at
            FROM longterm_agent_results
            WHERE agent_version=%s AND traded=TRUE AND as_of >= (CURRENT_DATE - %s::int)
        """, [_version(), days])
        rows = [{"verdict": v, "fwd_return_4w": f, "benchmark_return_4w": b, "filled": o}
                for v, f, b, o in cur.fetchall()]
    conn.close()
    total = len(rows)
    pending = sum(1 for r in rows if r["filled"] is None)
    stats = compute_trade_stats(rows)
    stats.update({"total_trades": total, "pending": pending, "days": days})
    return stats


def recent_signals(limit: int = 10, directional_only: bool = True) -> List[Dict[str, Any]]:
    clause = "AND verdict IN ('LONG','SHORT')" if directional_only else ""
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT ticker, as_of, verdict, traded, conviction, fwd_return_4w, benchmark_return_4w
            FROM longterm_agent_results
            WHERE agent_version=%s {clause}
            ORDER BY as_of DESC, ticker LIMIT %s
        """, [_version(), limit])
        out = [{"ticker": t, "as_of": a, "verdict": v, "traded": tr, "conviction": c,
                "fwd_return_4w": f, "benchmark_return_4w": b}
               for t, a, v, tr, c, f, b in cur.fetchall()]
    conn.close()
    return out


def errors_summary(days: int = 7) -> Dict[str, Any]:
    """Error counts from the operational log over the last `days`."""
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT level, COUNT(*) FROM longterm_agent_log
            WHERE log_time >= NOW() - (%s || ' days')::interval
            GROUP BY level
        """, [days])
        by_level = {lvl: cnt for lvl, cnt in cur.fetchall()}
        cur.execute("""SELECT MAX(log_time) FROM longterm_agent_log WHERE level='ERROR'""")
        last_err = cur.fetchone()[0]
    conn.close()
    return {"days": days, "by_level": by_level,
            "errors": by_level.get("ERROR", 0), "warnings": by_level.get("WARNING", 0),
            "last_error_time": last_err}


def recent_errors(limit: int = 10) -> List[Dict[str, Any]]:
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT log_time, event_type, ticker, message FROM longterm_agent_log
            WHERE level='ERROR' ORDER BY log_time DESC LIMIT %s
        """, [limit])
        out = [{"time": ts, "event": ev, "ticker": tk, "message": msg}
               for ts, ev, tk, msg in cur.fetchall()]
    conn.close()
    return out


def ticker_status(ticker: str) -> Dict[str, Any]:
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT as_of, verdict, traded, conviction, fwd_return_4w, benchmark_return_4w, signal
            FROM longterm_agent_results
            WHERE agent_version=%s AND ticker=%s
            ORDER BY as_of DESC LIMIT 1
        """, [_version(), ticker.upper()])
        r = cur.fetchone()
    conn.close()
    if not r:
        return {}
    as_of, verdict, traded, conv, f4, b4, signal = r
    return {"ticker": ticker.upper(), "as_of": as_of, "verdict": verdict, "traded": traded,
            "conviction": conv, "fwd_return_4w": f4, "benchmark_return_4w": b4,
            "thesis": (signal or {}).get("thesis") if isinstance(signal, dict) else None}


def weekly_report(days: int = 7) -> Dict[str, Any]:
    """Everything that happened to TRADES in the last `days`: opened, still open, closed.

    Three sections (a trade appears in exactly one):
      * new    — passed the gate with as_of inside the window (this week's batch);
      * open   — older trades whose 4w outcome hasn't matured, with the CURRENT
                 position return (last close vs entry close, direction-signed);
      * closed — graded inside the window: realized 4w excess + how the stopped
                 book exited (stop day N / horizon).
    """
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, as_of, verdict, conviction
            FROM longterm_agent_results
            WHERE agent_version=%s AND traded AND as_of >= CURRENT_DATE - %s
            ORDER BY as_of DESC, ticker
        """, [_version(), days])
        new = [{"ticker": t, "as_of": a, "verdict": v, "conviction": c}
               for t, a, v, c in cur.fetchall()]

        cur.execute("""
            SELECT ticker, as_of, verdict, conviction
            FROM longterm_agent_results
            WHERE agent_version=%s AND traded AND fwd_return_4w IS NULL
              AND as_of < CURRENT_DATE - %s
            ORDER BY as_of, ticker
        """, [_version(), days])
        open_rows = [{"ticker": t, "as_of": a, "verdict": v, "conviction": c}
                     for t, a, v, c in cur.fetchall()]
        for r in open_rows:
            cur.execute("""
                SELECT (SELECT close FROM stock_prices_daily
                        WHERE ticker=%(t)s AND date <= %(d)s ORDER BY date DESC LIMIT 1),
                       (SELECT close FROM stock_prices_daily
                        WHERE ticker=%(t)s ORDER BY date DESC LIMIT 1)
            """, {"t": r["ticker"], "d": r["as_of"]})
            entry, last = cur.fetchone()
            pos = None
            if entry and last:
                pos = float(last) / float(entry) - 1
                if r["verdict"] == "SHORT":
                    pos = -pos
            r["position_return"] = pos
            r["days_open"] = None if r["as_of"] is None else (date.today() - r["as_of"]).days

        cur.execute("""
            SELECT ticker, as_of, verdict, fwd_return_4w, benchmark_return_4w,
                   fwd_return_stopped, benchmark_return_stopped, exit_reason, exit_days
            FROM longterm_agent_results
            WHERE agent_version=%s AND traded
              AND outcome_filled_at >= CURRENT_DATE - %s
            ORDER BY as_of, ticker
        """, [_version(), days])
        closed = []
        for t, a, v, f4, b4, fs, bs, reason, edays in cur.fetchall():
            closed.append({"ticker": t, "as_of": a, "verdict": v,
                           "excess": trade_excess(v, f4, b4),
                           "excess_stopped": trade_excess(v, fs, bs),
                           "exit_reason": reason, "exit_days": edays})
    conn.close()

    graded = [c["excess"] for c in closed if c["excess"] is not None]
    return {"days": days, "new": new, "open": open_rows, "closed": closed,
            "closed_wins": sum(1 for e in graded if e > 0),
            "closed_graded": len(graded),
            "closed_mean_excess": statistics.fmean(graded) if graded else None}


def format_weekly_report(rep: Dict[str, Any]) -> str:
    """Plain-text rendering shared by the bot (/week) and the post-batch push."""
    p = _fmt_pct
    lines = [f"WEEK IN TRADES (last {rep['days']}d, v{_version()})"]
    lines.append(f"\nNew trades ({len(rep['new'])}):")
    if rep["new"]:
        for r in rep["new"]:
            conv = f"{r['conviction']:.0%}" if isinstance(r["conviction"], (int, float)) else "—"
            lines.append(f"  {r['as_of']} {r['ticker']:6s} {r['verdict']:5s} conv {conv}")
    else:
        lines.append("  none")
    lines.append(f"\nOpen positions ({len(rep['open'])}):")
    if rep["open"]:
        for r in rep["open"]:
            lines.append(f"  {r['as_of']} {r['ticker']:6s} {r['verdict']:5s} "
                         f"day {r['days_open']:>2}  pos {p(r['position_return'])}")
    else:
        lines.append("  none")
    lines.append(f"\nClosed this week ({len(rep['closed'])}):")
    if rep["closed"]:
        for r in rep["closed"]:
            exit_ = (f"stop d{r['exit_days']}" if r["exit_reason"] == "stop"
                     else r["exit_reason"] or "—")
            stopped = f", stopped {p(r['excess_stopped'])}" if r["exit_reason"] == "stop" else ""
            lines.append(f"  {r['as_of']} {r['ticker']:6s} {r['verdict']:5s} "
                         f"{p(r['excess'])} vs SPY ({exit_}{stopped})")
        lines.append(f"  → wins {rep['closed_wins']}/{rep['closed_graded']}"
                     f", mean excess {p(rep['closed_mean_excess'])}")
    else:
        lines.append("  none matured")
    return "\n".join(lines)


def _fmt_pct(x):
    return f"{x*100:+.1f}%" if isinstance(x, (int, float)) else "—"


def main():
    """CLI: print the full dashboard as text (same content the bot serves)."""
    sig = signals_summary()
    tr = trades_summary()
    err = errors_summary()
    print("=" * 60)
    print(f"LONG-TERM AGENT STATS  (version {_version()})")
    print("=" * 60)
    print(f"\nSIGNALS (last {sig['days']}d): {sig['total_runs']} runs, "
          f"{sig['directional_signals']} directional, {sig['trades']} trades")
    for v, d in sorted(sig["by_verdict"].items()):
        print(f"  {v:<8} {d['count']:>3}  (traded {d['traded']})")
    print(f"  last as_of {sig['last_as_of']} · last run {sig['last_run']}")
    wr = tr["win_rate"]
    print(f"\nTRADES: {tr['total_trades']} total · {tr['graded']} graded · {tr['pending']} pending")
    print(f"  win rate: {f'{wr:.0%}' if wr is not None else '—'} ({tr['wins']}/{tr['graded']})")
    print(f"  excess vs SPY: mean {_fmt_pct(tr['mean_excess'])} · median {_fmt_pct(tr['median_excess'])}")
    print(f"\nERRORS (last {err['days']}d): {err['errors']} errors, {err['warnings']} warnings")
    if err["last_error_time"]:
        print(f"  last error: {err['last_error_time']}")
    print("\nRecent signals:")
    for s in recent_signals(8):
        print(f"  {s['as_of']} {s['ticker']:<6} {s['verdict']:<8} "
              f"{'TRADE' if s['traded'] else '     '} conv={s['conviction']}")


if __name__ == "__main__":
    main()
