"""
Leakage canary (Backtest Plan B0 / §5; tightened by the 2026-07-19 PIT audit).

For a fixed `as_of`, assert that no reader returns data the decision could not have known —
the core no-lookahead guarantee that makes any backtest trustworthy. Integration tests: they
use the `db` fixture and skip cleanly when the DB is unreachable.

Every case runs at BOTH a midnight cohort stamp and an 06:30 pre-open stamp, and asserts
against the point-in-time cutoffs rather than the bare as_of day. That distinction is the
whole point: the previous version asserted `filed <= as_of.date()`, which ENCODED the
day-granularity bug instead of catching it — under the `open_d0` regime (decide 06:30, fill
09:30) a filing accepted at 16:30 the same day is data from after the position was opened.

Readers covered: news corpus, EDGAR + yfinance fundamentals, daily technicals, and the
advisory filing/insider readers. Deep-analysis TOOLS are covered by
test_deep_tools_canary.py; shift_library analog windows by test_shift_library.py.
"""
from datetime import datetime, date, timedelta

import psycopg2
import pytest

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.tools.news_corpus_tool import get_company_news_corpus
from longterm_agent.tools.fundamentals_tool import read_fundamentals
from longterm_agent.tools.filings_tool import read_insider_activity, read_recent_filings
from longterm_agent.tools.pit import daily_bar_cutoff, document_cutoff
from longterm_agent.tools.technical_tool import compute_daily_technicals

TICKER = "NVDA"
# Midnight = the standard backtest grid stamp; 06:30 = the monday_t1 pre-open regime.
AS_OFS = [datetime(2026, 5, 1, 0, 0), datetime(2026, 5, 1, 6, 30)]
_ids = ["midnight", "preopen0630"]


def _q(sql, args):
    conn = psycopg2.connect(**LongTermAgentConfig.get_db_config())
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.close()
    return rows


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


@pytest.mark.parametrize("as_of", AS_OFS, ids=_ids)
def test_news_corpus_no_future_articles(db, as_of):
    corpus = get_company_news_corpus(TICKER, as_of, 60, db, neo4j_manager=None)
    arts = corpus.get("articles", [])
    if not arts:
        pytest.skip("no articles in window")
    # Publication time is compared at full precision, so this is exact, not day-level.
    future = [a for a in arts if a.get("published") and a["published"] > as_of]
    assert not future, f"{len(future)} articles published after as_of leaked into the corpus"


@pytest.mark.parametrize("as_of", AS_OFS, ids=_ids)
def test_fundamentals_edgar_respects_the_document_cutoff(db, as_of):
    """A statement filed ON the as_of day was not knowable to a pre-open decision."""
    f = read_fundamentals(db, TICKER, as_of)
    lq = f.get("latest_quarter")
    if not lq:
        pytest.skip("no EDGAR statements <= as_of")
    filed = _as_date(lq["filed"])
    cutoff = document_cutoff(as_of)
    assert filed <= cutoff, (
        f"EDGAR filed_date {filed} is after the document cutoff {cutoff} "
        f"(as_of {as_of}) — a same-day filing leaked")


@pytest.mark.parametrize("as_of", AS_OFS, ids=_ids)
def test_advisory_filing_readers_respect_the_document_cutoff(db, as_of):
    """8-K/Form-4 readers feed the deep-analysis tools — same cutoff, same rule."""
    cutoff = document_cutoff(as_of)
    checked = False
    filings = read_recent_filings(db, TICKER, as_of, days=120)
    for f in filings.get("filings", []) or []:
        checked = True
        assert _as_date(f["filed"]) <= cutoff, f"filing {f} is later than cutoff {cutoff}"
    insider = read_insider_activity(db, TICKER, as_of, days=120)
    for r in insider.get("recent", []) or []:
        checked = True
        assert _as_date(r["filed"]) <= cutoff, f"Form 4 {r} is later than cutoff {cutoff}"
    if not checked:
        pytest.skip("no filings/insider rows in window")


def test_fundamentals_yfinance_snapshot_not_leaked(db):
    """At an as_of strictly BEFORE the earliest yfinance snapshot, no *yfinance-only* valuation
    field may leak backward (the real PIT asymmetry, BACKTEST §3.3). PIT-reconstructed PE/P/B
    (EDGAR × price, source='edgar+price') MAY be present — that's the B2 fix and is asserted
    positively in test_fundamentals_pit.py."""
    rows = _q("SELECT MIN(fetch_date) FROM fundamentals WHERE source='yfinance' AND ticker=%s",
              [TICKER])
    min_fetch = rows[0][0] if rows else None
    if not min_fetch:
        pytest.skip("no yfinance snapshot for ticker")
    before = datetime.combine(min_fetch - timedelta(days=1), datetime.min.time())
    f = read_fundamentals(db, TICKER, before)
    yf_only = {"forwardPE", "enterpriseToEbitda", "profitMargins", "grossMargins",
               "operatingMargins", "revenueGrowth", "earningsGrowth", "returnOnEquity"}
    leaked = yf_only & set(f.get("valuation", {}))
    assert not leaked, (
        f"yfinance-only valuation fields {leaked} present at as_of {before.date()} which is "
        f"before the first snapshot {min_fetch} — future fundamentals leaked")
    if f.get("valuation"):  # anything present must be the PIT reconstruction, not a leaked snapshot
        assert f.get("valuation_pit", {}).get("source") == "edgar+price"


@pytest.mark.parametrize("as_of", AS_OFS, ids=_ids)
def test_technicals_last_bar_le_as_of(db, as_of):
    """The latest close the technicals use must be from the last session COMPLETED
    before as_of (PIT cutoff) — never the as_of-day bar, never a future bar."""
    tech = compute_daily_technicals(db, TICKER, as_of, lookback_days=180)
    if not tech.get("available"):
        pytest.skip("not enough bars")
    cutoff = daily_bar_cutoff(as_of)
    rows = _q("""SELECT date, close FROM stock_prices_daily
                 WHERE ticker=%s AND date <= %s AND close IS NOT NULL
                 ORDER BY date DESC LIMIT 1""", [TICKER, cutoff])
    assert rows, "no price <= cutoff"
    db_date, db_close = rows[0]
    assert _as_date(db_date) <= cutoff
    assert abs(tech["last_close"] - float(db_close)) < 0.01, (
        f"technicals last_close {tech['last_close']} != DB close <= {cutoff} "
        f"(a bar later than the completed-session cutoff may have leaked)")
