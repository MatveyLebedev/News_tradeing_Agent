"""
Labeler `cohorts`: one weekly batch, judged as a whole — scored on its Langfuse SESSION
(`batch-<as_of>`), so the sessions list reads as a weekly P&L.

Restates the grader's columns like results_adapter does, aggregated over the cohort's traded
rows. Written only once every traded row of the cohort has its 4-week grade, so a cohort
number never changes after it appears. Replayed weeks (`pit_excluded`) are left out of the
aggregates, as everywhere the book is counted (stats.open_positions).
"""
from datetime import datetime, time, timedelta
from typing import Dict, Iterable

from longterm_agent.grade_results import HORIZON_DAYS
from longterm_agent.stats import trade_excess

from .base import SESSION_NODE, Label, ScoreSpec

NAME = "cohorts"
VERSION = "1"
TRACE_KIND = "lt_session"
NODE = SESSION_NODE
SETTLE_DAYS = 7   # a cohort with no trades is final once its horizon + this has passed

SCORES: Dict[str, ScoreSpec] = {
    "cohort_excess_4w": ScoreSpec("NUMERIC", "mean position excess vs SPY (4w) over the "
                                             "batch's traded rows, replays excluded"),
    "cohort_hit_4w": ScoreSpec("NUMERIC", "share of the batch's traded rows with excess_4w > 0",
                               min_value=0.0, max_value=1.0),
    "cohort_n_traded": ScoreSpec("NUMERIC", "traded rows in the batch, replays excluded",
                                 min_value=0.0),
}

_SQL = """
    SELECT agent_version, as_of, verdict, fwd_return_4w, benchmark_return_4w, entry_date,
           pit_excluded
    FROM longterm_agent_results
    WHERE traded
"""
_COHORTS_SQL = "SELECT DISTINCT agent_version, as_of FROM longterm_agent_results"


def session_id(as_of) -> str:
    """The Langfuse session of a weekly batch (batch_run_longterm: f"batch-{as_of:%Y-%m-%d}")."""
    return f"batch-{as_of}"


def trace_key(agent_version: str, as_of) -> str:
    return f"session:{agent_version}:{session_id(as_of)}"


def collect(cur, ctx) -> Iterable[Label]:
    cur.execute(_SQL)
    traded: Dict[tuple, list] = {}
    for dv, as_of, verdict, fwd, bench, entry, pit_excluded in cur.fetchall():
        if not pit_excluded:
            traded.setdefault((dv, as_of), []).append((verdict, fwd, bench, entry))
    cur.execute(_COHORTS_SQL)
    for dv, as_of in cur.fetchall():
        rows = traded.get((dv, as_of), [])
        excesses = [trade_excess(v, f, b) for v, f, b, _ in rows]
        if any(x is None for x in excesses):
            continue  # some trade not graded yet: the cohort is not final
        if rows:
            cutoff = max((e or as_of) for _, _, _, e in rows) + timedelta(days=HORIZON_DAYS)
        else:
            cutoff = as_of + timedelta(days=HORIZON_DAYS + SETTLE_DAYS)
            if cutoff >= ctx.today:
                continue
        common = dict(trace_key=trace_key(dv, as_of), trace_kind=TRACE_KIND, ticker=None,
                      as_of=as_of, node=NODE, labeler=NAME, labeler_version=VERSION,
                      trace_time=datetime.combine(as_of, time()), data_cutoff=cutoff,
                      horizon_days=HORIZON_DAYS,
                      value_json={"session_id": session_id(as_of), "agent_version": dv})
        yield Label(label_key="cohort_n_traded", value_num=float(len(rows)), **common)
        if rows:
            yield Label(label_key="cohort_excess_4w",
                        value_num=round(sum(excesses) / len(excesses), 4), **common)
            yield Label(label_key="cohort_hit_4w",
                        value_num=round(sum(x > 0 for x in excesses) / len(excesses), 4),
                        **common)
