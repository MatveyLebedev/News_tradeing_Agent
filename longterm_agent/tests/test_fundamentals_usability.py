"""
Tests for _has_usable_fundamentals — the guard that catches the JPM/bank case where
balance-sheet rows exist (has_statements=True) but revenue/margins are all None.
"""
from longterm_agent.agent.nodes import _has_usable_fundamentals


def test_no_statements_is_unusable():
    assert _has_usable_fundamentals({"has_statements": False}) is False
    assert _has_usable_fundamentals({}) is False


def test_revenue_present_is_usable():
    assert _has_usable_fundamentals({"has_statements": True, "revenue_yoy": 0.42}) is True


def test_margins_present_is_usable():
    fund = {"has_statements": True, "revenue_yoy": None,
            "margin_series": [{"gross_margin": None, "operating_margin": None, "net_margin": 0.21}]}
    assert _has_usable_fundamentals(fund) is True


def test_statements_but_all_none_is_unusable():
    # the bank case: has_statements=True but no usable income metrics
    fund = {"has_statements": True, "revenue_yoy": None,
            "margin_series": [{"gross_margin": None, "operating_margin": None, "net_margin": None}]}
    assert _has_usable_fundamentals(fund) is False


def test_empty_margin_series_unusable():
    assert _has_usable_fundamentals({"has_statements": True, "revenue_yoy": None, "margin_series": []}) is False


def test_fallback_profile_is_usable_when_reduce_fails():
    # the reduce-failure fallback must produce a real profile from bucket summaries
    from longterm_agent.agent.nodes import _fallback_profile
    summaries = [
        {"themes": ["AI demand", "regulation"], "attention": "high"},
        {"themes": ["AI demand", "supply chain"], "attention": "normal"},
    ]
    prof = _fallback_profile(summaries, sent_timeline=[0.3, -0.2])
    assert prof["sentiment_timeline"] == [0.3, -0.2]
    assert "AI demand" in prof["summary"]                      # most common theme surfaced
    assert prof["attention_timeline"] == ["high", "normal"]
    assert "unavailable" not in prof["summary"].lower() or "auto-summary" in prof["summary"].lower()


def test_retry_helper_succeeds_after_transient_failure():
    from longterm_agent.agent.nodes import _invoke_structured

    class _Flaky:
        def __init__(self): self.n = 0
        def with_structured_output(self, schema): return self
        def invoke(self, prompt):
            self.n += 1
            if self.n < 2:
                raise RuntimeError("transient parse error")
            return "ok"

    f = _Flaky()
    assert _invoke_structured(f, object, "p", retries=2) == "ok"
    assert f.n == 2  # failed once, succeeded on retry


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
