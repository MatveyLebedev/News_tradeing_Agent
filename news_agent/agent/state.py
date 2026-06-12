"""
State schema for the Trading Agent - Message-Based Architecture
"""
from typing import TypedDict, List, Dict, Optional, Any
from datetime import datetime


class TradingAgentState(TypedDict, total=False):
    """
    Message-based state schema for the trading agent graph
    
    The agent uses a message-based architecture where all reasoning
    and decisions are tracked as messages, enabling full conversation history
    """
    
    # Input data
    news_text: str
    news_id: Optional[int]
    timestamp: datetime
    
    # Message history (core of message-based architecture)
    messages: List[Dict[str, Any]]  # All agent messages/reasoning
    # Message format: {"role": "system"|"assistant"|"tool", "content": str, 
    #                  "name": Optional[str], "timestamp": datetime, "data": Optional[Dict]}
    
    # Base context (from base_context_node)
    recent_news: List[Dict[str, Any]]  # 10 most recent news items
    similar_news: List[Dict[str, Any]]  # 10 similar news with price impact

    # Significance classifier (from significance_check_node)
    is_significant: Optional[bool]  # None = not yet decided, True/False = classifier verdict
    significance_reasoning: str  # Reasoning behind the classifier decision
    
    # Macro context (from macro_context_node)
    macro_context: Dict[str, Any]  # event distribution, top stories, story momentum
    
    # Planning (from planning_node)
    action_plan: Dict[str, Any]  # LLM-generated action plan
    plan_steps: List[str]  # Specific steps to execute
    
    # Analysis results (from main_analysis_node)
    tickers: List[str]  # Identified tickers
    analysis_results: Dict[str, Any]  # All analysis data
    signals: List[Dict[str, Any]]  # Generated trading signals
    plan_deviations: List[str]  # Track when LLM deviates from plan
    
    # Reflection (from reflection_node)
    reflection_count: int  # Number of reflection iterations
    reflection_advice: List[str]  # Advice from reflection
    needs_refinement: bool  # Whether to loop back
    loop_to: str  # "planning" or "main_analysis" - where to loop back
    
    # Risk filtering (from risk_assessment_node)
    risk_flags: Dict[str, List[str]]  # Risk flags per ticker
    filtered_signals: List[Dict[str, Any]]  # Final filtered signals
    
    # Metadata
    iteration: int  # Current iteration number
    max_iterations: int  # Maximum reflection loops allowed (default: 2-3)
    _metadata: Optional[Dict[str, Any]]  # Optional metadata for tracing
