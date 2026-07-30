"""Intraday fill convention `mid_1m` + the mixed minute/session-day holding grid.

The long-term agent has only ever filled on DAILY bars (`exit_rules.py`: `open_t1`, and
`open_d0` for pre-open cohorts). This module adds the convention a *midday in-session*
cohort needs, and nothing else:

    mid_1m — the decision is stamped at `as_of` (a wall-clock ET moment inside the
             regular session), the agent is allowed `agent_minutes` of working time, and
             the fill is the OPEN of the first minute bar that starts STRICTLY AFTER
             `as_of + agent_minutes`.

"Strictly after" is the whole PIT claim of a midday entry. A bar whose open prints at the
same instant the agent finishes is a race the backtest would always win, so it is excluded;
`at_or_after=True` reproduces the looser convention `profit_compute/holding_curve.py` uses,
and exists only so the difference is measurable rather than assumed.

The holding grid has two legs, because the agent's thesis is weeks long but the entry is a
minute bar:

    intraday(h)  — exit h minutes after the fill, on the entry day's minute bars.
    eod(k)       — exit at the CLOSE of the k-th trading day after the entry day
                   (k=0 is the entry day's own close), on `stock_prices_daily`.

Both legs map onto ONE x axis — **session minutes held** — so the curve is continuous:
holding to the close of day k is `(390 - entry_point) + 390*k` session minutes, and the
untradable overnight gap contributes zero, which is the honest accounting (it cannot be
exited through). The two legs meet exactly at `eod(0)`, and that seam is a data check the
harness prints rather than a number it hides.

Nothing here reads the future: every function takes the entry side only. The exit-side
readers live in `holding_curve_lt.py`, which is grading code — allowed to see realized
prices by `docs/PIT_RULES.md` §4.
"""
from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional, Sequence, Tuple

# `historical_data_*` bars are tz-naive US/Eastern wall clock, regular session only:
# 390 one-minute bars, 09:30 through 15:59. Kept in sync with
# `profit_compute/holding_curve.py`, which owns the grid loader this module's caller uses.
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
SESSION_BARS = 390
GRID_POINTS = SESSION_BARS + 1  # point k = open of bar k; point 390 = the 16:00 print

FILL_CONVENTION_MID = "mid_1m"

# Pre-registered default: noon ET. Mid-session, past the opening auction's volatility and
# well clear of the closing one. It is a CONFIG CHOICE, not a swept parameter — moving it
# after seeing a curve turns the time of day into an 18th horizon (see the prereg §3).
DEFAULT_DECISION_TIME = time(12, 0)
DEFAULT_AGENT_MINUTES = 1.0


# ── entry side ───────────────────────────────────────────────────────────────────────


def decision_moment(as_of_day, decision_time: time, agent_minutes: float) -> datetime:
    """When the agent's answer exists: `as_of` + its working time.

    `agent_minutes` is an ASSUMPTION for this experiment (the user's 1 minute), not a
    measurement. `agent_results.execution_time_seconds` puts the intraday agent's own
    thinking time near 60s, but the long-term pipeline is a 7-node LangGraph with several
    LLM calls — see the prereg's power/latency section before reading 1 minute as fact.
    """
    day = as_of_day.date() if isinstance(as_of_day, datetime) else as_of_day
    return datetime.combine(day, decision_time) + timedelta(minutes=agent_minutes)


def entry_grid_point(decision: datetime, at_or_after: bool = False) -> int:
    """Grid point of the first bar we may fill at. May be ≥ SESSION_BARS (untradable).

    Default (`at_or_after=False`): the bar must START strictly after the decision, so a
    12:01:00 decision fills at the 12:02 open. With `at_or_after=True` it fills at 12:01 —
    the same instant the decision lands. The gap between the two is one minute of drift
    and it is the only difference between the two conventions.
    """
    open_dt = datetime.combine(decision.date(), SESSION_OPEN)
    offset = (decision - open_dt).total_seconds() / 60.0
    if offset < 0:
        return 0  # a pre-open decision fills at the opening print
    whole = int(offset)
    if at_or_after:
        # ceil: an exact minute boundary is itself a legal fill
        return whole if offset == whole else whole + 1
    return whole + 1


def first_real_bar(is_real: Sequence[bool], want: int, tolerance: int) -> Optional[int]:
    """First genuine bar at/after `want`, searching FORWARD only, within `tolerance`.

    Forward-only is the rule that separates a backtest from a wish: a gap at the entry
    minute may never be resolved by filling at an earlier print.
    """
    if want >= SESSION_BARS:
        return None
    for k in range(want, min(want + tolerance + 1, SESSION_BARS)):
        if is_real[k]:
            return k
    return None


# ── the holding grid ─────────────────────────────────────────────────────────────────

# (leg, n) where leg ∈ {"intraday", "eod"}. Ordering on the x axis is by held minutes,
# which the harness computes per trade — the same spec is a different number of minutes
# for a 09:35 entry and a 15:50 one.
ExitSpec = Tuple[str, int]

DEFAULT_INTRADAY_MIN = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240]
# Trading days, not calendar days. The ledger's 2w/4w/12w horizons (14/28/84 calendar
# days) land at k ≈ 10/20/60 here, which is why those three are on the grid.
DEFAULT_EOD_DAYS = [0, 1, 2, 3, 4, 5, 10, 15, 20, 30, 40, 60]


def default_grid() -> List[ExitSpec]:
    return ([("intraday", h) for h in DEFAULT_INTRADAY_MIN]
            + [("eod", k) for k in DEFAULT_EOD_DAYS])


def held_session_minutes(entry_point: int, spec: ExitSpec) -> int:
    """Position of an exit on the shared x axis: session minutes the trade is open.

    Overnight counts as zero because it cannot be traded through — a hold from Monday
    12:02 to Tuesday's close is 238 + 390 minutes of market, and calling it 1608 (wall
    clock) would put the two legs on incomparable axes.
    """
    leg, n = spec
    if leg == "intraday":
        return int(n)
    if leg == "eod":
        return (SESSION_BARS - int(entry_point)) + SESSION_BARS * int(n)
    raise ValueError(f"unknown exit leg: {leg!r}")


def parse_grid(intraday: str, eod: str) -> List[ExitSpec]:
    """CLI helper: two comma-separated lists → a de-duplicated, ordered spec list."""
    mins = sorted({int(x) for x in intraday.split(",") if x.strip()})
    days = sorted({int(x) for x in eod.split(",") if x.strip()})
    if any(m <= 0 for m in mins):
        raise ValueError("intraday horizons must be positive minutes")
    if any(d < 0 for d in days):
        raise ValueError("eod horizons are trading days ≥ 0")
    return [("intraday", m) for m in mins] + [("eod", d) for d in days]


# ── exit side (grading; allowed to see realized prices — PIT_RULES §4) ───────────────


def trading_day_closes(cur, ticker: str, entry_day: date, max_k: int,
                       calendar_slack: int = 14) -> List[Tuple[date, float]]:
    """Closes of trading days 0..max_k after (and including) `entry_day`, ascending.

    Returned list may be SHORTER than max_k+1 — that is the sample falling away at long
    horizons, not an error, and the caller counts it. Slack converts trading days to a
    calendar span with room for weekends and holidays.
    """
    span = entry_day + timedelta(days=int(max_k * 7 / 5) + calendar_slack)
    cur.execute("""
        SELECT date, close FROM stock_prices_daily
        WHERE ticker=%s AND date >= %s AND date <= %s AND close IS NOT NULL
        ORDER BY date LIMIT %s
    """, [ticker.upper(), entry_day, span, max_k + 1])
    return [(d, float(c)) for d, c in cur.fetchall()]


def eod_price(closes: Sequence[Tuple[date, float]], k: int) -> Optional[Tuple[date, float]]:
    """(date, close) of the k-th trading day, or None when the series does not reach it."""
    return closes[k] if 0 <= k < len(closes) else None


def chained_return(levels: Sequence[float]) -> float:
    """Compound a list of period returns given as levels; empty → 0.0."""
    out = 1.0
    for r in levels:
        out *= (1.0 + r)
    return out - 1.0


# ── the session calendar (holiday roll) ──────────────────────────────────────────────

# A date with fewer bars than this is a data hole, not a short session. Measured
# 2026-07-29: every ticker shows 390 bars on every day EXCEPT 2026-03-27 (146-148) and
# 2026-06-23 (23) — identical across all names, so those are fetcher outages, and a cohort
# rolled onto one would fill on a skeleton tape. US half-days (~210 bars) do not occur in
# the current window; the threshold sits below them deliberately so a real half-day would
# still count as a session.
MIN_SESSION_BARS = 200


def next_session(day: date, sessions, max_days: int = 5) -> Optional[date]:
    """The rule for a cohort that lands on a closed market: **trade the next session**.

    Pure, so the rule is testable without a database. `sessions` is the set of dates that
    actually traded. Returns `day` itself when it is a session; otherwise walks forward at
    most `max_days` (a longer gap than that is not a holiday, it is missing data, and
    silently rolling a week would move the cohort into a different news week).
    """
    for i in range(max_days + 1):
        cand = day + timedelta(days=i)
        if cand in sessions:
            return cand
    return None


def load_session_dates(conn, start: date, end: date, probe: str = "AAPL",
                       min_bars: int = MIN_SESSION_BARS) -> set:
    """Dates with a usable minute session, read off one liquid probe ticker.

    One probe is enough because the coverage holes are global (verified across six names):
    the fetcher either got the day for everyone or for no one.
    """
    table = "historical_data_" + probe.lower().replace("-", "_").replace(".", "_")
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT timestamp::date, count(*) FROM {table} "  # noqa: S608 - fixed probe name
            "WHERE timestamp >= %s AND timestamp < %s GROUP BY 1 HAVING count(*) >= %s",
            (datetime.combine(start, time(0, 0)),
             datetime.combine(end + timedelta(days=1), time(0, 0)), min_bars),
        )
        return {r[0] for r in cur.fetchall()}


def is_session_day(minute_grid: Optional[Any]) -> bool:
    """A cohort date with no minute bars is a market holiday (or a data hole).

    2026-05-25 (Memorial Day) is exactly this case in the current window and it IS one of
    the Monday cohorts the existing `monday_t1` grid ran — a midday entry there is not a
    thin sample, it is an impossible trade.
    """
    return minute_grid is not None
