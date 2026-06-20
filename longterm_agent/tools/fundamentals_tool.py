"""
Fundamentals reader + trend computation (§ fundamentals_analysis).

Reads the `fundamentals` table (built in Phase 0) AS-OF the analysis date — never
seeing a statement before its filing or a snapshot before its fetch:

    EDGAR rows    : WHERE filed_date <= as_of   (point-in-time as-filed statements)
    yfinance rows : WHERE fetch_date <= as_of   (valuation / estimates snapshot)

The fetcher stores normalized values only; all derived metrics (growth, margins, FCF,
debt, expectation gap) are computed HERE at read time, so definitions can change without
re-backfilling.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, date

import psycopg2


def _rows(db_manager, sql, params):
    # Use a FRESH connection rather than the shared pool: this tool runs after the
    # multi-minute background-profiling LLM gap, by which point a pooled connection has
    # often been closed server-side (the pool doesn't health-check). One short-lived
    # connection per run is cheap and avoids the stale-connection OperationalError.
    conn = psycopg2.connect(**db_manager.db_config)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _edgar_quarters(db_manager, ticker, as_of, kind, limit=9):
    """Quarterly (non-FY) statements of one kind, newest first, filed by as_of."""
    rows = _rows(db_manager, """
        SELECT period_end, fiscal_period, filed_date, data
        FROM fundamentals
        WHERE ticker=%s AND source='edgar' AND kind=%s
          AND fiscal_period <> 'FY' AND filed_date <= %s
        ORDER BY period_end DESC LIMIT %s
    """, [ticker.upper(), kind, as_of.date(), limit])
    return [{"period_end": r[0], "fiscal_period": r[1], "filed_date": r[2], "data": r[3]} for r in rows]


def _yf_latest(db_manager, ticker, as_of, kind):
    rows = _rows(db_manager, """
        SELECT data FROM fundamentals
        WHERE ticker=%s AND source='yfinance' AND kind=%s AND fetch_date <= %s
        ORDER BY fetch_date DESC LIMIT 1
    """, [ticker.upper(), kind, as_of.date()])
    return rows[0][0] if rows else None


def _yoy_match(quarters, recent):
    """Find the same fiscal quarter ~1 year before `recent` (handles the missing-Q4 fiscal layout)."""
    fp = recent["fiscal_period"]
    rd = recent["period_end"]
    for q in quarters:
        if q is recent or q["fiscal_period"] != fp:
            continue
        days = (rd - q["period_end"]).days
        if 330 <= days <= 400:
            return q
    return None


def _safe_div(a, b):
    try:
        return a / b if a is not None and b else None
    except Exception:
        return None


def _revenue(d):
    """Revenue, deriving it from gross_profit + cost_of_revenue when the filer uses a
    revenue us-gaap tag our EDGAR extractor doesn't capture (e.g. NVDA)."""
    r = d.get("revenue")
    if r is not None:
        return r
    gp, cor = d.get("gross_profit"), d.get("cost_of_revenue")
    if gp is not None and cor is not None:
        return gp + cor
    return None


def _margin_series(income):
    """[(period, gross_m, op_m, net_m)] newest→oldest for up to 6 quarters."""
    out = []
    for q in income[:6]:
        d = q["data"]
        rev = _revenue(d)
        out.append({
            "period": str(q["period_end"]),
            "fp": q["fiscal_period"],
            "gross_margin": round(_safe_div(d.get("gross_profit"), rev), 3) if _safe_div(d.get("gross_profit"), rev) is not None else None,
            "operating_margin": round(_safe_div(d.get("operating_income"), rev), 3) if _safe_div(d.get("operating_income"), rev) is not None else None,
            "net_margin": round(_safe_div(d.get("net_income"), rev), 3) if _safe_div(d.get("net_income"), rev) is not None else None,
        })
    return out


def _expectation_gap(eps_rev):
    """Net up/down EPS estimate revisions (last 30d) across forward periods — the alpha signal."""
    if not eps_rev or "records" not in eps_rev:
        return None
    up = down = 0
    for rec in eps_rev["records"]:
        up += rec.get("upLast30days", 0) or 0
        down += rec.get("downLast30days", 0) or 0
    if up == 0 and down == 0:
        return None
    net = up - down
    direction = "upgrades" if net > 0 else ("downgrades" if net < 0 else "balanced")
    return {"up_30d": up, "down_30d": down, "net": net, "direction": direction}


def read_fundamentals(db_manager, ticker: str, as_of: datetime) -> Dict[str, Any]:
    """Assemble an as-of fundamentals summary for the LLM verdict + CLI."""
    income = _edgar_quarters(db_manager, ticker, as_of, "income_statement")
    info = _yf_latest(db_manager, ticker, as_of, "info") or {}
    eps_rev = _yf_latest(db_manager, ticker, as_of, "eps_revisions")

    result: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "as_of": as_of.date().isoformat(),
        "has_statements": bool(income),
        "n_quarters": len(income),
        "revenue_yoy": None,
        "revenue_qoq": None,
        "margin_series": _margin_series(income),
        "valuation": {},
        "fcf": None,
        "debt": {},
        "expectation_gap": _expectation_gap(eps_rev),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
    }

    # revenue growth
    if income:
        recent = income[0]
        rev0 = _revenue(recent["data"])
        prior_y = _yoy_match(income, recent)
        if prior_y and rev0:
            ratio = _safe_div(rev0, _revenue(prior_y["data"]))
            result["revenue_yoy"] = round(ratio - 1, 4) if ratio else None
        if len(income) > 1 and rev0:
            qoq = _safe_div(rev0, _revenue(income[1]["data"]))
            result["revenue_qoq"] = round(qoq - 1, 4) if qoq else None
        result["latest_quarter"] = {"period": str(recent["period_end"]),
                                    "fp": recent["fiscal_period"], "filed": str(recent["filed_date"])}

    # valuation + FCF + debt from the yfinance snapshot
    if info:
        result["valuation"] = {
            k: info.get(k) for k in
            ["trailingPE", "forwardPE", "priceToSalesTrailing12Months", "enterpriseToEbitda",
             "marketCap", "profitMargins", "grossMargins", "operatingMargins",
             "revenueGrowth", "earningsGrowth", "returnOnEquity"]
            if info.get(k) is not None
        }
        result["fcf"] = info.get("freeCashflow")
        result["debt"] = {"total_debt": info.get("totalDebt"), "total_cash": info.get("totalCash")}
        if info.get("totalDebt") is not None and info.get("totalCash") is not None:
            result["debt"]["net_debt"] = info["totalDebt"] - info["totalCash"]

    return result
