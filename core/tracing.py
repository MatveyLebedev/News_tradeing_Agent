"""Langfuse tracing for agent runs (self-hosted; replaced LangSmith on 2026-10-28).

One trace per agent run: a root `longterm_run` observation whose input is the run key and
whose output is the decision, with every LangGraph node and LLM call nested under it.

The trace id is derived, not stored: `run_trace_id(run_kind, detection_version, ticker,
as_of)` hashes the same key a results / backtest row is identified by (the cohort DATE, not
the clock time), so a grader can attach outcome scores to a run's trace weeks later with
nothing new persisted. A re-run of the same key lands in the same trace, which is the point.

Off unless LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL are set and
LT_TRACE is not "false". Best-effort by construction: the SDK exports from a background
thread and drops on error, so an unreachable Langfuse never fails or slows a run.

Environments (LANGFUSE_TRACING_ENVIRONMENT overrides the entrypoint's default):
live (scheduler, forward cohorts) · backtest · eval · dev (manual CLI runs).

    from core import tracing
    tracing.init(default_environment="live")
    with tracing.agent_run(ticker, as_of, cfg, session_id="weekly-2026-11-01") as run:
        final = graph.invoke(init, config=run.config)
        run.end(final)
    tracing.flush()   # before a short-lived process exits
"""
import os
from contextlib import contextmanager

from core.settings import get_settings

_client = None


def enabled() -> bool:
    s = get_settings()
    return (os.getenv("LT_TRACE", "true").lower() != "false"
            and bool(s.langfuse_public_key and s.langfuse_secret_key and s.langfuse_base_url))


def _compact(data=None, **_):
    """Langfuse mask: replace the article list in graph state with its size and ids.

    Every LangGraph node span records the full state as its input, and `news_corpus`
    (hundreds of titles + summaries) dominates it — stored ~10x per run it is most of the
    trace volume. The text is still in the trace where it matters: inside the map
    prompts that read it. The ids keep it recoverable from Postgres.
    """
    try:
        if isinstance(data, dict):
            out = {}
            for k, v in data.items():
                if k == "news_corpus" and isinstance(v, list):
                    out[k] = {"n_articles": len(v),
                              "ids": [a.get("id") for a in v if isinstance(a, dict)]}
                else:
                    out[k] = _compact(v)
            return out
        if isinstance(data, list):
            return [_compact(x) for x in data]
    except Exception:
        pass
    return data


def init(default_environment: str = "dev") -> bool:
    """Create the process-wide Langfuse client. Call once, before the first run.
    Returns True if tracing is on."""
    global _client
    if _client is not None:
        return True
    if not enabled():
        print("⚠ Langfuse not configured (LANGFUSE_* unset or LT_TRACE=false) — tracing OFF")
        return False
    from langfuse import Langfuse  # heavy import (OpenTelemetry), keep lazy

    s = get_settings()
    env = os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or default_environment
    _client = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key,
                       base_url=s.langfuse_base_url, environment=env, mask=_compact)
    print(f"✓ Langfuse tracing ON → {s.langfuse_base_url} (environment '{env}')")
    return True


def flush():
    if _client is not None:
        _client.flush()


def client():
    """The process-wide Langfuse client, or None while tracing is off."""
    return _client


def run_trace_key(run_kind: str, detection_version: str, ticker: str, as_of) -> str:
    """One run's identity as a string: the trace-id seed, and the key its labels live under
    in `trace_labels`. `run_kind` is "live", "manual" or the backtest_id."""
    day = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
    return f"{run_kind}:{detection_version}:{ticker.upper()}:{day}"


def trace_id_for(key: str) -> str:
    from langfuse import Langfuse
    return Langfuse.create_trace_id(seed=key)


def run_trace_id(run_kind: str, detection_version: str, ticker: str, as_of) -> str:
    """Deterministic trace id for one run. `run_kind` is "live" or the backtest_id."""
    return trace_id_for(run_trace_key(run_kind, detection_version, ticker, as_of))


def trace_attributes(ticker: str, as_of, prov: dict, run_kind: str, session_id: str,
                     tags=()) -> dict:
    """`propagate_attributes` kwargs for a run's trace. Shared by live runs and by traces
    rebuilt from snapshots (longterm_agent.labeling), so both look the same in Langfuse.
    Propagated to every observation, so keep it to short strings (the full provenance,
    knobs included, goes on the root observation only)."""
    meta = {"ticker": ticker.upper(), "as_of": str(as_of), "run_kind": run_kind,
            "pipeline_version": str(prov["pipeline_version"]),
            "detection_version": str(prov["detection_version"]),
            "git_revision": str(prov["git_revision"])}
    all_tags = [f"pipeline:{prov['pipeline_version']}", f"detection:{prov['detection_version']}",
                f"run:{run_kind}", *tags]
    return {"trace_name": f"longterm_run {ticker.upper()}", "session_id": session_id,
            "tags": all_tags, "metadata": meta, "version": str(prov["pipeline_version"])}


def decision(final: dict) -> dict:
    """The run's outcome as the trace output: small, readable, no state dump."""
    if not isinstance(final, dict):
        return {}
    if final.get("no_data"):
        return {"no_data": True, "article_count": final.get("article_count")}
    keep = ("direction", "conviction", "horizon", "thesis", "invalidation_conditions")
    sig = final.get("signal") or {}
    gate = final.get("gate_trace") or {}
    shift = final.get("background_shift") or {}
    return {"signal": {k: sig.get(k) for k in keep if k in sig},
            "gate": {k: gate.get(k) for k in ("passed", "conviction_raw", "conviction_final",
                                              "gate_mode", "threshold") if k in gate},
            "risk_flags": final.get("risk_flags") or [],
            "shift": {k: shift.get(k) for k in ("has_shifted", "direction") if k in shift}}


class _Run:
    def __init__(self, config: dict, span=None, trace_id: str = None):
        self.config = config
        self.trace_id = trace_id
        self._span = span

    def end(self, final):
        if self._span is not None:
            try:
                self._span.update(output=decision(final))
            except Exception as e:
                print(f"  ⚠ trace output not recorded: {e}")


@contextmanager
def agent_run(ticker: str, as_of, cfg, *, session_id: str, run_kind: str = "live",
              tags=()):
    """Wrap ONE graph.invoke. Yields an object with `.config` for graph.invoke and
    `.end(final)` to record the decision. Tracing off → config is {} and end() is a no-op,
    so call sites need no branches."""
    if _client is None:
        yield _Run({})
        return
    from langfuse import propagate_attributes
    from langfuse.langchain import CallbackHandler

    prov = cfg.provenance()
    trace_id = run_trace_id(run_kind, prov["detection_version"], ticker, as_of)
    with _client.start_as_current_observation(
            name="longterm_run", as_type="agent", trace_context={"trace_id": trace_id},
            input={"ticker": ticker.upper(), "as_of": str(as_of)},
            metadata={"provenance": prov}) as span:
        with propagate_attributes(**trace_attributes(ticker, as_of, prov, run_kind,
                                                     session_id, tags)):
            yield _Run({"callbacks": [CallbackHandler()], "run_name": "longterm_graph"},
                       span=span, trace_id=trace_id)
