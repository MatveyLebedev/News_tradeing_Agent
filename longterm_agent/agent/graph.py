"""
LangGraph wiring for the long-term agent (Phase 4 — full pipeline).

  corpus_collection ──(no_data?)──► END
                     └► background_profiling ► shift_detection ► fundamentals_analysis
                        ► similar_shift_analysis ► synthesis ► reflection ──(loop?)──┐
                                                                  │                  │
                                                                  ▼                  │
                                                              risk_gate ► END        │
                        ▲──────────── loop_to: shift_detection / fundamentals_analysis ┘
"""
from typing import Any
from langgraph.graph import StateGraph, END

from ..state import LongTermAgentState
from .nodes import (
    corpus_collection_node, background_profiling_node, shift_detection_node,
    fundamentals_analysis_node, similar_shift_analysis_node, synthesis_node,
    reflection_node, risk_gate_node,
)


def _route_after_corpus(state: LongTermAgentState) -> str:
    return "no_data" if state.get("no_data") else "profile"


def _route_after_reflection(state: LongTermAgentState) -> str:
    # reflection_node already forces needs_refinement=False once max iterations are hit,
    # so checking the flag + a valid loop target is sufficient to bound the loop.
    if state.get("needs_refinement"):
        loop_to = state.get("loop_to")
        if loop_to in ("shift_detection", "fundamentals_analysis"):
            return loop_to
    return "proceed"


def create_longterm_graph(llm_map: Any, llm_reduce: Any, config: Any, db_manager: Any, neo4j_manager: Any = None):
    g = StateGraph(LongTermAgentState)

    g.add_node("corpus_collection",
               lambda s: corpus_collection_node(s, db_manager, neo4j_manager, config))
    g.add_node("background_profiling",
               lambda s: background_profiling_node(s, llm_map, llm_reduce, config, db_manager))
    g.add_node("shift_detection",
               lambda s: shift_detection_node(s, llm_reduce, config, neo4j_manager))
    g.add_node("fundamentals_analysis",
               lambda s: fundamentals_analysis_node(s, llm_reduce, db_manager, config))
    g.add_node("similar_shift_analysis",
               lambda s: similar_shift_analysis_node(s, db_manager, config))
    g.add_node("synthesis",
               lambda s: synthesis_node(s, llm_reduce, config))
    g.add_node("reflection",
               lambda s: reflection_node(s, llm_reduce, config))
    g.add_node("risk_gate",
               lambda s: risk_gate_node(s, db_manager, config))

    g.set_entry_point("corpus_collection")
    g.add_conditional_edges("corpus_collection", _route_after_corpus,
                            {"no_data": END, "profile": "background_profiling"})
    g.add_edge("background_profiling", "shift_detection")
    g.add_edge("shift_detection", "fundamentals_analysis")
    g.add_edge("fundamentals_analysis", "similar_shift_analysis")
    g.add_edge("similar_shift_analysis", "synthesis")
    g.add_edge("synthesis", "reflection")
    g.add_conditional_edges("reflection", _route_after_reflection, {
        "shift_detection": "shift_detection",
        "fundamentals_analysis": "fundamentals_analysis",
        "proceed": "risk_gate",
    })
    g.add_edge("risk_gate", END)

    compiled = g.compile()
    print("✓ Long-term agent graph compiled (Phase 4: full pipeline with reflection loop)")
    return compiled
