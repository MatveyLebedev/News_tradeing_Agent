"""Exit discipline (Phase 5.5): structural stop-loss / take-profit on daily closes.

The pipeline generates invalidation conditions but nothing enforced them: a trade was
held to the fixed horizon no matter what, so two counter-trend blowups (INTC −41%,
QCOM −37%) erased more than the entire rest of the wf_30x v2 book earned. The stop is a
STRUCTURAL rule (round prior, robust across 6–12% on the in-sample sweep — see
docs/GATE_AB_2026Q2.md era analysis in VALIDATION_REPORT_v0.2.md §5c): it caps the
per-trade left tail; it is not a tuned edge claim. Validation happens FORWARD: graders
fill BOTH books (hold-to-horizon and stopped) on every matured trade, so live data
accumulates the comparison automatically.

Simulation conventions (mirror grade_shifts._ret):
  - entry price  = last daily close ≤ entry_date;
  - exit at the CLOSE of the breach day — a gap through the level exits BEYOND it
    (INTC stopped at −15%, not −10%; no intraday data, and this is the honest side);
  - stop checked before take-profit on the same day (conservative);
  - take_profit=0 disables it (measured useless at the 4w horizon: 1 trade in 44
    ever reached +20%).
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

Path = List[Tuple[Any, float]]  # [(date, close)] ascending


def fetch_price_path(cur, ticker: str, entry_date, horizon_days: int,
                     tolerance_days: int = 7) -> Path:
    """Daily closes spanning [entry−tolerance, entry+horizon] (tolerance finds the entry
    close when entry_date is a weekend/holiday, same as grade_shifts)."""
    cur.execute("""
        SELECT date, close FROM stock_prices_daily
        WHERE ticker=%s AND date >= %s AND date <= %s AND close IS NOT NULL
        ORDER BY date
    """, [ticker.upper(), entry_date - timedelta(days=tolerance_days),
          entry_date + timedelta(days=horizon_days)])
    return [(d, float(c)) for d, c in cur.fetchall()]


def simulate_exit(path: Path, entry_date, direction: str, horizon_days: int,
                  stop_loss: float = 0.10, take_profit: float = 0.0) -> Optional[Dict[str, Any]]:
    """Walk the daily path → exit record, or None when no entry close exists.

    Returns {entry_date, entry_px, exit_date, exit_px, reason, days} with
    reason ∈ stop | take_profit | horizon.
    """
    eligible = [(d, c) for d, c in path if d <= entry_date]
    if not eligible:
        return None
    e_date, e_px = eligible[-1]
    end = entry_date + timedelta(days=horizon_days)
    sign = 1 if direction == "LONG" else -1

    def _rec(d, c, reason):
        return {"entry_date": e_date, "entry_px": e_px, "exit_date": d, "exit_px": c,
                "reason": reason, "days": (d - e_date).days}

    for d, c in path:
        if d <= e_date or d > end:
            continue
        pos = sign * (c - e_px) / e_px
        if stop_loss and pos <= -stop_loss:
            return _rec(d, c, "stop")
        if take_profit and pos >= take_profit:
            return _rec(d, c, "take_profit")
    d, c = [(d, c) for d, c in path if d <= end][-1]
    return _rec(d, c, "horizon")


def stopped_returns(cur, ticker: str, entry_date, direction: str, horizon_days: int,
                    benchmark: str = "SPY", stop_loss: float = 0.10,
                    take_profit: float = 0.0) -> Optional[Dict[str, Any]]:
    """The stopped book for one trade: RAW stock + benchmark returns entry→exit (same
    units/conventions as the hold-to-horizon columns; excess is computed downstream by
    the same trade_excess the hold book uses). None when prices are missing."""
    path = fetch_price_path(cur, ticker, entry_date, horizon_days)
    sim = simulate_exit(path, entry_date, direction, horizon_days, stop_loss, take_profit)
    if sim is None:
        return None
    bench_path = fetch_price_path(cur, benchmark, entry_date, horizon_days)
    b_entry = [(d, c) for d, c in bench_path if d <= sim["entry_date"]]
    b_exit = [(d, c) for d, c in bench_path if d <= sim["exit_date"]]
    if not b_entry or not b_exit:
        return None
    fwd = (sim["exit_px"] - sim["entry_px"]) / sim["entry_px"]
    bench = (b_exit[-1][1] - b_entry[-1][1]) / b_entry[-1][1]
    return {"fwd": fwd, "bench": bench, "reason": sim["reason"],
            "days": sim["days"], "exit_date": sim["exit_date"]}
