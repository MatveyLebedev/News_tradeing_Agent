"""
Labeler `results_adapter`: the grader's columns restated as labels on the run that decided.

Recomputes nothing. grade_results.py stays the one place that prices a trade; this module
only reads what it filled in `longterm_agent_results`, signs it by the position (stats.
trade_excess: LONG earns stock − SPY, SHORT earns SPY − stock) and records the last market
day each number reads, which is what `label_available_at` is built from.

Only LONG/SHORT rows carry outcomes (the grader skips NEUTRAL), including rows the risk gate
blocked: `traded` rides along in value_json so either book can be selected downstream.
"""
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, List

from core import tracing
from longterm_agent.grade_results import (EXTRA_HORIZONS, FUND_REALIZED_LAG_DAYS, HORIZON_DAYS,
                                          _decision_moment)
from longterm_agent.stats import trade_excess

from .base import RUN_NODE, Label, ScoreSpec

NAME = "results_adapter"
VERSION = "1"
# longterm_agent_results rows are written by the weekly batch and its replays, both traced
# with run_kind "live" (batch_run_longterm → tracing.agent_run's default).
RUN_KIND = "live"
# Entry is the first session after as_of. When a row predates the entry_date column, the
# cutoff is pushed past a long weekend rather than guessed short.
_ENTRY_FALLBACK_DAYS = 4

_HORIZONS = {"4w": HORIZON_DAYS, **EXTRA_HORIZONS}
_POSITION = "position excess vs {bench} over {h} from the entry fill (LONG: stock − {bench}; " \
            "SHORT: {bench} − stock)"

SCORES: Dict[str, ScoreSpec] = {
    "excess_2w": ScoreSpec("NUMERIC", _POSITION.format(bench="SPY", h="2 weeks")),
    "excess_4w": ScoreSpec("NUMERIC", _POSITION.format(bench="SPY", h="4 weeks")),
    "excess_12w": ScoreSpec("NUMERIC", _POSITION.format(bench="SPY", h="12 weeks")),
    "hit_4w": ScoreSpec("BOOLEAN", "1 when excess_4w > 0"),
    "excess_sector_4w": ScoreSpec("NUMERIC", _POSITION.format(bench="the sector ETF",
                                                              h="4 weeks")),
    "excess_beta_4w": ScoreSpec("NUMERIC", "excess_4w against a beta-matched SPY leg"),
    "excess_stopped": ScoreSpec("NUMERIC", "excess_4w under the structural stop-loss exit "
                                           "(exit_rules), the stopped book"),
    "exit_reason": ScoreSpec("CATEGORICAL", "how the stopped book left the trade",
                             categories=(("horizon", 0.0), ("stop", -1.0),
                                         ("take_profit", 1.0))),
    "worst_position_4w": ScoreSpec("NUMERIC", "max adverse excursion: the worst position "
                                              "return on any close within 4 weeks"),
    "shift_detection.outcome": ScoreSpec(
        "CATEGORICAL", "did the detected news-background shift persist over the 4 weeks "
                       "after as_of (post-window AFFECTS sentiment)",
        categories=(("persisted", 1.0), ("mixed", 0.0), ("reverted", -1.0))),
    "fundamentals_analysis.realized": ScoreSpec(
        "CATEGORICAL", "the next filed quarter vs the picture at as_of "
                       f"(read {FUND_REALIZED_LAG_DAYS} days later)",
        categories=(("improving", 1.0), ("stable", 0.0), ("deteriorating", -1.0))),
}

_SQL = """
    SELECT agent_version, ticker, as_of, verdict, traded, pit_excluded, run_timestamp,
           entry_date, fill_convention,
           fwd_return_2w, benchmark_return_2w, fwd_return_4w, benchmark_return_4w,
           fwd_return_12w, benchmark_return_12w, sector_benchmark, sector_benchmark_return_4w,
           fwd_return_stopped, benchmark_return_stopped, exit_reason, exit_days,
           worst_position_return_4w, beta, excess_beta, aux_targets
    FROM longterm_agent_results
    WHERE verdict IN ('LONG', 'SHORT')
      AND (fwd_return_2w IS NOT NULL OR fwd_return_4w IS NOT NULL
           OR aux_targets IS NOT NULL)
"""


def _r4(x):
    return None if x is None else round(float(x), 4)


def labels_from_row(r: Dict[str, Any]) -> List[Label]:
    """Pure: one graded results row → its labels."""
    verdict, as_of = r["verdict"], r["as_of"]
    key = tracing.run_trace_key(RUN_KIND, r["agent_version"], r["ticker"], as_of)
    decided = _decision_moment(r.get("run_timestamp")) or datetime.combine(as_of, time())
    entry = r.get("entry_date") or as_of + timedelta(days=_ENTRY_FALLBACK_DAYS)
    ctx = {"verdict": verdict, "traded": bool(r.get("traded")),
           "pit_excluded": r.get("pit_excluded"), "fill_convention": r.get("fill_convention"),
           "entry_date": str(entry), "entry_date_inferred": r.get("entry_date") is None}
    out: List[Label] = []

    def add(node, label_key, cutoff, horizon, num=None, cat=None, status="final",
            reason=None, raw=None):
        out.append(Label(trace_key=key, ticker=r["ticker"].upper(), as_of=as_of, node=node,
                         label_key=label_key, labeler=NAME, labeler_version=VERSION,
                         trace_time=decided, data_cutoff=cutoff, horizon_days=horizon,
                         value_num=num, value_cat=cat, status=status, reason=reason,
                         value_json={**ctx, **(raw or {})}))

    for tag, days in _HORIZONS.items():
        fwd, bench = r.get(f"fwd_return_{tag}"), r.get(f"benchmark_return_{tag}")
        x = trade_excess(verdict, fwd, bench)
        if x is None:
            continue
        cutoff = entry + timedelta(days=days)
        add(RUN_NODE, f"excess_{tag}", cutoff, days, num=_r4(x),
            raw={"fwd": fwd, "bench": bench})
        if tag == "4w":
            add(RUN_NODE, "hit_4w", cutoff, days, num=1.0 if x > 0 else 0.0)
            sector = trade_excess(verdict, r.get("fwd_return_4w"),
                                  r.get("sector_benchmark_return_4w"))
            if sector is not None:
                add(RUN_NODE, "excess_sector_4w", cutoff, days, num=_r4(sector),
                    raw={"sector_benchmark": r.get("sector_benchmark"),
                         "sector_return": r.get("sector_benchmark_return_4w")})
            if r.get("excess_beta") is not None:
                add(RUN_NODE, "excess_beta_4w", cutoff, days, num=_r4(r["excess_beta"]),
                    raw={"beta": r.get("beta")})
            if r.get("worst_position_return_4w") is not None:
                add(RUN_NODE, "worst_position_4w", cutoff, days,
                    num=_r4(r["worst_position_return_4w"]))

    stopped = trade_excess(verdict, r.get("fwd_return_stopped"),
                           r.get("benchmark_return_stopped"))
    if stopped is not None:
        exit_days = r.get("exit_days")
        cutoff = entry + timedelta(days=exit_days if exit_days is not None else HORIZON_DAYS)
        raw = {"exit_reason": r.get("exit_reason"), "exit_days": exit_days,
               "fwd": r.get("fwd_return_stopped"), "bench": r.get("benchmark_return_stopped")}
        add(RUN_NODE, "excess_stopped", cutoff, HORIZON_DAYS, num=_r4(stopped), raw=raw)
        if r.get("exit_reason"):
            add(RUN_NODE, "exit_reason", cutoff, HORIZON_DAYS, cat=r["exit_reason"], raw=raw)

    aux = r.get("aux_targets") or {}
    for node, label_key, aux_key, lag in (
            ("shift_detection", "outcome", "shift_outcome", HORIZON_DAYS),
            ("fundamentals_analysis", "realized", "fundamentals_realized",
             FUND_REALIZED_LAG_DAYS)):
        target = aux.get(aux_key)
        if not target:
            continue
        cat = target.get("label")
        known = cat in {c for c, _ in SCORES[f"{node}.{label_key}"].categories}
        add(node, label_key, as_of + timedelta(days=lag), lag, cat=cat if known else None,
            status="final" if known else "unavailable",
            reason=None if known else (target.get("note") or cat), raw=target)
    return out


def collect(cur) -> Iterable[Label]:
    cur.execute(_SQL)
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        yield from labels_from_row(dict(zip(cols, row)))
