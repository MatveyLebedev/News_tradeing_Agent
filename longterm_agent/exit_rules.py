"""Fill convention (open_t1) + exit discipline (Phase 5.5) on daily bars.

Fill convention — `open_t1` (2026-07-19, see WALK_FORWARD_PROTOCOL.md "Fill convention"):
  - entry = OPEN of the first trading bar STRICTLY AFTER the as_of date;
  - exit  = close on/before entry_date + horizon (the horizon anchors at the entry).
  The previous convention (entry = last close ≤ as_of) let a weekend decision "fill" at
  Friday's close — a price printed before up to ~38h of news the decision had read. The
  overnight/weekend gap is untradable by construction; under open_t1 it is excluded from
  the book in BOTH directions, and every decision input strictly precedes the fill.

Exit discipline (Phase 5.5): the pipeline generates invalidation conditions but nothing
enforced them: a trade was held to the fixed horizon no matter what, so two counter-trend
blowups (INTC −41%, QCOM −37%) erased more than the entire rest of the wf_30x v2 book
earned. The stop is a STRUCTURAL rule (round prior, robust across 6–12% on the in-sample
sweep — see VALIDATION_REPORT_v0.2.md §5c): it caps the per-trade left tail; it is not a
tuned edge claim. Validation happens FORWARD: graders fill BOTH books (hold-to-horizon
and stopped) on every matured trade.

Simulation conventions:
  - entry price = entry-bar OPEN (open_t1); the entry bar's own CLOSE already counts —
    an open-entry position can breach the stop on day 0;
  - exit at the CLOSE of the breach day — a gap through the level exits BEYOND it
    (INTC stopped at −15%, not −10%; no intraday data, and this is the honest side);
  - stop checked before take-profit on the same day (conservative);
  - take_profit=0 disables it (measured useless at the 4w horizon: 1 trade in 44
    ever reached +20%).
"""
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

Path = List[Tuple[Any, float]]  # [(date, close)] ascending

# Stamped into graded rows; bump only with a pre-registered convention change.
FILL_CONVENTION = "open_t1"
# Pre-open cohorts (as_of before 09:30 with the PIT feature cutoff active) may fill at
# the as_of day's OWN open — legal because that bar is not a decision input there.
FILL_CONVENTION_D0 = "open_d0"
# In-session decisions (e.g. the Monday-midday book) fill on MINUTE bars: neither daily
# convention can price them. `open_t1` would buy the NEXT session's open — ~21h late, the
# rest of the decision day discarded — and `open_d0` would buy the as_of day's 09:30 open,
# hours BEFORE the decision existed, which is look-ahead. See docs/BENCHMARK_CONVENTION.md
# and longterm_agent/intraday_entry.py, which implements the same rule research-side.
FILL_CONVENTION_MID = "mid_1m"

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
# Order routing between "the signal exists" and "the order rests". 0 by default on the LIVE
# path because the decision moment there is `run_timestamp`, stamped AFTER the pipeline
# finished — its runtime is already inside the anchor and must not be charged twice.
DEFAULT_LATENCY_MIN = 0
# How far past the wanted minute we may look for a real print. A thin name can simply not
# trade for a few minutes; beyond this the bar is missing, not late.
DEFAULT_MINUTE_TOLERANCE = 15


def minute_table(ticker: str) -> str:
    """Per-ticker minute table. Mirrors profit_compute.holding_curve._table."""
    return "historical_data_" + ticker.lower().replace("-", "_").replace(".", "_")


def in_session(moment: datetime) -> bool:
    """Is this wall-clock ET moment inside the regular session? A decision stamped at
    15:59 still has a fill; one at 16:00 does not — the 16:00 print is the close.

    The weekday test is not decoration. Until it was added this answered TRUE for a Sunday
    12:40, because it read only the clock — and a Sunday batch that overran into those hours
    (2026-07-26 ran 06:23→15:39) produced 59 rows that `resolve_entry` then treated as
    in-session decisions. They can never fill: the market was shut, so no minute bar exists,
    and the row sits PENDING for good.

    Holidays are NOT covered here — a weekday holiday still reads TRUE — because that needs
    the price table and this function is pure and called per row. `resolve_entry` handles it
    at the point where a cursor is in hand.
    """
    if moment.weekday() >= 5:          # 5,6 = Saturday, Sunday
        return False
    return SESSION_OPEN <= moment.time() < SESSION_CLOSE


def market_traded_on(cur, day) -> bool:
    """Did the market trade at all on `day`? Distinguishes a holiday from a thin ticker.

    Uses the daily bars rather than the minute tables because grading runs the morning
    after at the earliest, by which time the day's daily bar exists (or does not, which is
    the answer). SPY specifically: it is the benchmark, it never fails to print on a real
    session, and one row beats scanning 121 per-ticker minute tables.
    """
    try:
        cur.execute("SELECT 1 FROM stock_prices_daily WHERE ticker='SPY' AND date=%s LIMIT 1",
                    [day])
        return cur.fetchone() is not None
    except Exception:
        return True     # unknown ⇒ assume a session, i.e. keep the stricter PENDING path


def entry_bar_mid(cur, ticker: str, decision_et: datetime,
                  latency_minutes: int = DEFAULT_LATENCY_MIN,
                  tolerance_minutes: int = DEFAULT_MINUTE_TOLERANCE
                  ) -> Optional[Tuple[Any, float]]:
    """(entry_date, entry_px) under `mid_1m`: the OPEN of the first minute bar that starts
    STRICTLY AFTER `decision_et + latency_minutes`, on the decision's own day.

    Strictly after, not at-or-after: a bar stamped 12:02 covers 12:02:00–12:02:59, so a
    decision at 12:02:00 could otherwise fill at a price printed in the same minute it was
    made. None when the moment is outside the session, or no real print lands inside the
    tolerance (holiday, halt, or a hole the minute fetcher never filled — those are
    permanent after ~30 days, so they must surface rather than silently fall back).
    """
    if not in_session(decision_et):
        return None
    want = decision_et + timedelta(minutes=latency_minutes)
    hi = min(datetime.combine(decision_et.date(), SESSION_CLOSE),
             want + timedelta(minutes=tolerance_minutes))
    if want >= hi:
        return None
    try:
        cur.execute(  # noqa: S608 - table name derived from a fixed ticker list
            f"SELECT timestamp, open, close FROM {minute_table(ticker)} "
            "WHERE timestamp > %s AND timestamp <= %s ORDER BY timestamp LIMIT 1",
            [want, hi])
    except Exception:
        return None  # no minute table for this ticker (ETFs today) — caller decides
    row = cur.fetchone()
    if not row:
        return None
    ts, o, c = row
    px = float(o) if o else (float(c) if c else 0.0)
    return (ts.date(), px) if px > 0 else None


def resolve_entry(cur, ticker: str, as_of_date, decision_et: Optional[datetime] = None,
                  tolerance_days: int = 7, latency_minutes: int = DEFAULT_LATENCY_MIN
                  ) -> Optional[Tuple[str, Any, float]]:
    """(convention, entry_date, entry_px) — ONE place that picks the fill convention.

    An in-session decision fills on minute bars (`mid_1m`); anything else keeps the daily
    convention it already had. Deciding per row from the decision's own clock, rather than
    from a config flag, is what lets a Sunday 06:00 book and a Monday 12:00 book be graded
    correctly by the same grader — and keeps every row already in the table untouched.
    """
    if decision_et is not None and in_session(decision_et) \
            and decision_et.date() == as_of_date:
        eb = entry_bar_mid(cur, ticker, decision_et, latency_minutes)
        if eb is not None:
            return (FILL_CONVENTION_MID, eb[0], eb[1])
        # No minute print. Two different situations, and they must not share an answer:
        #   * the market never opened that day (a weekday holiday — Monday cohorts are
        #     exposed to several) — then there was no in-session decision to price, and the
        #     daily convention below is the right one, not a fallback;
        #   * the market DID trade and this ticker simply did not print — that is a data
        #     hole or a halt, and it stays PENDING rather than quietly changing convention.
        if market_traded_on(cur, as_of_date):
            return None
    eb = entry_bar(cur, ticker, as_of_date, tolerance_days)
    return (FILL_CONVENTION, eb[0], eb[1]) if eb else None


def entry_bar(cur, ticker: str, as_of_date, tolerance_days: int = 7,
              nth: int = 1, include_asof_day: bool = False) -> Optional[Tuple[Any, float]]:
    """(entry_date, entry_px) of the nth trading bar after `as_of_date`.

    include_asof_day=False (open_t1): bars strictly after the as_of date — for cohorts
    whose as_of-day session was a decision input (legacy Friday grids, weekend live).
    include_asof_day=True (open_d0): the as_of day's own bar counts — for PRE-OPEN
    cohorts (e.g. Monday 06:30) where the PIT cutoff keeps that session out of the
    decision, so its 09:30 open is the first fill opportunity.
    nth=1 is the canonical fill; nth=2 is the diagnostic next-bar book (day-1 value).
    None when fewer than nth bars exist within the tolerance window — either the entry
    session hasn't happened/landed in stock_prices_daily yet, or the ticker is halted.
    """
    date_cmp = ">=" if include_asof_day else ">"
    cur.execute(f"""
        SELECT date, open, close FROM stock_prices_daily
        WHERE ticker=%s AND date {date_cmp} %s AND date <= %s AND close IS NOT NULL
        ORDER BY date LIMIT %s
    """, [ticker.upper(), as_of_date,
          as_of_date + timedelta(days=tolerance_days + nth - 1), nth])
    rows = cur.fetchall()
    if len(rows) < nth:
        return None
    d, o, c = rows[nth - 1]
    return d, float(o) if o else float(c)  # open is fully populated; close = safety net


def close_on_or_before(cur, ticker: str, target, tolerance_days: int = 7) -> Optional[float]:
    """Latest daily close on/before target (within tolerance). Returns float or None."""
    cur.execute("""
        SELECT close FROM stock_prices_daily
        WHERE ticker=%s AND date <= %s AND date >= %s AND close IS NOT NULL
        ORDER BY date DESC LIMIT 1
    """, [ticker.upper(), target, target - timedelta(days=tolerance_days)])
    r = cur.fetchone()
    return float(r[0]) if r else None


def t1_return(cur, ticker: str, as_of_date, horizon_days: int, tolerance_days: int = 7,
              nth: int = 1, include_asof_day: bool = False,
              entry: Optional[Tuple[Any, float]] = None) -> Optional[Dict[str, Any]]:
    """Hold-book return under the open-entry convention: entry-bar open → close ≤
    entry + horizon. Returns {entry_date, entry_px, ret} or None (no entry bar yet /
    no prices).

    `entry` INJECTS an already-resolved (entry_date, entry_px) instead of re-deriving it
    from daily bars — the one hook a `mid_1m` fill needs, since its price lives in the
    minute tables. Everything after the entry is daily-close arithmetic anchored at
    entry_date and is identical under both conventions.
    """
    eb = entry if entry is not None else entry_bar(cur, ticker, as_of_date, tolerance_days,
                                                   nth, include_asof_day)
    if eb is None:
        return None
    e_date, e_px = eb
    exit_ = close_on_or_before(cur, ticker, e_date + timedelta(days=horizon_days),
                               tolerance_days)
    if not e_px or exit_ is None:
        return None
    return {"entry_date": e_date, "entry_px": e_px, "ret": (exit_ - e_px) / e_px}


def fetch_price_path(cur, ticker: str, as_of_date, horizon_days: int,
                     tolerance_days: int = 7, include_asof_day: bool = False) -> Path:
    """Daily closes spanning (as_of, as_of + tolerance + horizon] — everything the trade
    can touch: the entry bar lies within as_of + tolerance, its horizon ends at
    entry + horizon. include_asof_day widens the span to [as_of, …] for open_d0 cohorts
    whose entry bar IS the as_of day."""
    date_cmp = ">=" if include_asof_day else ">"
    cur.execute(f"""
        SELECT date, close FROM stock_prices_daily
        WHERE ticker=%s AND date {date_cmp} %s AND date <= %s AND close IS NOT NULL
        ORDER BY date
    """, [ticker.upper(), as_of_date,
          as_of_date + timedelta(days=tolerance_days + horizon_days)])
    return [(d, float(c)) for d, c in cur.fetchall()]


def simulate_exit(path: Path, entry_date, entry_px: float, direction: str,
                  horizon_days: int, stop_loss: float = 0.10,
                  take_profit: float = 0.0) -> Optional[Dict[str, Any]]:
    """Walk daily closes from the entry bar (day-0 close INCLUDED) → exit record.

    Returns {entry_date, entry_px, exit_date, exit_px, reason, days} with
    reason ∈ stop | take_profit | horizon; None when no bar exists in the window.
    """
    if not entry_px:
        return None
    end = entry_date + timedelta(days=horizon_days)
    sign = 1 if direction == "LONG" else -1

    def _rec(d, c, reason):
        return {"entry_date": entry_date, "entry_px": entry_px, "exit_date": d,
                "exit_px": c, "reason": reason, "days": (d - entry_date).days}

    last = None
    for d, c in path:
        if d < entry_date or d > end:
            continue
        last = (d, c)
        pos = sign * (c - entry_px) / entry_px
        if stop_loss and pos <= -stop_loss:
            return _rec(d, c, "stop")
        if take_profit and pos >= take_profit:
            return _rec(d, c, "take_profit")
    if last is None:
        return None
    return _rec(last[0], last[1], "horizon")


def stopped_returns(cur, ticker: str, as_of_date, direction: str, horizon_days: int,
                    benchmark: str = "SPY", stop_loss: float = 0.10,
                    take_profit: float = 0.0, tolerance_days: int = 7,
                    nth: int = 1, include_asof_day: bool = False,
                    entry: Optional[Tuple[Any, float]] = None,
                    bench_entry: Optional[Tuple[Any, float]] = None
                    ) -> Optional[Dict[str, Any]]:
    """The stopped book for one trade under the open-entry convention: RAW stock +
    benchmark returns entry→exit (same units/conventions as the hold columns; excess is
    computed downstream by the same trade_excess the hold book uses). None when prices
    are missing.

    `entry`/`bench_entry` inject resolved fills for a `mid_1m` trade — see `t1_return`.
    Both must be supplied together: pricing the stock intraday and the benchmark at a
    daily open would measure the two legs over different windows.
    """
    eb = entry if entry is not None else entry_bar(cur, ticker, as_of_date, tolerance_days,
                                                   nth, include_asof_day)
    if eb is None:
        return None
    # A mid-session entry's own day is part of the trade, so the path must include it.
    span_from_asof = include_asof_day or (entry is not None and eb[0] == as_of_date)
    path = fetch_price_path(cur, ticker, as_of_date, horizon_days, tolerance_days,
                            span_from_asof)
    sim = simulate_exit(path, eb[0], eb[1], direction, horizon_days, stop_loss, take_profit)
    if sim is None:
        return None
    bb = bench_entry if bench_entry is not None else entry_bar(
        cur, benchmark, as_of_date, tolerance_days, nth, include_asof_day)
    if bb is None:
        return None
    b_exit = close_on_or_before(cur, benchmark, sim["exit_date"], tolerance_days)
    if b_exit is None:
        return None
    fwd = (sim["exit_px"] - sim["entry_px"]) / sim["entry_px"]
    bench = (b_exit - bb[1]) / bb[1]
    return {"fwd": fwd, "bench": bench, "reason": sim["reason"], "days": sim["days"],
            "exit_date": sim["exit_date"], "entry_date": sim["entry_date"],
            "entry_px": sim["entry_px"]}
