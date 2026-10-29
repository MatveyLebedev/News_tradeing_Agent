"""
Labeler `map_buckets`: did the map model's weekly sentiment match how the stock traded that
week?

background_profiling summarizes the corpus week by week (`bucket_summaries`, each with its
`_week` window "YYYY-MM-DD..YYYY-MM-DD" and a sentiment in −1..1); the reduce step and shift
detection are built on those numbers. This labels each week with the stock's excess over SPY
across the same window (close on or before the start → close on or before the end), and
whether a clearly signed sentiment (|s| ≥ 0.2) had the same sign as that excess.

The window ends at or before as_of, so these labels are knowable when the run is: they grade
the map step, they do not look into the run's future. One span per week; the same calendar
week reappears in every run whose lookback covers it, so dedupe by (ticker, week) before
training on them.
"""
from bisect import bisect_right
from datetime import date, datetime, time
from typing import Dict, Iterable, List, Optional, Tuple

from core import tracing
from longterm_agent.grade_results import BENCHMARK, _decision_moment

from .base import Label, ScoreSpec
from .store import existing_keys

NAME = "map_buckets"
VERSION = "1"
RUN_KIND = "live"
NODE = "background_profiling"
SIGNED_SENTIMENT = 0.2

SCORES: Dict[str, ScoreSpec] = {
    f"{NODE}.bucket_excess": ScoreSpec(
        "NUMERIC", "stock − SPY across one weekly bucket's window (close on or before its "
                   "start → close on or before its end)"),
    f"{NODE}.bucket_agree": ScoreSpec(
        "BOOLEAN", f"1 when a week's map sentiment (|s| ≥ {SIGNED_SENTIMENT}) had the sign of "
                   "that week's stock − SPY excess"),
}

_SQL = """
    SELECT s.detection_version, s.ticker, s.as_of, r.verdict, r.run_timestamp,
           (SELECT jsonb_agg(jsonb_build_object('w', b->>'_week', 's', b->'sentiment',
                                                'n', b->'_count'))
              FROM jsonb_array_elements(s.state->'bucket_summaries') b)
    FROM longterm_run_snapshots s
    LEFT JOIN longterm_agent_results r
      ON r.agent_version = s.detection_version AND r.ticker = s.ticker AND r.as_of = s.as_of
    WHERE jsonb_typeof(s.state->'bucket_summaries') = 'array'
    ORDER BY s.as_of, s.ticker
"""


def close_on_or_before(series: List[Tuple[date, float]], day: date) -> Optional[Tuple[date, float]]:
    i = bisect_right(series, (day, float("inf")))
    return series[i - 1] if i else None


def window_excess(stock, bench, start: date, end: date) -> Optional[Tuple[float, date]]:
    """(stock − SPY over the window, last close date used), or None without two closes."""
    legs = []
    for series in (stock, bench):
        a, b = close_on_or_before(series, start), close_on_or_before(series, end)
        if a is None or b is None or b[0] <= a[0]:
            return None
        legs.append((b[1] / a[1] - 1, b[0]))
    return round(legs[0][0] - legs[1][0], 4), max(legs[0][1], legs[1][1])


def parse_week(w: str) -> Optional[Tuple[date, date]]:
    try:
        a, b = (w or "").split("..")
        return date.fromisoformat(a), date.fromisoformat(b)
    except ValueError:
        return None


def collect(cur, ctx) -> Iterable[Label]:
    done = {k for k, _, _, _ in existing_keys(cur, NAME, VERSION)}
    cur.execute(_SQL)
    rows = cur.fetchall()
    bench = ctx.closes(BENCHMARK)
    for dv, ticker, as_of, verdict, run_ts, buckets in rows:
        key = tracing.run_trace_key(RUN_KIND, dv, ticker, as_of)
        if key in done or not buckets:
            continue  # a run's weeks are labeled together, once
        stock = ctx.closes(ticker)
        windows = [(b, parse_week(b.get("w"))) for b in buckets]
        last_end = max((w[1] for _, w in windows if w), default=None)
        # Both series must REACH the last week's end (grade_shifts' guard), or the newest
        # week would be priced on a frozen close and, being written once, never fixed.
        if last_end is None or not stock or not bench \
                or min(stock[-1][0], bench[-1][0]) < last_end:
            continue
        trace_time = _decision_moment(run_ts) or datetime.combine(as_of, time())
        for b, window in windows:
            if window is None:
                continue
            got = window_excess(stock, bench, *window)
            if got is None:
                continue
            excess, last = got
            raw = {"week": b["w"], "sentiment": b.get("s"), "n_articles": b.get("n"),
                   "verdict": verdict}
            common = dict(trace_key=key, ticker=ticker.upper(), as_of=as_of, node=NODE,
                          span=f"week:{b['w']}", labeler=NAME, labeler_version=VERSION,
                          trace_time=trace_time, data_cutoff=last,
                          horizon_days=(window[1] - window[0]).days, value_json=raw)
            yield Label(label_key="bucket_excess", value_num=excess, **common)
            s = b.get("s")
            if isinstance(s, (int, float)) and abs(s) >= SIGNED_SENTIMENT and excess != 0:
                yield Label(label_key="bucket_agree",
                            value_num=1.0 if (s > 0) == (excess > 0) else 0.0, **common)
