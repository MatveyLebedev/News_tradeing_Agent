"""
LangGraph wiring for the long-term agent (Phase 8 — deltas + parallel branches).

  corpus_collection ──(no_data?)──► END
                     └► background_profiling ► shift_detection ─┬► fundamentals_analysis ─┐
                                                                └► similar_shift_analysis ┴─(join)► deep_analysis
                        ► synthesis ► reflection ──(loop?)─► risk_gate ► END
                              ▲   loop_to shift_detection ──► shift_detection (re-fans both branches)
                              └── loop_to fundamentals_analysis ──► refine_fanout ─┬► fundamentals_analysis
                                                                                   └► similar_shift_analysis

Phase 8 mechanics: nodes return DELTAS; `messages` merges via operator.add (see state.py),
so the two parallel branches can both append without conflict. deep_analysis joins on a
BARRIER edge ([fundamentals, analogs] → deep) — which is why the reflection loop to
fundamentals goes through `refine_fanout`: re-running only one branch would leave the
barrier waiting forever. The passthrough re-fans BOTH (the analogs branch is
deterministic + DB-only, so the re-run costs no LLM calls).
"""
from typing import Any
from langgraph.graph import StateGraph, END

from ..state import LongTermAgentState
from .nodes import (
    corpus_collection_node, background_profiling_node, shift_detection_node,
    fundamentals_analysis_node, similar_shift_analysis_node, deep_analysis_node,
    synthesis_node, reflection_node, risk_gate_node,
)


def _route_after_corpus(state: LongTermAgentState) -> str:
    return "no_data" if state.get("no_data") else "profile"


def _route_after_reflection(state: LongTermAgentState) -> str:
    # reflection_node already forces needs_refinement=False once max iterations are hit,
    # so checking the flag + a valid loop target is sufficient to bound the loop.
    if state.get("needs_refinement"):
        loop_to = state.get("loop_to")
        if loop_to == "shift_detection":
            return "shift_detection"
        if loop_to == "fundamentals_analysis":
            return "refine_fanout"
    return "proceed"


def create_longterm_graph(llm_map: Any, llm_reduce: Any, config: Any, db_manager: Any,
                          neo4j_manager: Any = None, llm_strong: Any = None):
    # Strong-model split (STRONG_LLM_MODEL, default = LLM_MODEL → this stays llm_reduce):
    # only the three judgment-heavy nodes below get it — synthesis, the fundamentals
    # verdict, and the deep-analysis REDUCE (deep's map threads stay on llm_map). All
    # three are advisory-path, so the split can never move a detection label.
    if llm_strong is None:
        _strong = getattr(config, "STRONG_LLM_MODEL", "") or ""
        if _strong and _strong != getattr(config, "LLM_MODEL", ""):
            llm_strong = config.make_llm(model=_strong, max_tokens=8000)
        else:
            llm_strong = llm_reduce

    g = StateGraph(LongTermAgentState)

    g.add_node("corpus_collection",
               lambda s: corpus_collection_node(s, db_manager, neo4j_manager, config))
    g.add_node("background_profiling",
               lambda s: background_profiling_node(s, llm_map, llm_reduce, config, db_manager))
    g.add_node("shift_detection",
               lambda s: shift_detection_node(s, llm_reduce, config, neo4j_manager))
    g.add_node("fundamentals_analysis",
               lambda s: fundamentals_analysis_node(s, llm_strong, db_manager, config))
    g.add_node("similar_shift_analysis",
               lambda s: similar_shift_analysis_node(s, db_manager, config))
    g.add_node("deep_analysis",
               lambda s: deep_analysis_node(s, llm_map, llm_strong, db_manager, neo4j_manager, config))
    g.add_node("synthesis",
               lambda s: synthesis_node(s, llm_strong, config))
    g.add_node("reflection",
               lambda s: reflection_node(s, llm_reduce, config))
    g.add_node("risk_gate",
               lambda s: risk_gate_node(s, db_manager, config))
    # Passthrough for the reflection→fundamentals loop: re-fans BOTH parallel branches so
    # the barrier join at deep_analysis is satisfied on the second pass too.
    g.add_node("refine_fanout", lambda s: {})

    g.set_entry_point("corpus_collection")
    g.add_conditional_edges("corpus_collection", _route_after_corpus,
                            {"no_data": END, "profile": "background_profiling"})
    g.add_edge("background_profiling", "shift_detection")
    # Phase 8 fan-out: the two branches are independent (fundamentals/technicals vs analog
    # lookup) — run them in parallel and JOIN at deep_analysis (barrier edge).
    g.add_edge("shift_detection", "fundamentals_analysis")
    g.add_edge("shift_detection", "similar_shift_analysis")
    g.add_edge("refine_fanout", "fundamentals_analysis")
    g.add_edge("refine_fanout", "similar_shift_analysis")
    g.add_edge(["fundamentals_analysis", "similar_shift_analysis"], "deep_analysis")
    g.add_edge("deep_analysis", "synthesis")
    g.add_edge("synthesis", "reflection")
    g.add_conditional_edges("reflection", _route_after_reflection, {
        "shift_detection": "shift_detection",
        "refine_fanout": "refine_fanout",
        "proceed": "risk_gate",
    })
    g.add_edge("risk_gate", END)

    compiled = g.compile()
    print("✓ Long-term agent graph compiled (Phase 8: parallel branches + delta state)")
    return compiled
