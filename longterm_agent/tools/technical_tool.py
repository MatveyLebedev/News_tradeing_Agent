"""
Long-term daily technicals (§ fundamentals_analysis) — reads stock_prices_daily AS-OF
the analysis date. Best-effort: returns {"available": False} if too few bars exist
(the daily backfill may not have run for every ticker yet).
"""
from statistics import mean
from typing import Any, Dict
from datetime import datetime, timedelta

import psycopg2

from .pit import daily_bar_cutoff


def compute_daily_technicals(db_manager, ticker: str, as_of: datetime, lookback_days: int = 180) -> Dict[str, Any]:
    start = (as_of - timedelta(days=lookback_days * 2)).date()  # pull extra for MA warmup
    # Fresh connection (runs after the long LLM gap — see fundamentals_tool._rows note).
    conn = psycopg2.connect(**db_manager.db_config)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, close FROM stock_prices_daily
                WHERE ticker=%s AND date <= %s AND date >= %s
                ORDER BY date
            """, [ticker.upper(), daily_bar_cutoff(as_of), start])
            rows = cur.fetchall()
    finally:
        conn.close()

    closes = [float(r[1]) for r in rows if r[1] is not None]
    if len(closes) < 20:
        return {"available": False, "n_bars": len(closes)}

    last = closes[-1]
    window = closes[-min(len(closes), lookback_days):]
    ret_window = (last / window[0] - 1) if window[0] else None
    ma50 = mean(closes[-50:]) if len(closes) >= 50 else None
    ma200 = mean(closes[-200:]) if len(closes) >= 200 else None

    trend = None
    if ma50 and ma200:
        trend = "uptrend" if (last > ma50 > ma200) else ("downtrend" if (last < ma50 < ma200) else "mixed")
    elif ma50:
        trend = "above_ma50" if last > ma50 else "below_ma50"

    return {
        "available": True,
        "n_bars": len(closes),
        "last_close": round(last, 2),
        "return_window_pct": round(ret_window * 100, 1) if ret_window is not None else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "trend": trend,
        "support": round(min(window), 2),
        "resistance": round(max(window), 2),
    }
