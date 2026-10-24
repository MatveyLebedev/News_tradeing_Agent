"""
Telegram STATS bot for the long-term agent (Phase 6).

Read-only dashboard over Telegram: trades, signals, and errors straight from
longterm_agent_results + longterm_agent_log. Separate from the alert push (notifier.py)
and from the existing Q&A bot (telegram_bot/bot.py).

Commands:
  /start, /help        — command list
  /stats               — overview (signals + trades win-rate + error health)
  /signals [N]         — latest N directional signals (default 10)
  /trades              — actionable-trade outcomes (win rate vs SPY, mean/median excess)
  /open                — live book: every open position with unrealized P&L
  /errors [N]          — recent errors from the operational log
  /ticker SYM          — latest verdict + thesis for one ticker

Auth: TELEGRAM_ALLOWED_USER_IDS (comma-separated user ids). Empty = open to anyone who
finds the bot (fine for a private bot, but set it).

    python -m longterm_agent.stats_bot
"""
import asyncio
import logging
from functools import wraps
from html import escape


from telegram import BotCommand, Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from longterm_agent.config import LongTermAgentConfig
from longterm_agent import stats
from longterm_agent import forward_report

log = logging.getLogger(__name__)

_cfg = LongTermAgentConfig()
ALLOWED_IDS = {int(x) for x in _cfg.TELEGRAM_ALLOWED_USER_IDS.split(",") if x.strip().isdigit()}

# Telegram caps a message at 4096 chars; leave room for the "(1/3)" part counter.
TELEGRAM_MSG_LIMIT = 4096
_CHUNK_LIMIT = 3900


def _pct(x):
    return f"{x*100:+.1f}%" if isinstance(x, (int, float)) else "—"


def _split_html(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Split a message on line boundaries so each part fits Telegram's cap.

    Every formatter in this module emits self-contained HTML per line — tags open and
    close on the same line — so a newline is always a safe cut. A single line longer
    than the limit is hard-split; that can tear a tag, which is what the plain-text
    retry in _reply_long is for.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


async def _reply_long(update: Update, text: str, parse_mode: str = "HTML") -> None:
    """reply_text that survives a message over Telegram's 4096-char cap.

    /open went silent for two days because the live book outgrew the limit: the
    BadRequest killed the handler before the chart ever sent, and with no error handler
    registered the user saw nothing at all. Message length is a property of the data,
    not of the command, so every variable-length reply goes through here.
    """
    parts = _split_html(text)
    n = len(parts)
    for i, part in enumerate(parts, 1):
        body = part
        if n > 1:
            body += f"\n<i>({i}/{n})</i>" if parse_mode == "HTML" else f"\n({i}/{n})"
        try:
            await update.message.reply_text(body, parse_mode=parse_mode)
        except BadRequest:
            # A hard-split can tear an HTML tag. Unstyled text looks worse than HTML
            # and is infinitely better than the silence this function exists to fix.
            log.exception("HTML reply failed, retrying as plain text")
            await update.message.reply_text(part, parse_mode=None)


def authorized(fn):
    @wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if ALLOWED_IDS and uid not in ALLOWED_IDS:
            await update.message.reply_text("⛔ Not authorized.")
            return
        return await fn(update, context)
    return wrapper


# --- command handlers -------------------------------------------------------
@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Long-term agent — stats bot</b>\n"
        "/stats — overview\n"
        "/signals [N] — latest directional signals\n"
        "/trades — trade outcomes &amp; win rate\n"
        "/open — live book: open positions &amp; unrealized P&amp;L\n"
        "/week — all trades this week: new, open, closed\n"
        "/gate — what the risk gate filtered, and whether it paid\n"
        "/health — failure rate, price freshness, grader backlog\n"
        "/errors [N] — recent errors\n"
        "/ticker SYM — latest call for a ticker\n"
        "/freshness — data-source freshness (news/prices/SPY/fundamentals)",
        parse_mode="HTML")


@authorized
async def cmd_freshness(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.freshness import check_freshness, format_report
    rep = await asyncio.to_thread(check_freshness)
    icon = "❌" if rep["any_stale"] else "✅"
    await _reply_long(
        update, f"{icon} <b>Data freshness</b>\n<pre>{escape(format_report(rep))}</pre>")


@authorized
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sig, tr, err = await asyncio.gather(
        asyncio.to_thread(stats.signals_summary),
        asyncio.to_thread(stats.trades_summary),
        asyncio.to_thread(stats.errors_summary),
    )
    wr = tr["win_rate"]
    verdicts = " · ".join(f"{v} {d['count']}" for v, d in sorted(sig["by_verdict"].items())) or "none"
    text = (
        f"📈 <b>Long-term agent</b>  <code>v{escape(_cfg.AGENT_VERSION)}</code>"
        f" · build <code>{escape(_cfg.PIPELINE_VERSION)}</code>\n\n"
        f"<b>Signals</b> (last {sig['days']}d): {sig['total_runs']} runs · "
        f"{sig['directional_signals']} directional · {sig['trades']} trades\n"
        f"  {escape(verdicts)}\n"
        f"  last as_of {sig['last_as_of']}\n\n"
        f"<b>Trades</b>: {tr['total_trades']} total · {tr['graded']} graded · {tr['pending']} pending\n"
        f"  win rate {f'{wr:.0%}' if wr is not None else '—'} ({tr['wins']}/{tr['graded']})\n"
        f"  excess vs SPY: mean {_pct(tr['mean_excess'])} · median {_pct(tr['median_excess'])}\n\n"
        f"<b>Errors</b> (last {err['days']}d): {err['errors']} errors, {err['warnings']} warnings"
    )
    # Open exposure is the part a win-rate line hides: most of the book is usually
    # unmatured, and a mean over the graded minority is not "how the agent is doing".
    h = await asyncio.to_thread(stats.health_report)
    if h["pending"] or h["overdue"]:
        text += f"\n\n<b>Open</b>: {h['pending']} not matured"
        if h["next_mature"]:
            text += f" · next {h['next_mature']}"
        if h["overdue"]:
            text += f"\n  ⚠️ {h['overdue']} matured but ungraded"
    if err["last_error_time"]:
        text += f"\n  last: {escape(str(err['last_error_time'])[:19])}"
    await _reply_long(update, text)


@authorized
async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = 10
    if context.args and context.args[0].isdigit():
        n = min(int(context.args[0]), 30)
    rows = await asyncio.to_thread(stats.recent_signals, n)
    if not rows:
        await update.message.reply_text("No directional signals yet.")
        return
    lines = ["<b>Latest signals</b>"]
    for s in rows:
        mark = "💼" if s["traded"] else "·"
        conv = f"{s['conviction']:.0%}" if isinstance(s["conviction"], (int, float)) else "—"
        outcome = ""
        if s["fwd_return_4w"] is not None and s["benchmark_return_4w"] is not None:
            ex = stats.trade_excess(s["verdict"], s["fwd_return_4w"], s["benchmark_return_4w"])
            outcome = f" → {_pct(ex)} vs SPY"
        lines.append(f"{mark} {escape(str(s['as_of']))} <b>{escape(s['ticker'])}</b> "
                     f"{escape(s['verdict'])} ({conv}){escape(outcome)}")
    await _reply_long(update, "\n".join(lines))


@authorized
async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tr, fwd = await asyncio.gather(
        asyncio.to_thread(stats.trades_summary),
        asyncio.to_thread(forward_report.bot_summary),
    )
    wr = tr["win_rate"]
    text = (
        f"💼 <b>Trades</b> (actionable, last {tr['days']}d)\n"
        f"total {tr['total_trades']} · graded {tr['graded']} · pending {tr['pending']}\n"
        f"win rate: <b>{f'{wr:.0%}' if wr is not None else '—'}</b> ({tr['wins']}/{tr['graded']})\n"
        f"excess vs SPY — mean {_pct(tr['mean_excess'])}, median {_pct(tr['median_excess'])}\n\n"
        f"{fwd}\n\n"
        f"<i>A trade = a directional signal that passed the risk gate. "
        f"Win = beat SPY over 4 weeks. Pending = horizon not matured yet.</i>"
    )
    await _reply_long(update, text)


@authorized
async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Replayed weeks ride along, marked and outside the totals — hidden, they read as weeks
    # that were never filled.
    rows = await asyncio.to_thread(stats.open_positions, include_excluded=True)
    await _reply_long(update, stats.format_open_positions_html(rows))
    # Same chart the weekly digest uses — the live book is the live book.
    try:
        from longterm_agent import charts
        png = await asyncio.to_thread(charts.open_positions_chart, rows)
        if png:
            await update.message.reply_photo(png)
    except Exception:
        log.exception("open-positions chart failed")


@authorized
async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rep = await asyncio.to_thread(stats.weekly_report)
    # HTML, not <pre>: a code block renders as Telegram's monospace "copy" card, which
    # reads as a dump. Text first, then the charts — a caption is capped at 1024 chars
    # and the digest outgrows it as soon as a week has more than a few positions.
    await _reply_long(update, stats.format_weekly_report_html(rep))
    await _send_charts(update, rep)


async def _send_charts(update: Update, rep) -> None:
    """Send whatever charts the data supports. Each builder returns None when a chart
    would imply more than the data holds; silence is the correct output then."""
    try:
        from longterm_agent import charts
    except Exception as e:                                   # matplotlib absent
        log.warning("charts unavailable: %s", e)
        return
    for build, arg in ((charts.open_positions_chart, rep["new"] + rep["open"]),
                       (charts.closed_trades_chart, rep["closed"])):
        try:
            png = await asyncio.to_thread(build, arg)
        except Exception:
            log.exception("chart build failed")
            continue
        if png:
            await update.message.reply_photo(png)


@authorized
async def cmd_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g = await asyncio.to_thread(stats.gate_breakdown)
    await _reply_long(update, stats.format_gate_breakdown(g))


@authorized
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    h = await asyncio.to_thread(stats.health_report)
    await _reply_long(update, stats.format_health(h))


@authorized
async def cmd_errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = 10
    if context.args and context.args[0].isdigit():
        n = min(int(context.args[0]), 30)
    summ, rows = await asyncio.gather(
        asyncio.to_thread(stats.errors_summary),
        asyncio.to_thread(stats.recent_errors, n),
    )
    if not rows:
        await update.message.reply_text(f"✅ No errors logged. ({summ['warnings']} warnings)")
        return
    lines = [f"⚠️ <b>Recent errors</b> ({summ['errors']} in {summ['days']}d)"]
    for r in rows:
        tk = f" {escape(r['ticker'])}" if r["ticker"] else ""
        lines.append(f"<code>{escape(str(r['time'])[:19])}</code> {escape(r['event'] or '')}{tk}\n"
                     f"  {escape((r['message'] or '')[:160])}")
    await _reply_long(update, "\n".join(lines))


@authorized
async def cmd_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ticker NVDA")
        return
    st = await asyncio.to_thread(stats.ticker_status, context.args[0])
    if not st:
        await update.message.reply_text(f"No runs for {escape(context.args[0].upper())}.")
        return
    await _reply_long(update, stats.format_ticker_status_html(st))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Turn a handler crash into a visible reply.

    With no error handler registered, python-telegram-bot logs "No error handlers are
    registered" and the user sees nothing — which is how an over-long /open read as
    "the bot is down" for two days. A named failure is the minimum honest answer.
    """
    log.error("handler failed", exc_info=context.error)
    msg = getattr(update, "effective_message", None)
    if msg is None:
        return
    try:
        await msg.reply_text(f"⚠️ Command failed: {type(context.error).__name__}")
    except Exception:
        log.exception("error reply failed")


async def _post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("stats", "Overview: signals, trades, errors"),
        BotCommand("signals", "Latest directional signals"),
        BotCommand("trades", "Trade outcomes & win rate"),
        BotCommand("open", "Live book: open positions & unrealized P&L"),
        BotCommand("week", "All trades this week: new, open, closed"),
        BotCommand("gate", "What the risk gate filtered, and whether it paid"),
        BotCommand("health", "Failure rate, price freshness, grader backlog"),
        BotCommand("errors", "Recent errors"),
        BotCommand("ticker", "Latest call for a ticker"),
    ])
    print("✓ stats bot ready")


def main():
    token = _cfg.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN (or LT_TELEGRAM_BOT_TOKEN) is not set")
    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("gate", cmd_gate))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("errors", cmd_errors))
    app.add_handler(CommandHandler("ticker", cmd_ticker))
    app.add_handler(CommandHandler("freshness", cmd_freshness))
    app.add_error_handler(on_error)
    print(f"Starting long-term stats bot (auth: {len(ALLOWED_IDS) or 'OPEN'} ids)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
