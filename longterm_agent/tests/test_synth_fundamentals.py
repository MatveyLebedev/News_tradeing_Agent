"""What synthesis is told about fundamentals (pure; no DB, no LLM).

The incumbent text is pinned byte-for-byte: the LLM response cache is content-addressed,
so an accidental space would invalidate every stored replay without any visible failure.
"""
import pytest

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.synth_fundamentals import fundamentals_block, margin_yoy

TECH = {"trend": "up", "return_window_pct": 12.5}
SERIES = [
    {"period": "2025-09-27", "fp": "Q3", "gross_margin": 0.382, "operating_margin": 0.05},
    {"period": "2025-06-28", "fp": "Q2", "gross_margin": 0.275, "operating_margin": -0.247},
    {"period": "2024-09-28", "fp": "Q3", "gross_margin": 0.15, "operating_margin": -0.682},
]
FUND = {"revenue_yoy": 0.0278, "expectation_gap": None, "margin_series": SERIES,
        "valuation": {"trailingPE": None, "forwardPE": None,
                      "priceToBook": 1.962, "priceToSalesTrailing12Months": 4.242}}
TREND = {"verdict": "confirms", "growth_sub": "stable", "profitability_sub": "improving",
         "balance_sheet_sub": "unknown", "margin_trend": "gross margin recovering"}


def test_default_is_the_incumbent():
    assert LongTermAgentConfig.SYNTH_FUNDAMENTALS == "thin"
    assert LongTermAgentConfig.provenance()["knobs"]["synth_fundamentals"] == "thin"


def test_thin_is_byte_identical_to_the_pre_change_prompt():
    """Pinned literally. If this must change, the LLM cache is invalidated — decide that
    deliberately, do not discover it later as unexplained replay drift."""
    fund = {"revenue_yoy": 0.12, "expectation_gap": 0.03,
            "valuation": {"trailingPE": 28.1, "forwardPE": 24.0}, "margin_series": SERIES}
    expected = ("  revenue YoY=0.12, valuation={'trailingPE': 28.1, 'forwardPE': 24.0}, "
                "expectation_gap=0.03\n"
                "  long-term technicals: trend=up, window return=12.5%\n")
    assert fundamentals_block("thin", fund, TREND, TECH) == expected


def test_thin_ignores_everything_rich_would_add():
    out = fundamentals_block("thin", FUND, TREND, TECH)
    assert "margins" not in out and "sub-verdicts" not in out and "P/B" not in out


def test_unknown_mode_falls_back_to_the_incumbent():
    """A typo in the env var must not silently deploy the challenger."""
    assert fundamentals_block("typo", FUND, TREND, TECH) == \
        fundamentals_block("thin", FUND, TREND, TECH)


# --- rich ------------------------------------------------------------------------------
def test_rich_replaces_an_all_none_pe_with_the_multiples_that_exist():
    """The measured blind spot: 20% of traded rows were shown {'trailingPE': None, ...}."""
    out = fundamentals_block("rich", FUND, TREND, TECH)
    assert "P/B=1.962" in out and "P/S=4.242" in out
    assert "'trailingPE': None" not in out


def test_rich_keeps_pe_when_it_exists():
    fund = dict(FUND, valuation={"trailingPE": 28.1, "forwardPE": 24.0, "priceToBook": 9.0})
    out = fundamentals_block("rich", fund, TREND, TECH)
    assert "28.1" in out and "P/B" not in out


def test_rich_says_so_when_no_multiple_is_available():
    fund = dict(FUND, valuation={})
    assert "unavailable" in fundamentals_block("rich", fund, TREND, TECH)


def test_rich_compares_margins_against_the_same_fiscal_quarter():
    """Q3 vs Q3, never Q3 vs Q2 — an index-based compare would invent a margin swing."""
    out = fundamentals_block("rich", FUND, TREND, TECH)
    assert "gross=0.382" in out and "2024-09-28" in out and "gross=0.15" in out
    assert "2025-06-28" not in out


def test_rich_surfaces_sub_verdicts_but_drops_the_unknown_ones():
    out = fundamentals_block("rich", FUND, TREND, TECH)
    assert "growth=stable" in out and "profitability=improving" in out
    assert "balance_sheet" not in out


def test_rich_adds_no_new_data_source():
    """Everything rich prints must already be in the dicts thin was handed."""
    out = fundamentals_block("rich", {}, {}, {})
    assert "revenue YoY=None" in out and "long-term technicals" in out


def test_margin_yoy_matches_on_fiscal_period():
    newest, year_ago = margin_yoy(SERIES)
    assert newest["period"] == "2025-09-27" and year_ago["period"] == "2024-09-28"


def test_margin_yoy_returns_none_when_no_matching_quarter():
    newest, year_ago = margin_yoy(SERIES[:2])
    assert newest["period"] == "2025-09-27" and year_ago is None
    assert margin_yoy([]) == (None, None)
