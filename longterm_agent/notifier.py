"""DEPRECATED shim — TelegramNotifier moved to core.notify (Phase 1).

Token/chat resolution is unchanged: LT_TELEGRAM_* with fallback to the shared
TELEGRAM_* vars (now via core.settings.telegram_creds).
"""
from core.notify import TelegramNotifier

__all__ = ["TelegramNotifier"]
