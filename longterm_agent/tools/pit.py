"""Point-in-time visibility of dated/ingested inputs for decision-side reads.

Three cutoffs, one rule each — "what could the decision at `as_of` actually have known?"

  daily_bar_cutoff : DAILY PRICE BARS. A bar of session date D becomes knowable only
      when that session closes (~16:00 ET). The previous `date <= as_of.date()` filter
      let a backtest as_of stamped at midnight see its own day's FULL session (close
      printed 16 hours later).

  document_cutoff  : THIRD-PARTY DOCUMENT DATES (EDGAR `filed_date`, 8-K/Form-4
      `filed_date`, earnings-report dates). Same session-close rule: a document dated D
      is treated as knowable only after D's session closes — market-moving filings and
      earnings releases cluster post-close, so this is the conservative side. The old
      `<= as_of.date()` comparison admitted up to 24h of same-day documents; under the
      `open_d0` pre-open regime (decide 06:30, fill 09:30) that admitted documents filed
      AFTER the position was opened. Refinement path: EDGAR publishes a per-filing
      acceptance datetime — once stored, it replaces this approximation exactly.

  ingest_cutoff    : OUR OWN SNAPSHOTS (yfinance / FMP / options fetches). Knowability
      is the moment WE pulled the row, not a session close — a weekly snapshot fetched
      Sunday 05:00 ET is legitimately available to the Sunday 06:00 batch. Compares
      against the row's ingestion timestamp (`created_at` / `fetched_at`), which Postgres
      writes via NOW() under a UTC session, so `as_of` (an ET wall-clock decision moment)
      is converted to UTC here.

Live weekend/pre-open runs are unaffected by any of this (the data didn't exist yet
anyway); the cutoffs only change what REPLAYED cohorts can see.

Which cutoff belongs to which input, and the rules for adding a new reader without
reintroducing lookahead: docs/PIT_RULES.md.
"""
import re
from datetime import datetime, timedelta, date
from typing import Any, Optional

import pytz

SESSION_CLOSE_HOUR = 16  # ET

_ET = pytz.timezone("America/New_York")
_UTC = pytz.UTC

# A string counts as a timestamp only when it is ENTIRELY one — so prose that merely
# mentions a date ("...guidance for 2026-Q3...") is never mistaken for a moment.
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?$"
)


def daily_bar_cutoff(as_of) -> date:
    """Latest daily-bar session date fully completed before `as_of`.

    A bare date is treated as midnight (start of day) — conservative.
    """
    if isinstance(as_of, datetime):
        if as_of.hour >= SESSION_CLOSE_HOUR:
            return as_of.date()
        return as_of.date() - timedelta(days=1)
    return as_of - timedelta(days=1)


def document_cutoff(as_of) -> date:
    """Latest `filed_date` / report date a decision at `as_of` may read.

    Same session-close rule as `daily_bar_cutoff` (see module docstring): a document
    dated D is knowable only after D's session closes. Kept as its own name because it
    answers a different question and gets a different fix later (EDGAR acceptance
    datetime), even though the arithmetic is identical today.
    """
    return daily_bar_cutoff(as_of)


def ingest_cutoff(as_of) -> datetime:
    """UTC-naive upper bound for ingestion timestamps (`created_at` / `fetched_at`).

    Those columns are written DB-side by NOW() under a UTC session, while `as_of` is an
    ET wall-clock decision moment — so localise to ET and convert. A snapshot pulled
    before the decision stays visible (no live regression for the same-morning weekly
    fetch); one pulled later in the same day is excluded from a replay. A bare date is
    treated as ET midnight (start of day) — conservative.
    """
    if not isinstance(as_of, datetime):
        as_of = datetime(as_of.year, as_of.month, as_of.day)
    if as_of.tzinfo is not None:
        return as_of.astimezone(_UTC).replace(tzinfo=None)
    return _ET.localize(as_of).astimezone(_UTC).replace(tzinfo=None)


# --------------------------------------------------------------------------- date scanning
# Used by BOTH the raw-Cypher PIT enforcement (tools/analysis_tools.py) and the
# leakage canaries: one implementation of "how late is this payload dated?", tested once.

def as_moment(value: Any) -> Optional[datetime]:
    """Naive-ET datetime of a date-like value, else None.

    Accepts Neo4j temporals (anything with `.to_native()`), python date/datetime, and
    strings that are entirely an ISO date/timestamp. Aware values are converted to ET;
    a bare date becomes that day's start (the lenient edge — a date-only row is not
    evidence of an intraday leak).
    """
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        try:
            value = to_native()
        except Exception:
            return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_ET).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        s = value.strip()
        if not _ISO_RE.match(s):
            return None
        try:
            return as_moment(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def max_moment(obj: Any) -> Optional[datetime]:
    """Latest date-like moment anywhere inside a nested structure (dict/list/scalar).

    None when the payload carries no parseable timestamp. Dict KEYS are scanned too —
    series payloads are commonly keyed by date.
    """
    best: Optional[datetime] = None

    def _walk(node: Any) -> None:
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(k)
                _walk(v)
            return
        if isinstance(node, (list, tuple, set)):
            for v in node:
                _walk(v)
            return
        m = as_moment(node)
        if m is not None and (best is None or m > best):
            best = m

    _walk(obj)
    return best
