"""Data-freshness contract between fetchers and consumers (Modification Plan Phase 2).

Fetchers and agents are decoupled by design; this module is the contract that decoupling
was missing: consumers can ask "is every source I depend on actually current?" before
trusting a run. Born from the 2026-06-15 incident where SPY silently froze for three
weeks and outcome grading was contaminated.

Sources checked (thresholds env-tunable via core.settings):
  news          max(news.published)                    calendar days   FRESH_NEWS_MAX_DAYS (2)
  prices        max(stock_prices_daily.date)           weekdays        FRESH_PRICES_MAX_TRADING_DAYS (3)
  prices_spy    same, ticker='SPY' (grading benchmark) weekdays        same threshold
  fundamentals  max(fundamentals.fetch_date)           calendar days   FRESH_FUNDAMENTALS_MAX_DAYS (14)

Fail-closed: a source whose query errors out is reported STALE, not skipped.
`evaluate_freshness` is pure (unit-testable); `check_freshness` adds the DB reads.
"""
from datetime import date, datetime, timedelta

from core.db import get_conn
from core.settings import get_settings

_QUERIES = {
    "news": "SELECT max(published) FROM news",
    "prices": "SELECT max(date) FROM stock_prices_daily",
    "prices_spy": "SELECT max(date) FROM stock_prices_daily WHERE ticker='SPY'",
    "fundamentals": "SELECT max(fetch_date) FROM fundamentals",
}


def _weekdays_between(start: date, end: date) -> int:
    """Weekdays strictly after `start` up to `end` — an approximation of trading days.

    Market holidays are NOT subtracted, so a holiday makes a source look one day
    *staler* than it market-wise is. That errs toward flagging, never toward silently
    accepting a frozen series — the safe direction for a freshness gate.
    """
    if end <= start:
        return 0
    days = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def _thresholds():
    s = get_settings()
    return {
        "news": s.fresh_news_max_days,
        "prices": s.fresh_prices_max_trading_days,
        "prices_spy": s.fresh_prices_max_trading_days,
        "fundamentals": s.fresh_fundamentals_max_days,
    }


def evaluate_freshness(maxes: dict, now: datetime = None, thresholds: dict = None) -> dict:
    """Pure evaluation: {source: max_value|None|Exception} → freshness report.

    Returns {"sources": {name: {max_value, age, threshold, unit, stale, error?}},
             "stale": [names], "any_stale": bool, "checked_at": iso}.
    """
    now = now or datetime.now()
    today = now.date()
    thresholds = {**_thresholds(), **(thresholds or {})}  # partial overrides allowed
    sources, stale_names = {}, []

    for name, threshold in thresholds.items():
        if name not in maxes:
            continue
        val = maxes[name]
        entry = {"max_value": None, "age": None, "threshold": threshold,
                 "unit": "weekdays" if name.startswith("prices") else "days", "stale": True}
        if isinstance(val, Exception) or val is None:
            entry["error"] = str(val) if val is not None else "no rows"
        else:
            entry["max_value"] = val
            if name.startswith("prices"):
                entry["age"] = _weekdays_between(val, today)
            else:
                as_date = val.date() if isinstance(val, datetime) else val
                entry["age"] = (today - as_date).days
            entry["stale"] = entry["age"] > threshold
        sources[name] = entry
        if entry["stale"]:
            stale_names.append(name)

    return {"sources": sources, "stale": stale_names,
            "any_stale": bool(stale_names), "checked_at": now.isoformat(timespec="seconds")}


def check_freshness(db_config: dict = None, now: datetime = None, thresholds: dict = None) -> dict:
    """Query all sources (one short-lived connection) and evaluate. Never raises.

    `thresholds` partially overrides the settings defaults — e.g. the Sunday batch
    preflight passes {"prices": 2, "prices_spy": 2} because it runs right after the
    Saturday fetch, while the ad-hoc default (5) tolerates the weekly cadence.
    """
    maxes = {}
    try:
        with get_conn(db_config) as conn:
            with conn.cursor() as cur:
                for name, sql in _QUERIES.items():
                    try:
                        cur.execute(sql)
                        maxes[name] = cur.fetchone()[0]
                    except Exception as e:  # fail-closed per source
                        conn.rollback()
                        maxes[name] = e
    except Exception as e:  # DB fully unreachable → everything stale
        maxes = {name: e for name in _QUERIES}
    return evaluate_freshness(maxes, now=now, thresholds=thresholds)


def format_report(report: dict) -> str:
    """Compact one-line-per-source text for alerts / the stats bot."""
    lines = []
    for name, e in report["sources"].items():
        mark = "STALE" if e["stale"] else "ok"
        if e.get("error"):
            lines.append(f"{name:13s} ERROR: {e['error'][:80]} → {mark}")
        else:
            mv = e["max_value"]
            mv = mv.strftime("%Y-%m-%d") if hasattr(mv, "strftime") else str(mv)
            lines.append(f"{name:13s} max={mv}  age={e['age']}{e['unit'][0]}  (≤{e['threshold']})  {mark}")
    return "\n".join(lines)
