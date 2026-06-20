"""
Telegram push notifier for the long-term agent (Phase 6).

One-way alerts from the batch/scheduler jobs (new or flipped signals, job summaries,
errors). Uses the plain Telegram Bot HTTP API via `requests` so the batch scripts stay
synchronous (no asyncio). Reads token/chat from LongTermAgentConfig (LT_TELEGRAM_* with
fallback to the shared TELEGRAM_* vars). If unconfigured it becomes a no-op that just
prints — the agent runs fine without Telegram.

The interactive *stats* bot is separate (stats_bot.py, python-telegram-bot).
"""
from html import escape
from typing import Optional

import requests

from longterm_agent.config import LongTermAgentConfig

_API = "https://api.telegram.org/bot{token}/sendMessage"

_DIR_EMOJI = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪", "NO_DATA": "⚫"}


class TelegramNotifier:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None, timeout: int = 15):
        self.token = token or LongTermAgentConfig.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or LongTermAgentConfig.TELEGRAM_CHAT_ID
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        """Send an HTML message. Returns True on success; never raises."""
        if not self.enabled:
            print(f"[telegram disabled] {text}")
            return False
        try:
            r = requests.post(
                _API.format(token=self.token),
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                print(f"  ⚠ telegram send failed: {r.status_code} {r.text[:200]}")
                return False
            return True
        except Exception as e:
            print(f"  ⚠ telegram send error: {e}")
            return False

    # --- formatted helpers -------------------------------------------------
    def notify_signal(self, ticker, as_of, direction, conviction, horizon, thesis,
                      traded: bool, previous: Optional[str] = None) -> bool:
        emoji = _DIR_EMOJI.get(direction, "•")
        flip = f" (was {escape(previous)})" if previous and previous != direction else ""
        tag = "TRADE" if traded else "signal"
        conv = f"{conviction:.0%}" if isinstance(conviction, (int, float)) else "n/a"
        msg = (f"{emoji} <b>{escape(str(ticker))} {escape(str(direction))}</b>{flip}  "
               f"<i>{tag}</i>\n"
               f"as_of {escape(str(as_of))} · conviction {conv} · {escape(str(horizon or '4–12w'))}\n"
               f"{escape((thesis or '')[:600])}")
        return self.send(msg)

    def notify_batch_summary(self, as_of, stats: dict) -> bool:
        msg = (f"📊 <b>Weekly batch complete</b> · as_of {escape(str(as_of))}\n"
               f"runs {stats.get('runs', 0)} · trades {stats.get('trades', 0)} · "
               f"signals {stats.get('signals', 0)} · no_data {stats.get('no_data', 0)} · "
               f"errors {stats.get('errors', 0)}")
        return self.send(msg)

    def notify_error(self, where: str, detail: str) -> bool:
        return self.send(f"⚠️ <b>longterm error</b> · {escape(where)}\n<code>{escape(detail[:500])}</code>")
