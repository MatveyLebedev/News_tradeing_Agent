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
from typing import Any, Dict, Optional
from datetime import datetime, date, timedelta

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


# --- point-in-time valuation (Backtest Plan B2: reconstruct PE/PB/PS, asymmetry-free) -------
def _close_on_or_before(db_manager, ticker, target: date, tolerance_days: int = 7):
    """Latest daily close on/before `target` (PIT price input — never a future bar).
    Returns (close, price_date) or (None, None)."""
    rows = _rows(db_manager, """
        SELECT date, close FROM stock_prices_daily
        WHERE ticker=%s AND date <= %s AND date >= %s AND close IS NOT NULL
        ORDER BY date DESC LIMIT 1
    """, [ticker.upper(), target, target - timedelta(days=tolerance_days)])
    return (float(rows[0][1]), rows[0][0]) if rows else (None, None)


def _edgar_income_all(db_manager, ticker, as_of, limit=12):
    """Income statements INCLUDING annual (FY) rows — needed for TTM reconstruction
    (the quarterly reader excludes FY). Filed by as_of, newest period first."""
    rows = _rows(db_manager, """
        SELECT period_end, fiscal_period, filed_date, data
        FROM fundamentals
        WHERE ticker=%s AND source='edgar' AND kind='income_statement' AND filed_date <= %s
        ORDER BY period_end DESC LIMIT %s
    """, [ticker.upper(), as_of.date(), limit])
    return [{"period_end": r[0], "fiscal_period": r[1], "data": r[3]} for r in rows]


def _latest_balance_equity(db_manager, ticker, as_of):
    """Latest as-filed stockholders' equity (any fiscal period incl. FY), filed by as_of."""
    rows = _rows(db_manager, """
        SELECT data FROM fundamentals
        WHERE ticker=%s AND source='edgar' AND kind='balance_sheet' AND filed_date <= %s
        ORDER BY period_end DESC LIMIT 1
    """, [ticker.upper(), as_of.date()])
    return rows[0][0].get("stockholders_equity") if rows else None


def _implied_shares(income):
    """Diluted share count implied by net_income / diluted EPS of the most recent period that
    carries both. Taken from ONE period so it is consistent with any share split (EPS sums
    across a split are not). None if unavailable."""
    for q in income:
        d = q["data"]
        eps, ni = d.get("eps_diluted"), d.get("net_income")
        if eps is not None and ni is not None and abs(eps) >= 0.01:
            shares = ni / eps
            if shares > 0:
                return shares
    return None


def _ttm_sum(income, getter):
    """Trailing-12-month sum of an *additive* dollar metric (net income, revenue), point-in-time
    and split-immune. Anchors on the latest annual (FY = a full 12 months) and rolls it forward:

        TTM = FY + Σ(quarters after the FY end) − Σ(matching earliest quarters within the FY year)

    e.g. once Q1 of the new year is filed, TTM = FY + Q1_new − Q1_prior. Returns None if any
    needed piece is missing (never fabricates a partial year)."""
    annuals = [q for q in income if q["fiscal_period"] == "FY"]
    quarters = [q for q in income if q["fiscal_period"] != "FY"]
    if not annuals:
        return None
    anchor = max(annuals, key=lambda q: q["period_end"])
    base = getter(anchor["data"])
    if base is None:
        return None
    e_a = anchor["period_end"]
    post = sorted([q for q in quarters if q["period_end"] > e_a], key=lambda q: q["period_end"])
    fy_qs = sorted([q for q in quarters if e_a - timedelta(days=370) < q["period_end"] <= e_a],
                   key=lambda q: q["period_end"])
    if len(post) > len(fy_qs):
        return None  # can't match the stub quarters to prior-year quarters → bail
    total = base
    for q in post:
        v = getter(q["data"])
        if v is None:
            return None
        total += v
    for q in fy_qs[:len(post)]:
        v = getter(q["data"])
        if v is None:
            return None
        total -= v
    return total


def valuation_pit(db_manager, ticker: str, as_of: datetime) -> Dict[str, Any]:
    """Reconstruct trailing PE / P/B / P/S at `as_of` from EDGAR (TTM, as-filed) × the daily
    close. Both inputs are point-in-time, so — unlike the forward-only yfinance snapshot — these
    are available at ANY as_of and are recomputed at the analysis date rather than frozen at a
    fetch date. This removes the §3.3 valuation asymmetry (live vs backtest use the same path).

    Ratios go through market cap (= close × implied diluted shares) and TTM *dollar* figures, so
    they are immune to share splits. Returns {} of metrics it can't form (missing EDGAR/price)."""
    income = _edgar_income_all(db_manager, ticker, as_of)
    price, price_date = _close_on_or_before(db_manager, ticker, as_of.date())
    out: Dict[str, Any] = {"source": "edgar+price", "price": price,
                           "price_date": price_date.isoformat() if price_date else None}
    shares = _implied_shares(income)
    if not income or price is None or not shares:
        return out
    mktcap = price * shares
    out["marketCap"] = round(mktcap)

    ttm_ni = _ttm_sum(income, lambda d: d.get("net_income"))
    if ttm_ni and ttm_ni > 0:
        out["trailingPE"] = round(mktcap / ttm_ni, 3)
        out["ttm_eps"] = round(ttm_ni / shares, 4)

    equity = _latest_balance_equity(db_manager, ticker, as_of)
    if equity and equity > 0:
        out["priceToBook"] = round(mktcap / equity, 3)

    ttm_rev = _ttm_sum(income, _revenue)
    if ttm_rev and ttm_rev > 0:
        out["priceToSalesTrailing12Months"] = round(mktcap / ttm_rev, 3)
    return out


# --- Phase 12: STORED-field wiring (expert checklist #43-47, #49-50, #53-54) -----------
# Pure block builders over already-fetched rows (unit-testable without a DB); fetching
# stays in read_fundamentals. All EDGAR cash-flow values are stored sign-normalized
# positive (verified against NVDA/AAPL/XOM filings), so FCF = OCF − capex directly.

def _cash_flow_block(qs) -> Dict[str, Any]:
    """True PIT FCF, capex intensity, capital return — summed over the available as-filed
    quarters (~TTM when 4 are present; window labeled so the LLM sees the coverage)."""
    vals = [q["data"] for q in qs]
    pairs = [(d.get("operating_cash_flow"), d.get("capex")) for d in vals
             if d.get("operating_cash_flow") is not None and d.get("capex") is not None]
    if not pairs:
        return {"quarters": 0}
    s_ocf = sum(o for o, _ in pairs)
    s_capex = sum(c for _, c in pairs)
    fcf = s_ocf - s_capex
    bb = sum(d.get("stock_buyback") or 0 for d in vals)
    dv = sum(d.get("dividends_paid") or 0 for d in vals)
    return {
        "quarters": len(pairs),
        "window": f"{qs[-1]['period_end']}..{qs[0]['period_end']}",
        "fcf": fcf,
        "capex_intensity": round(_safe_div(s_capex, s_ocf), 3) if _safe_div(s_capex, s_ocf) is not None else None,
        "buybacks": bb,
        "dividends": dv,
        "capital_return_vs_fcf": round(_safe_div(bb + dv, fcf), 3) if fcf and fcf > 0 else None,
    }


def _balance_block(d) -> Dict[str, Any]:
    """PIT leverage from the latest as-filed balance sheet."""
    if not d:
        return {}
    lt, st = d.get("long_term_debt"), d.get("short_term_debt")
    total_debt = (lt or 0) + (st or 0) if (lt is not None or st is not None) else None
    cr = _safe_div(d.get("current_assets"), d.get("current_liabilities"))
    net_debt = (total_debt - d["cash_and_equivalents"]
                if total_debt is not None and d.get("cash_and_equivalents") is not None else None)
    dte = _safe_div(total_debt, d.get("stockholders_equity")) if total_debt is not None else None
    return {
        "long_term_debt": lt, "short_term_debt": st,
        "current_ratio": round(cr, 2) if cr is not None else None,
        "net_debt": net_debt,
        "debt_to_equity": round(dte, 2) if dte is not None else None,
    }


def _opex_block(income) -> Dict[str, Any]:
    """R&D / SG&A intensity (share of revenue) newest quarter + change vs the same quarter
    a year ago (fiscal-matched, so seasonality doesn't masquerade as a trend)."""
    if not income:
        return {}
    recent = income[0]
    rev0 = _revenue(recent["data"])
    out: Dict[str, Any] = {}
    for key, name in (("rd_expense", "rd_intensity"), ("sga_expense", "sga_intensity")):
        cur = _safe_div(recent["data"].get(key), rev0)
        out[name] = round(cur, 3) if cur is not None else None
        prior = _yoy_match(income, recent)
        if prior and cur is not None:
            prev = _safe_div(prior["data"].get(key), _revenue(prior["data"]))
            if prev is not None:
                out[f"{name}_yoy_delta"] = round(cur - prev, 3)
    return out


def _eps_trajectory(income) -> Dict[str, Any]:
    """Sequential basic-EPS path (#50): the series + mean QoQ delta as a slope proxy."""
    series = [q["data"].get("eps_basic") for q in income[:6]]
    series = [round(v, 2) if v is not None else None for v in series]
    known = [v for v in series if v is not None]
    out = {"series_newest_first": series}
    if len(known) >= 3:
        deltas = [known[i] - known[i + 1] for i in range(len(known) - 1)]
        avg = sum(deltas) / len(deltas)
        out["avg_qoq_delta"] = round(avg, 3)
        out["slope"] = "rising" if avg > 0.01 else ("falling" if avg < -0.01 else "flat")
    return out


def _street_block(info, price) -> Dict[str, Any]:
    """Analyst positioning + risk stats from the yfinance snapshot (#53-54)."""
    if not info:
        return {}
    out = {
        "recommendation_mean": info.get("recommendationMean"),
        "recommendation": info.get("recommendationKey"),
        "beta": info.get("beta"),
    }
    tgt = info.get("targetMeanPrice")
    if tgt and price:
        out["target_gap"] = round(tgt / price - 1, 3)
    hi, lo = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow")
    if price and hi and lo and hi > lo:
        out["pos_52w"] = round((price - lo) / (hi - lo), 2)  # 0=at low, 1=at high
    return out


def peer_forward_pe(db_manager, ticker: str, sector: str, as_of: datetime) -> Dict[str, Any]:
    """Relative multiple gap (#49): the company's forward P/E vs its sector peers' median
    (latest snapshot per peer fetched ≤ as_of). 'Expensive vs its peers' ≠ 'expensive'."""
    if not sector:
        return {}
    rows = _rows(db_manager, """
        SELECT DISTINCT ON (ticker) ticker, data->>'sector', data->>'forwardPE'
        FROM fundamentals
        WHERE source='yfinance' AND kind='info' AND fetch_date <= %s
        ORDER BY ticker, fetch_date DESC
    """, [as_of.date()])
    peers, own = [], None
    for tk, sec, fpe in rows:
        try:
            fpe = float(fpe)
        except (TypeError, ValueError):
            continue
        if fpe <= 0:
            continue
        if tk == ticker.upper():
            own = fpe
        elif sec == sector:
            peers.append(fpe)
    if own is None or len(peers) < 3:
        return {"n_peers": len(peers)}
    peers_sorted = sorted(peers)
    median = peers_sorted[len(peers_sorted) // 2]
    pct = sum(1 for p in peers_sorted if p <= own) / len(peers_sorted)
    return {"n_peers": len(peers), "forward_pe": own,
            "peer_median_fpe": round(median, 2),
            "peer_percentile": round(pct, 2),
            "gap_vs_median": round(own / median - 1, 3) if median else None}


# --- street consensus & realized surprises (advisory; guidance-vs-consensus groundwork) ---
def _fmp_latest(db_manager, ticker, as_of):
    """Latest FMP earnings snapshot ≤ as_of → (data, is_pit=True). If none exists (all
    snapshots post-date as_of — the pre-2026-07-15 backtest case), fall back to the
    EARLIEST later snapshot for HISTORY-ONLY use → (data, is_pit=False): the records
    inside are still PIT-filtered by report date in format_consensus, and FMP claims
    per-report estimates are frozen at report time (their claim → advisory)."""
    rows = _rows(db_manager, """
        SELECT data FROM fundamentals
        WHERE ticker=%s AND source='fmp' AND kind='earnings' AND fetch_date <= %s
        ORDER BY fetch_date DESC LIMIT 1
    """, [ticker.upper(), as_of.date()])
    if rows:
        return rows[0][0], True
    rows = _rows(db_manager, """
        SELECT data FROM fundamentals
        WHERE ticker=%s AND source='fmp' AND kind='earnings' AND fetch_date > %s
        ORDER BY fetch_date ASC LIMIT 1
    """, [ticker.upper(), as_of.date()])
    return (rows[0][0], False) if rows else (None, True)


def _yf_hist_future(db_manager, ticker, as_of):
    """earnings_history from the EARLIEST post-as_of analyst_estimates snapshot —
    the retro fallback when neither a strict yfinance snapshot nor FMP coverage
    exists (free FMP key covers ~40 symbols; the rest 402). History-only."""
    rows = _rows(db_manager, """
        SELECT data FROM fundamentals
        WHERE ticker=%s AND source='yfinance' AND kind='analyst_estimates' AND fetch_date > %s
          AND jsonb_array_length(COALESCE(data->'earnings_history', '[]'::jsonb)) > 0
        ORDER BY fetch_date ASC LIMIT 1
    """, [ticker.upper(), as_of.date()])
    return (rows[0][0] or {}).get("earnings_history") if rows else None


# Reporting lag buffer for quarter-end-labelled yfinance history rows: a quarter ending
# ≤ as_of may not have been REPORTED by as_of yet, so only quarters older than this many
# days count as realized when the row comes from a post-as_of snapshot.
YF_HIST_REPORT_LAG_DAYS = 45


def _yf_surprises(hist, as_of_date, buffer_days=0):
    """yfinance earnings_history rows → recent_surprises entries (EPS only — yfinance
    has no revenue-surprise history). Rows are quarter-end labelled."""
    from datetime import timedelta
    cutoff = str(as_of_date - timedelta(days=buffer_days))
    out = []
    for r in hist or []:
        d = str(r.get("period") or "")[:10]
        sp = r.get("surprisePercent")
        if d and d <= cutoff and sp is not None:
            out.append({"date": d, "eps_surprise_pct": round(sp * 100, 1),
                        "revenue_surprise_pct": None})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:4]


def format_consensus(est: Optional[dict], fmp: Optional[dict], as_of_date,
                     fmp_is_pit: bool = True,
                     yf_hist_future: Optional[list] = None) -> Dict[str, Any]:
    """Pure: latest yfinance analyst_estimates snapshot + FMP earnings snapshot → compact
    consensus view. PIT inside the snapshot too: FMP rows dated AFTER as_of are the
    upcoming reports (their actuals must be null at as_of by construction); rows ≤ as_of
    with actuals are the realized-surprise history.

    fmp_is_pit=False → the FMP snapshot post-dates as_of (retro backtest): the realized-
    surprise history is still usable (report-date filtered, estimates frozen at report
    time per FMP), but next_report is DROPPED — its estimate may embed post-as_of
    revisions. EPS-surprise fallback chain when FMP has no history: est's own
    earnings_history (strict snapshot, no buffer) → yf_hist_future (post-as_of snapshot,
    45-day reporting-lag buffer on quarter-end labels)."""
    out: Dict[str, Any] = {"available": False}
    if est:
        fwd = {}
        for row in est.get("earnings_estimate") or []:
            p = row.get("period") or row.get("index")
            if p in ("0q", "+1q"):
                fwd[f"eps_{p}"] = {k: row.get(k) for k in ("avg", "low", "high", "numberOfAnalysts")}
        for row in est.get("revenue_estimate") or []:
            p = row.get("period") or row.get("index")
            if p in ("0q", "+1q"):
                fwd[f"revenue_{p}"] = {k: row.get(k) for k in ("avg", "low", "high")}
        if fwd:
            out.update({"available": True, "street_forward": fwd})
        trend = {}
        for row in est.get("eps_trend") or []:
            if (row.get("period") or row.get("index")) == "0q":
                trend = {k: row.get(k) for k in ("current", "30daysAgo", "90daysAgo")}
        if trend:
            out["eps_trend_0q"] = trend

    if fmp and fmp.get("records"):
        past, upcoming = [], []
        for r in fmp["records"]:
            d = str(r.get("date") or "")
            if not d:
                continue
            if d <= str(as_of_date) and r.get("epsActual") is not None:
                past.append(r)
            elif d > str(as_of_date):
                upcoming.append(r)
        past.sort(key=lambda r: r["date"], reverse=True)
        upcoming.sort(key=lambda r: r["date"])
        surprises = []
        for r in past[:4]:
            ea, ee = r.get("epsActual"), r.get("epsEstimated")
            ra, re_ = r.get("revenueActual"), r.get("revenueEstimated")
            surprises.append({
                "date": r["date"],
                "eps_surprise_pct": round((ea - ee) / abs(ee) * 100, 1) if ea is not None and ee else None,
                "revenue_surprise_pct": round((ra - re_) / abs(re_) * 100, 1) if ra is not None and re_ else None,
            })
        if surprises:
            out.update({"available": True, "recent_surprises": surprises})
        if upcoming and fmp_is_pit:
            nxt = upcoming[0]
            out.update({"available": True,
                        "next_report": {"date": nxt.get("date"),
                                        "eps_estimated": nxt.get("epsEstimated"),
                                        "revenue_estimated": nxt.get("revenueEstimated")}})

    # EPS-surprise fallback (free FMP key covers ~40 symbols; yfinance covers the rest)
    if not out.get("recent_surprises"):
        fallback = _yf_surprises((est or {}).get("earnings_history"), as_of_date)
        if not fallback and yf_hist_future:
            fallback = _yf_surprises(yf_hist_future, as_of_date,
                                     buffer_days=YF_HIST_REPORT_LAG_DAYS)
        if fallback:
            out.update({"available": True, "recent_surprises": fallback})
    if not out["available"]:
        out["note"] = "no consensus snapshots as-of (collection starts 2026-07-15 forward)"
    return out


def read_consensus(db_manager, ticker: str, as_of: datetime) -> Dict[str, Any]:
    """AS-OF street consensus + realized surprise history (yfinance + FMP snapshots).
    Feeds the deep-analysis tool always; feeds the fundamentals verdict prompt too when
    LT_CONSENSUS_IN_VERDICT is on (stage B — its own A/B per the release discipline)."""
    est = _yf_latest(db_manager, ticker, as_of, "analyst_estimates")
    fmp, fmp_is_pit = _fmp_latest(db_manager, ticker, as_of)
    # Retro fallback for EPS surprises fetched lazily — only when neither strict source
    # has any history (one extra query on exactly the paths that would otherwise be empty).
    yf_hist_future = None
    if not est and not (fmp or {}).get("records"):
        yf_hist_future = _yf_hist_future(db_manager, ticker, as_of)
    return format_consensus(est, fmp, as_of.date(),
                            fmp_is_pit=fmp_is_pit, yf_hist_future=yf_hist_future)


def consensus_prompt_block(cons: Dict[str, Any]) -> str:
    """Pure: read_consensus output → compact prompt block for the fundamentals verdict.
    Deterministic rendering (the prompt is an LLM-cache key). Empty dict → ''."""
    if not cons:
        return ""
    lines = []
    fwd = cons.get("street_forward") or {}
    if fwd:
        parts = []
        for key in ("eps_0q", "eps_+1q", "revenue_0q", "revenue_+1q"):
            d = fwd.get(key)
            if d:
                seg = f"{key} avg={d.get('avg')}"
                if d.get("numberOfAnalysts"):
                    seg += f" (n={d['numberOfAnalysts']})"
                parts.append(seg)
        if parts:
            lines.append("  forward estimates: " + "; ".join(parts))
    trend = cons.get("eps_trend_0q") or {}
    if trend:
        lines.append(f"  eps estimate trend (0q): now={trend.get('current')}, "
                     f"30d ago={trend.get('30daysAgo')}, 90d ago={trend.get('90daysAgo')}")
    surp = cons.get("recent_surprises") or []
    if surp:
        segs = []
        for s in surp:
            e, r = s.get("eps_surprise_pct"), s.get("revenue_surprise_pct")
            seg = f"{s.get('date')}: EPS {'n/a' if e is None else f'{e:+}%'}"
            if r is not None:
                seg += f" / rev {r:+}%"
            segs.append(seg)
        lines.append("  realized surprises (newest first): " + "; ".join(segs))
    nxt = cons.get("next_report") or {}
    if nxt:
        lines.append(f"  next report: {nxt.get('date')} (street: EPS {nxt.get('eps_estimated')}, "
                     f"revenue {nxt.get('revenue_estimated')})")
    if not lines:
        lines.append(f"  {cons.get('note', 'no consensus data as-of')}")
    # Stage B.2 guard (ledger #12 post-mortem): the #12 book lost to ONE trade where
    # chronic-miss history alone initiated a short into a +24% catalyst rebound.
    lines.append(
        "  GUARD: surprise history modulates conviction ONLY. Never let beat/miss "
        "history alone initiate a direction — especially not AGAINST a large recent "
        "price move or the prevailing trend. Direction must be grounded in the news "
        "shift and current fundamentals; this block can strengthen or weaken it.")
    return (
        "STREET CONSENSUS & SURPRISE HISTORY (direction is set by the delta vs "
        "expectations, not absolute quality — 'good' results the street already expected "
        "are priced in):\n" + "\n".join(lines) + "\n"
    )


def consensus_posture_line(cons: Dict[str, Any]) -> str:
    """Pure: one-line consensus posture for the synthesis prompt. '' when nothing usable."""
    if not cons or not cons.get("available"):
        return ""
    parts = []
    trend = cons.get("eps_trend_0q") or {}
    cur, d90 = trend.get("current"), trend.get("90daysAgo")
    if cur is not None and d90 not in (None, 0):
        chg = (cur - d90) / abs(d90) * 100
        direction = "rising" if chg > 1 else ("falling" if chg < -1 else "flat")
        parts.append(f"0q EPS estimates {direction} ({chg:+.1f}% over 90d)")
    surp = [s.get("eps_surprise_pct") for s in (cons.get("recent_surprises") or [])
            if s.get("eps_surprise_pct") is not None]
    if surp:
        beats = sum(1 for s in surp if s > 0)
        parts.append(f"beat street {beats}/{len(surp)} of recent quarters "
                     f"(EPS surprises: {', '.join(f'{s:+}%' for s in surp)})")
    nxt = (cons.get("next_report") or {}).get("date")
    if nxt:
        parts.append(f"next report {nxt}")
    if not parts:
        return ""
    return ("STREET CONSENSUS (advisory — weigh what is already priced in; modulates "
            "conviction only, never a direction trigger by itself): "
            + "; ".join(parts) + ".\n")


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

    # Point-in-time valuation (EDGAR × price): asymmetry-free trailing PE / P/B / P/S, available
    # at any as_of — overlays the matching keys from the (forward-only) yfinance snapshot. The
    # snapshot's other fields (forwardPE, margins, growth, ROE) stay as supplemental context.
    pit = valuation_pit(db_manager, ticker, as_of)
    for k in ("trailingPE", "priceToBook", "priceToSalesTrailing12Months", "marketCap"):
        if pit.get(k) is not None:
            result["valuation"][k] = pit[k]
    result["valuation_pit"] = pit

    # Phase 12 — wire the STORED fields into the verdict's evidence (zero new data):
    cf_quarters = _edgar_quarters(db_manager, ticker, as_of, "cash_flow", limit=4)
    bal_rows = _rows(db_manager, """
        SELECT data FROM fundamentals
        WHERE ticker=%s AND source='edgar' AND kind='balance_sheet' AND filed_date <= %s
        ORDER BY period_end DESC LIMIT 1
    """, [ticker.upper(), as_of.date()])
    result["capital"] = _cash_flow_block(cf_quarters)
    result["balance"] = _balance_block(bal_rows[0][0] if bal_rows else None)
    result["opex"] = _opex_block(income)
    result["eps_trajectory"] = _eps_trajectory(income)
    result["street"] = _street_block(info, pit.get("price"))
    result["peer_multiples"] = peer_forward_pe(db_manager, ticker, result["sector"], as_of)

    return result
