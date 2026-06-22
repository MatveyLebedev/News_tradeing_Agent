"""
Read-only toolset for the deep_analysis node (Phase 7A — see DEEP_ANALYSIS_PLAN.md §3).

Wraps existing long-term + graph functions as LangChain @tools so the LLM can CHOOSE what
to investigate per company (adaptive depth) instead of every ticker getting the identical
fixed analysis. Two hard properties:

  * as-of aware — every tool reads point-in-time (the graph tools take `as_of` as their
    `timestamp` closure), so the no-lookahead discipline carries over for free.
  * read-only — no tool mutates state or places a trade. Safety by construction.

The node finalizes to DeepAnalysisOutput; nothing here can change the deterministic shift
label (that boundary is enforced in nodes.deep_analysis_node, not in the tools).
"""
import json
from typing import Any, List, Optional

from langchain_core.tools import tool

from .fundamentals_tool import read_fundamentals
from .technical_tool import compute_daily_technicals
from .news_corpus_tool import get_company_news_corpus
from .shift_library_tool import find_similar_shifts

# Curated subset of create_graph_tools we expose (the rest — price/validate/macro replay —
# aren't useful for multi-week background reasoning and would only clutter the toolset).
_GRAPH_TOOL_ALLOW = {
    "get_causal_chain_with_prices", "get_cross_ticker_correlation",
    "get_story_context", "get_story_momentum", "get_events_for_ticker",
    "get_novelty_score",
}


def build_deep_analysis_tools(
    ticker: str, as_of, db_manager: Any, neo4j_manager: Any, config: Any,
    shift: Optional[dict] = None, metrics: Optional[dict] = None,
) -> List[Any]:
    """Assemble the read-only @tool list for one run. `shift`/`metrics` close over the
    deterministic outputs so the analog tool can resolve the CURRENT shift's neighbors."""
    tk = ticker.upper()

    @tool
    def get_fundamentals_detail(quarters: int = 4) -> str:
        """Point-in-time fundamentals for the company: revenue YoY/QoQ, the quarterly margin
        series (gross/operating/net, newest→oldest), valuation multiples, free cash flow,
        debt, and the analyst estimate-revision gap. Use to drill into the quarters behind
        the shift. Args: quarters — how many recent quarters of margins to include (1-6)."""
        f = dict(read_fundamentals(db_manager, tk, as_of))
        f["margin_series"] = (f.get("margin_series") or [])[:max(1, min(quarters, 6))]
        return json.dumps(f, default=str)

    @tool
    def get_long_term_technicals() -> str:
        """Long-term daily technicals as-of the analysis date: trend (uptrend/downtrend/
        mixed), window return %, MA50/MA200. Use to check whether multi-month price action
        confirms or diverges from the news + fundamentals read."""
        return json.dumps(
            compute_daily_technicals(db_manager, tk, as_of, config.LOOKBACK_DAYS_TECHNICAL_LT),
            default=str)

    @tool
    def search_company_news(query: str, limit: int = 8) -> str:
        """Re-query this company's point-in-time news corpus for headlines whose title or
        summary mention the query terms (space-separated, OR semantics). Use to chase a
        specific thread (e.g. 'export controls', 'guidance cut'). Returns the most recent
        matches. Args: query — words to match; limit — max headlines (1-20)."""
        corpus = get_company_news_corpus(
            tk, as_of, config.LOOKBACK_DAYS_BACKGROUND, db_manager,
            neo4j_manager, config.AFFECTS_MIN_CONFIDENCE)
        terms = [w.lower() for w in query.split() if w.strip()]
        hits = []
        for a in corpus.get("articles", []):
            text = f"{a.get('title','')} {a.get('summary','')}".lower()
            if not terms or any(t in text for t in terms):
                hits.append({"published": str(a.get("published")), "title": a.get("title")})
        hits = hits[-max(1, min(limit, 20)):]
        return json.dumps({"query": query, "n": len(hits), "headlines": hits}, default=str)

    @tool
    def find_historical_analogs() -> str:
        """Closest graded historical shifts that resemble the CURRENT detected shift (same
        direction, similar magnitude/cause) with their realized 4-week base rate (median
        return, share positive). Use to weigh what past analogs imply. Empty = cold start
        (no graded analogs yet)."""
        s = shift or {}
        if not s.get("has_shifted"):
            return json.dumps({"n_analogs": 0, "note": "no shift → no analog lookup"})
        causal = [x.get("label") for x in (metrics or {}).get("new_stories", [])][:8]
        res = find_similar_shifts(
            db_manager, tk, as_of, config.AGENT_VERSION,
            direction=s.get("direction"), target_z=s.get("magnitude_z"),
            causal_types=causal, k=6)
        return json.dumps(res, default=str)

    tools: List[Any] = [
        get_fundamentals_detail, get_long_term_technicals,
        search_company_news, find_historical_analogs,
    ]

    # Sector/competitor read — reuse the short-term agent's as-of-aware factory verbatim.
    try:
        from news_agent.tools.sector_competitor_tool import create_analyze_sector_tool
        tools.append(create_analyze_sector_tool(as_of, db_manager))
    except Exception as e:
        print(f"  ⚠ sector tool unavailable for deep_analysis: {e}")

    # Curated graph subset (as-of aware) — only when the graph is reachable.
    if neo4j_manager is not None:
        try:
            from news_agent.tools.graph_tools import create_graph_tools
            for t in create_graph_tools(neo4j_manager, db_manager, as_of):
                if t.name in _GRAPH_TOOL_ALLOW:
                    tools.append(t)
        except Exception as e:
            print(f"  ⚠ graph tools unavailable for deep_analysis: {e}")

    return tools
