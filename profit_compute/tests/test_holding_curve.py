"""Guards for the two places where holding_curve can silently lie.

1. The entry point. `_session_point` is the whole PIT claim of this experiment — if it
   rounds down, the trade fills at a bar that started before the decision existed.
2. The grid/horizon arithmetic. A one-point shift moves every exit by a minute and no
   output ever looks wrong.
"""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from profit_compute.holding_curve import (
    GRID_POINTS,
    SESSION_BARS,
    SESSION_OPEN,
    _session_point,
    _table,
    resolve_latency,
    summarise,
)


def _dt(h, m, s=0):
    return datetime.combine(date(2026, 5, 14), time(h, m, s))


class TestSessionPoint:
    def test_exact_minute_is_the_next_bar_after_latency(self):
        # 10:00:00 news, 1-minute agent → decision at 10:01:00 → the 10:01 bar's open,
        # which is the first print at or after the decision.
        assert _session_point(_dt(10, 0), 1) == 31  # 09:30 -> 0, 10:01 -> 31

    def test_mid_minute_news_rounds_UP_never_down(self):
        # 10:00:30 + 1m = 10:01:30. The 10:01 bar opened at 10:01:00 — before the decision.
        # Filling there would be a look-back; the honest fill is the 10:02 open.
        assert _session_point(_dt(10, 0, 30), 1) == 32

    def test_zero_latency_still_never_fills_at_the_publication_bar_mid_minute(self):
        assert _session_point(_dt(10, 0, 1), 0) == 31

    def test_session_open_maps_to_point_zero(self):
        assert _session_point(_dt(9, 30), 0) == 0

    def test_latency_shifts_the_entry_one_for_one(self):
        base = _session_point(_dt(11, 0), 1)
        assert _session_point(_dt(11, 0), 6) - base == 5

    def test_last_tradable_point_is_before_the_close(self):
        # 15:59 is the final bar; its open is point 389, and point 390 is the 16:00 close.
        assert _session_point(_dt(15, 58), 1) == SESSION_BARS - 1
        assert GRID_POINTS - 1 == SESSION_BARS


class TestResolveLatency:
    """`flat` is a claim about the world; the other modes are measurements of it."""

    def _entry(self, ingest_min=10.0, exec_s=60.0):
        pub = _dt(10, 0)
        return {
            "published": pub,
            "fetch_time": None if ingest_min is None else pub + timedelta(minutes=ingest_min),
            "exec_seconds": exec_s,
        }

    def test_flat_ignores_every_measurement(self):
        assert resolve_latency(self._entry(), "flat", 1.0) == (1.0, "flat")

    def test_realistic_is_feed_delay_plus_thinking_time(self):
        latency, kind = resolve_latency(self._entry(ingest_min=10, exec_s=60), "realistic", 1.0)
        assert latency == pytest.approx(11.0)
        assert kind == "measured"

    def test_runtime_alone_is_not_the_live_latency(self):
        # The distinction the flat assumption hides: the agent being fast does not make the
        # story tradable, because the feed is what decides when the agent sees it.
        e = self._entry(ingest_min=10, exec_s=60)
        assert resolve_latency(e, "runtime", 1.0)[0] == pytest.approx(1.0)
        assert resolve_latency(e, "ingest", 1.0)[0] == pytest.approx(10.0)
        assert resolve_latency(e, "realistic", 1.0)[0] == pytest.approx(11.0)

    def test_missing_component_falls_back_and_admits_it(self):
        assert resolve_latency(self._entry(ingest_min=None), "realistic", 2.0) == (2.0, "fallback_flat")
        assert resolve_latency(self._entry(exec_s=None), "realistic", 2.0) == (2.0, "fallback_flat")
        assert resolve_latency(self._entry(exec_s=None), "runtime", 2.0) == (2.0, "fallback_flat")

    def test_negative_feed_delay_is_refused_not_used(self):
        # fetch_time before published is a clock artefact. Using it would move the entry
        # BACKWARDS from the news — a look-back dressed up as a fast feed.
        e = self._entry(ingest_min=-5, exec_s=60)
        assert resolve_latency(e, "ingest", 1.0) == (1.0, "fallback_flat")

    def test_measured_latency_moves_the_entry_point(self):
        pub = _dt(10, 0)
        assert _session_point(pub, 1.0) == 31
        assert _session_point(pub, 11.0) == 41  # ~10 min of feed delay is ~10 bars of drift

    def test_fractional_latency_rounds_up_to_the_next_bar(self):
        # 60.2s of runtime is not "one minute" — it lands at 10:01:12, so the fill is 10:02.
        assert _session_point(_dt(10, 0), 60.2 / 60.0) == 32


class TestGridArithmetic:
    """The exit-point contract: holding h minutes from point e is exactly point e + h."""

    def test_grid_covers_the_full_session_plus_the_close_print(self):
        open_dt = datetime.combine(date(2026, 5, 14), SESSION_OPEN)
        last_bar = open_dt + timedelta(minutes=SESSION_BARS - 1)
        assert last_bar.time() == time(15, 59)
        assert (open_dt + timedelta(minutes=GRID_POINTS - 1)).time() == time(16, 0)

    def test_room_at_entry_is_points_left_to_the_close(self):
        entry_pt = _session_point(_dt(15, 0), 1)  # 15:01 open
        room = GRID_POINTS - 1 - entry_pt
        assert room == 59  # 15:01 → 16:00

    def test_forward_fill_leaves_a_gap_tradable_but_not_real(self):
        px = np.full(GRID_POINTS, np.nan)
        real = np.zeros(GRID_POINTS, dtype=bool)
        px[10], real[10] = 100.0, True
        px[13], real[13] = 101.0, True
        filled = pd.Series(px).ffill().bfill().to_numpy()
        # an exit inside the gap gets the last print — that is what a real exit gets
        assert filled[11] == 100.0
        # but an ENTRY there must be refused, which is what `real` is for
        assert not real[11]


class TestTableName:
    @pytest.mark.parametrize("ticker,expected", [
        ("AAPL", "historical_data_aapl"),
        ("BRK-B", "historical_data_brk_b"),
        ("BRK.B", "historical_data_brk_b"),
    ])
    def test_ticker_maps_to_table(self, ticker, expected):
        assert _table(ticker) == expected


class TestSummarise:
    def _panel(self, rows):
        return pd.DataFrame(rows)

    def test_day_block_ci_is_wider_than_the_spread_of_a_single_day(self):
        # Six trades, two days, each day internally identical: the day-block bootstrap can
        # only ever resample {day A, day B}, so the CI must span both day means — a
        # per-trade CI would collapse to nearly zero width and call this significant.
        rows = []
        for day, ret in ((date(2026, 5, 14), 2.0), (date(2026, 5, 15), -1.0)):
            for i in range(3):
                rows.append({"horizon": 30, "day": day, "ret_pct": ret,
                             "censored": False, "raw_move_pct": ret, "trade_id": f"{day}{i}"})
        out = summarise(self._panel(rows), n_boot=500, n_perm=200, seed=1)
        row = out.iloc[0]
        assert row["mean_pct"] == pytest.approx(0.5)
        assert row["ci_lo"] == pytest.approx(-1.0)
        assert row["ci_hi"] == pytest.approx(2.0)

    def test_sign_flip_p_cannot_be_significant_on_two_days(self):
        rows = [{"horizon": 30, "day": date(2026, 5, 14 + d), "ret_pct": 1.0,
                 "censored": False, "raw_move_pct": 1.0, "trade_id": d} for d in range(2)]
        out = summarise(self._panel(rows), n_boot=200, n_perm=500, seed=1)
        # 2 days = 4 sign assignments; the best attainable p is ~0.5. A harness that
        # reports p<0.05 here is counting trades as if they were independent bets.
        assert out.iloc[0]["p_signflip"] > 0.2

    def test_censored_trades_are_counted(self):
        rows = [{"horizon": 240, "day": date(2026, 5, 14), "ret_pct": 1.0,
                 "censored": True, "raw_move_pct": 1.0, "trade_id": 0},
                {"horizon": 240, "day": date(2026, 5, 15), "ret_pct": 1.0,
                 "censored": False, "raw_move_pct": 1.0, "trade_id": 1}]
        out = summarise(self._panel(rows), n_boot=100, n_perm=100, seed=1)
        assert out.iloc[0]["n_censored"] == 1
