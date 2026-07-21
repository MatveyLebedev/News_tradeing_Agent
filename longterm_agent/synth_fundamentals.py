"""What the SYNTHESIS step is told about fundamentals (`LT_SYNTH_FUNDAMENTALS`).

The pipeline extracts far more than the deciding step reads. `fundamentals_analysis` is
handed a rich prompt — margin series, EPS trajectory, opex, the whole valuation dict, peer
multiples, FCF, balance sheet, street data, the whole technicals dict — and compresses all
of it into one verdict label plus prose. `synthesis`, which is where direction and
conviction are actually decided, then sees only that label, `revenue_yoy`, trailing/forward
P/E and `expectation_gap`.

Three measured consequences (79 traded holdout rows, 2026-07-21):
  * both P/E are None on **20%** of decisions — for a loss-making company the synthesis step
    is told `{'trailingPE': None, 'forwardPE': None}`, i.e. nothing, while `priceToBook` /
    `priceToSalesTrailing12Months` sit unused in the same dict (6 of those 16 rows);
  * `margin_series` reaches synthesis on **0%** of decisions;
  * nine fields the `FundamentalsTrend` call already generates and we already pay for —
    `revenue_growth_trend`, `margin_trend`, `fcf_trend`, `debt_trend` and the five
    sub-verdicts — are referenced exactly twice in the repo: in the prompt that asks for
    them and in the schema. Nothing ever reads them.

This is also the likeliest explanation for what deep analysis buys: `analysis_tools.py`
re-reads `margin_series` directly, so deep is a second, expensive path back to numbers the
verdict step already saw and compressed away. Traced on INTC 2026-04-06, where both legs
said LONG and only the deep leg cleared the gate (0.85 vs 0.70 raw) — its thesis cites the
15% → 38.2% gross-margin turn and the first positive operating margin, both verified real
and point-in-time, and neither visible to the other leg at synthesis.

`mode="thin"` is the incumbent and MUST stay byte-identical to the pre-2026-07-21 prompt:
the LLM cache is content-addressed, so any drift silently invalidates every stored replay.
"""
from typing import Any, Dict, List, Optional, Tuple

# Multiples worth showing when P/E is absent. Order is the order they render in.
FALLBACK_MULTIPLES = (
    ("priceToBook", "P/B"),
    ("priceToSalesTrailing12Months", "P/S"),
    ("enterpriseToEbitda", "EV/EBITDA"),
)
SUB_VERDICTS = ("growth_sub", "profitability_sub", "balance_sheet_sub",
                "capital_allocation_sub", "street_sub")


def margin_yoy(series: List[Dict[str, Any]]) -> Tuple[Optional[dict], Optional[dict]]:
    """(newest quarter, same fiscal quarter a year earlier) from a newest-first series.

    Matched on `fp`, not on position: the series is not guaranteed to be 4 quarters apart
    by index, and comparing Q3 against Q2 would invent a margin swing that never happened.
    """
    if not series:
        return None, None
    newest = series[0]
    fp = newest.get("fp")
    if not fp:
        return newest, None
    for row in series[1:]:
        if row.get("fp") == fp:
            return newest, row
    return newest, None


def _valuation_text(valuation: Dict[str, Any]) -> str:
    """P/E when it exists; otherwise the multiples that do — never an all-None dict."""
    pe = {k: valuation.get(k) for k in ("trailingPE", "forwardPE")}
    if any(v is not None for v in pe.values()):
        return str(pe)
    have = [(lbl, valuation.get(k)) for k, lbl in FALLBACK_MULTIPLES
            if valuation.get(k) is not None]
    if not have:
        return "unavailable (no earnings-, book- or sales-based multiple)"
    return ("no P/E (loss-making or not reported); "
            + ", ".join(f"{lbl}={v}" for lbl, v in have))


def fundamentals_block(mode: str, fund: Dict[str, Any], trend: Dict[str, Any],
                       tech: Dict[str, Any]) -> str:
    """The fundamentals + technicals lines of the synthesis prompt.

    `thin` reproduces the incumbent text exactly; `rich` adds only material that already
    exists upstream — nothing here triggers a new fetch or a new LLM call.
    """
    fund = fund or {}
    trend = trend or {}
    tech = tech or {}
    valuation = fund.get("valuation") or {}

    if mode != "rich":
        pe = {k: valuation.get(k) for k in ("trailingPE", "forwardPE")}
        return (f"  revenue YoY={fund.get('revenue_yoy')}, valuation={pe}, "
                f"expectation_gap={fund.get('expectation_gap')}\n"
                f"  long-term technicals: trend={tech.get('trend')}, "
                f"window return={tech.get('return_window_pct')}%\n")

    out = (f"  revenue YoY={fund.get('revenue_yoy')}, "
           f"valuation={_valuation_text(valuation)}, "
           f"expectation_gap={fund.get('expectation_gap')}\n")

    newest, year_ago = margin_yoy(fund.get("margin_series") or [])
    if newest:
        bits = [f"{k.replace('_margin', '')}={newest.get(k)}"
                for k in ("gross_margin", "operating_margin", "net_margin")
                if newest.get(k) is not None]
        line = f"  margins ({newest.get('period')}): " + ", ".join(bits)
        if year_ago:
            ybits = [f"{k.replace('_margin', '')}={year_ago.get(k)}"
                     for k in ("gross_margin", "operating_margin", "net_margin")
                     if year_ago.get(k) is not None]
            line += f" | same quarter a year earlier ({year_ago.get('period')}): " + \
                    ", ".join(ybits)
        out += line + "\n"

    subs = [f"{k.replace('_sub', '')}={trend.get(k)}" for k in SUB_VERDICTS
            if trend.get(k) and trend.get(k) != "unknown"]
    if subs:
        out += "  per-block sub-verdicts: " + ", ".join(subs) + "\n"
    phrases = [f"{k.replace('_trend', '')}: {trend.get(k)}" for k in
               ("revenue_growth_trend", "margin_trend", "fcf_trend", "debt_trend")
               if trend.get(k)]
    if phrases:
        out += "  trend read: " + "; ".join(phrases) + "\n"

    out += (f"  long-term technicals: trend={tech.get('trend')}, "
            f"window return={tech.get('return_window_pct')}%\n")
    return out
