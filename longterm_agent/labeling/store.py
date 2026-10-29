"""Postgres side of trace labeling: `trace_labels` (source of truth) and `trace_label_syncs`
(what each Langfuse project already holds)."""
import hashlib
from datetime import datetime
from typing import Any, Dict, Iterable, List

import pytz
from psycopg2.extras import Json, execute_values

from core import tracing
from longterm_agent.persistence import json_safe

from .base import Label

ET = pytz.timezone("America/New_York")

_DDL = """
CREATE TABLE IF NOT EXISTS trace_labels (
    id                 BIGSERIAL PRIMARY KEY,
    trace_kind         VARCHAR(16)  NOT NULL,   -- lt_run = a longterm agent run
    trace_key          VARCHAR(96)  NOT NULL,   -- core.tracing.run_trace_key (trace-id seed)
    trace_id           CHAR(32)     NOT NULL,   -- Langfuse trace id derived from trace_key
    ticker             VARCHAR(10),
    as_of              DATE,
    node               VARCHAR(40)  NOT NULL,   -- 'run' = the whole decision, else a graph node
    span               VARCHAR(80)  NOT NULL DEFAULT '',
    label_key          VARCHAR(48)  NOT NULL,
    value_num          DOUBLE PRECISION,
    value_cat          VARCHAR(48),
    value_json         JSONB,                   -- the raw inputs the label was computed from
    status             VARCHAR(12)  NOT NULL,   -- final | unavailable
    reason             TEXT,
    labeler            VARCHAR(48)  NOT NULL,
    labeler_version    VARCHAR(16)  NOT NULL,
    trace_time         TIMESTAMP    NOT NULL,   -- decision moment, naive ET
    horizon_days       INT,
    data_cutoff        DATE         NOT NULL,   -- last market day the label reads
    label_available_at TIMESTAMP    NOT NULL,   -- ET; consumers at as_of see it only if <= as_of
    created_at         TIMESTAMP    NOT NULL,   -- ET, set from Python (the DB clock is UTC)
    updated_at         TIMESTAMP    NOT NULL,   -- ET, bumped only when the value changes
    UNIQUE (trace_kind, trace_key, node, span, label_key, labeler_version)
);
CREATE INDEX IF NOT EXISTS idx_tl_ticker    ON trace_labels(ticker, as_of);
CREATE INDEX IF NOT EXISTS idx_tl_available ON trace_labels(label_key, label_available_at);

CREATE TABLE IF NOT EXISTS trace_label_syncs (
    label_id        BIGINT      NOT NULL REFERENCES trace_labels(id) ON DELETE CASCADE,
    lf_target       VARCHAR(16) NOT NULL,       -- sha256(Langfuse public key)[:16] = one project
    score_id        CHAR(32)    NOT NULL,
    observation_id  VARCHAR(32),                -- NULL = scored on the trace itself
    synced_at       TIMESTAMP   NOT NULL,       -- ET
    PRIMARY KEY (label_id, lf_target)
);
"""

_COLS = ("trace_kind", "trace_key", "trace_id", "ticker", "as_of", "node", "span", "label_key",
         "value_num", "value_cat", "value_json", "status", "reason", "labeler",
         "labeler_version", "trace_time", "horizon_days", "data_cutoff", "label_available_at",
         "created_at", "updated_at")


def now_et() -> datetime:
    return datetime.now(ET).replace(tzinfo=None)


def ensure_tables(cur) -> None:
    cur.execute(_DDL)


def lf_target(public_key: str) -> str:
    """A Langfuse project's identity in `trace_label_syncs`. Keyed by the public key, not the
    URL: the server reaches Langfuse at langfuse-web:3000, a laptop through a tunnel, and
    both are the same project."""
    return hashlib.sha256(public_key.encode()).hexdigest()[:16]


def score_id(label_row: Dict[str, Any]) -> str:
    """Deterministic score id. No labeler version in it, so a new version REPLACES the score
    in Langfuse (which shows the current label) while Postgres keeps every version."""
    return tracing.trace_id_for("score:" + ":".join(
        str(label_row[k]) for k in ("trace_key", "node", "span", "label_key")))


def _row(label: Label, stamp: datetime) -> tuple:
    value_json, _ = json_safe(label.value_json or {})
    return (label.trace_kind, label.trace_key, tracing.trace_id_for(label.trace_key),
            label.ticker, label.as_of, label.node, label.span, label.label_key,
            label.value_num, label.value_cat, Json(value_json), label.status, label.reason,
            label.labeler, label.labeler_version, label.trace_time, label.horizon_days,
            label.data_cutoff, label.label_available_at, stamp, stamp)


def upsert_labels(cur, labels: Iterable[Label]) -> Dict[str, int]:
    """Insert new labels; update a stored one only when its value actually changed (so an
    unchanged daily re-run neither bumps `updated_at` nor triggers a Langfuse re-send)."""
    stamp = now_et()
    rows = [_row(lb, stamp) for lb in labels]
    if not rows:
        return {"inserted": 0, "updated": 0, "unchanged": 0}
    changed = execute_values(cur, f"""
        INSERT INTO trace_labels ({", ".join(_COLS)}) VALUES %s
        ON CONFLICT (trace_kind, trace_key, node, span, label_key, labeler_version)
        DO UPDATE SET value_num=EXCLUDED.value_num, value_cat=EXCLUDED.value_cat,
                      value_json=EXCLUDED.value_json, status=EXCLUDED.status,
                      reason=EXCLUDED.reason, trace_time=EXCLUDED.trace_time,
                      horizon_days=EXCLUDED.horizon_days, data_cutoff=EXCLUDED.data_cutoff,
                      label_available_at=EXCLUDED.label_available_at,
                      updated_at=EXCLUDED.updated_at
        WHERE (trace_labels.value_num, trace_labels.value_cat, trace_labels.value_json,
               trace_labels.status, trace_labels.data_cutoff, trace_labels.trace_time)
              IS DISTINCT FROM
              (EXCLUDED.value_num, EXCLUDED.value_cat, EXCLUDED.value_json,
               EXCLUDED.status, EXCLUDED.data_cutoff, EXCLUDED.trace_time)
        RETURNING (xmax = 0)
    """, rows, page_size=200, fetch=True)
    inserted = sum(1 for (ins,) in changed if ins)
    return {"inserted": inserted, "updated": len(changed) - inserted,
            "unchanged": len(rows) - len(changed)}


def existing_keys(cur, labeler: str, version: str) -> set:
    """{(trace_key, node, span, label_key)} this labeler version already wrote, final or
    unavailable — what an incremental labeler skips."""
    cur.execute("""SELECT trace_key, node, span, label_key FROM trace_labels
                   WHERE labeler=%s AND labeler_version=%s""", [labeler, version])
    return set(cur.fetchall())


def final_values(cur, labeler: str, label_key: str) -> Dict[str, Dict[str, Any]]:
    """{trace_key: {value_num, value_cat, data_cutoff}} for one label of one labeler, its
    latest version — how a derived labeler reads another's output."""
    cur.execute("""SELECT DISTINCT ON (trace_key) trace_key, value_num, value_cat, data_cutoff
                   FROM trace_labels
                   WHERE labeler=%s AND label_key=%s AND node='run' AND status='final'
                   ORDER BY trace_key, labeler_version DESC""", [labeler, label_key])
    return {k: {"value_num": n, "value_cat": c, "data_cutoff": d}
            for k, n, c, d in cur.fetchall()}


def pending_sync(cur, target: str, limit: int = None) -> List[Dict[str, Any]]:
    """Final labels this Langfuse project does not hold yet (or holds an older value of)."""
    cur.execute(f"""
        SELECT l.id, l.trace_kind, l.trace_key, l.trace_id, l.ticker, l.as_of, l.node, l.span, l.label_key,
               l.value_num, l.value_cat, l.value_json, l.labeler, l.labeler_version,
               l.data_cutoff, l.label_available_at
        FROM trace_labels l
        LEFT JOIN trace_label_syncs s ON s.label_id = l.id AND s.lf_target = %s
        WHERE l.status = 'final' AND (s.label_id IS NULL OR s.synced_at < l.updated_at)
        ORDER BY l.trace_id, l.id
        {"LIMIT %s" if limit else ""}
    """, [target, limit] if limit else [target])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def mark_synced(cur, target: str, done: List[tuple]) -> None:
    """`done` = [(label_id, score_id, observation_id | None), ...]"""
    if not done:
        return
    stamp = now_et()
    execute_values(cur, """
        INSERT INTO trace_label_syncs (label_id, lf_target, score_id, observation_id, synced_at)
        VALUES %s
        ON CONFLICT (label_id, lf_target) DO UPDATE SET score_id=EXCLUDED.score_id,
            observation_id=EXCLUDED.observation_id, synced_at=EXCLUDED.synced_at
    """, [(lid, target, sid, oid, stamp) for lid, sid, oid in done])
