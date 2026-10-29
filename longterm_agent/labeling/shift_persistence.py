"""
Labeler `shift_persistence`: did a detected news-background shift persist over the four weeks
after as_of — for the runs the grader does not grade.

The grader fills `aux_targets.shift_outcome` only on LONG/SHORT rows, so most shifts (the
ones that ended NEUTRAL) were never checked. This labeler runs the grader's own estimator
(`_post_shift_daily_means` → `classify_shift_outcome`) on every other shifted run. The split
is by verdict, so each run gets `shift_detection.outcome` from exactly one labeler and the
two never race for the same score.
"""
from datetime import datetime, time, timedelta
from typing import Iterable

from core import tracing
from longterm_agent.grade_results import HORIZON_DAYS, _decision_moment, _post_shift_daily_means
from longterm_agent.targets import classify_shift_outcome

from .base import Label
from .results_adapter import SCORES as ADAPTER_SCORES
from .store import existing_keys

NAME = "shift_persistence"
VERSION = "1"
RUN_KIND = "live"
NODE, LABEL_KEY = "shift_detection", "outcome"
SCORES = {}  # writes `shift_detection.outcome`, whose config results_adapter defines
_KNOWN = {c for c, _ in ADAPTER_SCORES[f"{NODE}.{LABEL_KEY}"].categories}

_SQL = """
    SELECT s.detection_version, s.ticker, s.as_of,
           s.state->'background_shift'->>'direction', r.verdict, r.traded, r.pit_excluded,
           r.run_timestamp
    FROM longterm_run_snapshots s
    LEFT JOIN longterm_agent_results r
      ON r.agent_version = s.detection_version AND r.ticker = s.ticker AND r.as_of = s.as_of
    WHERE s.state->'background_shift'->>'has_shifted' = 'true'
      AND s.state->'background_shift'->>'direction' IN ('positive', 'negative')
      AND coalesce(r.verdict, '') NOT IN ('LONG', 'SHORT')
    ORDER BY s.as_of, s.ticker
"""


def collect(cur, ctx) -> Iterable[Label]:
    cur.execute(_SQL)
    rows = cur.fetchall()
    done = existing_keys(cur, NAME, VERSION)
    for dv, ticker, as_of, direction, verdict, traded, pit_excluded, run_ts in rows:
        key = tracing.run_trace_key(RUN_KIND, dv, ticker, as_of)
        cutoff = as_of + timedelta(days=HORIZON_DAYS)
        if (key, NODE, "", LABEL_KEY) in done or cutoff >= ctx.today:
            continue
        if ctx.neo is None:
            return  # the estimator reads graph labels; without the graph every run waits
        means = _post_shift_daily_means(ctx.neo, ctx.db_config, ticker,
                                        datetime.combine(as_of, time()), HORIZON_DAYS)
        target = classify_shift_outcome(direction, means)
        cat = target.get("label")
        known = cat in _KNOWN
        yield Label(trace_key=key, ticker=ticker.upper(), as_of=as_of, node=NODE,
                    label_key=LABEL_KEY, labeler=NAME, labeler_version=VERSION,
                    trace_time=_decision_moment(run_ts) or datetime.combine(as_of, time()),
                    data_cutoff=cutoff, horizon_days=HORIZON_DAYS,
                    value_cat=cat if known else None,
                    status="final" if known else "unavailable",
                    reason=None if known else (target.get("note") or cat),
                    value_json={"verdict": verdict, "traded": bool(traded),
                                "pit_excluded": pit_excluded, "shift_direction": direction,
                                **target})
