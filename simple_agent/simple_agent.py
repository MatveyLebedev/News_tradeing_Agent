"""
Simple Trading Agent - Single LLM call with recent news context

Input format: news_text (str), timestamp (datetime)
Output format: List[Dict] with keys: ticker, signal, confidence, reasoning
"""
import os
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import pytz
from langchain_openai import ChatOpenAI
from langsmith import traceable

_cfg_path = Path(__file__).parent / "config.json"
_cfg = json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}


class SimpleAgent:
    """
    Simple AI Trading Agent that analyzes news with a single LLM call
    
    Features:
    - Single LLM call (no graph, no tools)
    - Uses recent news as context
    - Same input/output format as complex agent
    
    Example:
        >>> from simple_agent import SimpleAgent
        >>> from datetime import datetime
        >>> 
        >>> agent = SimpleAgent()
        >>> signals = agent.run(
        ...     news_text="Apple announces breakthrough M4 chip...",
        ...     timestamp=datetime(2025, 11, 28, 14, 30)
        ... )
        >>> 
        >>> for signal in signals:
        ...     print(f"{signal['ticker']}: {signal['signal']}")
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        temperature: float = 0.0,
        db_config: Optional[Dict[str, str]] = None,
        enable_langsmith: bool = False
    ):
        """
        Initialize Simple Agent
        
        Args:
            model: Open Router model name (default: from LLM_MODEL env var)
            temperature: LLM temperature (default: 0.0)
            db_config: Database configuration dict (optional)
            enable_langsmith: Enable LangSmith tracing (default: False)
        """
        print("\n=== Initializing Simple Trading Agent (Baseline) ===")
        
        self.model_name = model or os.getenv("LLM_MODEL") or _cfg.get("LLM_MODEL")
        if not self.model_name:
            raise ValueError("Model name not provided and LLM_MODEL not set in environment or config.json")
            
        self.temperature = temperature
        self.enable_langsmith = enable_langsmith
        
        if enable_langsmith:
            self._enable_langsmith_tracing()
        
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")
        
        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            model=self.model_name,
            temperature=temperature,
            max_tokens=8000,
        )
        
        print(f"✓ LLM initialized (Open Router): {model}")
        print(f"  Temperature: {temperature}")
        
        # Database configuration - from environment variables
        self.db_config = db_config or {
            "host": os.getenv("DB_HOST"),
            "database": os.getenv("DB_NAME", "trade_agent"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "port": os.getenv("DB_PORT", "5432")
        }
        
        # Initialize DB connection (lazy)
        self._db_conn = None
        
        print("✓ Simple Agent initialized")
        if self.enable_langsmith:
            print("✓ LangSmith tracing enabled")
        print()
    
    def _enable_langsmith_tracing(self):
        """Enable LangSmith tracing"""
        langsmith_api_key = os.getenv("LANGCHAIN_API_KEY")
        if langsmith_api_key:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
            os.environ["LANGCHAIN_ENDPOINT"] = _cfg.get("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
            os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
            os.environ["LANGCHAIN_PROJECT"] = _cfg.get("LANGCHAIN_PROJECT", "baseline-agent")
        else:
            print("⚠️  LangSmith tracing requested but LANGCHAIN_API_KEY not set")
            self.enable_langsmith = False
    
    def _get_db_connection(self):
        """Get or create database connection"""
        if self._db_conn is None or self._db_conn.closed:
            import psycopg2
            self._db_conn = psycopg2.connect(**self.db_config)
        return self._db_conn
    
    def _is_us_market_ticker(self, ticker: str) -> bool:
        """
        Check if ticker is from US market (not foreign exchange)
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            True if US market ticker, False otherwise
        """
        # Remove common non-US market suffixes
        non_us_suffixes = [
            '.TO',   # Toronto Stock Exchange (Canada)
            '.L',    # London Stock Exchange
            '.PA',   # Euronext Paris
            '.DE',   # Deutsche Börse (Germany)
            '.HK',   # Hong Kong Stock Exchange
            '.T',    # Tokyo Stock Exchange
            '.AX',   # Australian Securities Exchange
            '.SW',   # Swiss Exchange
            '.MI',   # Borsa Italiana (Italy)
            '.AS',   # Euronext Amsterdam
            '.BR',   # Euronext Brussels
            '.HE',   # Helsinki Stock Exchange
            '.ST',   # Stockholm Stock Exchange
            '.OL',   # Oslo Stock Exchange
            '.CO',   # Copenhagen Stock Exchange
            '.SA',   # São Paulo Stock Exchange (Brazil)
            '.MX',   # Mexican Stock Exchange
            '.NZ',   # New Zealand Stock Exchange
            '.SI',   # Singapore Stock Exchange
            '.KS',   # Korea Stock Exchange
            '.TW',   # Taiwan Stock Exchange
        ]
        
        ticker_upper = ticker.upper()
        
        # Check for non-US suffixes
        for suffix in non_us_suffixes:
            if ticker_upper.endswith(suffix):
                return False
        
        return True
    
    def _get_recent_news(self, timestamp: datetime, hours: int = 24, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent news from database
        
        Args:
            timestamp: Reference timestamp (only news before this)
            hours: Hours to look back (default: 24)
            limit: Maximum number of news items (default: 10)
            
        Returns:
            List of news items with id, title, summary, published
        """
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            query = """
                SELECT id, title, summary, published
                FROM news
                WHERE published < %s
                  AND published >= %s - INTERVAL '%s hours'
                ORDER BY published DESC
                LIMIT %s
            """
            
            cursor.execute(query, (timestamp, timestamp, hours, limit))
            rows = cursor.fetchall()
            
            news_items = []
            for row in rows:
                news_items.append({
                    "id": row[0],
                    "title": row[1],
                    "summary": row[2],
                    "published": row[3].isoformat() if row[3] else None
                })
            
            cursor.close()
            
            if news_items:
                print(f"✓ Retrieved {len(news_items)} recent news items")
            else:
                print(f"No recent news found in last {hours} hours")
            
            return news_items
            
        except Exception as e:
            print(f"⚠️  Error retrieving recent news: {e}")
            return []
    
    @traceable(name="baseline_agent", run_type="chain")
    def run(
        self,
        news_text: str,
        timestamp: datetime,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Analyze news and generate trade signals with single LLM call
        
        Args:
            news_text: News article text to analyze
            timestamp: Publication timestamp (naive datetime assumed as ET, or timezone-aware)
            metadata: Optional metadata (not used in simple agent)
            
        Returns:
            List of trade signals
            Each signal contains:
            - ticker: Stock ticker symbol
            - signal: "buy" or "sell"
            - confidence: Confidence score (0-1)
            - reasoning: Detailed explanation
        """
        print(f"\n{'='*70}")
        print(f"ANALYZING NEWS (Simple Agent)")
        print(f"{'='*70}")
        
        # Convert timestamp to Eastern Time if needed
        eastern_tz = pytz.timezone('America/New_York')
        if timestamp.tzinfo is None:
            timestamp_et = eastern_tz.localize(timestamp)
            print(f"⚠️  Naive timestamp provided - assuming US Eastern Time")
        else:
            timestamp_et = timestamp.astimezone(eastern_tz)
            if timestamp.tzinfo != eastern_tz:
                print(f"ℹ️  Converting from {timestamp.tzinfo} to US Eastern Time")
        
        print(f"Timestamp: {timestamp_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"News preview: {news_text[:200]}...")
        print(f"{'='*70}\n")
        
        # Get recent news as context
        recent_news = self._get_recent_news(timestamp_et, hours=24, limit=10)
        
        # Build context string
        context_str = ""
        if recent_news:
            context_str = "\n\nRECENT NEWS CONTEXT (last 24 hours):\n"
            for i, news in enumerate(recent_news, 1):
                context_str += f"\n{i}. {news['title']}\n"
                context_str += f"   Published: {news['published']}\n"
                if news['summary']:
                    context_str += f"   Summary: {news['summary']}...\n"
        
        # Create system prompt
        system_prompt = """You are a trading signal generator. Analyze the news and generate trading signals.

Your task:
1. Identify relevant stock tickers mentioned in the news
2. Determine if the news is positive (buy signal) or negative (sell signal) for each ticker
3. Assess confidence level (0.0 to 1.0)
4. Provide clear reasoning

Output format (JSON array):
[
  {
    "ticker": "AAPL",
    "signal": "buy",
    "confidence": 0.85,
    "reasoning": "News positive effect: Major product breakthrough announced with strong market potential"
  }
]

Rules:
- **ONLY include US market tickers** (no .TO, .L, .PA, .DE, .HK, .T, .AX, .SW, .MI, .AS, etc. suffixes)
- Only include tickers with clear trading implications
- signal must be "buy" or "sell" (lowercase)
- confidence must be between 0.0 and 1.0
- reasoning should explain why the news has positive or negative effect
- Return empty array [] if no clear signals
- Consider recent news context to avoid duplicate signals or assess market conditions

Examples of VALID tickers: AAPL, MSFT, GOOGL, TSLA, NVDA (US market only)
Examples of INVALID tickers: AIR.PA (Paris), LUN.TO (Toronto), VOD.L (London)

Return ONLY the JSON array, no additional text."""

        # Create user prompt
        user_prompt = f"""NEWS TO ANALYZE:
{news_text}
{context_str}

Generate trading signals based on this news."""

        # Make single LLM call
        try:
            print("Making LLM call...")
            
            from langchain_core.messages import SystemMessage, HumanMessage
            response = self.llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            response_text = response.content.strip()
            
            # Parse JSON response
            # Remove markdown code blocks if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1])
            
            signals = json.loads(response_text)
            
            # Validate format
            if not isinstance(signals, list):
                print("⚠️  Invalid response format (not a list)")
                return []
            
            # Validate each signal
            validated_signals = []
            filtered_non_us = []
            
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                
                # Check required fields
                if not all(k in signal for k in ["ticker", "signal", "confidence", "reasoning"]):
                    print(f"⚠️  Skipping signal with missing fields: {signal}")
                    continue
                
                # Validate ticker is US market
                ticker = signal["ticker"]
                if not self._is_us_market_ticker(ticker):
                    print(f"⚠️  Filtering non-US market ticker: {ticker}")
                    filtered_non_us.append(ticker)
                    continue
                
                # Validate signal type
                if signal["signal"].lower() not in ["buy", "sell"]:
                    print(f"⚠️  Invalid signal type: {signal['signal']}")
                    continue
                
                # Validate confidence
                try:
                    conf = float(signal["confidence"])
                    if not (0.0 <= conf <= 1.0):
                        print(f"⚠️  Invalid confidence: {conf}")
                        continue
                    signal["confidence"] = conf
                except (ValueError, TypeError):
                    print(f"⚠️  Invalid confidence value: {signal['confidence']}")
                    continue
                
                # Normalize signal to lowercase
                signal["signal"] = signal["signal"].lower()
                
                validated_signals.append(signal)
            
            # Report filtered tickers
            if filtered_non_us:
                print(f"ℹ️  Filtered {len(filtered_non_us)} non-US market ticker(s): {', '.join(filtered_non_us)}")
            
            print(f"\n{'='*70}")
            print(f"ANALYSIS COMPLETE")
            print(f"{'='*70}")
            print(f"Generated {len(validated_signals)} signal(s)")
            print(f"{'='*70}\n")
            
            if validated_signals:
                for i, signal in enumerate(validated_signals, 1):
                    print(f"{i}. {signal['ticker']} - {signal['signal'].upper()}")
                    print(f"   Confidence: {signal['confidence']:.2%}")
                    print(f"   Reasoning: {signal['reasoning'][:100]}...")
                print()
            
            return validated_signals
            
        except json.JSONDecodeError as e:
            print(f"✗ Error parsing LLM response as JSON: {e}")
            print(f"Response: {response_text[:500]}")
            return []
        except Exception as e:
            print(f"✗ Error during analysis: {e}")
            return []
    
    def close(self):
        """Close database connection"""
        if self._db_conn and not self._db_conn.closed:
            self._db_conn.close()
            print("✓ Simple Agent closed")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

