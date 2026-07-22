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


def test_retry_helper_treats_none_as_a_failure(monkeypatch):
    """A 200 with an empty completion yields None from with_structured_output; the caller
    would crash on .model_dump(). Measured: 3 of 26 failures in one voided run. None must
    retry like any other transient, and raise — not return — if it never recovers."""
    import core.llm as L
    monkeypatch.setattr(L.time, "sleep", lambda s: None)

    class _NoneThenOk:
        def __init__(self): self.n = 0
        def with_structured_output(self, schema): return self
        def invoke(self, prompt):
            self.n += 1
            return None if self.n < 3 else "ok"

    f = _NoneThenOk()
    assert L.invoke_structured(f, object, "p") == "ok"
    assert f.n == 3

    class _AlwaysNone:
        def with_structured_output(self, schema): return self
        def invoke(self, prompt): return None

    import pytest
    with pytest.raises(ValueError):
        L.invoke_structured(_AlwaysNone(), object, "p", retries=1)


def test_retry_backoff_is_exponential_and_budget_is_wider(monkeypatch):
    """2 retries × ~3s did not survive real provider flakiness (14% and 23% of calls
    failed through it in two measured runs); the budget is now 4 retries spanning ~15s."""
    import core.llm as L
    sleeps = []
    monkeypatch.setattr(L.time, "sleep", lambda s: sleeps.append(s))

    class _AlwaysFails:
        def with_structured_output(self, schema): return self
        def invoke(self, prompt): raise RuntimeError("boom")

    import pytest
    with pytest.raises(RuntimeError):
        L.invoke_structured(_AlwaysFails(), object, "p")
    assert sleeps == [1.0, 2.0, 4.0, 8.0]
