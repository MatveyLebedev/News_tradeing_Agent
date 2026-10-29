"""
Labeler `decision_deltas`: what the two nodes that can CHANGE a decision were worth.

Derived, not priced: both labels read `counterfactual`'s stock_excess_4w (the labelers run in
order inside one transaction), so they move only when that label does.

risk_gate   For every directional verdict: did blocking or passing it pay? `value_4w` is what
            the gate added over trading every directional verdict: the avoided position
            excess when it blocked, 0 when it passed. `call_4w` names the four cases.
reflection  For the runs where reflection sent the pipeline round again (two synthesis
            passes in the snapshot's messages): `change` says what the second pass did, and
            `value_4w` = position excess of the final call − that of the first. A second pass
            that FAILED (synthesis unavailable → NEUTRAL) is `failed` and gets no value: an
            outage is not a judgment.
"""
import re
from datetime import datetime, time
from typing import Dict, Iterable, Optional, Tuple

from core import tracing
from longterm_agent.grade_results import _decision_moment

from . import counterfactual
from .base import Label, ScoreSpec
from .store import final_values

NAME = "decision_deltas"
VERSION = "1"
RUN_KIND = "live"
CONVICTION_STEP = 0.05   # a smaller conviction move between passes counts as no change

SCORES: Dict[str, ScoreSpec] = {
    "risk_gate.value_4w": ScoreSpec(
        "NUMERIC", "what the gate added over trading every directional verdict: the avoided "
                   "position excess when it blocked (positive = it avoided a loss), 0 when "
                   "it passed"),
    "risk_gate.call_4w": ScoreSpec(
        "CATEGORICAL", "the gate's call against the 4-week outcome",
        categories=(("passed_win", 1.0), ("blocked_loss", 1.0),
                    ("passed_loss", -1.0), ("blocked_win", -1.0))),
    "reflection.change": ScoreSpec(
        "CATEGORICAL", "what the second synthesis pass did to the first one's call",
        categories=(("none", 0.0), ("conviction", 1.0), ("direction", 2.0),
                    ("failed", -1.0))),
    "reflection.value_4w": ScoreSpec(
        "NUMERIC", "position excess (4w) of the final call minus that of the first pass"),
}

_SIGN = {"LONG": 1.0, "SHORT": -1.0, "NEUTRAL": 0.0}
_PASS = re.compile(r"^(LONG|SHORT|NEUTRAL) conviction=([0-9.]+)")

_GATE_SQL = """SELECT agent_version, ticker, as_of, verdict, traded, pit_excluded, run_timestamp
               FROM longterm_agent_results WHERE verdict IN ('LONG', 'SHORT')"""
# Only the head of each synthesis message: "LONG conviction=0.65: <thesis…>"
_REFLECT_SQL = """
    SELECT s.detection_version, s.ticker, s.as_of, r.verdict, r.traded, r.pit_excluded,
           r.run_timestamp,
           (SELECT array_agg(left(m->>'content', 80) ORDER BY ord)
              FROM jsonb_array_elements(s.state->'messages') WITH ORDINALITY AS t(m, ord)
             WHERE m->>'name' = 'synthesis')
    FROM longterm_run_snapshots s
    LEFT JOIN longterm_agent_results r
      ON r.agent_version = s.detection_version AND r.ticker = s.ticker AND r.as_of = s.as_of
    WHERE jsonb_typeof(s.state->'messages') = 'array'
"""


def parse_pass(head: str) -> Optional[Tuple[str, float, bool]]:
    """(direction, conviction, failed) from a synthesis message head, or None."""
    m = _PASS.match(head or "")
    if not m:
        return None
    return m.group(1), float(m.group(2)), "(synthesis unavailable)" in head


def gate_call(traded: bool, position_excess: float) -> Tuple[float, str]:
    won = position_excess > 0
    if traded:
        return 0.0, "passed_win" if won else "passed_loss"
    return round(-position_excess, 4), "blocked_win" if won else "blocked_loss"


def reflection_change(first: Tuple[str, float, bool], final: Tuple[str, float, bool]) -> str:
    if final[2]:
        return "failed"
    if final[0] != first[0]:
        return "direction"
    return "conviction" if abs(final[1] - first[1]) >= CONVICTION_STEP else "none"


def collect(cur, ctx) -> Iterable[Label]:
    stock = final_values(cur, counterfactual.NAME, "stock_excess_4w")

    def label(key, ticker, as_of, run_ts, node, label_key, cutoff, num=None, cat=None, raw=None):
        return Label(trace_key=key, ticker=ticker.upper(), as_of=as_of, node=node,
                     label_key=label_key, labeler=NAME, labeler_version=VERSION,
                     trace_time=_decision_moment(run_ts) or datetime.combine(as_of, time()),
                     data_cutoff=cutoff, horizon_days=counterfactual.HORIZONS["4w"],
                     value_num=num, value_cat=cat, value_json=raw or {})

    cur.execute(_GATE_SQL)
    for dv, ticker, as_of, verdict, traded, pit_excluded, run_ts in cur.fetchall():
        key = tracing.run_trace_key(RUN_KIND, dv, ticker, as_of)
        s = stock.get(key)
        if s is None:
            continue
        pos = _SIGN[verdict] * s["value_num"]
        value, call = gate_call(bool(traded), pos)
        raw = {"verdict": verdict, "traded": bool(traded), "pit_excluded": pit_excluded,
               "position_excess_4w": round(pos, 4)}
        yield label(key, ticker, as_of, run_ts, "risk_gate", "value_4w", s["data_cutoff"],
                    num=value, raw=raw)
        yield label(key, ticker, as_of, run_ts, "risk_gate", "call_4w", s["data_cutoff"],
                    cat=call, raw=raw)

    cur.execute(_REFLECT_SQL)
    for dv, ticker, as_of, verdict, traded, pit_excluded, run_ts, heads in cur.fetchall():
        passes = [p for p in (parse_pass(h) for h in (heads or [])) if p]
        if len(passes) < 2:
            continue  # reflection did not send the run round again
        key = tracing.run_trace_key(RUN_KIND, dv, ticker, as_of)
        first, final = passes[0], passes[-1]
        change = reflection_change(first, final)
        raw = {"verdict": verdict, "traded": bool(traded), "pit_excluded": pit_excluded,
               "first": {"direction": first[0], "conviction": first[1]},
               "final": {"direction": final[0], "conviction": final[1], "failed": final[2]},
               "passes": len(passes)}
        yield label(key, ticker, as_of, run_ts, "reflection", "change", as_of, cat=change,
                    raw=raw)
        s = stock.get(key)
        if s is not None and change != "failed":
            value = (_SIGN[final[0]] - _SIGN[first[0]]) * s["value_num"]
            yield label(key, ticker, as_of, run_ts, "reflection", "value_4w", s["data_cutoff"],
                        num=round(value, 4), raw=raw)
