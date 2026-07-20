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
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

Path = List[Tuple[Any, float]]  # [(date, close)] ascending

# Stamped into graded rows; bump only with a pre-registered convention change.
FILL_CONVENTION = "open_t1"
# Pre-open cohorts (as_of before 09:30 with the PIT feature cutoff active) may fill at
# the as_of day's OWN open — legal because that bar is not a decision input there.
FILL_CONVENTION_D0 = "open_d0"


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
              nth: int = 1, include_asof_day: bool = False) -> Optional[Dict[str, Any]]:
    """Hold-book return under the open-entry convention: entry-bar open → close ≤
    entry + horizon. Returns {entry_date, entry_px, ret} or None (no entry bar yet /
    no prices)."""
    eb = entry_bar(cur, ticker, as_of_date, tolerance_days, nth, include_asof_day)
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
                    nth: int = 1, include_asof_day: bool = False) -> Optional[Dict[str, Any]]:
    """The stopped book for one trade under the open-entry convention: RAW stock +
    benchmark returns entry→exit (same units/conventions as the hold columns; excess is
    computed downstream by the same trade_excess the hold book uses). None when prices
    are missing."""
    eb = entry_bar(cur, ticker, as_of_date, tolerance_days, nth, include_asof_day)
    if eb is None:
        return None
    path = fetch_price_path(cur, ticker, as_of_date, horizon_days, tolerance_days,
                            include_asof_day)
    sim = simulate_exit(path, eb[0], eb[1], direction, horizon_days, stop_loss, take_profit)
    if sim is None:
        return None
    bb = entry_bar(cur, benchmark, as_of_date, tolerance_days, nth, include_asof_day)
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
