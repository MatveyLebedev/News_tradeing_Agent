"""core.tracing: Langfuse must never change what a run computes, only observe it."""
from datetime import datetime

import pytest

from core import tracing


class _Cfg:
    @staticmethod
    def provenance():
        return {"detection_version": "3.1", "pipeline_version": "0.11.2", "git_revision": "abc"}


def test_disabled_tracing_is_a_transparent_no_op(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None)
    with tracing.agent_run("NVDA", datetime(2026, 11, 1, 6), _Cfg(), session_id="s") as run:
        assert run.config == {}          # graph.invoke(init, config={}) == graph.invoke(init)
        run.end({"signal": {"direction": "LONG"}})   # must not raise


def test_trace_id_is_keyed_by_cohort_date_not_clock_time():
    a = tracing.run_trace_id("live", "3.1", "nvda", datetime(2026, 11, 1, 6, 0))
    b = tracing.run_trace_id("live", "3.1", "NVDA", datetime(2026, 11, 1, 6, 7, 31))
    assert a == b and len(a) == 32
    # A hand run, another backtest or another detection version never share a trace.
    assert a != tracing.run_trace_id("manual", "3.1", "NVDA", datetime(2026, 11, 1))
    assert a != tracing.run_trace_id("monday_t1_fwd", "3.1", "NVDA", datetime(2026, 11, 1))
    assert a != tracing.run_trace_id("live", "3.2", "NVDA", datetime(2026, 11, 1))


def test_mask_drops_article_text_but_keeps_ids():
    state = {"ticker": "NVDA",
             "news_corpus": [{"id": 1, "title": "t", "summary": "long text"}, {"id": 2}],
             "nested": [{"news_corpus": [{"id": 3}]}]}
    out = tracing._compact(data=state)
    assert out["news_corpus"] == {"n_articles": 2, "ids": [1, 2]}
    assert out["nested"][0]["news_corpus"] == {"n_articles": 1, "ids": [3]}
    assert out["ticker"] == "NVDA" and state["news_corpus"][0]["summary"] == "long text"


def test_callbacks_do_not_change_llm_cache_keys():
    """Invariant 4 (MODIFICATION_PLAN): the content-addressed LLM cache keys on prompt +
    llm_string. A tracing callback rides in the invoke config and must touch neither."""
    pytest.importorskip("langchain_core")
    from langchain_core.caches import InMemoryCache
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.globals import get_llm_cache, set_llm_cache
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    class Recording(InMemoryCache):
        def __init__(self):
            super().__init__()
            self.keys = []

        def lookup(self, prompt, llm_string):
            self.keys.append((prompt, llm_string))
            return None

    class Spy(BaseCallbackHandler):
        def __init__(self):
            self.calls = 0

        def on_chat_model_start(self, *a, **k):
            self.calls += 1

    prev = get_llm_cache()
    cache = Recording()
    set_llm_cache(cache)
    try:
        llm = FakeListChatModel(responses=["x", "x"], cache=None)
        llm.invoke("same prompt")
        spy = Spy()
        llm.invoke("same prompt", config={"callbacks": [spy], "run_name": "longterm_graph",
                                          "metadata": {"langfuse_session_id": "s"}})
    finally:
        set_llm_cache(prev)
    assert spy.calls == 1
    assert len(cache.keys) == 2 and cache.keys[0] == cache.keys[1]
