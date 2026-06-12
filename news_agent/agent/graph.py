"""
LangGraph definition for the trading agent - Message-Based Architecture

Note: base_context and significance_check are NOT in this graph — they are pre-run
in TradingAgent.run() with LangSmith tracing disabled, so non-significant news
produce zero LangSmith traces. State entering this graph already has recent_news,
similar_news, and is_significant populated.
"""
from typing import Any
from langgraph.graph import StateGraph, END

from .state import TradingAgentState
from .nodes import (
    macro_context_node,
    planning_node,
    main_analysis_node,
    reflection_node,
    risk_assessment_node,
)


def should_reflect(state: TradingAgentState) -> str:
    """Decide next step after reflection."""
    needs_refinement = state.get("needs_refinement", False)
    reflection_count = state.get("reflection_count", 0)
    max_iterations = state.get("max_iterations", 5)

    if reflection_count >= max_iterations:
        return "proceed"

    if needs_refinement:
        loop_to = state.get("loop_to", "main_analysis")
        if loop_to == "planning":
            return "replan"
        return "refine_analysis"

    return "proceed"


def create_trading_graph(
    llm: Any,
    config: Any,
    db_manager: Any,
    neo4j_manager: Any = None,
) -> StateGraph:
    """
    Create the trading agent graph (main analysis only).

    Pre-graph (run untraced in TradingAgent.run()):
        base_context → significance_check → [if not significant → early return]

    Graph (this function, traced):
        build_macro_context → planning → main_analysis → reflection → risk_assessment → END
                                       ↑                       ↓
                                       └─── (reflection loop) ─┘
    """
    workflow = StateGraph(TradingAgentState)

    workflow.add_node(
        "build_macro_context",
        lambda state: macro_context_node(state, neo4j_manager),
    )
    workflow.add_node(
        "planning",
        lambda state: planning_node(state, llm),
    )
    workflow.add_node(
        "main_analysis",
        lambda state: main_analysis_node(state, llm, db_manager, config, neo4j_manager),
    )
    workflow.add_node(
        "reflection",
        lambda state: reflection_node(state, llm, config),
    )
    workflow.add_node(
        "risk_assessment",
        lambda state: risk_assessment_node(state, db_manager, config),
    )

    workflow.set_entry_point("build_macro_context")
    workflow.add_edge("build_macro_context", "planning")
    workflow.add_edge("planning", "main_analysis")
    workflow.add_edge("main_analysis", "reflection")
    workflow.add_conditional_edges(
        "reflection",
        should_reflect,
        {
            "replan": "planning",
            "refine_analysis": "main_analysis",
            "proceed": "risk_assessment",
        },
    )
    workflow.add_edge("risk_assessment", END)

    compiled_graph = workflow.compile()

    print("✓ Trading agent graph compiled successfully")
    print("  Pre-graph (untraced): base_context → significance_check")
    print("  Graph (traced):       build_macro_context → planning → main_analysis → reflection → risk_assessment → END")
    print("  Note: graph runs only when classifier marks news as significant")
    return compiled_graph
