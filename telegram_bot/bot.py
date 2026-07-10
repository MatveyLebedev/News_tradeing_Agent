"""Telegram bot — Q&A and news analysis via TradingAgent"""
import os
import re
import uuid
import asyncio
from datetime import datetime
from functools import wraps
from html import escape

import pytz
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.request import HTTPXRequest
from langgraph.prebuilt import create_react_agent

from langsmith import Client as LangSmithClient

from news_agent import TradingAgent
from news_agent.agent_config import AgentConfig
from news_agent.tools import (
    create_find_similar_news_tool,
    create_find_similar_news_with_price_tool,
    create_get_recent_news_tool,
    create_get_price_data_tool,
    create_get_technical_indicators_tool,
    create_analyze_sector_tool,
    create_ticker_historical_context_tool,
    create_graph_tools,
)

WAITING_NEWS, WAITING_DATE = range(2)

_URL_RE = re.compile(r'https?://\S+')


def _resolve_links(text: str, dm) -> tuple[str, list[str]]:
    """Replace known DB links in text with 'title — summary'.

    Returns:
        (replaced_text, list_of_preview_strings)
    """
    previews: list[str] = []
    def _replace(m):
        url = m.group(0).rstrip(".,;)")
        article = dm.get_news_by_link(url)
        if article:
            title = article.get("title") or url
            summary = article.get("summary") or ""
            previews.append(f"<b>{escape(title)}</b>\n{escape(summary)}" if summary
                            else f"<b>{escape(title)}</b>")
            return f"[{title}] {summary}" if summary else f"[{title}]"
        return m.group(0)
    replaced = _URL_RE.sub(_replace, text)
    return replaced, previews

_config = AgentConfig()

# Enable LangSmith tracing when API key is present
_LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
if _LANGCHAIN_API_KEY:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
    os.environ["LANGCHAIN_API_KEY"] = _LANGCHAIN_API_KEY
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "telegram-bot")
    print(f"✓ LangSmith tracing enabled → project: {os.environ['LANGCHAIN_PROJECT']}")

ALLOWED_IDS: set = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
}

_agent: TradingAgent = None
_ls_client: LangSmithClient = None


def _get_ls_client() -> LangSmithClient:
    global _ls_client
    if _ls_client is None and _LANGCHAIN_API_KEY:
        _ls_client = LangSmithClient()
    return _ls_client


def _share_run(run_id) -> str | None:
    """Return a public shareable LangSmith URL for run_id, or None on failure."""
    if not run_id or not _LANGCHAIN_API_KEY:
        return None
    try:
        client = _get_ls_client()
        return client.share_run(run_id)
    except Exception:
        return None


async def _send(update: Update, text: str, **kwargs) -> None:
    """Send a message with up to 3 retries on network/timeout errors."""
    for attempt in range(3):
        try:
            await update.message.reply_text(text, **kwargs)
            return
        except (TimedOut, NetworkError):
            if attempt == 2:
                raise
            await asyncio.sleep(3)


def get_agent() -> TradingAgent:
    global _agent
    if _agent is None:
        _agent = TradingAgent(_config)
    return _agent


def is_authorized(user_id: int) -> bool:
    return not ALLOWED_IDS or user_id in ALLOWED_IDS


def require_auth(end_value=None):
    """Decorator: deny access if user not in whitelist."""
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not is_authorized(update.effective_user.id):
                await update.message.reply_text("⛔ Access denied.")
                return end_value
            return await func(update, context)
        return wrapper
    return decorator


def _build_qa_tools(timestamp: datetime) -> list:
    agent = get_agent()
    dm = agent.db_manager
    tools = [
        create_find_similar_news_tool(timestamp, dm),
        create_find_similar_news_with_price_tool(timestamp, dm),
        create_get_recent_news_tool(timestamp, dm),
        create_get_price_data_tool(timestamp, dm),
        create_get_technical_indicators_tool(timestamp, dm),
        create_analyze_sector_tool(timestamp, dm),
        create_ticker_historical_context_tool(timestamp, dm),
    ]
    if agent.neo4j_manager:
        tools.extend(create_graph_tools(agent.neo4j_manager, dm, timestamp))
    return tools


def _table_to_pre(block: str) -> str:
    """Convert a markdown table block into a <pre> monospace block."""
    lines = block.strip().split("\n")
    rows = []
    for line in lines:
        # Skip separator rows like |---|:---| etc.
        if re.match(r"^\s*\|[\s:|*-]+\|\s*$", line):
            continue
        cells = [
            re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", c.strip())
            for c in line.strip().strip("|").split("|")
        ]
        rows.append(cells)
    if not rows:
        return escape(block)
    col_count = max(len(r) for r in rows)
    col_w = [max((len(r[i]) if i < len(r) else 0) for r in rows) for i in range(col_count)]
    out = []
    for idx, row in enumerate(rows):
        out.append(" | ".join(
            (row[i] if i < len(row) else "").ljust(col_w[i]) for i in range(col_count)
        ))
        if idx == 0:
            out.append("-+-".join("-" * w for w in col_w))
    return "<pre>" + escape("\n".join(out)) + "</pre>"


def _fmt(text: str) -> str:
    """Format non-table markdown text to Telegram HTML."""
    text = escape(text)
    text = re.sub(r"^#{1,3} (.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def _md_to_html(text: str) -> str:
    """Convert LLM markdown (including tables) to Telegram-compatible HTML."""
    table_re = re.compile(r"((?:\|.+\|\n?){2,})", re.MULTILINE)
    parts, last = [], 0
    for m in table_re.finditer(text):
        parts.append(_fmt(text[last:m.start()]))
        parts.append(_table_to_pre(m.group(0)))
        last = m.end()
    parts.append(_fmt(text[last:]))
    return "".join(parts)


def _format_signals(signals: list) -> str:
    if not signals:
        return "No trade signals generated (confidence below threshold)."
    lines = [f"<b>Analysis complete — {len(signals)} signal(s)</b>\n"]
    for s in signals:
        signal = s.get("signal", "?")
        ticker = s.get("ticker", "?")
        conf = s.get("confidence", 0)
        stop = s.get("stop_loss")
        tp = s.get("take_profit")
        reason = escape(s.get("reasoning", ""))
        emoji = "📈" if signal == "BUY" else ("📉" if signal == "SELL" else "➡️")
        parts = [f"{emoji} <b>{ticker}</b> — {signal} (confidence: {conf:.2f})"]
        if stop or tp:
            parts.append(f"Stop: <code>${stop}</code> | TP: <code>${tp}</code>")
        if reason:
            parts.append(reason[:400])
        lines.append("\n".join(parts))
    return "\n\n".join(lines)[:4096]


# ── handlers ──────────────────────────────────────────────────────────────────

@require_auth()
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I'm a trading news analyst bot.\n\n"
        "• Ask any market question (e.g. <i>how does Iran war affect oil price?</i>)\n"
        "• /analyze — submit a news article for trade signal analysis\n"
        "• /cancel — cancel current operation",
        parse_mode="HTML",
    )


@require_auth()
async def handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = update.message.text
    await update.message.reply_text("Researching… ⏳")

    # Resolve any links to DB articles
    dm = get_agent().db_manager
    resolved_question, previews = _resolve_links(question, dm)
    for preview in previews:
        await _send(update, preview, parse_mode="HTML")

    timestamp = datetime.now(pytz.timezone("America/New_York"))
    tools = _build_qa_tools(timestamp)

    system_prompt = (
        "You are a financial market analyst with access to a historical news and events database. "
        "Use the provided tools to research the question and give a concise, data-driven answer "
        "about market impacts, price movements, and financial trends. "
        f"Current time: {timestamp.strftime('%Y-%m-%d %H:%M %Z')}."
    )

    qa_agent = create_react_agent(get_agent().llm, tools)
    run_id = uuid.uuid4()

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: qa_agent.invoke(
                {"messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": resolved_question},
                ]},
                config={
                    "run_id": run_id,
                    "recursion_limit": 12,
                    "run_name": "telegram_qa",
                    "tags": ["telegram", "qa"],
                    "metadata": {
                        "user_id": str(update.effective_user.id),
                        "timestamp": timestamp.isoformat(),
                    },
                },
            ),
        )
        answer = result["messages"][-1].content
    except Exception as e:
        answer = f"Error during analysis: {e}"

    trace_url = await loop.run_in_executor(None, _share_run, run_id)
    suffix = f'\n\n🔍 <a href="{trace_url}">View agent trace</a>' if trace_url else ""
    await _send(update, _md_to_html(answer[:4096]) + suffix, parse_mode="HTML")


async def _run_analysis(update: Update, news_text: str, timestamp, notice: str = "") -> None:
    """Execute trade-signal analysis and reply with results."""
    await update.message.reply_text(
        f"{notice}Running analysis… ⏳ This may take up to a minute.",
        reply_markup=ReplyKeyboardRemove(),
    )
    loop = asyncio.get_running_loop()
    agent = get_agent()
    try:
        signals = await loop.run_in_executor(
            None,
            lambda: agent.run(
                news_text,
                timestamp,
                metadata={
                    "source": "telegram",
                    "user_id": str(update.effective_user.id),
                },
            ),
        )
    except Exception as e:
        await _send(update, f"Analysis error: {e}")
        return
    trace_url = await loop.run_in_executor(None, _share_run, agent.last_run_id)
    suffix = f'\n\n🔍 <a href="{trace_url}">View agent trace</a>' if trace_url else ""
    await _send(update, _format_signals(signals) + suffix, parse_mode="HTML")


@require_auth(ConversationHandler.END)
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please send the news article text or a link:")
    return WAITING_NEWS


_NOW_BTN = "📅 Now"
_DATE_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton(_NOW_BTN)]], resize_keyboard=True, one_time_keyboard=True
)


@require_auth(ConversationHandler.END)
async def receive_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text
    news_text, _previews = _resolve_links(raw_text, get_agent().db_manager)

    # If any link resolved to a DB article, grab its published date and run immediately
    urls = _URL_RE.findall(raw_text)
    for url in urls:
        url = url.rstrip(".,;)")
        article = get_agent().db_manager.get_news_by_link(url)
        if article and article.get("published"):
            published = article["published"]
            if isinstance(published, datetime):
                timestamp = published.replace(tzinfo=None)
            else:
                timestamp = datetime.strptime(str(published)[:16], "%Y-%m-%d %H:%M")

            title = escape(article.get("title") or "")
            summary = escape(article.get("summary") or "")
            date_str = timestamp.strftime("%Y-%m-%d %H:%M")
            preview = f"<b>{title}</b>\n\n{summary}\n\n📅 <code>{date_str}</code>"
            await _send(update, preview, parse_mode="HTML")

            eastern = pytz.timezone("America/New_York")
            now_et = datetime.now(eastern)
            notice = ""
            if eastern.localize(timestamp) > now_et:
                timestamp = now_et.replace(tzinfo=None)
                notice = "ℹ️ News date is in the future — running analysis with current time.\n\n"
            await _run_analysis(update, news_text, timestamp, notice)
            return ConversationHandler.END

    # No DB article found — ask for date as usual
    context.user_data["news_text"] = raw_text
    await update.message.reply_text(
        "Got it. Now send the publication date:\n"
        "Format: <code>YYYY-MM-DD</code> or <code>YYYY-MM-DD HH:MM</code>",
        parse_mode="HTML",
        reply_markup=_DATE_KEYBOARD,
    )
    return WAITING_DATE


@require_auth(ConversationHandler.END)
async def receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = update.message.text.strip()
    eastern = pytz.timezone("America/New_York")
    now_et = datetime.now(eastern)

    if date_str == _NOW_BTN:
        timestamp = now_et.replace(tzinfo=None)
    else:
        timestamp = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                timestamp = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                pass

    if timestamp is None:
        await update.message.reply_text(
            "Invalid date. Please use <code>YYYY-MM-DD</code> or <code>YYYY-MM-DD HH:MM</code>",
            parse_mode="HTML",
            reply_markup=_DATE_KEYBOARD,
        )
        return WAITING_DATE

    notice = ""
    if eastern.localize(timestamp) > now_et:
        timestamp = now_et.replace(tzinfo=None)
        notice = "ℹ️ News date is in the future — running analysis with current time.\n\n"

    raw_news_text = context.user_data.pop("news_text", "")
    news_text, _previews = _resolve_links(raw_news_text, get_agent().db_manager)
    await _run_analysis(update, news_text, timestamp, notice)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── entry point ───────────────────────────────────────────────────────────────

async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",   "Welcome message"),
        BotCommand("analyze", "Analyze news article for trade signals"),
        BotCommand("cancel",  "Cancel current operation"),
    ])


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    get_agent()  # warm up at startup

    request = HTTPXRequest(connect_timeout=30, read_timeout=60, write_timeout=60)
    app = Application.builder().token(token).request(request).post_init(_post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("analyze", cmd_analyze)],
        states={
            WAITING_NEWS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_news)],
            WAITING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))

    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
