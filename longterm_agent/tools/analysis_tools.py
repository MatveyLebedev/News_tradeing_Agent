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
import re
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

# Reject any write/DDL clause AND every procedure CALL. Blocking CALL removes the apoc/dbms
# side-effect surface (apoc.load.* can fetch URLs = SSRF, apoc.export writes files), so only
# pure read traversal (MATCH/WHERE/WITH/RETURN/ORDER BY/LIMIT) gets through. Conservative by
# design: a query that merely *mentions* one of these as a word is rejected (rephrase it).
_BLOCKED_CYPHER = re.compile(
    r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|DETACH|FOREACH|PERIODIC|CALL|LOAD\s+CSV)\b",
    re.IGNORECASE)


def make_graph_query_tool(neo4j_manager: Any, as_of, max_rows: int = 50, max_chars: int = 2500):
    """Factory for a READ-ONLY raw-Cypher tool bound to the graph and the analysis date.

    Read-only is enforced by TWO layers: (1) the blocklist above rejects writes/procedures up
    front, and (2) every query runs inside a transaction that is ALWAYS rolled back, so nothing
    it executes can ever persist — even if a write somehow slipped past the regex.
    """
    as_of_iso = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)

    @tool
    def run_graph_query(cypher: str) -> str:
        """Run a READ-ONLY Cypher query against the news knowledge graph for ad-hoc traversal the
        fixed graph tools don't cover. Pure reads only: MATCH / OPTIONAL MATCH / WHERE / WITH /
        RETURN / ORDER BY / LIMIT. Writes (CREATE/MERGE/DELETE/SET/REMOVE/…) and procedure CALLs
        are rejected.

        NO-LOOKAHEAD: the analysis date is bound as $as_of — always gate events with
        `WHERE e.date <= datetime($as_of)` so you never read the future.

        SCHEMA
          (:Company {ticker})
          (:Event {id, type, name, description, date})       # date is a neo4j datetime
            type ∈ earnings, product_launch, merger_acquisition, leadership_change, legal,
                   regulatory, corporate_announcement, geopolitical_conflict, macro_economic,
                   commodity_price, trade_policy, market_move, other
          (:Article {news_id})
          (:Story {key, domain, start_date, status})
          (Event)-[:AFFECTS {sentiment, confidence, move_1d, move_4h, move_40m, move_15m}]->(Company)
          (Event)-[:SOURCED_FROM]->(Article)
          (Event)-[:CAUSES|RELATED_TO]-(Event)

        EXAMPLES
          // earnings events on a peer, newest first, with the 1-day price reaction
          MATCH (e:Event {type:'earnings'})-[r:AFFECTS]->(:Company {ticker:'META'})
          WHERE e.date <= datetime($as_of)
          RETURN e.name, e.date, r.sentiment, r.move_1d ORDER BY e.date DESC LIMIT 10

          // a regulatory event on GOOGL: what caused it and which peers it also hit
          MATCH (e:Event {type:'regulatory'})-[:AFFECTS]->(:Company {ticker:'GOOGL'})
          WHERE e.date <= datetime($as_of)
          OPTIONAL MATCH (cause:Event)-[:CAUSES]->(e)
          OPTIONAL MATCH (e)-[:AFFECTS]->(peer:Company)
          RETURN e.name, collect(distinct cause.name) AS causes,
                 collect(distinct peer.ticker) AS also_hit LIMIT 10

          // events the graph extracted from a specific article id (from search_company_news)
          MATCH (a:Article {news_id:123})<-[:SOURCED_FROM]-(e:Event)-[:AFFECTS]->(c:Company)
          RETURN e.type, e.name, collect(c.ticker) AS affects LIMIT 10

        Returns up to 50 rows as JSON. Args: cypher — one read-only Cypher statement."""
        if not cypher or not cypher.strip():
            return json.dumps({"error": "empty query"})
        if _BLOCKED_CYPHER.search(cypher):
            return json.dumps({"error": "rejected: READ-ONLY tool — no writes (CREATE/MERGE/"
                                        "DELETE/SET/REMOVE/…) or procedure CALLs allowed"})
        driver = getattr(neo4j_manager, "_driver", None)
        if driver is None:
            return json.dumps({"error": "graph unavailable"})
        try:
            with driver.session() as session:
                tx = session.begin_transaction(timeout=15)
                try:
                    result = tx.run(cypher, {"as_of": as_of_iso})
                    rows = []
                    for i, record in enumerate(result):
                        if i >= max_rows:
                            break
                        rows.append(dict(record))
                finally:
                    tx.rollback()  # ALWAYS rollback — nothing this tool runs can persist
            out = json.dumps({"n": len(rows), "rows": rows}, default=str)
            return out if len(out) <= max_chars else out[:max_chars] + "…[truncated]"
        except Exception as e:
            return json.dumps({"error": str(e)[:300]})

    return run_graph_query


def build_deep_analysis_tools(
    ticker: str, as_of, db_manager: Any, neo4j_manager: Any, config: Any,
    shift: Optional[dict] = None, metrics: Optional[dict] = None,
) -> List[Any]:
    """Assemble the read-only @tool list for one run. `shift`/`metrics` close over the
    deterministic outputs so the analog tool can resolve the CURRENT shift's neighbors."""
    tk = ticker.upper()

    @tool
    def get_fundamentals_detail(ticker: str = None, quarters: int = 4) -> str:
        """Point-in-time fundamentals for a company — DEFAULTS to the company under analysis, but
        pass a PEER/competitor ticker to compare its numbers. Revenue YoY/QoQ, the quarterly
        margin series (gross/operating/net, newest→oldest), valuation multiples, free cash flow,
        debt, and the analyst estimate-revision gap. Use to drill the quarters behind the shift
        and to contrast against peers. Args: ticker — company to read (default: the one under
        analysis); quarters — how many recent quarters of margins to include (1-6)."""
        t = (ticker or tk).upper()
        f = dict(read_fundamentals(db_manager, t, as_of))
        f["margin_series"] = (f.get("margin_series") or [])[:max(1, min(quarters, 6))]
        return json.dumps(f, default=str)

    @tool
    def get_long_term_technicals(ticker: str = None) -> str:
        """Long-term daily technicals as-of the analysis date for a company — DEFAULTS to the
        company under analysis; pass a PEER ticker to compare. Trend (uptrend/downtrend/mixed),
        window return %, MA50/MA200. Use to check whether multi-month price action confirms or
        diverges from the news + fundamentals read. Args: ticker — company (default: under analysis)."""
        t = (ticker or tk).upper()
        return json.dumps(
            compute_daily_technicals(db_manager, t, as_of, config.LOOKBACK_DAYS_TECHNICAL_LT),
            default=str)

    @tool
    def search_company_news(query: str, ticker: str = None, limit: int = 8) -> str:
        """Re-query a company's point-in-time news corpus for headlines whose title or summary
        mention the query terms — DEFAULTS to the company under analysis; pass a PEER ticker to
        research its news instead. Use to chase a specific thread (e.g. 'export controls',
        'guidance cut') for this company or a peer. Args: query — words to match (space-separated,
        OR); ticker — company (default: under analysis); limit — max headlines (1-20)."""
        t = (ticker or tk).upper()
        corpus = get_company_news_corpus(
            t, as_of, config.LOOKBACK_DAYS_BACKGROUND, db_manager,
            neo4j_manager, config.AFFECTS_MIN_CONFIDENCE)
        terms = [w.lower() for w in query.split() if w.strip()]
        hits = []
        for a in corpus.get("articles", []):
            text = f"{a.get('title','')} {a.get('summary','')}".lower()
            if not terms or any(term in text for term in terms):
                hits.append({"published": str(a.get("published")), "title": a.get("title")})
        hits = hits[-max(1, min(limit, 20)):]
        return json.dumps({"ticker": t, "query": query, "n": len(hits), "headlines": hits}, default=str)

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
            db_manager, tk, as_of, config.DETECTION_VERSION,
            direction=s.get("direction"), target_z=s.get("magnitude_z"),
            causal_types=causal, k=6, min_analogs=getattr(config, "MIN_ANALOGS", 5))
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
        tools.append(make_graph_query_tool(neo4j_manager, as_of))  # raw read-only Cypher

    return tools
