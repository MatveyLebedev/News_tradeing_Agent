"""
Horizon-appropriate risk checks (§ risk_gate) — the long-term analogs of the intraday
agent's pre-news/volatility gates.

priced_in_check : has the stock already moved hard in the signal's direction over the
    shift window? If so the catalyst is (partly) in the price → cut conviction.
earnings_event_risk : is an earnings date inside the next N days? A thesis formed right
    before earnings is a binary-event bet, not a background trade → flag.

Both read AS-OF (no lookahead) and degrade gracefully when data is missing.
"""
from typing import Any, Dict
from datetime import datetime, timedelta

import psycopg2


def _conn(db_manager):
    return psycopg2.connect(**db_manager.db_config)


def priced_in_check(db_manager, ticker: str, as_of: datetime, shift_window_days: int) -> Dict[str, Any]:
    """Return the % price move over the shift window from stock_prices_daily (as-of)."""
    start = (as_of - timedelta(days=shift_window_days)).date()
    conn = _conn(db_manager)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, close FROM stock_prices_daily
                WHERE ticker=%s AND date <= %s AND date >= %s
                ORDER BY date
            """, [ticker.upper(), as_of.date(), start])
            rows = cur.fetchall()
    except Exception as e:
        return {"available": False, "note": f"price read failed: {e}"}
    finally:
        conn.close()

    closes = [float(r[1]) for r in rows if r[1] is not None]
    if len(closes) < 2:
        return {"available": False, "note": "insufficient daily bars"}
    move_pct = (closes[-1] - closes[0]) / closes[0] * 100.0
    return {"available": True, "window_move_pct": round(move_pct, 2),
            "first": closes[0], "last": closes[-1], "bars": len(closes)}


def earnings_event_risk(db_manager, ticker: str, as_of: datetime, within_days: int) -> Dict[str, Any]:
    """Flag if an upcoming earnings date (from the yfinance earnings_dates snapshot) is near."""
    conn = _conn(db_manager)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT data FROM fundamentals
                WHERE ticker=%s AND source='yfinance' AND kind='earnings_dates'
                  AND fetch_date <= %s
                ORDER BY fetch_date DESC LIMIT 1
            """, [ticker.upper(), as_of.date()])
            row = cur.fetchone()
    except Exception as e:
        return {"available": False, "note": f"earnings read failed: {e}"}
    finally:
        conn.close()

    if not row or not row[0]:
        return {"available": False, "note": "no earnings_dates snapshot"}

    records = (row[0] or {}).get("records", [])
    horizon = as_of + timedelta(days=within_days)
    upcoming = []
    for rec in records:
        p = rec.get("period")
        if not p:
            continue
        try:
            d = datetime.fromisoformat(str(p).replace("Z", "")).replace(tzinfo=None)
        except Exception:
            continue
        if as_of <= d <= horizon:
            upcoming.append(d.date().isoformat())
    return {"available": True, "earnings_within_days": within_days,
            "upcoming": sorted(upcoming), "flag": bool(upcoming)}
