"""Strong-model split (STRONG_LLM_MODEL): role fallback, provenance stamp, graph wiring.

The split may upgrade ONLY the three judgment-heavy advisory calls (synthesis,
fundamentals verdict, deep-analysis reduce); with the role unset everything must be
byte-identical to the pre-split build — same client object, same cache keys.
"""
from types import SimpleNamespace

from longterm_agent.agent.graph import create_longterm_graph


def test_strong_role_defaults_to_main_model():
    from core.settings import get_settings
    from longterm_agent.config import LongTermAgentConfig as cfg
    if not get_settings().strong_llm_model:
        assert cfg.STRONG_LLM_MODEL == cfg.LLM_MODEL


def test_provenance_stamps_strong_model():
    from longterm_agent.config import LongTermAgentConfig as cfg
    assert cfg.provenance()["strong_model"] == cfg.STRONG_LLM_MODEL


def test_graph_builds_strong_llm_only_when_role_differs():
    calls = []
    cfg = SimpleNamespace(
        LLM_MODEL="model/base", STRONG_LLM_MODEL="model/strong",
        make_llm=lambda model=None, max_tokens=None: calls.append((model, max_tokens)) or "strong-llm")
    create_longterm_graph(llm_map=None, llm_reduce=None, config=cfg, db_manager=None)
    assert calls == [("model/strong", 8000)]


def test_graph_reuses_reduce_llm_when_role_matches():
    def boom(**_):
        raise AssertionError("make_llm must not be called when the roles match")
    cfg = SimpleNamespace(LLM_MODEL="model/base", STRONG_LLM_MODEL="model/base", make_llm=boom)
    create_longterm_graph(llm_map=None, llm_reduce=None, config=cfg, db_manager=None)


def test_graph_tolerates_config_without_role():
    # e.g. the SimpleNamespace stubs in test_phase8 — absent attrs mean "split off".
    create_longterm_graph(llm_map=None, llm_reduce=None, config=SimpleNamespace(), db_manager=None)
