"""
Pure-logic tests for corpus retrieval: the word-boundary regex (the CAT/"category" fix)
and weekly bucketing. No DB.
"""
import re
from datetime import datetime, timedelta

from longterm_agent.tools.news_corpus_tool import _name_regex, bucket_by_week


def _matches(ticker, text):
    """Simulate the Postgres ~* word-boundary match in Python (\\y → \\b)."""
    rx = _name_regex(ticker)
    if rx is None:
        return False
    return re.search(rx.replace(r"\y", r"\b"), text, re.IGNORECASE) is not None


# --- word-boundary regex precision ---
def test_ticker_word_matches_standalone():
    assert _matches("CAT", "CAT cut its full-year guidance")
    assert _matches("CAT", "shares of Caterpillar rose")


def test_ticker_does_not_match_substring():
    # the original bug: '%CAT%' matched these. Word boundaries must not.
    assert not _matches("CAT", "a new product category launched")
    assert not _matches("CAT", "early indicators indicate growth")
    assert not _matches("CAT", "the application was approved")


def test_short_tickers_excluded_from_bare_match():
    # <3-char tickers (KO, T) must not be matched as bare symbols (collide with stray letters)
    assert "KO" not in (_name_regex("KO") or "")          # bare 2-char ticker not a token
    assert _matches("KO", "Coca-Cola raised prices")       # but the name still matches
    # a bare single-letter ticker (T) must not match a stray standalone "T"
    assert not _matches("T", "Plan T was delayed")


def test_unknown_short_ticker_returns_none():
    assert _name_regex("ZZ") is None  # no name, len<3 → nothing safe to match


def test_long_ticker_included():
    rx = _name_regex("NVDA")
    assert rx is not None and "NVDA" in rx and "Nvidia" in rx


# --- weekly bucketing ---
def _article(id, days_before_as_of, sentiment=None):
    return {"id": id, "published": datetime(2026, 6, 1) - timedelta(days=days_before_as_of),
            "title": f"a{id}", "summary": "", "affects_sentiment": sentiment}


def test_bucketing_counts_and_assignment():
    as_of = datetime(2026, 6, 1)
    arts = [_article(1, 1), _article(2, 2), _article(3, 8), _article(4, 20)]
    buckets = bucket_by_week(arts, as_of, lookback_days=28, bucket_days=7)
    assert len(buckets) == 4
    assert sum(b["count"] for b in buckets) == 4
    # newest two articles (1-2 days back) land in the last bucket
    assert buckets[-1]["count"] == 2


def test_bucketing_carries_sentiment():
    as_of = datetime(2026, 6, 1)
    arts = [_article(1, 1, "negative"), _article(2, 2, "positive")]
    buckets = bucket_by_week(arts, as_of, lookback_days=14, bucket_days=7)
    last = buckets[-1]
    assert set(last["affects_sentiments"]) == {"negative", "positive"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
