"""
Regression guards for the 2026-07-24 NaN-price incident.

What happened: yfinance returned daily bars whose OHLV were present but Close was NaN, for
131 tickers at once. Postgres `numeric` accepts NaN, so the poisoned value passed the
fetcher, passed every `close IS NOT NULL` filter, and detonated twice in the Sunday batch —
at `round(NaN)` in valuation_pit (49 of 64 tickers died) and at the JSONB write for the
survivors, which Postgres refuses ("invalid input syntax for type json"). The batch still
reported "1 trade" while the results table received nothing, so the live book silently
showed no new positions for a week.

Three independent layers are asserted here, because any one of them alone would have been
enough to prevent the loss:
  * the writer never stores a non-finite bar        (daily_price_fetcher.save_daily)
  * the reader never hands one to the analysis      (fundamentals_tool)
  * a failed write is loud, never a silent skip     (persistence.persist_run)

The fourth layer — a CHECK constraint on stock_prices_daily — is enforced by the database
itself (create_daily_price_table.sql) and is what makes the NaN untestable end-to-end here:
the row can no longer be inserted to reproduce it.
"""
import math
from datetime import date, datetime

import pytest

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.tools import fundamentals_tool as F


def _has_nan(obj) -> bool:
    """True if any float anywhere in a nested structure is non-finite."""
    if isinstance(obj, float):
        return not math.isfinite(obj)
    if isinstance(obj, dict):
        return any(_has_nan(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_nan(v) for v in obj)
    return False


# --- the helper ---------------------------------------------------------------------
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "abc", object()])
def test_finite_rejects_unusable_values(bad):
    assert F._finite(bad) is None


@pytest.mark.parametrize("good,want", [(1, 1.0), (2.5, 2.5), ("3.5", 3.5), (0, 0.0), (-4, -4.0)])
def test_finite_passes_real_numbers(good, want):
    assert F._finite(good) == want


# --- the reader ---------------------------------------------------------------------
def test_close_lookup_excludes_nan_in_sql(monkeypatch):
    """The NaN filter must live in the SQL, not in Python.

    The query is `ORDER BY date DESC LIMIT 1`: a post-hoc check on the single returned row
    can only discard it, leaving the caller with no price at all. Excluding NaN in the
    WHERE clause is what makes the lookup fall THROUGH the poisoned bar to the last good
    close — 07-23 instead of nothing.
    """
    captured = {}

    def fake_rows(db_manager, sql, params):
        captured["sql"] = sql
        bars = [(date(2026, 7, 24), float("nan")), (date(2026, 7, 23), 539.69)]
        if "'NaN'" in sql:                       # emulate Postgres applying the WHERE clause
            bars = [b for b in bars if not math.isnan(b[1])]
        return bars[:1]

    monkeypatch.setattr(F, "_rows", fake_rows)
    close, when = F._close_on_or_before(object(), "AMD", date(2026, 7, 26))

    assert "'NaN'" in captured["sql"], "the NaN exclusion must be part of the query"
    assert (close, when) == (539.69, date(2026, 7, 23))


def test_close_lookup_never_returns_nan(monkeypatch):
    """Even if a NaN reaches the result set, it must not leave the reader."""
    monkeypatch.setattr(F, "_rows", lambda *a: [(date(2026, 7, 24), float("nan"))])
    assert F._close_on_or_before(object(), "AMD", date(2026, 7, 26)) == (None, None)


# --- the analysis -------------------------------------------------------------------
def _stub_income():
    return [{"period_end": date(2025, 12, 31), "fiscal_period": "FY",
             "data": {"eps_diluted": 5.0, "net_income": 5e9}}]


def test_valuation_pit_survives_a_nan_close(monkeypatch):
    """`round(NaN)` raises ValueError — this is the exact line that killed 49 of 64
    tickers. A missing price must degrade to "no valuation", never to an exception."""
    monkeypatch.setattr(F, "_close_on_or_before", lambda *a, **k: (float("nan"), date(2026, 7, 24)))
    monkeypatch.setattr(F, "_edgar_income_all", lambda *a, **k: _stub_income())
    monkeypatch.setattr(F, "_latest_balance_equity", lambda *a, **k: 1e10)

    out = F.valuation_pit(object(), "JPM", datetime(2026, 7, 26))

    assert "marketCap" not in out, "a market cap cannot be formed without a price"
    assert out["price"] is None
    assert not _has_nan(out), "a NaN in this dict is what Postgres rejects at the JSONB write"


def test_valuation_pit_still_works_with_a_good_close(monkeypatch):
    """The guard must not turn healthy inputs into missing data."""
    monkeypatch.setattr(F, "_close_on_or_before", lambda *a, **k: (350.0, date(2026, 7, 24)))
    monkeypatch.setattr(F, "_edgar_income_all", lambda *a, **k: _stub_income())
    monkeypatch.setattr(F, "_latest_balance_equity", lambda *a, **k: 1e10)

    out = F.valuation_pit(object(), "JPM", datetime(2026, 7, 26))

    assert out["marketCap"] == round(350.0 * (5e9 / 5.0))
    assert out["trailingPE"] > 0
    assert not _has_nan(out)


def test_implied_shares_ignores_nan_fundamentals():
    nan_rows = [{"period_end": date(2025, 12, 31), "fiscal_period": "FY",
                 "data": {"eps_diluted": float("nan"), "net_income": 5e9}}]
    assert F._implied_shares(nan_rows) is None


# --- the write ----------------------------------------------------------------------
class _FakeDB:
    db_config = {"host": "unused"}


def test_persist_run_escalates_a_failed_write(monkeypatch):
    """A decision that fails to persist must reach the op log.

    This was a bare `print` to container stdout. Nothing downstream — /open, /week,
    grading — can tell an unpersisted trade from "the agent had no opinion", which is
    precisely why a whole cohort went missing without an alert.
    """
    from longterm_agent import oplog, persistence

    logged = []
    monkeypatch.setattr(persistence, "_ensure_results_table", lambda db: None)
    monkeypatch.setattr(oplog, "error",
                        lambda event, message, **kw: logged.append((event, message, kw)))

    def refuse(_db):
        raise Exception('invalid input syntax for type json')
    monkeypatch.setattr(persistence, "_conn", refuse)

    state = {"ticker": "WMT", "as_of": datetime(2026, 7, 26),
             "signal": {"direction": "LONG", "conviction": 0.55},
             "filtered_signal": {"direction": "LONG", "conviction": 0.55}}

    assert persistence.persist_run(_FakeDB(), state, LongTermAgentConfig) is False
    assert logged, "a persistence failure must be logged, not just printed"
    event, message, kw = logged[0]
    assert event == "PERSIST_FAILED"
    assert "WMT" in message
    assert kw.get("details", {}).get("traded") is True, "the log must say a TRADE was lost"


def test_batch_counts_unpersisted_decisions():
    """The batch summary must contradict itself rather than over-report.

    `trades` counts decisions; a decision that never reached the table is not in the book.
    The two numbers have to be reportable side by side.
    """
    from core.notify import TelegramNotifier

    sent = {}
    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier.send = lambda msg: sent.setdefault("msg", msg) or True

    notifier.notify_batch_summary(date(2026, 7, 26),
                                  {"runs": 64, "trades": 1, "signals": 1,
                                   "no_data": 5, "errors": 49, "not_persisted": 10})

    assert "NOT persisted" in sent["msg"]
    assert "10" in sent["msg"]


# --- the JSONB boundary -------------------------------------------------------------
def test_json_safe_scrubs_and_reports_paths():
    """A decision must survive a bad float in a field that only explains it."""
    from longterm_agent.persistence import json_safe

    clean, scrubbed = json_safe({
        "signal": {"direction": "SHORT", "conviction": 0.62},
        "shift_analogs": {"n_analogs": 6, "median_fwd_return_4w": float("nan")},
        "series": [1.0, float("inf"), 3.0],
    })

    assert clean["shift_analogs"]["median_fwd_return_4w"] is None
    assert clean["series"] == [1.0, None, 3.0]
    assert clean["signal"] == {"direction": "SHORT", "conviction": 0.62}, "good data untouched"
    assert set(scrubbed) == {"shift_analogs.median_fwd_return_4w", "series[1]"}


def test_persist_run_survives_a_nan_in_a_diagnostic_field(monkeypatch):
    """The exact NOW @ 2026-07-26 payload: a traded SHORT whose analog median was NaN.

    Before the scrub this raised `invalid input syntax for type json` and the trade was
    lost. Now the row is written and the bad field is reported instead.
    """
    from longterm_agent import oplog, persistence

    captured, warned = {}, []

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            if params and "INSERT INTO longterm_agent_results" in sql:
                captured["params"] = params
        def fetchone(self): return [4242]

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def close(self): pass

    monkeypatch.setattr(persistence, "_ensure_results_table", lambda db: None)
    monkeypatch.setattr(persistence, "_conn", lambda db: _Conn())
    monkeypatch.setattr(persistence, "write_shift_row", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(oplog, "warning",
                        lambda event, message, **kw: warned.append((event, message)))
    import longterm_agent.snapshots as snaps
    monkeypatch.setattr(snaps, "write_snapshot", lambda *a, **k: True)

    state = {"ticker": "NOW", "as_of": datetime(2026, 7, 26),
             "signal": {"direction": "SHORT", "conviction": 0.62},
             "filtered_signal": {"direction": "SHORT", "conviction": 0.62},
             "shift_analogs": {"n_analogs": 6, "median_fwd_return_4w": float("nan")}}

    assert persistence.persist_run(_FakeDB(), state, LongTermAgentConfig) is True
    assert not _has_nan(list(captured["params"])), "no NaN may reach the driver"
    assert warned and warned[0][0] == "NONFINITE_SCRUBBED"
    assert "median_fwd_return_4w" in warned[0][1], "the log must name the offending field"


# --- the analog pool and the grader ---------------------------------------------------
def test_analog_median_ignores_nan_outcomes(monkeypatch):
    """`IS NOT NULL` passes a Postgres NaN, and one NaN makes median() NaN."""
    from longterm_agent.tools import shift_library_tool as S

    rows = [("AAPL", date(2026, 6, 25), "up", 1.0, [], 0.05, 0.01),
            ("MSFT", date(2026, 6, 25), "up", 1.1, [], float("nan"), float("nan")),
            ("NVDA", date(2026, 6, 25), "up", 1.2, [], 0.09, 0.02)]

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): pass
        def fetchall(self): return rows

    class _Conn:
        def cursor(self): return _Cur()
        def close(self): pass

    monkeypatch.setattr(S, "_conn", lambda db: _Conn())
    monkeypatch.setattr(S, "ensure_table", lambda db: True)
    out = S.find_similar_shifts(_FakeDB(), "AMD", datetime(2026, 7, 26), "0.2.0",
                                "up", 1.0, None, k=6, min_analogs=2)

    assert out["n_analogs"] == 2, "the NaN row is not an analog"
    assert not _has_nan(out)
    assert out["median_fwd_return_4w"] == 0.07


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, {"ret": float("nan")}])
def test_grade_helper_rejects_non_finite(bad):
    from longterm_agent.grade_results import _finite_ret
    assert _finite_ret(bad) is None


def test_grade_helper_passes_real_returns():
    from longterm_agent.grade_results import _finite_ret
    assert _finite_ret({"ret": 0.0512}) == 0.0512
    assert _finite_ret(-0.03) == -0.03


# --- the fetcher --------------------------------------------------------------------
def test_save_daily_drops_the_bar_that_started_it_all(fetcher_pkg, monkeypatch):
    """A bar with real OHLV and Close=NaN — the literal 2026-07-24 payload — must not
    be written. The healthy bar next to it must still be.

    Uses `fetcher_pkg` so the package's own `config` wins: imported bare, this file
    resolved the repo-root shim instead and passed for the wrong reason.
    """
    pd = pytest.importorskip("pandas")
    import daily_price_fetcher as fetcher

    df = pd.DataFrame(
        {"Open": [321.73, 321.79], "High": [323.29, 334.36], "Low": [319.35, 321.61],
         "Close": [321.66, float("nan")], "Adj Close": [321.66, float("nan")],
         "Volume": [40840800, 47402209]},
        index=pd.to_datetime([date(2026, 7, 23), date(2026, 7, 24)]),
    )

    written = {}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        rowcount = 0
        def execute(self, *a, **k): pass

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def close(self): pass

    monkeypatch.setattr(fetcher, "get_db_connection", lambda *a, **k: _Conn())
    monkeypatch.setattr(fetcher, "execute_values",
                        lambda cur, sql, rows: written.setdefault("rows", rows))

    fetcher.save_daily("AAPL", df)

    assert len(written["rows"]) == 1, "the NaN bar must not be written"
    assert written["rows"][0][1] == date(2026, 7, 23)
    assert not _has_nan(list(written["rows"][0]))


def _capture_daily_save(fetcher, monkeypatch):
    """Stub the DB and the op log; return what save_daily wrote and what it warned."""
    seen = {"rows": [], "warnings": []}

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        rowcount = 0

    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): pass
        def close(self): pass

    monkeypatch.setattr(fetcher, "get_db_connection", lambda *a, **k: _Conn())
    monkeypatch.setattr(fetcher, "execute_values",
                        lambda cur, sql, rows: seen["rows"].extend(rows))
    monkeypatch.setattr(fetcher.logger, "warning",
                        lambda event, message, **kw: seen["warnings"].append((event, message, kw)))
    return seen


def test_save_daily_drops_the_same_day_bar_yahoo_has_not_republished(fetcher_pkg, monkeypatch,
                                                                     capsys):
    """The shape every 20:30 ET run got from 2026-09-02 on: the day's bar with NaN
    Open/High/Low/Close and a real Volume (yfinance #2895). It must still not be written —
    moving the fetch later is the fix, not loosening this — and the warning must say WHICH
    date and WHICH fields. "dropped 1 bar(s)" alone hid for seven weeks that it was always
    the same-day bar, which is why bar D only ever arrived at D+1 20:30.
    """
    pd = pytest.importorskip("pandas")
    import daily_price_fetcher as fetcher

    nan = float("nan")
    df = pd.DataFrame(
        {"Open": [721.37, nan], "High": [722.40, nan], "Low": [701.10, nan],
         "Close": [704.05, nan], "Adj Close": [704.05, nan], "Volume": [3091500, 3017200]},
        index=pd.to_datetime([date(2026, 10, 22), date(2026, 10, 23)]),
    )
    seen = _capture_daily_save(fetcher, monkeypatch)

    fetcher.save_daily("CAT", df)

    assert [r[1] for r in seen["rows"]] == [date(2026, 10, 22)], "only the published bar"
    assert not _has_nan(seen["rows"])
    event, _message, kw = seen["warnings"][0]
    assert event == "DAILY_BAR_DROPPED"
    assert kw["details"]["bars"] == [{"date": "2026-10-23",
                                      "fields": ["Open", "High", "Low", "Close"]}]
    assert "2026-10-23 [Open/High/Low/Close]" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
@pytest.mark.parametrize("field", ["Open", "High", "Low", "Close"])
def test_save_daily_never_writes_a_bar_missing_any_one_price(fetcher_pkg, monkeypatch,
                                                             field, bad):
    """One non-finite price costs the whole bar, whichever price it is."""
    pd = pytest.importorskip("pandas")
    import daily_price_fetcher as fetcher

    bar = {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Adj Close": 100.5,
           "Volume": 1000}
    bar[field] = bad
    df = pd.DataFrame([bar], index=pd.to_datetime([date(2026, 10, 23)]))
    seen = _capture_daily_save(fetcher, monkeypatch)

    assert fetcher.save_daily("AAPL", df) == 0
    assert seen["rows"] == []
    assert seen["warnings"][0][2]["details"]["bars"][0]["fields"] == [field]


def test_save_daily_keeps_a_bar_whose_only_gap_is_adj_close(fetcher_pkg, monkeypatch):
    """Adj Close is not a price the guard protects: a missing one falls back to Close and
    the bar is written. Only OHLC (and an unusable Volume) cost a bar."""
    pd = pytest.importorskip("pandas")
    import daily_price_fetcher as fetcher

    df = pd.DataFrame([{"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5,
                        "Adj Close": float("nan"), "Volume": 1000}],
                      index=pd.to_datetime([date(2026, 10, 23)]))
    seen = _capture_daily_save(fetcher, monkeypatch)

    fetcher.save_daily("AAPL", df)

    assert len(seen["rows"]) == 1 and seen["rows"][0][6] == 100.5
    assert seen["warnings"] == []
