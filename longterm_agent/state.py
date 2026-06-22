"""State schema for the long-term agent graph."""
from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime


class LongTermAgentState(TypedDict, total=False):
    # --- Input ---
    ticker: str
    company_name: str
    as_of: datetime
    lookback_days: int
    shift_window_days: int

    # --- Message history (reasoning chain, LangChain-native like news_agent) ---
    messages: List[Dict[str, Any]]

    # --- corpus_collection ---
    news_corpus: List[Dict[str, Any]]      # id, published, title, summary, affects_sentiment?, sources
    corpus_provenance: Dict[str, int]
    period_buckets: List[Dict[str, Any]]   # weekly buckets
    article_count: int
    no_data: bool                          # True → early NO_DATA exit

    # --- background_profiling ---
    bucket_summaries: List[Dict[str, Any]]  # map-step outputs
    background_profile: Dict[str, Any]      # reduce output (the "news background")

    # --- shift_detection (later phase) ---
    background_metrics: Dict[str, Any]
    background_shift: Dict[str, Any]

    # --- fundamentals / analogs / synthesis (later phases) ---
    fundamentals: Dict[str, Any]
    technical_longterm: Dict[str, Any]
    shift_analogs: Dict[str, Any]

    # --- deep_analysis (Phase 7A; advisory tool-calling enrichment, never edits background_shift) ---
    enriched_analysis: Dict[str, Any]

    signal: Dict[str, Any]

    # --- reflection / risk ---
    reflection_count: int
    needs_refinement: bool
    loop_to: str
    risk_flags: List[str]
    risk_checks: Dict[str, Any]
    filtered_signal: Optional[Dict[str, Any]]

    # --- meta ---
    _metadata: Optional[Dict[str, Any]]
