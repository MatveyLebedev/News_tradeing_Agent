"""
Langfuse side of trace labeling: score configs, the trace lookup, traces rebuilt from
snapshots, and the score push.

Every label goes onto the observation of the node it grades (the latest one, when a refine
loop ran the node twice); a whole-decision label, or a node the trace lacks, is scored on
the trace itself. A labeled run with no trace in Langfuse — it predates tracing, or
Langfuse was down that Sunday — gets one rebuilt from `longterm_run_snapshots` first.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytz

from core import tracing

from .base import RUN_NODE, ScoreSpec, score_name
from .store import mark_synced, pending_sync, score_id

ET = pytz.timezone("America/New_York")
ROOT = "longterm_run"
# A live trace reaches Langfuse within minutes. Only a run older than this may be called
# missing and rebuilt, or an ingestion lag would get a second, synthetic copy of it.
REBUILD_MIN_AGE = timedelta(days=1)

# (graph node, snapshot keys it produced). The node's real output is its state delta; a
# snapshot keeps those values, so a rebuilt span carries the same keys a live one does.
_NODES = (
    ("corpus_collection", ("corpus_ref",)),
    ("background_profiling", ("bucket_summaries", "background_profile")),
    ("shift_detection", ("background_metrics", "background_shift")),
    ("fundamentals_analysis", ("fundamentals_trend", "technical_longterm")),
    ("similar_shift_analysis", ("shift_analogs",)),
    ("deep_analysis", ("enriched_analysis",)),
    ("synthesis", ("signal",)),
    ("reflection", ("reflection_advice",)),
    ("risk_gate", ("risk_flags", "risk_checks", "filtered_signal")),
)


# --- score configs -------------------------------------------------------------------------
def ensure_score_configs(api, specs: Dict[str, ScoreSpec]) -> Dict[str, str]:
    """Create the configs that are missing; return {score name: config id}. Configs cannot
    be deleted in Langfuse (only archived), so an existing one is reused, never replaced."""
    from langfuse.api import ConfigCategory

    existing, page = {}, 1
    while True:
        res = api.score_configs.get(page=page, limit=100)
        for c in res.data:
            if not c.is_archived:
                existing.setdefault(c.name, c)
        if page >= (res.meta.total_pages or 1):
            break
        page += 1
    ids = {}
    for name, spec in specs.items():
        c = existing.get(name)
        if c is None:
            kw = {"description": spec.description}
            if spec.categories:
                kw["categories"] = [ConfigCategory(label=lb, value=v) for lb, v in spec.categories]
            if spec.min_value is not None:
                kw["min_value"] = spec.min_value
            if spec.max_value is not None:
                kw["max_value"] = spec.max_value
            c = api.score_configs.create(name=name, data_type=spec.data_type, **kw)
            print(f"  + score config {name} ({spec.data_type})")
        elif str(c.data_type) != spec.data_type:
            print(f"  ⚠ score config {name}: Langfuse has {c.data_type}, code wants "
                  f"{spec.data_type} — scores sent without a config")
            continue
        ids[name] = c.id
    return ids


# --- what a trace holds --------------------------------------------------------------------
def observation_index(api, trace_id: str) -> Dict[str, Any]:
    """{node name: observation id} for one trace, plus `_env`. Empty dict = no trace."""
    res = api.observations.get_many(trace_id=trace_id, fields="core,basic", limit=1000)
    latest: Dict[str, Any] = {}
    for o in res.data:
        if o.name and (o.name not in latest or o.start_time > latest[o.name].start_time):
            latest[o.name] = o
    if not latest:
        return {}
    root = latest.get(ROOT) or next(iter(latest.values()))
    return {"_env": root.environment, **{name: o.id for name, o in latest.items()}}


# --- traces rebuilt from snapshots ---------------------------------------------------------
_SNAP_SQL = """
    SELECT s.id, s.detection_version, s.pipeline_version, s.ticker, s.as_of, s.state,
           s.created_at, r.run_timestamp, r.execution_time_seconds, r.gate_trace,
           r.pit_excluded
    FROM longterm_run_snapshots s
    LEFT JOIN longterm_agent_results r
      ON r.agent_version = s.detection_version AND r.ticker = s.ticker AND r.as_of = s.as_of
"""


def fetch_snapshot(cur, detection_version: str, ticker: str, as_of) -> Optional[Dict]:
    cur.execute(_SNAP_SQL + " WHERE s.detection_version=%s AND s.ticker=%s AND s.as_of=%s",
                [detection_version, ticker, as_of])
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def iter_snapshots(cur):
    cur.execute(_SNAP_SQL + " ORDER BY s.as_of, s.ticker")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        yield dict(zip(cols, row))


def run_window(snap: Dict[str, Any]):
    """(start, end) of the run as aware UTC datetimes. The results row is stamped when it is
    persisted, i.e. when the pipeline finished; its runtime puts the start before that.
    Without a results row the snapshot's own stamp (DB clock, UTC) stands in for the end."""
    end = snap.get("run_timestamp")
    if end is not None:
        end = end.astimezone(timezone.utc) if end.tzinfo else ET.localize(end).astimezone(
            timezone.utc)
    else:
        end = snap["created_at"].replace(tzinfo=timezone.utc)
    secs = snap.get("execution_time_seconds") or 60.0
    return end - timedelta(seconds=float(secs)), end


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def _span_at(lf, name: str, as_type: str, start: datetime, *, parent=None, trace_id=None,
             **fields):
    """An observation that STARTED in the past. The SDK's public start_observation always
    stamps now(), so this goes one level down to the OTel tracer it wraps (langfuse==5.0.4,
    pinned; test_labeling guards these internals). End it with `.end(end_time=_ns(t))`."""
    from opentelemetry import trace as otel
    from langfuse._client.attributes import LangfuseOtelSpanAttributes

    if parent is None:
        ctx = otel.set_span_in_context(
            lf._create_remote_parent_span(trace_id=trace_id, parent_span_id=None))
    else:
        ctx = otel.set_span_in_context(parent._otel_span)
    span = lf._otel_tracer.start_span(name=name, start_time=_ns(start), context=ctx)
    if parent is None:
        span.set_attribute(LangfuseOtelSpanAttributes.AS_ROOT, True)
    return lf._create_observation_from_otel_span(otel_span=span, as_type=as_type, **fields)


def rebuild_trace(lf, snap: Dict[str, Any]) -> Dict[str, Any]:
    """Write one run's trace from its snapshot, under the id the live run would have had.
    Returns the same shape as `observation_index`. Node timings are synthetic (evenly
    spaced inside the real run window); outputs are the snapshot's, i.e. the real ones."""
    from langfuse import propagate_attributes

    state = snap["state"] or {}
    ident = state.get("identity") or {}
    prov_doc = state.get("provenance") or {}
    prov = {"detection_version": snap["detection_version"],
            "pipeline_version": snap.get("pipeline_version")
            or prov_doc.get("pipeline_version", "unknown"),
            "git_revision": prov_doc.get("git_revision", "unknown")}
    key = tracing.run_trace_key("live", snap["detection_version"], snap["ticker"], snap["as_of"])
    trace_id = tracing.trace_id_for(key)
    start, end = run_window(snap)
    tags = ["rebuilt:snapshot"] + (["pit_excluded"] if snap.get("pit_excluded") else [])
    final = {"no_data": ident.get("no_data"), "article_count": ident.get("article_count"),
             "signal": state.get("signal"), "gate_trace": snap.get("gate_trace"),
             "risk_flags": state.get("risk_flags"), "background_shift": state.get("background_shift")}

    nodes = [(n, {k: state.get(k) for k in keys}) for n, keys in _NODES
             if any(state.get(k) is not None for k in keys)]
    step = (end - start) / (len(nodes) + 1)
    index = {"_env": lf._environment}  # the spans below carry the client's environment
    with propagate_attributes(**tracing.trace_attributes(
            snap["ticker"], snap["as_of"], prov, "live", f"batch-{snap['as_of']}", tags)):
        root = _span_at(lf, ROOT, "agent", start, trace_id=trace_id,
                        input={"ticker": snap["ticker"], "as_of": str(snap["as_of"])},
                        output=tracing.decision(final),
                        metadata={"provenance": prov_doc, "rebuilt_from_snapshot": snap["id"],
                                  "timing": "synthetic: node spans are evenly spaced inside "
                                            "the real run window"})
        graph = _span_at(lf, "longterm_graph", "chain", start, parent=root)
        t = start
        for name, output in nodes:
            node = _span_at(lf, name, "span", t, parent=graph, output=output)
            t += step
            node.end(end_time=_ns(t))
            index[name] = node.id
        graph.end(end_time=_ns(end))
        root.end(end_time=_ns(end))
    index[ROOT] = root.id
    return index


# --- the push ------------------------------------------------------------------------------
def _score(label: Dict[str, Any], obs_id: Optional[str], env: Optional[str],
           specs: Dict[str, ScoreSpec], config_ids: Dict[str, str]):
    from langfuse.api import CreateScoreRequest

    name = score_name(label["node"], label["label_key"])
    data_type = specs[name].data_type
    value = label["value_cat"] if data_type == "CATEGORICAL" else float(label["value_num"])
    return CreateScoreRequest(
        id=score_id(label), trace_id=label["trace_id"].strip(), observation_id=obs_id,
        name=name, value=value, data_type=data_type, config_id=config_ids.get(name),
        environment=env,
        source="API",
        # Stamped when the outcome became knowable, not when it was sent: score charts then
        # read as "what we learned when", and a re-sync to a new project is byte-identical.
        # Langfuse keeps a score id's FIRST timestamp on upsert (verified on 4.70.1); values
        # and metadata do update.
        timestamp=ET.localize(label["label_available_at"]),
        comment=f"{label['labeler']} v{label['labeler_version']} · data through "
                f"{label['data_cutoff']}",
        metadata={"labeler": label["labeler"], "labeler_version": label["labeler_version"],
                  "data_cutoff": str(label["data_cutoff"]),
                  "label_available_at": label["label_available_at"].isoformat(),
                  "ticker": label["ticker"], "as_of": str(label["as_of"]),
                  "trace_key": label["trace_key"]})


def push(cur, lf, target: str, specs: Dict[str, ScoreSpec], config_ids: Dict[str, str],
         rebuild: bool = True, limit: int = None) -> Dict[str, int]:
    """Send every pending label of this Langfuse project, one batch per trace. A trace's
    labels are marked synced only if the whole batch was accepted."""
    labels = pending_sync(cur, target, limit)
    by_trace: Dict[str, List[Dict]] = {}
    for lb in labels:
        by_trace.setdefault(lb["trace_id"].strip(), []).append(lb)
    stats = {"traces": len(by_trace), "scores": 0, "rebuilt": 0, "no_trace": 0, "rejected": 0}
    now = datetime.now(timezone.utc)
    for trace_id, group in by_trace.items():
        index = observation_index(lf.api, trace_id)
        if not index and rebuild:
            _, dv, ticker, day = group[0]["trace_key"].split(":")
            snap = fetch_snapshot(cur, dv, ticker, day)
            if snap is not None and run_window(snap)[1] < now - REBUILD_MIN_AGE:
                index = rebuild_trace(lf, snap)
                lf.flush()  # the spans before their scores: a score must never outlive its trace
                stats["rebuilt"] += 1
        if not index:
            stats["no_trace"] += 1
            continue
        reqs, done = [], []
        for lb in group:
            obs = None if lb["node"] == RUN_NODE else index.get(lb["node"])
            reqs.append(_score(lb, obs, index.get("_env"), specs, config_ids))
            done.append((lb["id"], reqs[-1].id, obs))
        res = lf.api.scores.create(request=reqs)
        errors = getattr(res, "errors", None)
        if errors:
            stats["rejected"] += len(reqs)
            print(f"  ⚠ {group[0]['trace_key']}: {getattr(res, 'rejected', '?')} of "
                  f"{len(reqs)} scores rejected — {errors[0].message}")
            continue
        mark_synced(cur, target, done)
        stats["scores"] += len(reqs)
    return stats
