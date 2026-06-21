"""
Leakage canary (Backtest Plan B0 / §5).

For a fixed `as_of`, assert that no reader returns data dated AFTER `as_of` — the core
no-lookahead guarantee that makes any backtest trustworthy. Integration tests: they use the
`db` fixture and skip cleanly when the DB is unreachable.

Readers covered: news corpus, EDGAR + yfinance fundamentals, daily technicals. The
shift_library analog window-completed / version filters are covered by test_shift_library.py.
"""
from datetime import datetime, date, timedelta

import psycopg2
import pytest

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.tools.news_corpus_tool import get_company_news_corpus
from longterm_agent.tools.fundamentals_tool import read_fundamentals
from longterm_agent.tools.technical_tool import compute_daily_technicals

TICKER = "NVDA"
AS_OF = datetime(2026, 5, 1)


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


def test_news_corpus_no_future_articles(db):
    corpus = get_company_news_corpus(TICKER, AS_OF, 60, db, neo4j_manager=None)
    arts = corpus.get("articles", [])
    if not arts:
        pytest.skip("no articles in window")
    future = [a for a in arts if a.get("published") and _as_date(a["published"]) > AS_OF.date()]
    assert not future, f"{len(future)} articles published after as_of leaked into the corpus"


def test_fundamentals_edgar_filed_le_as_of(db):
    f = read_fundamentals(db, TICKER, AS_OF)
    lq = f.get("latest_quarter")
    if not lq:
        pytest.skip("no EDGAR statements <= as_of")
    filed = _as_date(lq["filed"])
    assert filed <= AS_OF.date(), f"EDGAR filed_date {filed} is AFTER as_of {AS_OF.date()}"


def test_fundamentals_yfinance_snapshot_not_leaked(db):
    """At an as_of strictly BEFORE the earliest yfinance snapshot, valuation must be empty —
    i.e. a future-dated snapshot must not leak backward (the real PIT asymmetry, BACKTEST §3.3)."""
    rows = _q("SELECT MIN(fetch_date) FROM fundamentals WHERE source='yfinance' AND ticker=%s",
              [TICKER])
    min_fetch = rows[0][0] if rows else None
    if not min_fetch:
        pytest.skip("no yfinance snapshot for ticker")
    before = datetime.combine(min_fetch - timedelta(days=1), datetime.min.time())
    f = read_fundamentals(db, TICKER, before)
    assert not f.get("valuation"), (
        f"yfinance valuation present at as_of {before.date()} which is before the first "
        f"snapshot {min_fetch} — future fundamentals leaked")


def test_technicals_last_bar_le_as_of(db):
    """The latest close the technicals use must be the DB close on/before as_of (no future bar)."""
    tech = compute_daily_technicals(db, TICKER, AS_OF, lookback_days=180)
    if not tech.get("available"):
        pytest.skip("not enough bars")
    rows = _q("""SELECT date, close FROM stock_prices_daily
                 WHERE ticker=%s AND date <= %s AND close IS NOT NULL
                 ORDER BY date DESC LIMIT 1""", [TICKER, AS_OF.date()])
    assert rows, "no price <= as_of"
    db_date, db_close = rows[0]
    assert _as_date(db_date) <= AS_OF.date()
    assert abs(tech["last_close"] - float(db_close)) < 0.01, (
        f"technicals last_close {tech['last_close']} != DB close <= as_of {db_close} "
        f"(a future bar may have leaked)")
