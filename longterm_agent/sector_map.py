"""Static peer-group map for the sector-relative baseline (Phase 13, detection v3).

Frozen snapshot of yfinance sector/industry for the tracked universe (generated
2026-07-12 from the fundamentals table). STATIC ON PURPOSE: the baseline is a detection
input, so it must be deterministic and available BEFORE any DB/LLM step; edits are
label-affecting (detection release discipline).

Granularity rule (validated on the AMD←Broadcom contagion case): a SECTOR median is too
coarse — the 2026-06-04 Broadcom wave hit *Semiconductors*, not the 26-name Technology
sector, whose median stayed flat and hid the wave. So the baseline group is the
INDUSTRY when it has ≥4 members (with Semiconductor Equipment merged into Semiconductors
— they trade with the semis complex), else the SECTOR.
"""

SECTOR_GROUPS = {
    "Basic Materials": ["APD", "ECL", "LIN", "NEM", "SHW"],
    "Communication Services": ["CHTR", "CMCSA", "DIS", "GOOG", "GOOGL", "META", "NFLX",
                               "T", "TMUS", "VZ"],
    "Consumer Cyclical": ["ABNB", "AMZN", "BKNG", "CMG", "HD", "LOW", "MCD", "NKE",
                          "SBUX", "TJX", "TSLA"],
    "Consumer Defensive": ["COST", "EL", "KO", "PEP", "PG", "TGT", "WMT"],
    "Energy": ["COP", "CVX", "EOG", "MPC", "PSX", "SLB", "XOM"],
    "Financial Services": ["AXP", "BAC", "BLK", "BRK-B", "C", "CB", "GS", "JPM", "MA",
                           "MS", "PYPL", "SPGI", "V", "WFC"],
    "Healthcare": ["ABBV", "ABT", "AMGN", "BMY", "CI", "CVS", "DHR", "GILD", "HUM",
                   "ISRG", "JNJ", "LLY", "MDT", "MRK", "PFE", "REGN", "TMO", "UNH",
                   "VRTX"],
    "Industrials": ["BA", "CAT", "DE", "GE", "HON", "LMT", "MMM", "NOC", "RTX", "UNP",
                    "UPS"],
    "Real Estate": ["AMT", "CCI", "EQIX", "PLD", "PSA"],
    "Technology": ["AAPL", "ACN", "ADBE", "ADP", "ADSK", "AMAT", "AMD", "AVGO", "CDNS",
                   "CRM", "CSCO", "FTNT", "IBM", "INTC", "INTU", "LRCX", "MSFT", "MU",
                   "NOW", "NVDA", "ORCL", "PANW", "QCOM", "SNPS", "TXN", "UBER"],
    "Utilities": ["AEP", "DUK", "NEE", "SO"],
}

SECTOR_OF = {tk: sec for sec, tks in SECTOR_GROUPS.items() for tk in tks}

# Industry-level refinements (≥4 members after the semis-equipment merge). Tickers not
# covered here fall back to their sector group.
_INDUSTRY_GROUPS = {
    "Semiconductors": ["AMD", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TXN",
                       "AMAT", "LRCX"],  # equipment merged: trades with the complex
    "Software - Application": ["ADBE", "ADP", "ADSK", "CDNS", "CRM", "INTU", "NOW", "UBER"],
    "Software - Infrastructure": ["FTNT", "MSFT", "ORCL", "PANW", "SNPS"],
    "Banks - Diversified": ["BAC", "C", "JPM", "WFC"],
    "Credit Services": ["AXP", "MA", "PYPL", "V"],
    "Drug Manufacturers - General": ["ABBV", "AMGN", "BMY", "GILD", "JNJ", "LLY",
                                     "MRK", "PFE"],
    "Healthcare Plans": ["CI", "CVS", "HUM", "UNH"],
    "Aerospace & Defense": ["BA", "GE", "LMT", "NOC", "RTX"],
    "Telecom Services": ["CHTR", "CMCSA", "T", "TMUS", "VZ"],
    "Specialty Chemicals": ["APD", "ECL", "LIN", "SHW"],
    "Utilities - Regulated Electric": ["AEP", "DUK", "NEE", "SO"],
}

# ticker → its baseline group name (industry refinement wins over sector)
BASELINE_GROUP_OF = {}
BASELINE_GROUPS = {}
for _sec, _tks in SECTOR_GROUPS.items():
    for _tk in _tks:
        BASELINE_GROUP_OF[_tk] = _sec
for _ind, _tks in _INDUSTRY_GROUPS.items():
    for _tk in _tks:
        BASELINE_GROUP_OF[_tk] = _ind
for _tk, _grp in BASELINE_GROUP_OF.items():
    BASELINE_GROUPS.setdefault(_grp, []).append(_tk)

# Groups left with <4 members after refinement (e.g. NEM alone in Basic Materials once
# the chemicals moved to their industry group) give no usable baseline — unmap them so
# v3 honestly degrades to v2 for those tickers instead of testing against 1-2 names.
for _grp in [g for g, tks in BASELINE_GROUPS.items() if len(tks) < 4]:
    for _tk in BASELINE_GROUPS.pop(_grp):
        del BASELINE_GROUP_OF[_tk]

# Share-class twins are the SAME company — never each other's "peer", or the company's
# own sentiment leaks into its baseline and cancels its own shift.
_TWINS = {"GOOGL": {"GOOG"}, "GOOG": {"GOOGL"}}


def peers_for(ticker: str):
    """Baseline-group peers of `ticker` (excluding itself and share-class twins)."""
    tk = ticker.upper()
    grp = BASELINE_GROUP_OF.get(tk)
    if not grp:
        return []
    drop = {tk} | _TWINS.get(tk, set())
    return [t for t in BASELINE_GROUPS[grp] if t not in drop]
