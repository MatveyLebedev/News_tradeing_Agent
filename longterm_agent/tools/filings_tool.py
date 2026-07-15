"""
AS-OF readers over the 9.x advisory tables (filings / insider_trades / options_iv /
short-interest snapshot) — consumed by deep-analysis tools. All reads are point-in-time
(filed_date / snapshot_date / fetch_date ≤ as_of) and degrade to {"available": False}
when a table is missing or empty. Pure formatters are split out for unit tests.
"""
from datetime import timedelta
from typing import Any, Dict, List

import psycopg2


def _conn(db_manager):
    return psycopg2.connect(**db_manager.db_config)


def _q(db_manager, sql, params):
    conn = _conn(db_manager)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- filings
def read_recent_filings(db_manager, ticker: str, as_of, days: int = 45,
                        limit: int = 12) -> Dict[str, Any]:
    """Recent 8-K/dilution filings with a short excerpt of the press-release exhibit
    (or body) — enough for the LLM to decide what to chase via search/graph tools."""
    try:
        rows = _q(db_manager, """
            SELECT form, filed_date, items, COALESCE(exhibit_text, body_text), url
            FROM filings
            WHERE ticker=%s AND filed_date <= %s AND filed_date >= %s
            ORDER BY filed_date DESC LIMIT %s
        """, [ticker.upper(), as_of.date(), as_of.date() - timedelta(days=days), limit])
    except Exception as e:
        return {"available": False, "note": f"filings read failed: {e}"}
    if not rows:
        return {"available": False, "note": f"no filings in the last {days}d as-of"}
    return {"available": True, "ticker": ticker.upper(), "window_days": days,
            "filings": [{"form": f, "filed": str(d), "items": items,
                         "excerpt": (text or "")[:600], "url": url}
                        for f, d, items, text, url in rows]}


# --------------------------------------------------------------------------- insider
def format_insider(rows: List[tuple], days: int) -> Dict[str, Any]:
    """Pure: insider transaction rows → aggregate + notable list. Codes: P=open-market
    buy, S=sale (the two the risk story cares about; grants/awards excluded from nets)."""
    if not rows:
        return {"available": False, "note": f"no insider filings in the last {days}d as-of"}
    buys = [r for r in rows if r[3] == "P"]
    sells = [r for r in rows if r[3] == "S"]

    def _net(sub):
        return round(sum((r[5] or 0) for r in sub), 0)

    def _val(sub):
        return round(sum((r[6] or 0) for r in sub), 0)

    recent = [{"filed": str(r[0]), "insider": r[1], "title": r[2], "code": r[3],
               "acquired": r[4], "shares": r[5], "value_usd": r[6]} for r in rows[:10]]
    return {"available": True, "window_days": days,
            "open_market_buys": {"n": len(buys), "shares": _net(buys), "value_usd": _val(buys)},
            "open_market_sells": {"n": len(sells), "shares": _net(sells), "value_usd": _val(sells)},
            "cluster_selling": len({r[1] for r in sells}) >= 3,  # ≥3 distinct insiders selling
            "recent": recent}


def read_insider_activity(db_manager, ticker: str, as_of, days: int = 90) -> Dict[str, Any]:
    try:
        rows = _q(db_manager, """
            SELECT filed_date, insider_name, insider_title, code, acquired, shares, value
            FROM insider_trades
            WHERE ticker=%s AND filed_date <= %s AND filed_date >= %s
            ORDER BY filed_date DESC
        """, [ticker.upper(), as_of.date(), as_of.date() - timedelta(days=days)])
    except Exception as e:
        return {"available": False, "note": f"insider read failed: {e}"}
    return format_insider(rows, days)


# --------------------------------------------------------------------------- options
def read_options_iv(db_manager, ticker: str, as_of) -> Dict[str, Any]:
    """Latest IV snapshot as-of + a 30d-back reference for the trend direction."""
    try:
        rows = _q(db_manager, """
            SELECT snapshot_date, data FROM options_iv
            WHERE ticker=%s AND snapshot_date <= %s
            ORDER BY snapshot_date DESC LIMIT 1
        """, [ticker.upper(), as_of.date()])
        back = _q(db_manager, """
            SELECT snapshot_date, data FROM options_iv
            WHERE ticker=%s AND snapshot_date <= %s
            ORDER BY snapshot_date DESC LIMIT 1
        """, [ticker.upper(), as_of.date() - timedelta(days=30)])
    except Exception as e:
        return {"available": False, "note": f"options read failed: {e}"}
    if not rows:
        return {"available": False,
                "note": "no IV snapshot as-of (forward archive starts 2026-07-15)"}
    snap_date, data = rows[0]
    out = {"available": True, "snapshot_date": str(snap_date), **(data or {})}
    if back and back[0][1]:
        prev = back[0][1]
        cur_iv = ((data or {}).get("expiries") or [{}])[0].get("atm_call_iv")
        prev_iv = (prev.get("expiries") or [{}])[0].get("atm_call_iv")
        if cur_iv is not None and prev_iv is not None:
            out["atm_iv_30d_change"] = round(cur_iv - prev_iv, 4)
    return out


# --------------------------------------------------------------------------- short interest
def read_short_interest(db_manager, ticker: str, as_of) -> Dict[str, Any]:
    """Latest short-interest figures from the weekly yfinance info snapshot (9.5)."""
    try:
        rows = _q(db_manager, """
            SELECT fetch_date, data FROM fundamentals
            WHERE ticker=%s AND source='yfinance' AND kind='info' AND fetch_date <= %s
            ORDER BY fetch_date DESC LIMIT 1
        """, [ticker.upper(), as_of.date()])
    except Exception as e:
        return {"available": False, "note": f"short-interest read failed: {e}"}
    if not rows:
        return {"available": False, "note": "no info snapshot as-of"}
    fetch_date, info = rows[0]
    keys = ("sharesShort", "sharesShortPriorMonth", "shortRatio", "shortPercentOfFloat",
            "dateShortInterest")
    vals = {k: (info or {}).get(k) for k in keys}
    if all(v is None for v in vals.values()):
        return {"available": False,
                "note": "snapshot predates short-interest keys (collected from 2026-07-15)"}
    if vals.get("shortPercentOfFloat") is not None:
        vals["shortPercentOfFloat"] = round(vals["shortPercentOfFloat"] * 100, 2)
    return {"available": True, "snapshot_date": str(fetch_date), **vals}
