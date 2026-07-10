"""
Processing nodes for the trading agent graph - Message-Based Architecture

All nodes work with message-based state and LLM orchestration
"""
from typing import Dict, Any, List, Optional
from langsmith import traceable
from langchain_core.messages import SystemMessage, ToolMessage
import json
import re

from .state import TradingAgentState
from .context_builder import (
    add_message,
    format_plan_for_llm
)
from ..llm_errors import raise_if_infra
from ..schemas import PlanningOutput, MainAnalysisOutput, ReflectionOutput, SignificanceOutput, SimilarNewsTickerExtraction, PrimaryTickerExtraction
from ..tools import (
    get_recent_news_items,
    get_historical_prices,
    calculate_technical_indicators,
    assess_risk,
    create_find_similar_news_tool,
    create_find_similar_news_with_price_tool,
    create_ticker_analysis_tool,
    create_ticker_historical_context_tool,
    create_get_recent_news_tool,
    create_get_price_data_tool,
    create_get_price_at_time_tool,
    create_get_technical_indicators_tool,
    create_analyze_sector_tool,
    create_check_risk_tool,
    create_validate_ticker_tool,
    create_graph_tools,
)
from ..tools.find_similar_news_tool import find_similar_news, enrich_similar_news_with_reaction, find_similar_news_with_price_impact, compute_typical_move_pct
from ..tools.pre_news_movement_tool import check_multiple_tickers_pre_news, create_get_pre_news_movement_tool


# --- Tradability gate -------------------------------------------------------
# ETFs / indices / leveraged products we never want as single-name signals.
_NON_TRADABLE_TICKERS = {
    "SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "XLE", "XLF", "XLK", "XLV",
    "USO", "UNG", "GLD", "SLV", "TLT", "IEF", "HYG", "LQD", "EEM", "EFA",
    "VXX", "UVXY", "SQQQ", "TQQQ", "SOXL", "SOXS", "ARKK",
}


def _is_tradable(ticker: Optional[str], db_manager: Any) -> bool:
    """True if ticker is a US single-name and not an ETF/index/leveraged product.

    NOTE: we intentionally do NOT require a local historical_data_* table here.
    Local OHLC coverage is tiny (~120 tickers) and was dropping liquid names like
    DELL/MSTR/FLNC. Backtests now price missing tickers via the Yahoo fallback, so
    the gate only filters non-tradable instruments, not "tickers we lack data for".
    """
    if not ticker:
        return False
    tk = ticker.upper().strip()
    if tk in _NON_TRADABLE_TICKERS:
        return False
    # US single-name shape: 1-5 letters, optional .A/.B class suffix. Excludes
    # foreign/exchange-suffixed symbols (e.g. 7203.T, BMW.DE) and index codes (^GSPC).
    if not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", tk):
        return False
    return True


def _dedupe_conflicting_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If the same ticker has both buy and sell, keep only the highest-confidence one."""
    by_ticker: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for s in signals:
        tk = (s.get("ticker") or "").upper()
        if not tk:
            out.append(s)
            continue
        prev = by_ticker.get(tk)
        if prev is None:
            by_ticker[tk] = s
        elif s.get("confidence", 0) > prev.get("confidence", 0):
            by_ticker[tk] = s
    out.extend(by_ticker.values())
    return out


# --- Anti-chase guard -------------------------------------------------------
# Headlines that announce a move that ALREADY happened. Trading the SAME direction
# (buy after a surge, sell after a plunge) chases a move that is usually largely
# priced-in within the 40-min window — a top source of losing trades
# (e.g. "Dell skyrockets 32%", "Cerebras surges 81%", "Zoom jumps").
_UP_MOVE_RE = re.compile(
    r"\b(skyrocket|soar|surg|spik|rocket|jump|rall|pop|gain|rise|climb|rebound|rais)\w*", re.I
)
_DOWN_MOVE_RE = re.compile(
    r"\b(plung|tumbl|sink|slump|crash|plummet|slid|drop|fall|fell|sank|tank|nosediv)\w*", re.I
)
_STRONG_MOVE_RE = re.compile(
    r"\b(skyrocket\w*|soar\w*|surg\w*|plung\w*|tumbl\w*|crash\w*|plummet\w*|rocket\w*|"
    r"spike[ds]?|best day|worst day|record (?:high|day)|all[\s-]?time high)\b", re.I
)
_MOVE_PCT_RE = re.compile(r"\d{1,3}(?:\.\d+)?\s?%")


def _already_moved_direction(news_text: str) -> Optional[str]:
    """Return 'up'/'down' if the headline announces a move that already happened.

    Fires only when there's a strong move verb (skyrocket/soar/surge/plunge/...) OR
    a move verb plus a percentage magnitude, so neutral headlines don't trigger.
    Returns None when ambiguous (both directions present) or no clear move.
    """
    head = (news_text or "")[:200]
    if not (_STRONG_MOVE_RE.search(head) or _MOVE_PCT_RE.search(head)):
        return None
    up = bool(_UP_MOVE_RE.search(head))
    down = bool(_DOWN_MOVE_RE.search(head))
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return None


@traceable(name="base_context_node", run_type="chain")
def base_context_node(state: TradingAgentState, db_manager: Any, config: Any) -> TradingAgentState:
    """
    Step 1: Retrieve base context before planning
    Gets 10 recent news and 10 similar historical news
    
    Args:
        state: Current agent state
        db_manager: Database manager instance
        config: Agent configuration
        
    Returns:
        Updated state with recent_news and similar_news
    """
    news_text = state["news_text"]
    timestamp = state["timestamp"]
    
    print("\n=== Base Context Retrieval ===")
    
    # Initialize messages if not present
    if "messages" not in state:
        state["messages"] = []
    
    # Add system message
    add_message(state, "system", f"Analyzing news: {news_text[:100]}...", data={"timestamp": timestamp})
    
    # Get 10 most recent news
    print("  → Fetching 10 most recent news...")
    recent_news = get_recent_news_items(
        timestamp=timestamp,
        db_manager=db_manager,
        hours=24,
        limit=10
    )
    state["recent_news"] = recent_news
    
    # Fetch a wider candidate pool of similar historical news. The significance
    # classifier filters this down to items with KNOWN market reactions; downstream
    # nodes (planning/main_analysis) slice the list themselves as needed.
    print("  → Finding 30 similar historical news (candidate pool)...")
    similar_news = find_similar_news(
        news_text=news_text,
        timestamp=timestamp,
        db_manager=db_manager,
        top_k=30,
    )
    state["similar_news"] = similar_news
    
    # Add message about context retrieval
    context_summary = f"Retrieved {len(recent_news)} recent news items and {len(similar_news)} similar historical events"
    add_message(
        state,
        "assistant",
        context_summary,
        name="base_context",
        data={"recent_count": len(recent_news), "similar_count": len(similar_news)}
    )
    
    print(f"✓ Base context retrieved: {len(recent_news)} recent, {len(similar_news)} similar")
    
    return state


@traceable(name="significance_check_node", run_type="chain")
def significance_check_node(
    state: TradingAgentState,
    llm: Any,
    db_manager: Any,
    config: Any = None,
) -> TradingAgentState:
    """
    Step 1.5: Classify whether news is a SIGNIFICANT market event.

    Pipeline:
      A. Extract primary ticker per similar news (main `llm`).
      B. Enrich each similar news with 40-min market reaction via DB
         (published <= current news timestamp - 1h, set by base_context_node).
      C. Re-rank: items WITH known reaction first (by similarity), fallback to
         items without — take top 10 for the prompt.
      D. Final classification (main `llm`) using news text + enriched history.

    All LLM calls use the agent's main `llm` (config.LLM_MODEL) for highest quality.
    """
    news_text = state["news_text"]
    timestamp = state["timestamp"]
    similar_news = state.get("similar_news", []) or []

    print("\n=== Significance Check ===")

    # --- A. Same-ticker historical reactions (guaranteed price data) -----------
    # Extract the CURRENT news primary ticker via LLM (same approach as graph DB
    # writes, graph_db.event_extractor) and pull past same-ticker news whose 40m
    # reaction is known. This is the most relevant context and ensures the prompt
    # actually contains reaction data instead of "0 with known reaction".
    primary_ticker: Optional[str] = None
    ticker_examples: List[Dict[str, Any]] = []
    try:
        ext = llm.with_structured_output(PrimaryTickerExtraction).invoke(
            "Identify the single primary publicly-traded US stock ticker this news is about. "
            "Return null for macro/geopolitical/foreign-only news with no clear US-listed ticker.\n\n"
            f"News:\n{news_text[:1500]}"
        )
        primary_ticker = (ext.ticker or "").upper().strip() or None
    except Exception as e:
        print(f"  ⚠️  Primary ticker extraction failed (non-fatal): {e}")

    if primary_ticker:
        try:
            raw = find_similar_news_with_price_impact(
                ticker=primary_ticker,
                search_query=news_text,
                timestamp=timestamp,
                db_manager=db_manager,
                top_k=8,
            )
            for r in raw:
                ticker_examples.append({
                    **r,
                    "ticker": primary_ticker,
                    "price_change_pct": r.get("price_change_pct_40min"),
                })
            print(f"  → Primary ticker {primary_ticker}: {len(ticker_examples)} same-ticker examples with 40m reaction")
        except Exception as e:
            print(f"  ⚠️  Same-ticker reaction lookup failed (non-fatal): {e}")

    # Typical |40m move| per ticker, cached — used to normalize reactions so the LLM
    # can tell whether a move is large *for that ticker* (0.8% on a utility != on a chip stock).
    typical_cache: Dict[str, Optional[float]] = {}

    def typical_move(tk: Optional[str]) -> Optional[float]:
        if not tk:
            return None
        if tk not in typical_cache:
            typical_cache[tk] = compute_typical_move_pct(db_manager, tk, timestamp)
        return typical_cache[tk]

    def fmt_line(n: Dict[str, Any], show_sim: bool = True) -> str:
        title = n.get("title", "")
        ticker = n.get("ticker")
        sim = n.get("similarity")
        sim_s = f"(sim={sim:.2f}) " if (show_sim and sim is not None) else ""
        pct = n.get("price_change_pct")
        if ticker and pct is not None:
            tm = typical_move(ticker)
            norm = f", {abs(pct) / tm:.1f}x typical" if tm else ""
            return (
                f"- [{ticker}] {sim_s}\"{title}\" → 40m: {pct:+.2f}% "
                f"(high {n.get('highest_price_pct', 0):+.2f}%, low {n.get('lowest_price_pct', 0):+.2f}%{norm})"
            )
        if ticker:
            return f"- [{ticker}] {sim_s}\"{title}\" → no price data"
        return f"- {sim_s}\"{title}\" → no ticker detected"

    # --- A2. Recent news for this ticker (chronological) + reactions -----------
    # Latest storyline on the ticker (what's already priced in / ongoing context),
    # independent of semantic similarity to the current news.
    recent_ticker_news: List[Dict[str, Any]] = []
    if primary_ticker:
        try:
            recent_raw = get_recent_news_items(
                timestamp=timestamp, db_manager=db_manager,
                hours=72, ticker=primary_ticker, limit=5,
            )
            recent_ticker_news = enrich_similar_news_with_reaction(
                recent_raw, {n.get("id"): primary_ticker for n in recent_raw}, db_manager
            )
            print(f"  → Recent {primary_ticker} news (72h): {len(recent_ticker_news)}")
        except Exception as e:
            print(f"  ⚠️  Recent ticker news lookup failed (non-fatal): {e}")

    # --- B. Cross-ticker semantic pool (LLM ticker extraction per headline) -----
    ticker_per_id: Dict[int, Optional[str]] = {}
    enriched: List[Dict[str, Any]] = list(similar_news)
    if similar_news:
        try:
            headlines_block = "\n".join(
                f"- id={n.get('id')}: {n.get('title', '')}"
                for n in similar_news
            )
            extract_prompt = (
                "For each headline below, return the primary publicly-traded US stock ticker "
                "mentioned (uppercase, e.g. AAPL). Return null if the headline mentions no "
                "single clear US public-market ticker (macro/geopolitical/private-company news).\n\n"
                f"Headlines:\n{headlines_block}\n\n"
                "Return JSON mapping news_id -> ticker_or_null."
            )
            llm_extract = llm.with_structured_output(SimilarNewsTickerExtraction)
            extraction: SimilarNewsTickerExtraction = llm_extract.invoke(extract_prompt)
            ticker_per_id = {int(k): (v.upper() if v else None) for k, v in extraction.tickers.items()}
            print(f"  → Extracted tickers for {sum(1 for v in ticker_per_id.values() if v)}/{len(similar_news)} similar news")
        except Exception as e:
            print(f"  ⚠️  Ticker extraction failed (non-fatal): {e}")
            ticker_per_id = {}

        try:
            enriched = enrich_similar_news_with_reaction(similar_news, ticker_per_id, db_manager)
        except Exception as e:
            print(f"  ⚠️  Enrichment failed (non-fatal): {e}")
            enriched = list(similar_news)

    # --- C. Merge & rank: same-ticker reactions first, then cross-ticker --------
    # Dedupe by news id so an item isn't shown twice.
    seen_ids = {e.get("id") for e in ticker_examples}
    cross = [n for n in enriched if n.get("id") not in seen_ids]
    with_reaction = [n for n in cross if n.get("price_change_pct") is not None]
    without_reaction = [n for n in cross if n.get("price_change_pct") is None]
    with_reaction.sort(key=lambda n: n.get("similarity", 0.0), reverse=True)
    without_reaction.sort(key=lambda n: n.get("similarity", 0.0), reverse=True)

    PROMPT_LIMIT = 10
    ranked = (ticker_examples + with_reaction + without_reaction)[:PROMPT_LIMIT]
    n_with_reaction = sum(1 for n in ranked if n.get("price_change_pct") is not None)
    print(
        f"  → Context: {len(ticker_examples)} same-ticker + {len(cross)} cross-ticker candidates "
        f"→ top {len(ranked)} to classifier ({n_with_reaction} with known 40m reaction)"
    )

    similar_block = "\n".join(fmt_line(n) for n in ranked) if ranked else "(no similar news available)"
    recent_block = (
        "\n".join(fmt_line(n, show_sim=False) for n in recent_ticker_news)
        if recent_ticker_news else "(none found)"
    )

    # Typical-volatility yardstick for the primary ticker (for normalization context).
    tm_primary = typical_move(primary_ticker)
    vol_note = (
        f"Primary ticker {primary_ticker}: typical |40-min move| ≈ {tm_primary:.2f}% "
        f"(reactions above are also shown as a multiple of this, e.g. '2.0x typical' = unusually large)."
        if (primary_ticker and tm_primary) else
        "(no volatility baseline available for the primary ticker)"
    )

    # Model-aware calibration. deepseek-v4-pro is far more conservative than gemini:
    # taken literally, the "be strict / no history → not significant" framing makes it
    # reject every catalyst (earnings, M&A, macro) when historical analogs are muted or
    # absent. For deepseek we lead with CATALYST TYPE and demote historical reaction to a
    # secondary, non-vetoing signal. Other models keep the original (calibrated) framing.
    is_deepseek = "deepseek" in (getattr(config, "LLM_MODEL", "") or "").lower()

    if is_deepseek:
        strictness_preamble = (
            "Judge PRIMARILY on the strength of the catalyst itself. A clear hard catalyst "
            "(earnings/guidance surprise, M&A, regulatory action, Fed/CPI/rate surprise, "
            "sector-wide sanctions, major product launch/recall) IS significant even when "
            "historical analogs were muted or have NO price data — fresh catalysts often "
            "lack a clean historical match. Use historical reaction only as SECONDARY "
            "supporting evidence; never let muted or missing history veto a genuine catalyst. "
            "Reserve NOT significant for pure commentary/opinion, foreign-only or "
            "private-company news, and already-priced-in repeat coverage."
        )
        decision_rules = """DECISION RULES (catalyst type is the PRIMARY driver):
- If the news fits a SIGNIFICANT category above, classify SIGNIFICANT unless RECENT NEWS shows it is already known / repeatedly covered (already priced in) or it is pure repeat/commentary.
- Historical reaction is SUPPORTING only: a >~1.5x typical past move reinforces significance, but a muted (<1x) or MISSING reaction does NOT by itself make a real catalyst insignificant.
- Express confidence >= 0.7 when a clear hard catalyst is present, even without historical price data."""
    else:
        strictness_preamble = (
            "Be STRICT. When in doubt, classify as NOT significant. The downstream agent is "
            "expensive; only invoke it for news with clear, immediate trading impact."
        )
        decision_rules = """DECISION RULES (judge reactions RELATIVE to the ticker's typical move, not in absolute %):
- Similar past news that moved the stock >~1.5x its typical 40-min move → leans significant.
- Reactions around or below 1x typical (normal noise for that ticker) → leans NOT significant,
  even if the absolute % looks large for a high-volatility name.
- If the RECENT NEWS shows this catalyst is already known / repeatedly covered → likely already
  priced in → leans NOT significant.
- No historical reaction data → require clear fit with a SIGNIFICANT category above; otherwise NOT significant.
- Confidence < 0.7 → you should probably classify as NOT significant."""

    classifier_prompt = f"""TASK: Classify whether this news is a SIGNIFICANT market-moving event for US stock prices in the next 40 minutes.

{strictness_preamble}

NEWS:
{news_text}

VOLATILITY BASELINE (for normalization):
{vol_note}

RECENT NEWS FOR THIS TICKER (last 72h, chronological — what is already known / priced in):
{recent_block}

HISTORICAL CONTEXT — past similar news with KNOWN market reaction (40-min window):
{similar_block}

CLASSIFY AS SIGNIFICANT ONLY IF news is a hard catalyst with immediate price impact, e.g.:
  - Earnings release / guidance change for a specific US-listed company
  - M&A announcement (deal price, regulatory block, hostile bid)
  - Product launch/recall with material revenue impact
  - Regulatory action (SEC, FDA, DOJ) targeting a specific US-listed company
  - Macro/Fed action with direct US-market impact (rate decision, CPI surprise, major policy shift)
  - Geopolitical event with clear, immediate US-market linkage (war escalation, sanctions on a sector)
  - Executive change at a mega-cap or critical insider trading event
  - Court ruling materially affecting a public company

CLASSIFY AS NOT SIGNIFICANT:
  - Opinion pieces, analyst commentary, retrospectives, "what does it mean" articles
  - Generic market commentary or trend pieces without new information
  - Foreign-only company news with no US-listed equivalent (ADR or correlated US peer)
  - Lifestyle / human-interest / consumer-trend articles
  - Indirect or speculative impact ("could affect", "may benefit")
  - Repeat coverage of already-known information
  - Historical context articles or roundups of multiple unrelated events

{decision_rules}

historical_reaction_summary RULES:
- Only describe reactions for tickers that actually appear in the HISTORICAL CONTEXT block above.
- Do NOT invent magnitudes or reference a different company. If no reaction data is shown,
  set historical_reaction_summary to exactly "no historical price data available".

Return: is_significant (bool), confidence (0-1), category, historical_reaction_summary, reasoning."""

    try:
        llm_struct = llm.with_structured_output(SignificanceOutput)
        decision: SignificanceOutput = llm_struct.invoke(classifier_prompt)

        # Ground the historical reaction: when the prompt contained NO known 40-min
        # reactions, the LLM has nothing to summarize and tends to fabricate figures
        # (and even cite the wrong company). Overwrite with a deterministic marker.
        if n_with_reaction == 0:
            decision.historical_reaction_summary = "no historical price data available"

        # Apply confidence threshold from config: must be significant AND confident enough.
        threshold = getattr(config, "SIGNIFICANCE_CONFIDENCE_THRESHOLD", 0.8) if config else 0.8
        passes_threshold = decision.is_significant and decision.confidence >= threshold

        state["is_significant"] = passes_threshold
        state["significance_reasoning"] = decision.reasoning

        if passes_threshold:
            verdict = "SIGNIFICANT"
        elif decision.is_significant:
            verdict = f"BELOW THRESHOLD ({decision.confidence:.2f} < {threshold}) → treating as not significant"
        else:
            verdict = "NOT significant"
        print(f"  → {verdict} (confidence={decision.confidence:.2f}, category={decision.category})")
        print(f"  → Historical reaction: {decision.historical_reaction_summary}")
        print(f"  → Reasoning: {decision.reasoning[:200]}")

        add_message(
            state,
            "assistant",
            f"Significance: {verdict} ({decision.category}, conf={decision.confidence:.2f}, threshold={threshold})\n"
            f"Historical reaction: {decision.historical_reaction_summary}\n"
            f"Reasoning: {decision.reasoning}",
            name="significance_check",
            data={**decision.model_dump(), "threshold": threshold, "passes_threshold": passes_threshold},
        )

        if not passes_threshold:
            state["signals"] = []
            state["filtered_signals"] = []
            state["tickers"] = []

    except Exception as e:
        # Infra/quota/transport failures must NOT be silently treated as
        # "significant, 0 signals" — that pollutes agent_results and hides outages.
        # Raise loudly so the batch records a real error and retries the item.
        raise_if_infra(e, context="significance classifier")
        # Genuine logic/parse error: fail CLOSED (skip expensive downstream graph).
        print(f"  ✗ Classifier failed (logic error, treating as NOT significant): {e}")
        state["is_significant"] = False
        state["significance_reasoning"] = f"Classifier logic error, skipped: {e}"

    return state


@traceable(name="macro_context_node", run_type="chain")
def macro_context_node(state: TradingAgentState, neo4j_manager: Any) -> TradingAgentState:
    """
    Step 1b: Build macro market context snapshot before planning.
    Queries Neo4j for event type distribution and top macro narratives.
    Skipped silently if neo4j_manager is None.
    """
    print("\n=== Macro Context ===")

    if neo4j_manager is None:
        print("  → Neo4j not available, skipping macro context")
        state["macro_context"] = {}
        return state

    timestamp = state["timestamp"]
    pub_iso = timestamp.isoformat()

    try:
        type_dist = neo4j_manager.query("""
            MATCH (e:Event)
            WHERE e.date <= datetime($pub)
              AND e.date >= datetime($pub) - duration({days: 7})
            RETURN e.type AS event_type, count(e) AS count
            ORDER BY count DESC
        """, {"pub": pub_iso})

        top_stories = neo4j_manager.query("""
            MATCH (s:Story)
            WHERE s.domain IN ['geopolitics', 'macro_markets', 'regulatory']
              AND coalesce(s.status, 'active') = 'active'
              AND s.start_date >= datetime($pub) - duration({days: 14})
              AND s.start_date <= datetime($pub)
            RETURN s.label AS label, s.domain AS domain,
                   s.article_count AS article_count
            ORDER BY s.article_count DESC LIMIT 10
        """, {"pub": pub_iso})

        macro_ctx = {
            "event_distribution": type_dist or [],
            "top_macro_stories": top_stories or [],
        }
        state["macro_context"] = macro_ctx

        add_message(
            state, "assistant",
            f"Macro context: {len(type_dist)} event types, {len(top_stories)} active macro stories",
            name="macro_context",
            data=macro_ctx,
        )
        print(f"✓ Macro context built: {len(type_dist)} event types, {len(top_stories)} stories")

    except Exception as e:
        print(f"⚠️  Macro context error (non-fatal): {e}")
        state["macro_context"] = {}

    return state


@traceable(name="planning_node", run_type="chain")
def planning_node(state: TradingAgentState, llm: Any) -> TradingAgentState:
    """
    Step 2: Create action plan based on news and context
    LLM customizes plan template for this specific news
    
    Args:
        state: Current agent state
        llm: Language model instance
        
    Returns:
        Updated state with action_plan and plan_steps
    """
    news_text = state["news_text"]
    recent_news = state.get("recent_news", [])
    similar_news = state.get("similar_news", [])
    
    print("\n=== Planning ===")
    
    # Get existing messages from state
    messages = state.get("messages", [])
    
    # Format similar news summary
    similar_summary = ""
    if similar_news:
        similar_summary = "\nSimilar Historical Events:\n"
        for i, news in enumerate(similar_news[:5], 1):
            similar_summary += f"{i}. {news.get('title', 'N/A')} - Price impact: {news.get('price_change_pct', 0):+.2f}%\n"
    
    # Build macro context summary for planning
    macro_ctx = state.get("macro_context", {})
    macro_summary = ""
    if macro_ctx:
        evt_dist = macro_ctx.get("event_distribution", [])
        top_stories = macro_ctx.get("top_macro_stories", [])
        if evt_dist or top_stories:
            macro_summary = "\nMACRO ENVIRONMENT:\n"
            if evt_dist:
                macro_summary += "  Event types (last 7d): " + ", ".join(
                    f"{ed.get('event_type', '?')}({ed.get('count', 0)})" for ed in evt_dist[:6]
                ) + "\n"
            if top_stories:
                macro_summary += "  Top narratives: " + ", ".join(
                    f"{ts.get('label', '?')}[{ts.get('article_count', 0)} articles]" for ts in top_stories[:3]
                ) + "\n"

    # Plan template with novelty-first approach
    plan_template = """
    RECOMMENDED WORKFLOW:
    
    1. Identify tickers mentioned in news (validate_ticker_tool if needed)
    
    2. ⭐ FIRST: Assess NOVELTY — get_novelty_score(<news_topic>, ticker=<ticker>)
       - This determines if the market already knows this information
       - Novelty score < 0.3 → news is mostly priced in, expect small move
       - Novelty score > 0.7 → genuinely new information, expect larger move
    
    3. ⭐ PRIORITY: Use ticker_analysis_tool for EACH ticker
       - Historical patterns with EXACT price impacts (40-min window)
       - Example: ticker_analysis_tool("AAPL", top_k=10)
    
    4. ⭐ PRIORITY: Use find_similar_news_with_price_tool for historical analysis
       - Specific similar news with exact price changes
       - Example: find_similar_news_with_price_tool("AAPL", "Apple chip breakthrough", top_k=10)
    
    5. Build causal chain and GROUND it in real prices (macro AND company news):
       - search_events / get_events_for_ticker → then MANDATORY get_event_price_impact(event_id)
         (and get_causal_chain_with_prices for macro) → use the actual move_15m/40m numbers
       - get_cross_ticker_correlation to see which companies react and in which direction
       - Never invent reaction figures; if the graph has nothing, lower confidence
    
    6. Get supporting data as needed:
       - get_pre_news_movement_tool("AAPL") - did the stock already move before the news? (anti-chase)
       - get_price_data_tool("AAPL") - 3 days of price data with 15min intervals
       - get_technical_indicators_tool("AAPL") - RSI, ATR, Bollinger
       - get_story_momentum(ticker) - is attention increasing or decreasing?
    
    7. Generate signals calibrated by novelty (set novelty_score on each signal):
       - High novelty + strong historical pattern = high confidence
       - Low novelty (< 0.3) = mostly priced in → usually emit NO signal
       - Already moved pre-news in the signal direction = priced in → emit NO signal
    """
    
    # Create system message with task
    system_prompt = f"""TASK: Create a customized action plan for analyzing this news.

NEWS:
{news_text}

RECENT CONTEXT:
- {len(recent_news)} recent news items in last 24h
{similar_summary}
{macro_summary}
PLAN TEMPLATE:
{plan_template}

MAIN TASK: 
- Generate trading signals for the news. I will trade these signals short-term (40 minutes).
- If news has long-term effect, it will still affect price in short term.
- Predict reaction of news on market based on historical patterns from similar news.
- Current market trends will not affect prices as strongly as breaking news.
- Generate a signal ONLY when the news is a genuine catalyst with a clear directional edge. Signal format: {{"ticker": "AMD", "signal": "buy"/"sell", "confidence": 0.0-1.0, "reasoning": "news positive/negative effect"}}
- "No trade" is a valid and preferred outcome when there is no clear edge — return an empty signal list rather than a low-confidence guess.
- All data provided for you is data BEFORE news publication.
- Do not be too conservative, your signals will be checked for risk in next step.

KEY STRATEGY:
- FIRST assess novelty with get_novelty_score() — this determines expected impact magnitude
- Use ticker_analysis_tool() for historical patterns with EXACT price impacts
- Use graph tools (search_events, get_causal_chain_with_prices) for deep reasoning chains
- Base predictions on historical price movements AND novelty assessment, not just sentiment

NEWS TIMESTAMP: {state["timestamp"].strftime('%Y-%m-%d %H:%M:%S %Z')} (US Eastern Time)

Your task:
1. Review the news and context (including macro environment if available)
2. Customize the plan template for THIS specific news
3. PRIORITIZE novelty assessment and ticker_analysis_tool
4. For macro/geopolitical news, plan causal chain analysis
5. Add or remove steps as needed
6. Order steps by priority (novelty first, then historical patterns)"""
    
    system_msg = SystemMessage(content=system_prompt)
    
    # Combine messages and invoke LLM with structured output
    messages_to_send = messages + [system_msg]
    
    try:
        # Use structured output with Pydantic model
        llm_with_structure = llm.with_structured_output(PlanningOutput)
        plan_data: PlanningOutput = llm_with_structure.invoke(messages_to_send)
        
        # Convert to dict for state storage
        plan_dict = plan_data.model_dump()
        state["action_plan"] = plan_dict
        state["plan_steps"] = plan_dict["plan_steps"]
        
        # Add message
        plan_summary = f"Created action plan with {len(state['plan_steps'])} steps"
        add_message(
            state,
            "assistant",
            f"{plan_summary}\nReasoning: {plan_dict.get('reasoning', 'N/A')}",
            name="planning",
            data={"plan": plan_dict}
        )
        
        print(f"✓ Plan created with {len(state['plan_steps'])} steps")
        for i, step in enumerate(state['plan_steps'], 1):
            print(f"  {i}. {step}")
            
    except Exception as e:
        print(f"✗ Error in planning: {e}")
        # Default plan
        state["action_plan"] = {"plan_steps": ["Analyze news"], "reasoning": "Default plan", "priority_areas": [], "expected_tickers": 1}
        state["plan_steps"] = ["Analyze news"]
    
    return state


@traceable(name="main_analysis_node", run_type="chain")
def main_analysis_node(state: TradingAgentState, llm: Any, db_manager: Any, config: Any, neo4j_manager: Any = None) -> TradingAgentState:
    """
    Step 3: Execute analysis with flexibility to deviate from plan
    LLM can modify plan on-the-fly based on discoveries
    
    LLM can dynamically use tools:
    - find_similar_news(search_query, ...) with LLM-generated search queries
      Example: "Apple chip performance breakthrough" or "Microsoft AI competition"
    - get_recent_news_items(..., ticker="AAPL", ...) for ticker-specific news
      Example: Get recent AAPL news to check for other announcements
    
    Args:
        state: Current agent state
        llm: Language model instance
        db_manager: Database manager instance
        config: Agent configuration
        
    Returns:
        Updated state with analysis_results, signals, and plan_deviations
    """
    news_text = state["news_text"]
    timestamp = state["timestamp"]
    plan_steps = state.get("plan_steps", [])
    
    print("\n=== Main Analysis ===")
    
    # Initialize tracking
    if "plan_deviations" not in state:
        state["plan_deviations"] = []
    
    # Get existing messages from state
    messages = state.get("messages", [])
    plan_text = format_plan_for_llm(state.get("action_plan", {}))
    
    # Create LLM-callable tools with context
    tools = [
        create_find_similar_news_tool(timestamp, db_manager),
        create_find_similar_news_with_price_tool(timestamp, db_manager),
        create_ticker_analysis_tool(timestamp, db_manager, news_text),
        create_ticker_historical_context_tool(timestamp, db_manager),
        create_get_recent_news_tool(timestamp, db_manager),
        create_get_price_data_tool(timestamp, db_manager),
        create_get_price_at_time_tool(timestamp, db_manager),
        create_get_technical_indicators_tool(timestamp, db_manager),
        create_analyze_sector_tool(timestamp, db_manager),
        create_check_risk_tool(timestamp, db_manager),
        create_validate_ticker_tool(),
        create_get_pre_news_movement_tool(timestamp, db_manager),
    ]
    if neo4j_manager is not None:
        tools.extend(create_graph_tools(neo4j_manager, db_manager, timestamp))
    llm_with_tools = llm.bind_tools(tools)
    
    # Build macro context summary if available
    macro_ctx = state.get("macro_context", {})
    macro_section = ""
    if macro_ctx:
        evt_dist = macro_ctx.get("event_distribution", [])
        top_stories = macro_ctx.get("top_macro_stories", [])
        if evt_dist or top_stories:
            macro_section = "\n=== MACRO CONTEXT (pre-computed) ===\n"
            if evt_dist:
                macro_section += "Event type distribution (last 7 days):\n"
                for ed in evt_dist[:8]:
                    macro_section += f"  - {ed.get('event_type', '?')}: {ed.get('count', 0)}\n"
            if top_stories:
                macro_section += "Top macro narratives:\n"
                for ts in top_stories[:5]:
                    macro_section += f"  - {ts.get('label', '?')} (articles: {ts.get('article_count', 0)}, domain: {ts.get('domain', '?')})\n"

    # Create system message with task
    system_prompt = f"""{plan_text}

TASK: Execute the analysis plan. You have FLEXIBILITY to:
- Deviate from the plan if you discover something important
- Add analysis not in the plan if relevant
- Skip steps that don't apply
- Explain any deviations
- Use available tools to gather additional information if needed

NEWS:
{news_text}

NEWS TIMESTAMP: {timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')} (US Eastern Time)

Available information:
- Recent news context: {len(state.get('recent_news', []))} items
- Similar historical events: {len(state.get('similar_news', []))} items
{macro_section}
=== DEEP REASONING CHAIN PATTERNS ===

You MUST build deep reasoning chains — use the output of one tool call to decide the next.
Follow these patterns (adapt to each news article):

CHAIN 1 — NOVELTY ASSESSMENT (most important for short-term trading):
  Step A: get_novelty_score(search_query="<core topic of news>", ticker="<ticker>")
          → tells you if this information is NEW or already priced in
  Step B: IF novelty_score < 0.5 → get_story_momentum(ticker) to see how fast coverage is growing
          → high articles_per_day + low novelty = very likely priced in, reduce confidence
  Step C: IF novelty_score > 0.7 → this is genuinely new; proceed with full historical analysis

CHAIN 2 — CAUSAL IMPACT PROPAGATION (for macro/geopolitical AND company news):
  Step A: search_events(query="<event topic>") or get_events_for_ticker(ticker) → find events in graph
  Step B: MANDATORY — for the best event_id, call get_event_price_impact(event_id)
          (and get_causal_chain_with_prices(event_id, depth=2) for macro/geo)
          → use the ACTUAL move_15m/move_40m values from these edges as your magnitude/direction.
  Step C: get_cross_ticker_correlation(event_type="<type>", days=30)
          → which tickers react strongest to this type of event?
  Step D: For under-covered tickers: ticker_analysis_tool(ticker) → deeper analysis
  RULE: Base every magnitude/direction claim on real numbers returned by the tools above.
        Do NOT invent "historical reaction" figures. If the graph returns nothing for the
        ticker/event, say so and lower confidence — do not fabricate a number.

CHAIN 3 — MARKET CONTEXT CALIBRATION:
  Step A: get_macro_snapshot(days=7) → overall market regime
  Step B: get_technical_indicators_tool(ticker) → is stock overbought/oversold?
  Step C: Reasoning: RSI > 70 + positive news = limited upside; RSI < 30 + negative news = may already be bottoming

CHAIN 4 — HISTORICAL OUTCOME DISTRIBUTION:
  Step A: ticker_analysis_tool(ticker, top_k=10) → price_statistics with avg, median, std_dev
  Step B: IF std_dev is high → outcomes are scattered; find WHY
  Step C: For outlier events: get_event_replay(event_id) → see full context of outlier case
          → what macro conditions or related events caused the outlier?
  Step D: Match current conditions to historical scenarios to calibrate prediction

KEY PRINCIPLE: Novel information moves prices. Repetition of known narratives does NOT.
Always assess novelty BEFORE predicting direction and magnitude.

=== PRIORITY TOOLS ===

1. get_novelty_score: Assess how novel this news is (0-1 score)
   - THE most important signal for short-term trading
   - Example: get_novelty_score("Nvidia Blackwell pricing premium", ticker="NVDA")

2. ticker_analysis_tool: Comprehensive ticker analysis with similar news
   - Historical news with EXACT price impacts (40-min window)
   - Statistical analysis: avg/median price change, volatility, confidence score
   - Example: ticker_analysis_tool("AAPL", top_k=10)

3. find_similar_news_with_price_tool: Historical news with price changes
   - Example: find_similar_news_with_price_tool("AAPL", "Apple chip breakthrough", top_k=10)

4. find_similar_news_tool: Broader pattern matching across all tickers
   - Example: find_similar_news_tool("chip shortage announcement", top_k=5)

=== GRAPH TOOLS (use for deep reasoning chains) ===

5. get_story_momentum: Market attention momentum for a ticker/domain
   - High articles_per_day = heavily covered = more likely priced in
   - Example: get_story_momentum("NVDA") or get_story_momentum("geopolitics")

6. get_macro_snapshot: Structured macro environment view
   - Event type distribution + top macro Stories
   - Example: get_macro_snapshot(days=7)

7. get_causal_chain_with_prices: Cause/effect chain WITH price impacts
   - See how events propagate through companies with actual % moves
   - Example: get_causal_chain_with_prices(event_id, depth=2)

8. get_event_price_impact: Price movements for all companies affected by an event
   - Returns move_15m, move_40m, move_4h, move_1d from graph
   - Example: get_event_price_impact(event_id)

9. get_cross_ticker_correlation: Which tickers react to an event type
   - Example: get_cross_ticker_correlation("geopolitical_conflict", days=30)

10. get_event_replay: Full historical case study of an event
    - Timeline of articles, price impacts, nearby events
    - Example: get_event_replay(event_id)

11. search_events: Semantic search for events in graph
    - Example: search_events("Iran oil shipping disruption", limit=10)

12. get_events_for_ticker: All events for a company with cause/effect chains
    - Example: get_events_for_ticker("NVDA", days=30)

13. get_story_context: Ongoing narrative for a ticker/domain
    - Example: get_story_context("NVDA") or get_story_context("geopolitics")

14. find_related_events: Traverse cause/effect graph from an event
    - Example: find_related_events(event_id, depth=2)

=== SUPPORTING TOOLS ===

15. get_recent_news_tool: Recent news articles (get_recent_news_tool(hours=48))
16. get_price_data_tool: OHLCV, last 3 days, 15-min bars (get_price_data_tool("AAPL"))
17. get_price_at_time_tool: Price at news timestamp (get_price_at_time_tool("AAPL"))
18. get_technical_indicators_tool: RSI, ATR, MAs, Bollinger (get_technical_indicators_tool("AAPL"))
19. ticker_historical_context_tool: 7-day price trends (ticker_historical_context_tool("AAPL"))
20. analyze_sector_tool: Sector + competitors (analyze_sector_tool("AAPL", max_competitors=3))
21. check_risk_tool: Risk assessment (check_risk_tool("AAPL", sentiment_intensity=85, sentiment_type="bullish"))
22. validate_ticker_tool: Validate US ticker (validate_ticker_tool("AAPL"))
23. get_pre_news_movement_tool: Price/volume action BEFORE this news (get_pre_news_movement_tool("AAPL"))
    - If the stock already moved strongly in your news's direction, the catalyst is priced in → do NOT chase

RECOMMENDED ANALYSIS WORKFLOW:
1. Identify tickers in the news
2. Assess NOVELTY first: get_novelty_score() → determines expected price impact magnitude
3. Use ticker_analysis_tool() for EACH ticker → historical patterns with price impacts
4. Build reasoning chain: use output from step 2-3 to decide which deep chain to follow
5. Calibrate with macro context and technical indicators
6. Generate trading signals — adjust confidence based on novelty score

SIGNAL FORMAT:
- ticker: Stock ticker symbol (e.g., "AMD")
- signal: "buy" or "sell" (lowercase)
- confidence: Float between 0.0 and 1.0
- reasoning: Explanation including novelty assessment and historical pattern evidence
- novelty_score: the 0-1 value you obtained from get_novelty_score for this ticker (REQUIRED)

IMPORTANT:
- Do NOT force a signal. Return an empty signal list ("no trade") when there is no clear, immediate directional edge — this is preferred over a low-confidence guess.
- Only emit a signal with confidence >= 0.5 backed by novelty + a historical price pattern.
- ALWAYS reference novelty score and historical price patterns in reasoning, and set the
  novelty_score field on each signal from get_novelty_score.
- If novelty is low (< 0.3), reduce confidence proportionally (and usually emit no signal).
- ANTI-CHASE: call get_pre_news_movement_tool(ticker); if the stock already moved in your
  signal's direction before publication, the move is likely priced in — emit no signal.
- Never fabricate historical reaction magnitudes — only cite numbers returned by tools.

After gathering all necessary information, provide your complete analysis."""
    
    system_msg = SystemMessage(content=system_prompt)
    
    # Combine messages and invoke LLM with tool calling
    messages_to_send = messages + [system_msg]
    
    try:
        # Tool calling loop with structured output at the end
        max_iterations = getattr(config, "MAX_TOOL_CALL_ITERATIONS", 3)
        iteration = 0
        final_analysis = None
        tools_called_count = 0
        
        print(f"  → Starting analysis (max {max_iterations} tool call iterations)...")
        
        while iteration < max_iterations:
            iteration += 1
            
            # Force structured output on last iteration to prevent infinite loops
            if iteration >= max_iterations:
                print(f"  → Max iterations reached ({max_iterations}), forcing structured output...")
                llm_structured = llm.with_structured_output(MainAnalysisOutput)
                final_analysis = llm_structured.invoke(messages_to_send)
                break
            
            # Use tool calling
            response = llm_with_tools.invoke(messages_to_send)
            
            # Add AI response to messages
            messages_to_send.append(response)
            
            # Check if LLM wants to call tools
            if hasattr(response, 'tool_calls') and response.tool_calls:
                tools_called_count += len(response.tool_calls)
                print(f"\n  → LLM calling {len(response.tool_calls)} tool(s) (iteration {iteration}/{max_iterations})...")
                
                # Execute tools
                tool_messages = []
                tool_calls_made = []
                
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get('name', '')
                    tool_args = tool_call.get('args', {})
                    tool_call_id = tool_call.get('id', '')
                    
                    print(f"    - {tool_name}({tool_args})")
                    tool_calls_made.append({"name": tool_name, "args": tool_args})
                    
                    # Find and execute tool
                    tool_result = None
                    for tool in tools:
                        if tool.name == tool_name:
                            try:
                                tool_result = tool.invoke(tool_args)
                                break
                            except Exception as e:
                                tool_result = json.dumps({"error": str(e)})
                                print(f"      ✗ Error: {e}")
                                break
                    
                    if tool_result is None:
                        tool_result = json.dumps({"error": f"Tool {tool_name} not found"})
                    
                    # Add tool result message
                    tool_messages.append(
                        ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call_id
                        )
                    )
                
                # Track tool calls in state
                add_message(
                    state,
                    "assistant",
                    f"Used {len(tool_calls_made)} tool(s) to gather information",
                    name="main_analysis",
                    data={"tool_calls": tool_calls_made}
                )
                
                # Add tool results to messages and continue loop
                messages_to_send.extend(tool_messages)
                
                # If we've called many tools, consider getting structured output
                if tools_called_count >= 12:
                    print(f"  → {tools_called_count} tools called, requesting final analysis...")
                    llm_structured = llm.with_structured_output(MainAnalysisOutput)
                    final_analysis = llm_structured.invoke(messages_to_send)
                    break
                continue
            else:
                # No tool calls - LLM is ready for final analysis
                print("  → No more tool calls needed, getting structured output...")
                llm_structured = llm.with_structured_output(MainAnalysisOutput)
                final_analysis = llm_structured.invoke(messages_to_send)
                break
        
        # Process final analysis
        if final_analysis:
            if isinstance(final_analysis, MainAnalysisOutput):
                analysis_data = final_analysis.model_dump()
            else:
                analysis_data = final_analysis
            
            # Extract results
            tickers = analysis_data.get("tickers", [])
            state["tickers"] = tickers
            
            # Validate sentiment data
            sentiment_data = analysis_data.get("sentiment", {})
            validated_sentiment = {}
            for ticker, sent_info in sentiment_data.items():
                if not isinstance(sent_info, dict):
                    sent_info = {"sentiment": sent_info}

                # Validate sentiment type
                sentiment_raw = sent_info.get("sentiment", "neutral")
                sentiment_str = str(sentiment_raw).lower()
                if sentiment_str not in ["bullish", "bearish", "neutral"]:
                    sentiment_str = "neutral"
                
                # Clamp intensity to 0-100
                intensity = max(0, min(100, int(sent_info.get("intensity", 50))))
                
                validated_sentiment[ticker] = {
                    "sentiment": sentiment_str,
                    "intensity": intensity,
                    "reasoning": sent_info.get("reasoning", "No reasoning provided"),
                    "key_phrases": sent_info.get("key_phrases", [])
                }
            
            analysis_data["sentiment"] = validated_sentiment
            state["analysis_results"] = analysis_data
            signals = analysis_data.get("signals", [])

            # "No trade" is a valid, desirable outcome: forcing a synthetic
            # fallback signal injected pure noise (~50% win rate, no edge).
            # Keep an empty list when the model finds no real catalyst.
            if not signals:
                print("  → No actionable signal for this news (no-trade).")

            state["signals"] = signals
            
            # Track deviations
            deviations = analysis_data.get("deviations_from_plan", [])
            if deviations:
                state["plan_deviations"].extend(deviations)
                print(f"⚠️  Plan deviations: {len(deviations)}")
                for dev in deviations:
                    print(f"    - {dev}")
            
            # Add final message
            add_message(
                state,
                "assistant",
                json.dumps(analysis_data, indent=2),
                name="main_analysis",
                data={"iteration": iteration}
            )
            
            # Fetch actual data for tickers
            print(f"\n  → Fetching data for {len(tickers)} tickers...")
            for ticker in tickers:
                # Get historical prices
                price_data = get_historical_prices(
                    ticker=ticker,
                    timestamp=timestamp,
                    lookback_days=config.LOOKBACK_DAYS_TECHNICAL,
                    db_manager=db_manager
                )
                
                if price_data is not None:
                    # Calculate technical indicators
                    indicators = calculate_technical_indicators(
                        ticker=ticker,
                        price_data=price_data
                    )
                    print(f"    ✓ {ticker}: Technical indicators calculated")
            
            # Add summary message
            analysis_summary = f"Analysis complete: {len(tickers)} tickers analyzed ({', '.join(tickers)})"
            if deviations:
                analysis_summary += f"\nPlan deviations: {', '.join(deviations)}"
            if iteration > 1:
                analysis_summary += f"\nTool calls made: {iteration - 1} iteration(s)"
            
            add_message(
                state,
                "assistant",
                analysis_summary,
                name="main_analysis",
                data={
                    "tickers": tickers,
                    "deviations": deviations,
                    "signals_count": len(state["signals"]),
                    "tool_iterations": iteration - 1
                }
            )
            
            print(f"✓ Analysis complete: {len(tickers)} tickers, {len(state['signals'])} signals")
        else:
            print("⚠️  No final analysis received from LLM")
            state["tickers"] = []
            state["signals"] = []
            state["analysis_results"] = {}
            
    except Exception as e:
        print(f"✗ Error in main analysis: {e}")
        state["analysis_results"] = {"error": str(e)}
        state["tickers"] = []
        state["signals"] = []
    
    return state


@traceable(name="reflection_node", run_type="chain")
def reflection_node(state: TradingAgentState, llm: Any, config: Any) -> TradingAgentState:
    """
    Step 4: Reflect on analysis and decide next step
    Can loop to planning OR main_analysis OR proceed
    
    Args:
        state: Current agent state
        llm: Language model instance
        config: Agent configuration
        
    Returns:
        Updated state with reflection_advice, needs_refinement, loop_to
    """
    reflection_count = state.get("reflection_count", 0)
    max_iterations = state.get("max_iterations", getattr(config, "MAX_REFLECTION_ITERATIONS", 5))
    
    print(f"\n=== Reflection (iteration {reflection_count + 1}/{max_iterations}) ===")
    
    # Get existing messages from state
    messages = state.get("messages", [])
    tickers = state.get("tickers", [])
    signals = state.get("signals", [])
    deviations = state.get("plan_deviations", [])
    
    # "No trade" is acceptable: if the analysis found no real catalyst, proceed
    # to risk assessment with an empty signal list instead of forcing noise.
    if not signals or len(signals) == 0:
        print("✓ No signals generated — accepting no-trade outcome, proceeding.")
        state["needs_refinement"] = False
        state["loop_to"] = "risk_assessment"
        state["reflection_advice"] = []
        state["reflection_count"] = reflection_count + 1
        return state
    
    # Create system message with task
    system_prompt = f"""TASK: Reflect on the analysis and decide next steps.
    IMPORTANT: Agent MUST generate at least 1 signal. Empty result is NOT acceptable.
    

NEWS TIMESTAMP: {state["timestamp"].strftime('%Y-%m-%d %H:%M:%S %Z')} (US Eastern Time)

CURRENT STATE:
- Tickers analyzed: {', '.join(tickers) if tickers else 'None'}
- Signals generated: {len(signals)} (REQUIRED: at least 1)
- Plan deviations: {len(deviations)}

Review the analysis and decide:

CRITICAL REQUIREMENT: Agent MUST generate at least 1 signal. If signals list is empty, you MUST loop back to main_analysis.

1. **SATISFIED** - Analysis is complete and sound (cant be improved useint avalabele tools) AND at least 1 signal generated
   → Set needs_refinement: false
   → Proceed to risk assessment
   → ONLY proceed if signals list has at least 1 signal

2. **NEEDS BETTER PLAN** - The plan itself was flawed (can be improved useint avalabele tools)
   → Set needs_refinement: true, loop_to: "planning"
   → Provide advice for replanning

3. **NEEDS MORE ANALYSIS** - Plan is good but execution incomplete (can be improved useint avalabele tools)
   → Set needs_refinement: true, loop_to: "main_analysis"
   → Provide specific advice

Current iteration: {reflection_count + 1}/{max_iterations}"""
    
    system_msg = SystemMessage(content=system_prompt)
    
    # Combine messages and invoke LLM with structured output
    messages_to_send = messages + [system_msg]
    
    try:
        # Use structured output with Pydantic model
        llm_with_structure = llm.with_structured_output(ReflectionOutput)
        reflection_data: ReflectionOutput = llm_with_structure.invoke(messages_to_send)
        
        # Convert to dict for easier access
        reflection_dict = reflection_data.model_dump()
        
        state["needs_refinement"] = reflection_dict["needs_refinement"]
        state["loop_to"] = reflection_dict["loop_to"]
        state["reflection_advice"] = reflection_dict["advice"]
        state["reflection_count"] = reflection_count + 1
        
        # Add message
        if state["needs_refinement"]:
            decision = f"Needs refinement → loop to {state['loop_to']}"
            print(f"⚠️  {decision}")
            for advice in state["reflection_advice"]:
                print(f"    - {advice}")
        else:
            decision = "Analysis complete → proceeding to risk assessment"
            print(f"✓ {decision}")
        
        add_message(
            state,
            "assistant",
            f"{decision}\nReasoning: {reflection_dict.get('reasoning', 'N/A')}",
            name="reflection",
            data=reflection_dict
        )
            
    except Exception as e:
        print(f"✗ Error in reflection: {e}")
        state["needs_refinement"] = False
        state["reflection_count"] = reflection_count + 1
    
    # Safety: Don't loop forever
    if state["reflection_count"] >= max_iterations:
        print("⚠️  Max iterations reached, forcing proceed")
        state["needs_refinement"] = False
    
    return state


@traceable(name="risk_assessment_node", run_type="chain")
def risk_assessment_node(state: TradingAgentState, db_manager: Any, config: Any) -> TradingAgentState:
    """
    Step 5: Filter unreliable signals based on risk checks
    Checks for pre-news price movements and other risk factors
    
    Args:
        state: Current agent state
        db_manager: Database manager instance
        config: Agent configuration
        
    Returns:
        Updated state with risk_flags and filtered_signals
    """
    tickers = state.get("tickers", [])
    signals = state.get("signals", [])
    timestamp = state["timestamp"]
    
    print("\n=== Risk Assessment ===")
    
    if not signals:
        print("No signals to assess")
        state["filtered_signals"] = []
        state["risk_flags"] = {}
        return state

    # --- Deduplicate conflicting buy+sell on the same ticker -------------------
    before = len(signals)
    signals = _dedupe_conflicting_signals(signals)
    if len(signals) < before:
        print(f"  → Deduped conflicting signals: {before} → {len(signals)} (kept higher-confidence side)")

    # --- Tradability gate: drop ETFs/indices/foreign + tickers without price data
    tradable_signals = []
    for s in signals:
        tk = s.get("ticker")
        if _is_tradable(tk, db_manager):
            tradable_signals.append(s)
        else:
            print(f"  ✗ Dropping {tk}: not a tradable US single-name with price data")
    signals = tradable_signals
    state["signals"] = signals
    state["tickers"] = [s.get("ticker") for s in signals if s.get("ticker")]
    tickers = state["tickers"]
    if not signals:
        print("  → No tradable signals remain after tradability gate")
        state["filtered_signals"] = []
        state["risk_flags"] = {}
        return state

    # Check pre-news movements for all tickers
    pre_news_window = getattr(config, "PRE_NEWS_WINDOW_MINUTES", 60)
    print(f"  → Checking pre-news movements for {len(tickers)} tickers...")
    pre_news_results = check_multiple_tickers_pre_news(
        tickers=tickers,
        news_timestamp=timestamp,
        db_manager=db_manager,
        window_minutes=pre_news_window
    )
    
    # Assess risk for each ticker
    risk_flags = {}
    for ticker in tickers:
        flags = []
        
        # Check pre-news movement
        pre_news = pre_news_results.get(ticker, {})
        if pre_news.get("suspicious", False):
            flags.extend(pre_news.get("flags", []))
            print(f"    ⚠️  {ticker}: {', '.join(pre_news.get('flags', []))}")
        
        # Check volatility
        risk_data = assess_risk(
            ticker=ticker,
            timestamp=timestamp,
            db_manager=db_manager,
            sentiment_data={},
            technical_indicators={},
            sector_analysis={}
        )
        
        if risk_data.get("is_hot_stock", False):
            flags.append("Stock already hot from recent events")
            print(f"    ⚠️  {ticker}: Already a hot stock")
        
        if flags:
            risk_flags[ticker] = flags
    
    state["risk_flags"] = risk_flags
    
    confidence_threshold = getattr(config, "CONFIDENCE_THRESHOLD", 0.7)
    novelty_min = getattr(config, "NOVELTY_MIN", 0.3)
    played_out_k = getattr(config, "PRE_NEWS_PLAYED_OUT_K", 1.5)
    require_pre_news_data = getattr(config, "PRE_NEWS_REQUIRE_DATA", True)
    moved_dir = _already_moved_direction(state.get("news_text", ""))

    # Filter signals — record the REAL reason each dropped signal was filtered so the
    # trace reflects what actually happened (not a generic "risk flags" message).
    filtered_signals = []
    drop_reasons: Dict[str, str] = {}
    for signal in signals:
        ticker = signal.get("ticker")
        conf = signal.get("confidence", 0)
        sig_dir = (signal.get("signal") or "").lower()
        novelty = signal.get("novelty_score")
        pre_news = pre_news_results.get(ticker, {})

        # 1. Confidence threshold
        if conf < confidence_threshold:
            drop_reasons[ticker] = f"low_confidence ({conf:.0%} < {confidence_threshold:.0%})"
            print(f"  ✗ {ticker}: confidence {conf:.0%} < threshold {confidence_threshold:.0%}")
            continue

        # 2. Novelty veto — already-known narrative is priced in (Item 2)
        if novelty is not None and novelty < novelty_min:
            drop_reasons[ticker] = f"priced_in_low_novelty (novelty={novelty:.2f} < {novelty_min})"
            print(f"  ✗ {ticker}: low novelty {novelty:.2f} < {novelty_min} → priced in")
            continue

        # 3/4. Pre-news "already played out" gate (Item F)
        has_pre_data = pre_news.get("has_data", False)
        pre_move = pre_news.get("price_change_pct", 0.0)
        if require_pre_news_data and not has_pre_data:
            # No price data to verify whether the move already happened → fail closed.
            drop_reasons[ticker] = "no_pre_news_data (fail-closed)"
            print(f"  ✗ {ticker}: no pre-news price data → no trade (fail-closed)")
            continue
        if has_pre_data:
            typical = compute_typical_move_pct(db_manager, ticker, timestamp)
            same_dir = (pre_move > 0 and sig_dir == "buy") or (pre_move < 0 and sig_dir == "sell")
            if typical and same_dir and abs(pre_move) >= played_out_k * typical:
                drop_reasons[ticker] = (
                    f"already_played_out (pre-news {pre_move:+.2f}% ≈ "
                    f"{abs(pre_move) / typical:.1f}x typical {typical:.2f}%, same direction)"
                )
                print(f"  ✗ {ticker}: already played out pre-news ({pre_move:+.2f}%, "
                      f"{abs(pre_move) / typical:.1f}x typical) → no trade")
                continue

        # 5. Text anti-chase: headline announces a move that already happened
        if (moved_dir == "up" and sig_dir == "buy") or (moved_dir == "down" and sig_dir == "sell"):
            drop_reasons[ticker] = f"anti_chase_headline ({moved_dir}-move already in headline)"
            print(f"  ✗ Anti-chase: dropping {ticker} {sig_dir.upper()} — headline shows {moved_dir}-move already happened")
            continue

        # 6. Risk flags (pre-news leak / hot stock)
        if ticker in risk_flags and len(risk_flags[ticker]) > 0:
            drop_reasons[ticker] = f"risk_flag ({', '.join(risk_flags[ticker][:2])})"
            print(f"  ✗ Filtering out {ticker} signal due to: {', '.join(risk_flags[ticker][:2])}")
            continue

        filtered_signals.append(signal)
        print(f"  ✓ {ticker} signal passed risk checks (confidence {conf:.0%})")

    if not signals:
        print("  ⚠️  No signals generated.")

    state["filtered_signals"] = filtered_signals

    # Add message — report the actual breakdown of drop reasons.
    n_dropped = len(signals) - len(filtered_signals)
    filter_summary = f"Risk assessment: {len(signals)} signals → {len(filtered_signals)} passed"
    if n_dropped > 0:
        reason_list = "; ".join(f"{tk}: {why}" for tk, why in drop_reasons.items())
        filter_summary += f"\nDropped {n_dropped}: {reason_list}"

    add_message(
        state,
        "assistant",
        filter_summary,
        name="risk_assessment",
        data={
            "total_signals": len(signals),
            "filtered_signals": len(filtered_signals),
            "risk_flags": risk_flags,
            "drop_reasons": drop_reasons,
        }
    )
    
    print(f"\n✓ Risk assessment complete: {len(filtered_signals)}/{len(signals)} signals passed")
    
    return state
