"""Shared helpers to distinguish recoverable infra/quota LLM errors from logic errors.

The agent used to "fail-open" on ANY exception in the significance classifier and
similar-news retrieval, silently treating broken runs (e.g. OpenRouter weekly key
limit -> HTTP 403) as "significant, 0 signals". That polluted agent_results with
fake empty rows and hid outages. We now detect infra/quota/transport errors and
raise them loudly so the batch records a real error and the item is retried later.
"""
from __future__ import annotations

# Substrings that indicate an infrastructure/quota/transport problem (NOT a model
# reasoning issue). Matched case-insensitively against str(exception).
_INFRA_MARKERS = (
    "key limit exceeded",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "quota",
    "insufficient_quota",
    "too many requests",
    "error code: 429",
    "error code: 403",
    "error code: 401",
    "error code: 402",
    "error code: 500",
    "error code: 502",
    "error code: 503",
    "error code: 529",
    "overloaded",
    "timeout",
    "timed out",
    "connection error",
    "connection aborted",
    "connection reset",
    "broken pipe",
    "service unavailable",
    "bad gateway",
)


class LLMInfraError(RuntimeError):
    """Raised when an LLM/embedding call fails for infra/quota/transport reasons."""


def is_infra_error(exc: BaseException) -> bool:
    """True if the exception looks like a quota/rate-limit/transport failure."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _INFRA_MARKERS)


def raise_if_infra(exc: BaseException, context: str = "") -> None:
    """Re-raise as LLMInfraError if `exc` is an infra/quota error; else return."""
    if is_infra_error(exc):
        prefix = f"{context}: " if context else ""
        raise LLMInfraError(f"{prefix}{exc}") from exc
