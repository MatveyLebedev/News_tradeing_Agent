"""
Main Trading Agent - LangGraph-based AI agent for news analysis and trade signal generation

All timestamps use US Eastern Time (America/New_York) for consistency with US market hours
"""
from datetime import datetime, time, timedelta
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree, tracing_context
import pytz

from .agent_config import AgentConfig
from .agent.graph import create_trading_graph
from .agent.state import TradingAgentState
from .agent.nodes import base_context_node, significance_check_node
from .llm_errors import LLMInfraError
from .database import DBManager
from graph_db import Neo4jManager


class TradingAgent:
    """
    AI Trading Agent that analyzes news and generates trade signals
    
    Features:
    - Multi-ticker analysis from single news article
    - Time-aware data queries (prevents data leakage)
    - Comprehensive analysis: sentiment, technical, sector, risk
    - Historical pattern matching using vector embeddings
    - LangSmith tracing for observability
    - Confidence-based signal filtering
    
    Example:
        >>> from news_agent import TradingAgent
        >>> from datetime import datetime
        >>> 
        >>> agent = TradingAgent()
        >>> signals = agent.run(
        ...     news_text="Apple announces breakthrough M4 chip...",
        ...     timestamp=datetime(2025, 1, 15, 14, 30)
        ... )
        >>> 
        >>> for signal in signals:
        ...     print(f"{signal['ticker']}: {signal['signal']}")
    """
    
    def __init__(self, config: Optional[AgentConfig] = None):
        """
        Initialize the Trading Agent
        
        Args:
            config: Optional custom configuration (uses defaults if not provided)
        """
        self.config = config or AgentConfig()
        
        # Set up LangSmith tracing
        if self.config.LANGCHAIN_TRACING_V2:
            self._enable_tracing()
        
        # Initialize LLM via Open Router
        print("\n=== Initializing Trading Agent ===")
        # Cap max_tokens to avoid OpenRouter 402 when credits are low
        safe_max_tokens = min(self.config.LLM_MAX_OUTPUT_TOKENS, 2048)
        self.llm = ChatOpenAI(
            api_key=self.config.OPENROUTER_API_KEY,
            base_url=self.config.OPENROUTER_BASE_URL,
            model=self.config.LLM_MODEL,
            temperature=self.config.LLM_TEMPERATURE,
            max_tokens=safe_max_tokens,
        )
        print(f"✓ LLM initialized (Open Router): {self.config.LLM_MODEL}")
        print(f"  Temperature: {self.config.LLM_TEMPERATURE}")

        
        # Initialize database manager
        self.db_manager = DBManager(
            db_config=self.config.get_db_config(),
            min_conn=2,
            max_conn=10
        )
        
        # Initialize Neo4j (optional — agent works without it)
        self.neo4j_manager = None
        try:
            self.neo4j_manager = Neo4jManager()
            print("✓ Neo4j connected")
        except Exception as e:
            print(f"⚠️  Neo4j unavailable, graph tools disabled: {e}")
        
        # Create the LangGraph
        self.graph = create_trading_graph(
            llm=self.llm,
            config=self.config,
            db_manager=self.db_manager,
            neo4j_manager=self.neo4j_manager,
        )
        self.last_run_id: Optional[str] = None

        print("✓ Trading Agent initialized")
        print(f"  Confidence threshold: {self.config.CONFIDENCE_THRESHOLD}")
        print(f"  LangSmith tracing: {'enabled' if self.config.LANGCHAIN_TRACING_V2 else 'disabled'}")
        print()
    
    def _enable_tracing(self):
        """Enable LangSmith tracing"""
        enabled = self.config.enable_langsmith_tracing()
        if not enabled:
            print("⚠️  LangSmith tracing requested but API key not configured")
    
    def run(
        self,
        news_text: str,
        timestamp: datetime,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Analyze news and generate trade signals.

        Flow:
          1. Pre-classify (UNTRACED, via tracing_context(enabled=False)):
             base_context_node + significance_check_node.
             If classifier marks news as not significant → return [] (no LangSmith trace created).
          2. Otherwise call the @traceable inner method to run the full analysis graph.
        """
        print(f"\n{'='*70}")
        print("ANALYZING NEWS")
        print(f"{'='*70}")

        eastern_tz = pytz.timezone('America/New_York')
        if timestamp.tzinfo is None:
            timestamp_et = eastern_tz.localize(timestamp)
            print("⚠️  Naive timestamp provided - assuming US Eastern Time")
        else:
            timestamp_et = timestamp.astimezone(eastern_tz)
            if timestamp.tzinfo != eastern_tz:
                print(f"ℹ️  Converting from {timestamp.tzinfo} to US Eastern Time")

        print(f"Timestamp: {timestamp_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"News preview: {news_text[:200]}...")
        print(f"{'='*70}\n")

        timestamp_et = self.db_manager.validate_timestamp(timestamp_et)

        # --- Trading hours gate ------------------------------------------------
        # News must fall inside the regular US session AND at least
        # MIN_TIME_BEFORE_CLOSE_MINUTES before the close so the 40-min trade
        # window stays inside RTH. Filters weekends + after/pre-market + last hour.
        cfg = self.config
        open_t = time(cfg.MARKET_OPEN_HOUR, cfg.MARKET_OPEN_MINUTE)
        latest_entry = (
            datetime.combine(timestamp_et.date(), time(cfg.MARKET_CLOSE_HOUR, cfg.MARKET_CLOSE_MINUTE))
            - timedelta(minutes=cfg.MIN_TIME_BEFORE_CLOSE_MINUTES)
        ).time()
        is_weekday = timestamp_et.weekday() < 5
        in_window = is_weekday and open_t <= timestamp_et.time() <= latest_entry
        if not in_window:
            reason = (
                "weekend" if not is_weekday
                else f"outside trading window {open_t.strftime('%H:%M')}–{latest_entry.strftime('%H:%M')} ET"
            )
            print(f"\n{'='*70}")
            print(f"ANALYSIS SKIPPED — {reason}")
            print(f"News time: {timestamp_et.strftime('%a %H:%M %Z')}")
            print("(No classifier call, no LangSmith trace.)")
            print(f"{'='*70}\n")
            self.last_run_id = None
            return []

        initial_state: TradingAgentState = {
            "news_text": news_text,
            "news_id": metadata.get("news_id") if metadata else None,
            "timestamp": timestamp_et,
            "messages": [],
            "recent_news": [],
            "similar_news": [],
            "macro_context": {},
            "is_significant": None,
            "significance_reasoning": "",
            "action_plan": {},
            "plan_steps": [],
            "tickers": [],
            "analysis_results": {},
            "signals": [],
            "plan_deviations": [],
            "reflection_count": 0,
            "reflection_advice": [],
            "needs_refinement": False,
            "loop_to": "main_analysis",
            "risk_flags": {},
            "filtered_signals": [],
            "iteration": 0,
            "max_iterations": self.config.MAX_REFLECTION_ITERATIONS,
        }
        if metadata:
            initial_state["_metadata"] = metadata

        # --- Pre-classify with tracing disabled ---------------------------------
        # No LangSmith trace is produced for these calls. If the classifier
        # rejects the news, nothing about this run reaches LangSmith.
        self.last_run_id = None
        try:
            with tracing_context(enabled=False):
                initial_state = base_context_node(initial_state, self.db_manager, self.config)
                initial_state = significance_check_node(
                    initial_state, self.llm, self.db_manager, self.config
                )
        except LLMInfraError:
            # Quota/rate-limit/transport outage: propagate so the batch marks this
            # item as a real error and retries it later (do NOT save a fake 0-signal row).
            raise
        except Exception as e:
            # Genuine logic error in pre-classification: fail closed (skip downstream graph).
            print(f"⚠️  Pre-classification logic error (treating as NOT significant): {e}")
            initial_state["is_significant"] = False
            initial_state["significance_reasoning"] = f"Pre-classification logic error: {e}"

        if initial_state.get("is_significant") is False:
            print(f"\n{'='*70}")
            print("ANALYSIS SKIPPED — news classified as NOT SIGNIFICANT")
            print(f"Reason: {initial_state.get('significance_reasoning', 'n/a')}")
            print("(No LangSmith trace created.)")
            print(f"{'='*70}\n")
            return []

        # --- Full analysis (traced) --------------------------------------------
        return self._run_main_analysis(initial_state, metadata, timestamp_et, news_text)

    @traceable(name="run_trading_agent", run_type="chain")
    def _run_main_analysis(
        self,
        initial_state: TradingAgentState,
        metadata: Optional[Dict[str, Any]],
        timestamp_et: datetime,
        news_text: str,
    ) -> List[Dict[str, Any]]:
        """Run the main LangGraph analysis (traced). Called only for significant news."""
        rt = get_current_run_tree()
        if rt:
            self.last_run_id = str(rt.id)

        try:
            config = {
                "run_name": f"news_analysis_{timestamp_et.strftime('%Y%m%d_%H%M%S')}",
                "tags": ["news-analysis", "trading-signal", "production"],
                "metadata": {
                    "timestamp": timestamp_et.isoformat(),
                    "timezone": "America/New_York",
                    "news_length": len(news_text),
                    "confidence_threshold": self.config.CONFIDENCE_THRESHOLD,
                    "significance_confidence_threshold": self.config.SIGNIFICANCE_CONFIDENCE_THRESHOLD,
                    **(metadata or {}),
                },
            }

            result = self.graph.invoke(initial_state, config=config)

            filtered_signals = result.get("filtered_signals", result.get("signals", []))
            total_signals = len(result.get("signals", []))

            print(f"\n{'='*70}")
            print("ANALYSIS COMPLETE")
            print(f"{'='*70}")
            print(f"Generated {total_signals} signal(s)")
            print(f"Final signals after risk assessment: {len(filtered_signals)}")
            print(f"Reflection iterations: {result.get('reflection_count', 0)}")
            print(f"{'='*70}\n")

            return filtered_signals

        except Exception as e:
            print(f"\n✗ Error running agent: {e}")
            raise
    
    @traceable(name="batch_analyze", run_type="chain")
    def batch_analyze(
        self,
        news_items: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Analyze multiple news articles in batch
        
        Args:
            news_items: List of dicts with 'text' and 'timestamp' keys
            Example: [
                {"text": "Apple announces...", "timestamp": datetime(...)},
                {"text": "Microsoft launches...", "timestamp": datetime(...)}
            ]
            
        Returns:
            List of signal lists (one per news item)
        """
        
        print(f"\n{'='*70}")
        print(f"BATCH ANALYSIS: {len(news_items)} news items")
        print(f"{'='*70}\n")
        
        results = []
        for idx, item in enumerate(news_items, 1):
            print(f"\n--- Processing item {idx}/{len(news_items)} ---\n")
            
            signals = self.run(
                news_text=item['text'],
                timestamp=item['timestamp'],
                metadata={
                    "batch_index": idx,
                    "total_items": len(news_items)
                }
            )
            results.append(signals)
        
        print(f"\n{'='*70}")
        print("BATCH ANALYSIS COMPLETE")
        print(f"{'='*70}")
        print(f"Total signals generated: {sum(len(r) for r in results)}")
        print(f"{'='*70}\n")
        
        return results
    
    def close(self):
        """Close database connections"""
        if self.neo4j_manager:
            self.neo4j_manager.close()
        if self.db_manager:
            self.db_manager.close_all_connections()
            print("✓ Trading Agent closed")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


# Convenience function for quick usage
def analyze_news(
    news_text: str,
    timestamp: datetime,
    config: Optional[AgentConfig] = None
) -> List[Dict[str, Any]]:
    """
    Convenience function to analyze news without creating agent instance
    
    Args:
        news_text: News article text
        timestamp: Publication timestamp
        config: Optional custom configuration
        
    Returns:
        List of trade signals
    """
    with TradingAgent(config) as agent:
        return agent.run(news_text, timestamp)



