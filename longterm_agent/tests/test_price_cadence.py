"""
The daily price bars must stay on a DAILY job, independent of the fundamentals flag.

Prices and statements go stale on different clocks. While the bars rode along with the
weekly fundamentals fetch (until 2026-07-28) two things followed, both measured:

  * a cohort opened on Sunday had no SPY entry bar until the FOLLOWING Saturday, so /open
    could compute vs-SPY for only part of the book (6 of 12 positions on 2026-07-28);
  * a bad fetch stayed in the table for days, because nothing re-read that window — the
    2026-07-24 NaN incident.

Three properties are pinned here: the daily job exists, it is NOT gated behind
FETCH_FUNDAMENTALS, and its refetch window stays wider than its cadence (the overlap is
what heals a dropped bar — see daily_price_fetcher.save_daily, which now drops non-finite
bars rather than storing them).

The `fetcher_pkg` fixture (conftest) is what makes these run at all: the package's flat
`config` import collides with the repo-root shim, and the first version of this file
skipped silently in the full suite.
"""
import inspect


def test_daily_bars_have_their_own_daily_job(fetcher_pkg):
    fetcher, _ = fetcher_pkg
    assert hasattr(fetcher, "scheduled_daily_bars_fetch")
    src = inspect.getsource(fetcher.run_scheduler)
    assert "schedule.every().day.at(DAILY_BARS_FETCH_TIME)" in src, \
        "the bars must be on a daily schedule, not a weekly one"


def test_daily_bars_are_not_gated_by_the_fundamentals_flag(fetcher_pkg):
    """The regression that matters: grading and /open must not go dark because someone
    turned off fundamentals."""
    fetcher, cfg = fetcher_pkg
    src = inspect.getsource(fetcher.run_scheduler)
    bars_block = src.split("if DAILY_BARS_FETCH_ENABLED:")[1].split("if FETCH_FUNDAMENTALS:")[0]
    assert "FETCH_FUNDAMENTALS" not in bars_block
    assert cfg.DAILY_BARS_FETCH_ENABLED is True, "on by default"


def test_weekly_fundamentals_job_no_longer_fetches_bars(fetcher_pkg):
    fetcher, _ = fetcher_pkg
    src = inspect.getsource(fetcher.scheduled_fundamentals_fetch)
    assert "fetch_all_daily" not in src, "bars moved out; two jobs must not both write them"


def test_bar_fetch_uses_the_configured_heal_window(fetcher_pkg, monkeypatch):
    """The window must exceed the cadence — that overlap is the self-heal."""
    fetcher, cfg = fetcher_pkg
    import daily_price_fetcher

    seen = {}
    monkeypatch.setattr(daily_price_fetcher, "fetch_all_daily",
                        lambda **kw: seen.update(kw) or {"rows": 0, "failed": []})
    fetcher.scheduled_daily_bars_fetch(verbose=False)

    assert seen.get("period") == cfg.DAILY_BARS_PERIOD
    assert cfg.DAILY_BARS_PERIOD.endswith(("mo", "y")), \
        f"a {cfg.DAILY_BARS_PERIOD} window on a daily cadence leaves no room to heal"


def test_bars_land_after_yahoo_republishes_and_again_before_the_grader(fetcher_pkg,
                                                                      monkeypatch):
    """Two passes, checked on the real scheduler rather than on run_scheduler's source.

    At 20:30 ET Yahoo served the day's bar with NaN OHLC (just after the 20:00 extended-hours
    close), so bar D reached the table at D+1 20:30 and the 07:00 grader ran a session
    behind (2026-09-02 → 10-23). The evening pass must sit past that window, and a second
    pass must run before the grader.
    """
    import types
    from datetime import time
    import schedule
    from longterm_agent.config import LongTermAgentConfig

    fetcher, cfg = fetcher_pkg
    jobs = schedule.Scheduler()

    def stop():
        raise KeyboardInterrupt   # run_scheduler's own exit path, so its loop returns
    monkeypatch.setattr(fetcher, "schedule", types.SimpleNamespace(
        every=jobs.every, next_run=lambda: jobs.next_run, run_pending=stop))
    for name in ("_init_graph_tables", "run_startup_db_maintenance", "_test_embedding_api"):
        monkeypatch.setattr(fetcher, name, lambda *a, **k: None)

    fetcher.run_scheduler(verbose=False)

    bar_times = sorted(j.at_time for j in jobs.jobs
                       if "scheduled_daily_bars_fetch" in j.job_func.func.__code__.co_names)
    grade = time(*(int(x) for x in LongTermAgentConfig.GRADE_TIME.split(":")))
    retry, evening = bar_times
    assert evening >= time(21, 30), "20:00-21:30 ET is when Yahoo serves the bar as NaN"
    assert retry < grade, "the second pass must land the bar before the grader reads it"


def test_bar_fetch_time_is_after_the_close_and_does_not_collide(fetcher_pkg):
    _, cfg = fetcher_pkg
    hh, _mm = (int(x) for x in cfg.DAILY_BARS_FETCH_TIME.split(":"))
    assert 17 <= hh <= 23, "bars must be pulled after the 16:00 ET close settles"
    others = {cfg.OPTIONS_FETCH_TIME, cfg.MACRO_FETCH_TIME, cfg.FILINGS_FETCH_TIME}
    assert cfg.DAILY_BARS_FETCH_TIME not in others, "daily jobs must not share a slot"
