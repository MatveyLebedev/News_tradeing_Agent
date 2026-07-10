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
  /errors [N]          — recent errors from the operational log
  /ticker SYM          — latest verdict + thesis for one ticker

Auth: TELEGRAM_ALLOWED_USER_IDS (comma-separated user ids). Empty = open to anyone who
finds the bot (fine for a private bot, but set it).

    python -m longterm_agent.stats_bot
"""
import asyncio
from functools import wraps
from html import escape


from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from longterm_agent.config import LongTermAgentConfig
from longterm_agent import stats
from longterm_agent import forward_report

_cfg = LongTermAgentConfig()
ALLOWED_IDS = {int(x) for x in _cfg.TELEGRAM_ALLOWED_USER_IDS.split(",") if x.strip().isdigit()}


def _pct(x):
    return f"{x*100:+.1f}%" if isinstance(x, (int, float)) else "—"


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
        "/errors [N] — recent errors\n"
        "/ticker SYM — latest call for a ticker",
        parse_mode="HTML")


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
        f"📈 <b>Long-term agent</b>  <code>v{escape(_cfg.AGENT_VERSION)}</code>\n\n"
        f"<b>Signals</b> (last {sig['days']}d): {sig['total_runs']} runs · "
        f"{sig['directional_signals']} directional · {sig['trades']} trades\n"
        f"  {escape(verdicts)}\n"
        f"  last as_of {sig['last_as_of']}\n\n"
        f"<b>Trades</b>: {tr['total_trades']} total · {tr['graded']} graded · {tr['pending']} pending\n"
        f"  win rate {f'{wr:.0%}' if wr is not None else '—'} ({tr['wins']}/{tr['graded']})\n"
        f"  excess vs SPY: mean {_pct(tr['mean_excess'])} · median {_pct(tr['median_excess'])}\n\n"
        f"<b>Errors</b> (last {err['days']}d): {err['errors']} errors, {err['warnings']} warnings"
    )
    if err["last_error_time"]:
        text += f"\n  last: {escape(str(err['last_error_time'])[:19])}"
    await update.message.reply_text(text, parse_mode="HTML")


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
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
    await update.message.reply_text(text, parse_mode="HTML")


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
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@authorized
async def cmd_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ticker NVDA")
        return
    st = await asyncio.to_thread(stats.ticker_status, context.args[0])
    if not st:
        await update.message.reply_text(f"No runs for {escape(context.args[0].upper())}.")
        return
    conv = f"{st['conviction']:.0%}" if isinstance(st["conviction"], (int, float)) else "—"
    text = (f"<b>{escape(st['ticker'])}</b> — {escape(st['verdict'] or '?')} "
            f"{'💼 traded' if st['traded'] else ''}\n"
            f"as_of {st['as_of']} · conviction {conv}\n")
    if st["fwd_return_4w"] is not None and st["benchmark_return_4w"] is not None:
        ex = stats.trade_excess(st["verdict"], st["fwd_return_4w"], st["benchmark_return_4w"])
        text += f"realized 4w: {_pct(st['fwd_return_4w'])} (excess {_pct(ex)})\n"
    if st.get("thesis"):
        text += f"\n{escape(st['thesis'][:700])}"
    await update.message.reply_text(text, parse_mode="HTML")


async def _post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("stats", "Overview: signals, trades, errors"),
        BotCommand("signals", "Latest directional signals"),
        BotCommand("trades", "Trade outcomes & win rate"),
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
    app.add_handler(CommandHandler("errors", cmd_errors))
    app.add_handler(CommandHandler("ticker", cmd_ticker))
    print(f"Starting long-term stats bot (auth: {len(ALLOWED_IDS) or 'OPEN'} ids)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
