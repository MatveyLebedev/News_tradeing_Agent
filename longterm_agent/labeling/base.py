"""Label and score-config value types shared by every labeler."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Tuple

RUN_NODE = "run"  # the whole decision; any other node is a LangGraph node name


@dataclass(frozen=True)
class ScoreSpec:
    """A Langfuse score config. `categories` maps label → numeric value (CATEGORICAL only)."""
    data_type: str                      # NUMERIC | BOOLEAN | CATEGORICAL
    description: str
    categories: Tuple[Tuple[str, float], ...] = ()
    min_value: Optional[float] = None
    max_value: Optional[float] = None


@dataclass(frozen=True)
class Label:
    trace_key: str          # core.tracing.run_trace_key(...) — also the trace-id seed
    ticker: str
    as_of: date
    node: str               # RUN_NODE or a graph node name
    label_key: str
    labeler: str
    labeler_version: str
    trace_time: datetime    # the decision moment, naive ET
    data_cutoff: date       # last market day the label reads
    horizon_days: Optional[int] = None
    value_num: Optional[float] = None
    value_cat: Optional[str] = None
    value_json: Dict[str, Any] = field(default_factory=dict)
    status: str = "final"   # final | unavailable (measured, but there was nothing to grade)
    reason: Optional[str] = None
    trace_kind: str = "lt_run"
    span: str = ""

    @property
    def label_available_at(self) -> datetime:
        """The first moment a consumer may know this label: the day after its cutoff.

        Not the moment the grader happened to fill it. A label backfilled months late was
        still knowable the day after its last price, and a few-shot consumer that waited for
        the fill date would only see fewer analogs, never leak. Next midnight ET also covers
        the daily bar that lands at 22:30 ET (yahoo-daily-bar NaN window)."""
        return datetime.combine(self.data_cutoff + timedelta(days=1), time())


def score_name(node: str, label_key: str) -> str:
    """Langfuse score name: bare for whole-decision labels, `node.key` otherwise."""
    return label_key if node == RUN_NODE else f"{node}.{label_key}"
