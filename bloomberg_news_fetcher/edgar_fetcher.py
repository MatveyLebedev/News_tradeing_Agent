"""
SEC EDGAR point-in-time fundamentals fetcher.

Pulls the XBRL `companyfacts` feed for a ticker and normalizes the raw us-gaap
concepts into per-period income / balance-sheet / cash-flow statements. Every
statement carries the *original* 10-Q/10-K filing date (`filed_date`), which is the
point-in-time key the agent uses to avoid lookahead bias:

    a statement is only visible when  filed_date <= as_of

This is the FREE, true-PIT source for historical statements (the source of truth
for what was actually reported). Valuation ratios and analyst estimates are NOT in
filings — those come from the yfinance side (see fundamentals_fetcher.py).

SEC fair-access policy requires a descriptive User-Agent with contact info and
caps requests at ~10/sec. Set SEC_EDGAR_USER_AGENT in the environment.
"""
import os
import json
import time
from pathlib import Path
from datetime import datetime, date, timedelta

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

CIK_CACHE_PATH = Path(__file__).parent / "sec_cik_map.json"
CIK_CACHE_MAX_AGE_DAYS = 30

# Polite pacing between SEC requests (SEC limit is 10 req/s; we go far slower).
SEC_REQUEST_DELAY_SEC = 0.25

# How many years of history to keep (bounds row count).
EDGAR_HISTORY_YEARS = int(os.getenv("EDGAR_HISTORY_YEARS", "7"))

# Concept maps: logical field -> ordered candidate us-gaap tags, most-preferred first.
# Different filers use different tags, so we try several. Resolution is *per period*
# (see _collect_concept / _build_periods): each period picks the most-preferred
# candidate that actually covers it, rather than locking onto one tag globally.
INCOME_CONCEPTS = {
    # Revenue tag varies a lot by sector. Standard industrial/tech tags come first
    # (order preserved so currently-working filers are unaffected); sector fallbacks
    # below catch filers that report under non-standard tags:
    #   - RevenueFromContractWithCustomerIncludingAssessedTax: general fallback, e.g.
    #     energy names (XOM/CVX) whose top line includes excise/assessed taxes.
    #   - RevenuesNetOfInterestExpense: banks' total net revenue (the bank top line).
    #     Per-period resolution matters here: banks tag `Revenues` only for the full
    #     year, so quarters fall through to this tag for their revenue.
    #   - InterestAndDividendIncomeOperating / InterestIncomeExpenseNet / NoninterestIncome:
    #     bank income components, used only when no total-revenue tag covers the period.
    #     net_margin (= net_income / total revenue) is the meaningful bank margin —
    #     there is no gross/operating margin (banks don't tag those concepts).
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "RevenuesNetOfInterestExpense",
        "InterestAndDividendIncomeOperating",
        "InterestIncomeExpenseNet",
        "NoninterestIncome",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "eps_basic": ["EarningsPerShareBasic"],
}
BALANCE_CONCEPTS = {
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "total_liabilities": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "stockholders_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "short_term_debt": ["LongTermDebtCurrent", "DebtCurrent"],
    "inventory": ["InventoryNet"],
}
CASHFLOW_CONCEPTS = {
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "depreciation_amortization": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "stock_buyback": ["PaymentsForRepurchaseOfCommonStock"],
}

# Instant (balance-sheet) concepts have no `start`; duration concepts do.
_DURATION_KINDS = {"income_statement": INCOME_CONCEPTS, "cash_flow": CASHFLOW_CONCEPTS}
_INSTANT_KINDS = {"balance_sheet": BALANCE_CONCEPTS}

_VALID_FP = {"Q1", "Q2", "Q3", "Q4", "FY"}


def get_user_agent() -> str:
    ua = os.getenv("SEC_EDGAR_USER_AGENT")
    if not ua:
        ua = "trade-agent-research admin@example.com"
        print("⚠️  SEC_EDGAR_USER_AGENT not set — using a generic default. "
              "Set it to 'YourName your@email' to comply with SEC fair-access policy.")
    return ua


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": get_user_agent(), "Accept-Encoding": "gzip, deflate"})
    return s


# ---------------------------------------------------------------------------
# Ticker -> CIK
# ---------------------------------------------------------------------------
def load_cik_map(session: requests.Session = None, force: bool = False) -> dict:
    """Return {TICKER: cik_10digit}. Cached locally for CIK_CACHE_MAX_AGE_DAYS."""
    if not force and CIK_CACHE_PATH.exists():
        age_days = (time.time() - CIK_CACHE_PATH.stat().st_mtime) / 86400
        if age_days < CIK_CACHE_MAX_AGE_DAYS:
            try:
                return json.loads(CIK_CACHE_PATH.read_text())
            except Exception:
                pass

    session = session or _session()
    raw = session.get(SEC_TICKER_MAP_URL, timeout=30).json()
    cik_map = {}
    for row in raw.values():
        cik_map[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
    try:
        CIK_CACHE_PATH.write_text(json.dumps(cik_map))
    except Exception:
        pass
    return cik_map


def ticker_to_cik(ticker: str, cik_map: dict) -> str | None:
    """Resolve a ticker to its zero-padded CIK, tolerating BRK-B / BRK.B style variants."""
    t = ticker.upper()
    for variant in (t, t.replace("-", "."), t.replace(".", "-"), t.replace("-", ""), t.replace(".", "")):
        if variant in cik_map:
            return cik_map[variant]
    return None


# ---------------------------------------------------------------------------
# companyfacts -> normalized statements
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _classify_duration(start: date, end: date) -> str | None:
    """Map a reporting span to 'Q' (a single quarter) or 'FY' (full year); skip YTD spans."""
    days = (end - start).days
    if 80 <= days <= 100:
        return "Q"
    if 350 <= days <= 380:
        return "FY"
    return None


def _collect_concept(us_gaap: dict, candidates: list) -> list:
    """Return normalized entries across *all* candidate tags, each tagged with its
    candidate priority (index in `candidates`, lower = more preferred).

    We can't pick a single tag globally and stop at the first one that exists: filers
    sometimes report different tags at different granularities. Banks, for example, tag
    `Revenues` only for the full year but report `RevenuesNetOfInterestExpense` per
    quarter — picking `Revenues` globally would leave every quarter without revenue.
    Instead we gather every candidate and let `_build_periods` resolve each period to
    the lowest-priority tag that actually covers it (ties broken by earliest filing)."""
    out = []
    for prio, tag in enumerate(candidates):
        node = us_gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        if not units:
            continue
        unit_key = next(iter(units))  # USD, or USD/shares for EPS
        for e in units[unit_key]:
            end = _parse_date(e.get("end", ""))
            if end is None:
                continue
            out.append({
                "start": _parse_date(e.get("start")) if e.get("start") else None,
                "end": end,
                "val": e.get("val"),
                "fy": e.get("fy"),
                "fp": e.get("fp"),
                "form": e.get("form", ""),
                "filed": _parse_date(e.get("filed", "")),
                "prio": prio,
            })
    return out


def _build_periods(us_gaap: dict, concept_map: dict, instant: bool) -> dict:
    """
    Group concept values into periods.

    Duration concepts (income / cash flow) are keyed by (period_end, fiscal_period).
    Instant concepts (balance sheet) are keyed by period_end alone — later filings
    repeat a prior balance date as a comparative, so keying by the date and keeping
    the original (earliest-filed) filing collapses those duplicates.

    For each concept we keep the value from the *original* filing (earliest `filed`),
    so backtests see as-originally-reported numbers, not later restatements.

    Note: cash-flow values in 10-Qs are year-to-date, so only Q1 and FY cash flows
    pass the single-period duration filter; mid-year quarterly cash flow is omitted
    in v1 (would require YTD subtraction at read time).
    """
    periods = {}
    for concept, candidates in concept_map.items():
        for e in _collect_concept(us_gaap, candidates):
            form = e["form"] or ""
            if not (form.startswith("10-K") or form.startswith("10-Q")):
                continue
            fp = e["fp"]
            if fp not in _VALID_FP or e["val"] is None or e["filed"] is None:
                continue

            if instant:
                key = e["end"]
            else:
                if e["start"] is None:
                    continue
                period_type = _classify_duration(e["start"], e["end"])
                if period_type is None:
                    continue
                if (period_type == "FY") != (fp == "FY"):
                    continue  # FY span must be an FY period and vice-versa
                key = (e["end"], fp)

            rec = periods.setdefault(key, {"values": {}, "filed": {}, "fp": {}, "prio": {}})
            # Per period/concept, prefer the lowest-priority candidate tag that covers
            # it; within the same tag keep the earliest filing (as-originally-reported,
            # so backtests see original numbers rather than later restatements).
            prev_prio = rec["prio"].get(concept)
            prev_filed = rec["filed"].get(concept)
            better = (
                prev_prio is None
                or e["prio"] < prev_prio
                or (e["prio"] == prev_prio and e["filed"] < prev_filed)
            )
            if better:
                rec["values"][concept] = e["val"]
                rec["filed"][concept] = e["filed"]
                rec["fp"][concept] = fp
                rec["prio"][concept] = e["prio"]
    return periods


def extract_statements(facts: dict) -> list:
    """Turn a companyfacts payload into normalized statement records (one per period/kind)."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return []

    cutoff = date.today() - timedelta(days=365 * EDGAR_HISTORY_YEARS)
    records = []

    for kind, cmap in {**_DURATION_KINDS, **_INSTANT_KINDS}.items():
        instant = kind in _INSTANT_KINDS
        periods = _build_periods(us_gaap, cmap, instant=instant)
        for key, rec in periods.items():
            if not rec["values"]:
                continue
            if instant:
                period_end = key
                # fiscal period from the original (earliest-filed) filing of this balance date
                earliest_concept = min(rec["filed"], key=rec["filed"].get)
                fiscal_period = rec["fp"][earliest_concept]
            else:
                period_end, fiscal_period = key
            if period_end < cutoff:
                continue
            # statement is "available" once every line item has been filed
            filed_date = max(rec["filed"].values())
            records.append({
                "kind": kind,
                "period_end": period_end,
                "fiscal_period": fiscal_period,
                "filed_date": filed_date,
                "data": rec["values"],
            })
    return records


def fetch_fundamentals_edgar(ticker: str, cik_map: dict = None, session: requests.Session = None) -> list:
    """
    Fetch and normalize EDGAR statements for one ticker.

    Returns a list of records:
        {kind, period_end (date), fiscal_period, filed_date (date), data (dict)}
    Empty list if the ticker has no CIK or no usable facts.
    """
    session = session or _session()
    cik_map = cik_map if cik_map is not None else load_cik_map(session)

    cik = ticker_to_cik(ticker, cik_map)
    if cik is None:
        print(f"  ⚠ {ticker}: no CIK in SEC map (likely non-US or ETF) — skipping EDGAR")
        return []

    url = SEC_COMPANYFACTS_URL.format(cik=cik)
    try:
        resp = session.get(url, timeout=30)
        time.sleep(SEC_REQUEST_DELAY_SEC)
        if resp.status_code == 404:
            print(f"  ⚠ {ticker} (CIK {cik}): no companyfacts (404)")
            return []
        resp.raise_for_status()
        facts = resp.json()
    except Exception as e:
        print(f"  ✗ {ticker}: EDGAR fetch failed: {e}")
        return []

    return extract_statements(facts)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Probe SEC EDGAR fundamentals for a ticker")
    ap.add_argument("ticker", help="e.g. AAPL")
    args = ap.parse_args()

    recs = fetch_fundamentals_edgar(args.ticker)
    print(f"\n{args.ticker}: {len(recs)} statement records")
    for r in sorted(recs, key=lambda x: (x["period_end"], x["kind"]), reverse=True)[:12]:
        print(f"  {r['period_end']} {r['fiscal_period']:3s} {r['kind']:16s} filed {r['filed_date']} "
              f"| {len(r['data'])} fields e.g. {list(r['data'].items())[:2]}")
