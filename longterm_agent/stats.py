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
from datetime import date, timedelta
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


def unrealized_excess(verdict: str, position_return, spy_return):
    """Excess-so-far of an OPEN position vs SPY, same convention the grader will use.

    `position_return` arrives direction-signed (a profitable SHORT is positive), so the
    sign is undone here and trade_excess applies it exactly once — flipping twice is how
    a SHORT's excess quietly goes wrong.
    """
    if position_return is None or spy_return is None:
        return None
    raw = -position_return if verdict == "SHORT" else position_return
    return trade_excess(verdict, raw, spy_return)


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
    sig = signal if isinstance(signal, dict) else {}
    return {"ticker": ticker.upper(), "as_of": as_of, "verdict": verdict, "traded": traded,
            "conviction": conv, "fwd_return_4w": f4, "benchmark_return_4w": b4,
            "thesis": sig.get("thesis"), "horizon": sig.get("horizon"),
            "supporting_factors": sig.get("supporting_factors") or [],
            "key_risks": sig.get("key_risks") or [],
            "invalidation_conditions": sig.get("invalidation_conditions") or []}


def format_ticker_status_html(st: Dict[str, Any]) -> str:
    """Telegram-HTML /ticker: the agent's full reasoning for its latest call.

    thesis / factors / risks / invalidation come straight from the synthesis output —
    the same JSON the decision was made on, not a retelling.
    """
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    conv = f"{st['conviction']:.0%}" if isinstance(st["conviction"], (int, float)) else "—"
    out = [f"<b>{esc(st['ticker'])}</b> — {esc(st['verdict'] or '?')} "
           f"{'💼 traded' if st['traded'] else ''}",
           f"as_of {esc(st['as_of'])} · conviction {conv}"
           + (f" · {esc(st['horizon'])}" if st.get("horizon") else "")]
    if st["fwd_return_4w"] is not None and st["benchmark_return_4w"] is not None:
        ex = trade_excess(st["verdict"], st["fwd_return_4w"], st["benchmark_return_4w"])
        out.append(f"realized 4w: {_fmt_pct(st['fwd_return_4w'])} (excess {_fmt_pct(ex)})")
    if st.get("thesis"):
        out.append(f"\n{esc(st['thesis'][:700])}")
    for title, key, cap in (("Why", "supporting_factors", 4),
                            ("Risks", "key_risks", 4),
                            ("Invalidated if", "invalidation_conditions", 3)):
        items = st.get(key) or []
        if items:
            out.append(f"\n<b>{title}</b>")
            out += [f"  • {esc(str(i)[:200])}" for i in items[:cap]]
    return "\n".join(out)


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
        # Live P&L on BOTH lists: a position opened this week is "new" for the digest but
        # is just as open, and a blank return on the freshest trades is what makes the
        # chart empty exactly when there is most to look at.
        for r in open_rows + new:
            _stamp_position(cur, r)

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


def _stamp_position(cur, r) -> None:
    """Attach the current direction-signed return and days held to a live position.

    Entry price comes from the stamped `entry_px` (the open of the first bar after the
    decision) whenever the grader has written one. The close-on-or-before-as_of lookup is
    only a fallback for rows graded before that stamp existed: it is the RETIRED
    convention, and it prices the trade at a close the decision could not have traded at.
    """
    cur.execute("""SELECT entry_px, entry_date FROM longterm_agent_results
                   WHERE ticker=%s AND as_of=%s AND agent_version=%s LIMIT 1""",
                [r["ticker"], r["as_of"], _version()])
    row = cur.fetchone()
    entry = float(row[0]) if row and row[0] else None
    entry_date = row[1] if row else None
    if entry is None:
        cur.execute("""SELECT close FROM stock_prices_daily
                       WHERE ticker=%s AND date <= %s ORDER BY date DESC LIMIT 1""",
                    [r["ticker"], r["as_of"]])
        got = cur.fetchone()
        entry = float(got[0]) if got and got[0] else None
    cur.execute("""SELECT close FROM stock_prices_daily
                   WHERE ticker=%s ORDER BY date DESC LIMIT 1""", [r["ticker"]])
    got = cur.fetchone()
    last = float(got[0]) if got and got[0] else None

    pos = None
    if entry and last:
        pos = last / entry - 1
        if r["verdict"] == "SHORT":
            pos = -pos
    r["position_return"] = pos
    r["entry_px_used"] = entry   # for /open's "entry → last" line
    r["last_px"] = last
    ref = entry_date or r["as_of"]
    r["days_open"] = None if ref is None else (date.today() - ref).days


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


def format_weekly_report_html(rep: Dict[str, Any]) -> str:
    """Telegram-HTML rendering of the same report.

    Not `<pre>`: wrapping the whole digest in a code block is what gives Telegram's
    monospace "copy" card, which reads as a dump rather than a report. Telegram's HTML
    has no tables, headings or lists, so structure comes from <b> lines and spacing, and
    <code> is reserved for individual values so numbers still align at a glance.
    """
    p = _fmt_pct
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    out = [f"📆 <b>Week in trades</b> · last {rep['days']}d · <code>v{esc(_version())}</code>"]

    out.append(f"\n<b>New</b> ({len(rep['new'])})")
    for r in rep["new"]:
        conv = f"{r['conviction']:.0%}" if isinstance(r["conviction"], (int, float)) else "—"
        out.append(f"  <b>{esc(r['ticker'])}</b> {esc(r['verdict'])} · conv <code>{conv}</code>"
                   f" · {esc(r['as_of'])}")
    if not rep["new"]:
        out.append("  <i>none</i>")

    out.append(f"\n<b>Open</b> ({len(rep['open'])})")
    for r in rep["open"]:
        out.append(f"  <b>{esc(r['ticker'])}</b> {esc(r['verdict'])} · day "
                   f"<code>{r['days_open']}</code> · <code>{p(r['position_return'])}</code>")
    if not rep["open"]:
        out.append("  <i>none</i>")

    out.append(f"\n<b>Closed</b> ({len(rep['closed'])})")
    for r in rep["closed"]:
        extra = ""
        if r["exit_reason"] == "stop":
            extra = f" · <i>stopped d{r['exit_days']}</i> {p(r['excess_stopped'])}"
        elif r["exit_reason"] and r["exit_reason"] != "horizon":
            extra = f" · <i>{esc(r['exit_reason'])}</i>"
        out.append(f"  <b>{esc(r['ticker'])}</b> {esc(r['verdict'])} · "
                   f"<code>{p(r['excess'])}</code> vs SPY{extra}")
    if rep["closed"]:
        out.append(f"  → wins <b>{rep['closed_wins']}/{rep['closed_graded']}</b>, "
                   f"mean <code>{p(rep['closed_mean_excess'])}</code>")
        if rep["closed_graded"] < 10:
            # Deliberate: a mean over a handful of trades is noise, and this project has
            # already been misled once by reading a small book as a result.
            out.append(f"  <i>⚠ {rep['closed_graded']} graded trades — not a sample size; "
                       f"read as a log, not a performance figure.</i>")
    else:
        out.append("  <i>none matured</i>")
    return "\n".join(out)


# --- /gate: what the selection stage actually did ----------------------------------------
def gate_breakdown(days: int = 90) -> Dict[str, Any]:
    """Which vetoes and penalties fired, and how the FILTERED signals then performed.

    The open question this bot exists to make visible: across six backtest books the gate
    added value in none and subtracted in two, and the signals its machinery cut beat the
    ones it kept. Live rows are the only uncontaminated evidence that will ever accumulate,
    so they are worth watching from the first week.

    `gate_trace` is written by persistence from pipeline 0.9.0 onward; rows written by
    older builds have none and are reported as uncovered rather than guessed at.
    """
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.columns
                       WHERE table_name='longterm_agent_results' AND column_name='gate_trace'""")
        if not cur.fetchone()[0]:
            # No column at all: every row predates 0.9.0. Distinct from "no signals" —
            # reporting the wrong reason sends someone hunting a problem that isn't there.
            conn.close()
            return {"days": days, "covered": 0, "total": 0, "vetoes": {}, "penalties": {},
                    "filtered": [], "traded": [], "column_missing": True}
        cur.execute("""
            SELECT verdict, traded, gate_trace, fwd_return_4w, benchmark_return_4w
            FROM longterm_agent_results
            WHERE agent_version=%s AND as_of >= CURRENT_DATE - %s
              AND verdict IN ('LONG','SHORT')
        """, [_version(), days])
        rows = cur.fetchall()
    conn.close()

    vetoes: Dict[str, int] = {}
    penalties: Dict[str, int] = {}
    filtered, traded, covered = [], [], 0
    for verdict, was_traded, gt, f4, b4 in rows:
        if not gt:
            continue
        covered += 1
        for v in (gt.get("vetoes") or []):
            vetoes[v] = vetoes.get(v, 0) + 1
        for p in (gt.get("penalties") or {}):
            penalties[p] = penalties.get(p, 0) + 1
        ex = trade_excess(verdict, f4, b4)
        if ex is not None:
            (traded if was_traded else filtered).append(ex)
    return {"days": days, "covered": covered, "total": len(rows),
            "vetoes": vetoes, "penalties": penalties,
            "filtered": filtered, "traded": traded}


def format_gate_breakdown(g: Dict[str, Any]) -> str:
    p = _fmt_pct
    if g.get("column_missing"):
        return ("🚦 <b>Gate</b>\n<i>No gate traces stored yet — every row predates "
                "pipeline 0.9.0. The column is created on the next batch write.</i>")
    if not g["total"]:
        return "🚦 <b>Gate</b>\n<i>No directional signals in the window.</i>"
    if not g["covered"]:
        return ("🚦 <b>Gate</b>\n"
                f"<i>{g['total']} directional signals, none carrying a gate trace — those "
                f"rows predate pipeline 0.9.0. Traces start with the next batch.</i>")
    out = [f"🚦 <b>Gate</b> · last {g['days']}d · {g['covered']}/{g['total']} traced"]
    out.append("\n<b>Vetoes</b> (hard caps below the threshold)")
    out += ([f"  {k} · <code>{v}</code>" for k, v in
             sorted(g["vetoes"].items(), key=lambda x: -x[1])] or ["  <i>none fired</i>"])
    out.append("\n<b>Penalties</b> (subtracted from conviction)")
    out += ([f"  {k} · <code>{v}</code>" for k, v in
             sorted(g["penalties"].items(), key=lambda x: -x[1])] or ["  <i>none fired</i>"])

    f, t = g["filtered"], g["traded"]
    out.append("\n<b>Did filtering pay?</b> (graded 4w excess)")
    out.append(f"  kept    n=<code>{len(t)}</code> mean "
               f"<code>{p(statistics.fmean(t)) if t else '—'}</code>")
    out.append(f"  cut     n=<code>{len(f)}</code> mean "
               f"<code>{p(statistics.fmean(f)) if f else '—'}</code>")
    if t and f:
        d = statistics.fmean(t) - statistics.fmean(f)
        out.append(f"  → kept − cut: <b>{p(d)}</b>"
                   + ("  <i>(gate helping)</i>" if d > 0 else "  <i>(gate costing)</i>"))
    if len(t) + len(f) < 20:
        out.append("  <i>⚠ too few graded rows to mean anything — this is a log, not a verdict.</i>")
    return "\n".join(out)


# --- /health: is the pipeline telling the truth today? -----------------------------------
def health_report(days: int = 30) -> Dict[str, Any]:
    """Failure rate, price freshness, grader backlog.

    Each of these has already bitten once: a 14% synthesis-failure rate nearly passed for
    a finding, and a price feed one trading day behind silently blocked a matured trade
    from grading. None of them raise an error on their own — they have to be looked at.
    """
    from longterm_agent import backtest_metrics as M
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""SELECT signal FROM longterm_agent_results
                       WHERE agent_version=%s AND as_of >= CURRENT_DATE - %s""",
                    [_version(), days])
        sigs = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT max(date) FROM stock_prices_daily")
        price_max = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM longterm_agent_results
                       WHERE agent_version=%s AND traded AND fwd_return_4w IS NULL
                         AND as_of + 28 <= CURRENT_DATE""", [_version()])
        overdue = cur.fetchone()[0]
        cur.execute("""SELECT count(*), min(as_of + 28) FROM longterm_agent_results
                       WHERE agent_version=%s AND traded AND fwd_return_4w IS NULL
                         AND as_of + 28 > CURRENT_DATE""", [_version()])
        pending, next_mature = cur.fetchone()
    conn.close()
    return {"days": days, "runs": len(sigs), "failure_rate": M.failure_rate(sigs),
            "price_max": price_max,
            "price_lag_days": None if price_max is None else (date.today() - price_max).days,
            "overdue": overdue, "pending": pending, "next_mature": next_mature}


def format_health(h: Dict[str, Any]) -> str:
    ok = lambda good: "✅" if good else "⚠️"
    fr = h["failure_rate"]
    lag = h["price_lag_days"]
    out = ["🩺 <b>Health</b>"]
    out.append(f"{ok(fr <= 0.05)} synthesis failures: <code>{fr:.1%}</code> of {h['runs']} runs "
               f"(last {h['days']}d)")
    if fr > 0.05:
        out.append("  <i>failed calls fall back to NEUTRAL — trade counts are deflated.</i>")
    out.append(f"{ok(lag is not None and lag <= 4)} prices through <code>{h['price_max']}</code>"
               + (f" · {lag}d behind" if lag is not None else ""))
    out.append(f"{ok(h['overdue'] == 0)} grader backlog: <code>{h['overdue']}</code> matured "
               f"but ungraded")
    out.append(f"⏳ open: <code>{h['pending']}</code>"
               + (f" · next matures <code>{h['next_mature']}</code>" if h["next_mature"] else ""))
    return "\n".join(out)


# --- /open: the live book right now ------------------------------------------------------
def open_positions() -> List[Dict[str, Any]]:
    """Every ungraded trade of the current version, stamped with unrealized P&L.

    Unlike /week this has no window — a position is live until it grades, however long
    ago it opened. `excess_now` uses the grader's own legs (stock entry-bar open and the
    SPY entry-bar open under the row's fill convention, both to the latest close), so
    the number a position shows while open is directly comparable to the one it will
    close with.
    """
    from longterm_agent import exit_rules
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, as_of, verdict, conviction, entry_date, entry_px, fill_convention,
                   signal->>'thesis'
            FROM longterm_agent_results
            WHERE agent_version=%s AND traded AND fwd_return_4w IS NULL
            ORDER BY as_of, ticker
        """, [_version()])
        rows = [{"ticker": t, "as_of": a, "verdict": v, "conviction": c, "entry_date": ed,
                 "entry_px": float(px) if px is not None else None, "fill_convention": fc,
                 "thesis": th}
                for t, a, v, c, ed, px, fc, th in cur.fetchall()]

        cur.execute("""SELECT close FROM stock_prices_daily
                       WHERE ticker='SPY' ORDER BY date DESC LIMIT 1""")
        got = cur.fetchone()
        spy_last = float(got[0]) if got and got[0] else None
        spy_entries: Dict[Any, Any] = {}  # one SPY lookup per cohort, not per row

        for r in rows:
            _stamp_position(cur, r)
            ref = r["entry_date"] or r["as_of"]
            r["matures"] = ref + timedelta(days=28) if ref else None
            key = (r["as_of"], r["fill_convention"])
            if key not in spy_entries:
                eb = exit_rules.entry_bar(cur, "SPY", r["as_of"],
                                          include_asof_day=(r["fill_convention"] == "open_d0"))
                spy_entries[key] = eb[1] if eb else None
            spy_entry = spy_entries[key]
            spy_ret = (spy_last / spy_entry - 1) if spy_entry and spy_last else None
            r["excess_now"] = unrealized_excess(r["verdict"], r["position_return"], spy_ret)
    conn.close()
    return rows


def format_open_positions_html(rows: List[Dict[str, Any]]) -> str:
    """Telegram-HTML /open: the whole live book with unrealized P&L.

    Every number here is provisional and the footer says so explicitly — an unrealized
    mean over a handful of open positions has already been misread as a result once in
    this project.
    """
    p = _fmt_pct
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    head = f"💼 <b>Open positions</b> ({len(rows)}) · <code>v{esc(_version())}</code>"
    if not rows:
        return head + "\n<i>none — the book is flat.</i>"
    out = [head]
    for r in rows:
        day = r["days_open"] if r.get("days_open") is not None else "—"
        out.append(f"\n<b>{esc(r['ticker'])}</b> {esc(r['verdict'])} · day <code>{day}</code>"
                   f" · pos <code>{p(r['position_return'])}</code>"
                   f" · vs SPY <code>{p(r['excess_now'])}</code>")
        px = ""
        if r.get("entry_px_used") and r.get("last_px"):
            px = f" · <code>{r['entry_px_used']:.2f}</code> → <code>{r['last_px']:.2f}</code>"
        out.append(f"  opened {esc(r['as_of'])}{px} · matures {esc(r['matures'] or '—')}")
        # The agent's own words, not a paraphrase — /ticker SYM has the full version.
        if r.get("thesis"):
            out.append(f"  <i>{esc(r['thesis'][:180])}</i>")
    poss = [r["position_return"] for r in rows if r.get("position_return") is not None]
    exs = [r["excess_now"] for r in rows if r.get("excess_now") is not None]
    if poss:
        out.append(f"\n→ book mean: pos <code>{p(statistics.fmean(poss))}</code>"
                   + (f" · vs SPY <code>{p(statistics.fmean(exs))}</code>" if exs else ""))
    out.append("<i>⚠ unrealized, gross of costs; a position grades at its 4-week horizon "
               "and only that number counts.</i>")
    return "\n".join(out)
