#!/usr/bin/env python3
"""
Batch News Analysis Script

Features:
- Count news for selected period
- Filter by news type (optional)
- Select agent (simple or complex)
- Confirm before running
- Store results in database table
"""
import os
import sys
import psycopg2
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import pytz
import time
from tqdm import tqdm
from langsmith import traceable
from functools import partial

# Per-thread agent instances for parallel batch runs
_thread_local = threading.local()

# Add parent directory to path for imports (batch_analysis is subdirectory)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import config for LangSmith settings
try:
    from config import (
        LANGCHAIN_TRACING_V2,
        LANGCHAIN_API_KEY,
        LANGCHAIN_ENDPOINT
    )
except ImportError:
    # Fallback if config not available
    LANGCHAIN_TRACING_V2 = "true"
    LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_ENDPOINT = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")

# LangSmith project name for batch runs
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "batch-agent-runs")

# Eastern timezone: America/New_York (EDT in summer, EST in winter) — для рыночных часов
EASTERN_TZ = pytz.timezone("America/New_York")
POSTGRES_EASTERN_TZ = "America/New_York"

# Import agent versions from their modules
try:
    from simple_agent import __version__ as SIMPLE_AGENT_VERSION
except ImportError:
    SIMPLE_AGENT_VERSION = "1.0.0"  # Fallback

try:
    from news_agent import __version__ as COMPLEX_AGENT_VERSION
except ImportError:
    COMPLEX_AGENT_VERSION = "0.1.0"  # Fallback


class BatchAnalyzer:
    """Batch analyzer for running agents on multiple news items"""
    
    def __init__(self, enable_langsmith: bool = True):
        """
        Initialize batch analyzer
        
        Args:
            enable_langsmith: Enable LangSmith tracing (default: True)
        """
        # Load .env if available
        try:
            from dotenv import load_dotenv
            from pathlib import Path
            env_path = Path(__file__).parent.parent / '.env'
            if env_path.exists():
                load_dotenv(env_path)
        except ImportError:
            pass  # python-dotenv not required if using system env vars
        
        self.db_config = {
            "host": os.getenv("DB_HOST"),
            "database": os.getenv("DB_NAME", "trade_agent"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "port": os.getenv("DB_PORT", "5432")
        }
        self.conn = None
        self.agent = None
        self.enable_langsmith = enable_langsmith
        
        # CRITICAL: Enable LangSmith tracing BEFORE any agent imports
        # This ensures environment variables are set before agents are initialized
        if self.enable_langsmith:
            self._enable_langsmith_tracing()
    
    def _enable_langsmith_tracing(self):
        """
        Enable LangSmith tracing for batch runs
        
        CRITICAL: This must be called BEFORE any agent modules are imported.
        The @traceable decorator on agent methods needs these environment variables
        to be set when the decorator is evaluated (at import time).
        """
        # Use config values, with fallback to environment variables
        langsmith_api_key = LANGCHAIN_API_KEY or os.getenv("LANGCHAIN_API_KEY")
        if langsmith_api_key:
            # Set environment variables BEFORE any agent initialization
            # This is critical for LangSmith to work correctly
            os.environ["LANGCHAIN_TRACING_V2"] = LANGCHAIN_TRACING_V2 or os.getenv("LANGCHAIN_TRACING_V2", "true")
            os.environ["LANGCHAIN_ENDPOINT"] = LANGCHAIN_ENDPOINT or os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
            os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
            # Use module-level project name (can be overridden by environment variable)
            project_name = os.getenv("LANGCHAIN_PROJECT", LANGCHAIN_PROJECT)
            os.environ["LANGCHAIN_PROJECT"] = project_name
            print("✓ LangSmith tracing enabled for batch runs")
            print(f"  Project: {project_name}")
            print(f"  Endpoint: {os.environ.get('LANGCHAIN_ENDPOINT')}")
        else:
            print("⚠️  LangSmith tracing requested but LANGCHAIN_API_KEY not set")
            self.enable_langsmith = False
        
    def connect_db(self):
        """Connect to database"""
        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(**self.db_config)
        return self.conn

    def _ensure_connection(self):
        """Reconnect if server closed the connection (e.g. after long idle)."""
        try:
            conn = self.connect_db()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if self.conn and not self.conn.closed:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = None
            self.conn = psycopg2.connect(**self.db_config)
    
    def create_results_table(self):
        """Create agent_results table if it doesn't exist"""
        conn = self.connect_db()
        cursor = conn.cursor()
        
        # Read SQL file
        sql_file = os.path.join(os.path.dirname(__file__), "create_agent_results_table.sql")
        with open(sql_file, 'r') as f:
            sql = f.read()
        
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        
        print("✓ Database table ready: agent_results")
    
    def _get_type_column_name(self, cursor) -> str:
        """
        Check which column exists for filtering: 'type' or 'source'
        
        Args:
            cursor: Database cursor
            
        Returns:
            Column name to use: 'type' or 'source'
        """
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'news' AND column_name IN ('type', 'source')
            ORDER BY column_name = 'type' DESC
        """)
        result = cursor.fetchone()
        return result[0] if result else 'source'  # Default to 'source' if neither exists
    
    def get_available_news_types(self) -> List[str]:
        """
        Get list of available news types from database
        
        Checks for 'type' column first, falls back to 'source' column if 'type' doesn't exist.
        
        Returns:
            List of news type/source strings
        """
        conn = self.connect_db()
        cursor = conn.cursor()
        
        try:
            # Get the column name to use
            column_name = self._get_type_column_name(cursor)
            
            query = f"""
                SELECT DISTINCT {column_name} 
                FROM news 
                WHERE {column_name} IS NOT NULL 
                  AND {column_name} != ''
                ORDER BY {column_name}
            """
            
            cursor.execute(query)
            rows = cursor.fetchall()
            
            types = [row[0] for row in rows if row[0]]
            cursor.close()
            
            return types
            
        except Exception as e:
            # If anything goes wrong, return empty list
            cursor.close()
            print(f"  ⚠️  Could not fetch news types: {e}")
            return []
    
    def count_news(
        self,
        start_date: datetime,
        end_date: datetime,
        news_type: Optional[str] = None
    ) -> int:
        """
        Count news items in date range
        
        Args:
            start_date: Start of period
            end_date: End of period
            news_type: Optional filter by news type
            
        Returns:
            Count of news items
        """
        conn = self.connect_db()
        cursor = conn.cursor()
        
        if news_type:
            # Get the column name to use
            column_name = self._get_type_column_name(cursor)
            
            query = f"""
                SELECT COUNT(*) 
                FROM news 
                WHERE published >= %s 
                  AND published < %s
                  AND {column_name} = %s
            """
            cursor.execute(query, (start_date, end_date, news_type))
        else:
            query = """
                SELECT COUNT(*) 
                FROM news 
                WHERE published >= %s 
                  AND published < %s
            """
            cursor.execute(query, (start_date, end_date))
        
        count = cursor.fetchone()[0]
        cursor.close()
        
        return count
    
    def get_news(
        self,
        start_date: datetime,
        end_date: datetime,
        news_type: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get news items in date range
        
        Args:
            start_date: Start of period
            end_date: End of period
            news_type: Optional filter by news type
            limit: Optional limit on results
            
        Returns:
            List of news items
        """
        conn = self.connect_db()
        cursor = conn.cursor()
        
        # Get the column name to use
        column_name = self._get_type_column_name(cursor)
        
        if news_type:
            query = f"""
                SELECT id, title, summary, full_text, published, {column_name}
                FROM news 
                WHERE published >= %s 
                  AND published < %s
                  AND {column_name} = %s
                ORDER BY published ASC
            """
            params = [start_date, end_date, news_type]
        else:
            query = f"""
                SELECT id, title, summary, full_text, published, {column_name}
                FROM news 
                WHERE published >= %s 
                  AND published < %s
                ORDER BY published ASC
            """
            params = [start_date, end_date]
        
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        news_items = []
        for row in rows:
            news_items.append({
                "id": row[0],
                "title": row[1],
                "summary": row[2],
                "full_text": row[3],
                "published": row[4],
                "type": row[5] if len(row) > 5 else None  # Store as 'type' for consistency
            })
        
        cursor.close()
        return news_items

    def get_news_recent(self, limit: int = 50, tradable_only: bool = False) -> List[Dict[str, Any]]:
        """Get the N most recent news items (by published DESC)."""
        conn = self.connect_db()
        cursor = conn.cursor()
        column_name = self._get_type_column_name(cursor)
        tradable_filter = ""
        if tradable_only:
            tradable_filter = """
                AND EXTRACT(DOW FROM published) BETWEEN 1 AND 5
                AND published::time >= '09:30:00'
                AND published::time <= '15:00:00'
            """
        cursor.execute(
            f"""
            SELECT id, title, summary, full_text, published, {column_name}
            FROM news
            WHERE 1=1 {tradable_filter}
            ORDER BY published DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        news_items = [
            {
                "id": row[0],
                "title": row[1],
                "summary": row[2],
                "full_text": row[3],
                "published": row[4],
                "type": row[5] if len(row) > 5 else None,
            }
            for row in rows
        ]
        cursor.close()
        return news_items

    def get_news_last_in_market_hours(
        self,
        limit: int = 3,
        open_hour: int = 9,
        open_minute: int = 30,
        close_hour: int = 16,
        close_minute: int = 0,
        open_offset_minutes: int = 30,
        close_offset_minutes: int = 60,
        only_today: bool = True,
        on_date: Optional[datetime.date] = None
    ) -> List[Dict[str, Any]]:
        """
        Последние N новостей в торгуемые часы (ET). По умолчанию: только сегодня, окно 10:00–15:00.
        open_offset_minutes: минут после открытия (по умолч. 30 → с 10:00).
        close_offset_minutes: минут до закрытия (по умолч. 60 → до 15:00).
        only_today: только новости за сегодня по Eastern (по умолчанию True).
        on_date: если задано — фильтр по этой дате (ET).
        """
        from datetime import time
        conn = self.connect_db()
        cursor = conn.cursor()
        column_name = self._get_type_column_name(cursor)
        open_mins = open_hour * 60 + open_minute + open_offset_minutes
        close_mins = close_hour * 60 + close_minute - close_offset_minutes
        start_m = open_mins % (24 * 60)
        end_m = close_mins % (24 * 60)
        open_t = time(start_m // 60, start_m % 60, 0)
        close_t = time(end_m // 60, end_m % 60, 0)
        # В БД published хранится как naive Eastern (см. news_fetcher). Не используем AT TIME ZONE:
        # иначе ::time даёт время в таймзоне сессии БД (часто UTC), а не Eastern.
        date_cond = ""
        params = [open_t, close_t]
        use_date = on_date if on_date is not None else (datetime.now(EASTERN_TZ).date() if only_today else None)
        if use_date is not None:
            date_cond = " AND published::date = %s"
            params = [open_t, close_t, use_date]
        query = f"""
            SELECT id, title, summary, full_text, published, {column_name}
            FROM news
            WHERE (
                published::time >= %s
                AND published::time < %s
                {date_cond}
            )
            ORDER BY published DESC
            LIMIT %s
        """
        params.append(limit)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        news_items = [
            {
                "id": row[0],
                "title": row[1],
                "summary": row[2],
                "full_text": row[3],
                "published": row[4],
                "type": row[5] if len(row) > 5 else None,
            }
            for row in rows
        ]
        cursor.close()
        return news_items

    def initialize_agent(self, agent_type: str):
        """
        Initialize selected agent
        
        Args:
            agent_type: 'simple' or 'complex'
        """
        # CRITICAL: Ensure LangSmith environment is set BEFORE importing/initializing agent
        # This is required for LangSmith tracing to work correctly
        if self.enable_langsmith:
            langsmith_api_key = os.getenv("LANGCHAIN_API_KEY")
            if langsmith_api_key:
                os.environ["LANGCHAIN_TRACING_V2"] = "true"
                os.environ["LANGCHAIN_ENDPOINT"] = os.getenv(
                    "LANGCHAIN_ENDPOINT",
                    "https://api.smith.langchain.com"
                )
                os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
                os.environ["LANGCHAIN_PROJECT"] = os.getenv(
                    "LANGCHAIN_PROJECT",
                    "batch-agent-runs"
                )
        
        if agent_type == 'simple':
            from simple_agent import SimpleAgent
            # Enable LangSmith for simple agent if batch tracing is enabled
            self.agent = SimpleAgent(enable_langsmith=self.enable_langsmith)
            self.agent_name = 'simple_agent'
            self.agent_version = SIMPLE_AGENT_VERSION
        elif agent_type == 'complex':
            from news_agent import TradingAgent
            # Complex agent uses config-based LangSmith, ensure it's enabled
            # Environment variables are already set above
            self.agent = TradingAgent()
            self.agent_name = 'complex_agent'
            self.agent_version = COMPLEX_AGENT_VERSION
        else:
            raise ValueError(f"Unknown agent type: {agent_type}")
        
        print(f"✓ Initialized {self.agent_name} v{self.agent_version}")
        if self.enable_langsmith:
            print(f"  LangSmith tracing: enabled (trace name: {self.agent_name}-autorun)")
    
    def check_existing_result(
        self,
        news_id: int,
        agent_name: str,
        agent_version: str
    ) -> bool:
        """
        Check if a news item has already been analyzed by the same agent version
        
        Args:
            news_id: News item ID
            agent_name: Agent name ('simple_agent' or 'complex_agent')
            agent_version: Agent version
            
        Returns:
            True if result exists, False otherwise
        """
        conn = self.connect_db()
        cursor = conn.cursor()
        
        query = """
            SELECT COUNT(*) 
            FROM agent_results 
            WHERE news_id = %s 
              AND agent_name = %s 
              AND agent_version = %s
        """
        
        cursor.execute(query, (news_id, agent_name, agent_version))
        count = cursor.fetchone()[0]
        cursor.close()
        
        return count > 0
    
    def filter_already_processed(
        self,
        news_items: List[Dict[str, Any]],
        agent_name: str,
        agent_version: str
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter out news items that have already been processed
        
        Args:
            news_items: List of news items to check
            agent_name: Agent name
            agent_version: Agent version
            
        Returns:
            Tuple of (items_to_process, already_processed_items)
        """
        items_to_process = []
        already_processed = []

        if not news_items:
            return items_to_process, already_processed

        conn = self.connect_db()
        cursor = conn.cursor()
        news_ids = [n["id"] for n in news_items]
        cursor.execute(
            """
            SELECT news_id FROM agent_results
            WHERE news_id = ANY(%s)
              AND agent_name = %s
              AND agent_version = %s
            """,
            (news_ids, agent_name, agent_version),
        )
        done_ids = {row[0] for row in cursor.fetchall()}
        cursor.close()

        for news_item in news_items:
            if news_item["id"] in done_ids:
                already_processed.append(news_item)
            else:
                items_to_process.append(news_item)

        return items_to_process, already_processed
    
    def _run_agent_with_trace(self, news_text: str, timestamp: datetime, trace_name: str):
        """
        Wrapper to run agent with LangSmith tracing
        
        Args:
            news_text: News text to analyze (title + summary)
            timestamp: Publication timestamp
            trace_name: Name for LangSmith trace (format: {agent_name}-autorun)
            
        Returns:
            Signals from agent
        """
        # Complex agent traces internally and ONLY for news that passes the
        # significance classifier (see TradingAgent.run). Wrapping it here would
        # create a top-level trace for every run, so we call it directly.
        if self.enable_langsmith and self.agent_name != 'complex_agent':
            # Create a wrapper function that matches agent's actual input signature
            # This allows LangSmith to capture the exact input the agent receives
            def _agent_run(news_text: str, timestamp: datetime):
                # Call agent.run() with these exact parameters
                return self.agent.run(news_text=news_text, timestamp=timestamp)
            
            # Apply traceable decorator to create traced function with custom name
            traced_func = traceable(name=trace_name, run_type="chain")
            wrapped_func = traced_func(_agent_run)
            # Pass the same parameters the agent receives
            return wrapped_func(news_text=news_text, timestamp=timestamp)
        else:
            # No outer wrapper - agent controls its own tracing
            return self.agent.run(news_text=news_text, timestamp=timestamp)
    
    def analyze_news_item(self, news_item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze single news item with agent
        
        Args:
            news_item: News item dict
            
        Returns:
            Analysis results
        """
        # Extract title and summary from database
        title = news_item.get('title', '')
        summary = news_item.get('summary', '')
        
        # Agent input is title + summary (not full_text)
        news_text = f"{title}\n\n{summary}" if summary else title
        
        # Ensure timestamp is timezone-aware (Eastern: EDT/EST)
        timestamp = news_item['published']
        if timestamp.tzinfo is None:
            timestamp = EASTERN_TZ.localize(timestamp)
        
        # Create trace name for this specific run: {agent_name}-autorun
        trace_name = f"{self.agent_name}-autorun"
        
        # Run analysis with LangSmith tracing
        start_time = time.time()
        try:
            signals = self._run_agent_with_trace(news_text, timestamp, trace_name)
            execution_time = time.time() - start_time
            
            return {
                "success": True,
                "signals": signals,
                "signals_count": len(signals),
                "execution_time": execution_time,
                "error": None,
                "infra_error": False,
                "trace_name": trace_name if self.enable_langsmith else None
            }
        except Exception as e:
            execution_time = time.time() - start_time
            # Infra/quota/transport failures should NOT be persisted as a 0-signal
            # result: leaving the item unsaved lets the next run pick it up again.
            try:
                from news_agent.llm_errors import is_infra_error
                infra = is_infra_error(e)
            except Exception:
                infra = False
            if infra:
                print(f"  ⚠️  infra/quota error (will NOT save, retry next run): {e}")
            return {
                "success": False,
                "signals": [],
                "signals_count": 0,
                "execution_time": execution_time,
                "error": str(e),
                "infra_error": infra,
                "trace_name": trace_name if self.enable_langsmith else None
            }
    
    def save_result(
        self,
        news_item: Dict[str, Any],
        analysis_result: Dict[str, Any]
    ):
        """
        Save analysis result to database
        
        Args:
            news_item: News item that was analyzed
            analysis_result: Analysis results
        """
        query = """
            INSERT INTO agent_results (
                agent_name,
                agent_version,
                news_id,
                news_title,
                news_published,
                signals_generated,
                signals_json,
                execution_time_seconds,
                news_type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (
            self.agent_name,
            self.agent_version,
            news_item['id'],
            news_item['title'],
            news_item['published'],
            analysis_result['signals_count'],
            json.dumps(analysis_result['signals']),
            analysis_result['execution_time'],
            news_item.get('type')
        )
        for attempt in range(2):
            try:
                self._ensure_connection()
                conn = self.conn
                cursor = conn.cursor()
                cursor.execute(query, params)
                result_id = cursor.fetchone()[0]
                conn.commit()
                cursor.close()
                return result_id
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                if self.conn and not self.conn.closed:
                    try:
                        self.conn.close()
                    except Exception:
                        pass
                self.conn = None
                if attempt == 1:
                    raise
    
    def run_batch_analysis(
        self,
        start_date: datetime,
        end_date: datetime,
        agent_type: str,
        news_type: Optional[str] = None,
        limit: Optional[int] = None,
        skip_confirm: bool = False,
        news_items_override: Optional[List[Dict[str, Any]]] = None,
        workers: int = 1,
    ):
        """
        Run batch analysis on news items

        Args:
            start_date: Start of period (ignored if news_items_override set)
            end_date: End of period (ignored if news_items_override set)
            agent_type: 'simple', 'complex', or 'both'
            news_type: Optional filter by news type
            limit: Optional limit on number of items to analyze
            skip_confirm: If True, do not ask for confirmation before running
            news_items_override: If set, use this list instead of fetching by date range
        """
        print("\n" + "="*70)
        print("BATCH NEWS ANALYSIS")
        print("="*70)
        
        self.create_results_table()
        
        if news_items_override is not None:
            news_items = news_items_override
            print(f"\nUsing {len(news_items)} pre-selected news items (e.g. last in market hours)")
        else:
            total_count = self.count_news(start_date, end_date, news_type)
            print(f"\nFound {total_count} news items")
            print(f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            if news_type:
                print(f"Type filter: {news_type}")
            if total_count == 0:
                print("\n✗ No news items found in this period")
                return
            analyze_count = min(total_count, limit) if limit else total_count
            if limit and analyze_count < total_count:
                print(f"Limiting analysis to {analyze_count} items (of {total_count})")
            print(f"\nFetching {analyze_count} news items...")
            news_items = self.get_news(start_date, end_date, news_type, analyze_count)
            print(f"✓ Retrieved {len(news_items)} news items")
        
        # Show preview
        print("\nPreview of news items:")
        print("-" * 70)
        for i, item in enumerate(news_items[:5], 1):
            print(f"{i}. {item['title'][:60]}...")
            print(f"   Published: {item['published']}")
        if len(news_items) > 5:
            print(f"   ... and {len(news_items) - 5} more")
        
        # Confirm
        print("\n" + "="*70)
        if agent_type == 'both':
            print(f"Agents: SIMPLE + COMPLEX (both will run)")
        else:
            print(f"Agent: {agent_type.upper()}")
        print(f"Items to analyze: {len(news_items)}")
        if workers > 1:
            print(f"Parallel workers: {workers}")
        if agent_type == 'both':
            print(f"Total analyses: {len(news_items) * 2} (each news item analyzed twice)")
        print("="*70)
        
        if not skip_confirm:
            response = input("\nProceed with analysis? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("\n✗ Analysis cancelled")
                return
        
        # Handle both agents
        if agent_type == 'both':
            all_results = []
            
            # Run simple agent
            print("\n" + "="*70)
            print("RUNNING SIMPLE AGENT")
            print("="*70)
            simple_results = self._run_analysis_loop(news_items, 'simple', workers=workers)
            all_results.extend(simple_results)
            
            # Run complex agent
            print("\n" + "="*70)
            print("RUNNING COMPLEX AGENT")
            print("="*70)
            complex_results = self._run_analysis_loop(news_items, 'complex', workers=workers)
            all_results.extend(complex_results)
            
            # Combined summary
            print("\n" + "="*70)
            print("COMBINED ANALYSIS COMPLETE")
            print("="*70)
            print(f"Simple agent: {len(simple_results)} results")
            print(f"Complex agent: {len(complex_results)} results")
            print(f"Total results: {len(all_results)}")
            print(f"\n✓ All results saved to database table: agent_results")
            print("="*70 + "\n")
            
            return all_results
        else:
            # Single agent
            return self._run_analysis_loop(news_items, agent_type, workers=workers)
    
    @staticmethod
    def _parallel_worker(news_item: Dict[str, Any], agent_type: str, enable_langsmith: bool) -> Dict[str, Any]:
        """Analyze one news item in a worker thread (own agent + DB connection)."""
        if not hasattr(_thread_local, "analyzer"):
            _thread_local.analyzer = BatchAnalyzer(enable_langsmith=enable_langsmith)
            _thread_local.analyzer.initialize_agent(agent_type)
        worker = _thread_local.analyzer
        result = worker.analyze_news_item(news_item)
        # Skip persisting infra/quota failures so they're retried on the next run.
        if not result.get("infra_error"):
            result["result_id"] = worker.save_result(news_item, result)
        return result

    def _run_analysis_loop(self, news_items: List[Dict[str, Any]], agent_type: str, workers: int = 1) -> List[Dict[str, Any]]:
        """Run analysis loop for a single agent."""
        if agent_type == "simple":
            self.agent_name = "simple_agent"
            self.agent_version = SIMPLE_AGENT_VERSION
        else:
            self.agent_name = "complex_agent"
            self.agent_version = COMPLEX_AGENT_VERSION

        print(f"\nChecking for already processed items...")
        items_to_process, already_processed = self.filter_already_processed(
            news_items, self.agent_name, self.agent_version
        )

        if already_processed:
            print(f"  ⚠️  Skipping {len(already_processed)} already processed item(s):")
            for item in already_processed[:5]:
                print(f"    - {item['title'][:60]}...")
            if len(already_processed) > 5:
                print(f"    ... and {len(already_processed) - 5} more")

        if not items_to_process:
            print("\n" + "="*70)
            print("NO ITEMS TO PROCESS")
            print("="*70)
            print(f"All {len(news_items)} items have already been analyzed by {self.agent_name} v{self.agent_version}")
            print("="*70 + "\n")
            return []

        print(f"  ✓ {len(items_to_process)} item(s) to process, {len(already_processed)} already processed")

        print("\n" + "="*70)
        print(f"RUNNING {agent_type.upper()} AGENT ANALYSIS")
        if workers > 1:
            print(f"Workers: {workers}")
        print("="*70 + "\n")

        if workers > 1:
            results, total_signals, total_time, errors = self._run_parallel(
                items_to_process, agent_type, workers
            )
        else:
            results, total_signals, total_time, errors = self._run_sequential(
                items_to_process, agent_type
            )

        print("\n" + "="*70)
        print(f"{agent_type.upper()} AGENT ANALYSIS COMPLETE")
        print("="*70)
        print(f"Items analyzed: {len(items_to_process)}")
        if len(already_processed) > 0:
            print(f"Items skipped (already processed): {len(already_processed)}")
        print(f"Total items: {len(news_items)}")
        print(f"Total signals: {total_signals}")
        if len(items_to_process) > 0:
            print(f"Average signals per item: {total_signals / len(items_to_process):.2f}")
            print(f"Total time: {total_time:.2f}s")
            print(f"Average time per item: {total_time / len(items_to_process):.2f}s")
        if errors > 0:
            print(f"Errors: {errors}")
        if self.enable_langsmith:
            trace_name = f"{self.agent_name}-autorun"
            project_name = os.getenv("LANGCHAIN_PROJECT", "batch-agent-runs")
            print(f"\n✓ LangSmith tracing enabled")
            print(f"  Project: {project_name}")
            print(f"  View at: https://smith.langchain.com")
            print(f"\n  Look for traces with these names:")
            print(f"    - {trace_name} (batch wrapper, if created)")
            if self.agent_name == 'simple_agent':
                print(f"    - baseline_agent (from simple agent - this should always appear)")
            else:
                print(f"    - run_trading_agent (from complex agent - this should always appear)")
            print(f"  Note: The agent's own trace should always be visible if LangSmith is configured.")
        print(f"\n✓ Results saved to database table: agent_results")
        print("="*70 + "\n")

        return results

    def _run_sequential(
        self, items_to_process: List[Dict[str, Any]], agent_type: str
    ) -> Tuple[List[Dict[str, Any]], int, float, int]:
        print("Initializing agent...")
        self.initialize_agent(agent_type)

        results = []
        total_signals = 0
        total_time = 0.0
        errors = 0

        pbar = tqdm(
            items_to_process,
            desc=f"Analyzing ({agent_type})",
            unit="news",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        )

        for news_item in pbar:
            pbar.set_postfix_str(f"signals: {total_signals}, errors: {errors}")
            result = self.analyze_news_item(news_item)
            if result["success"]:
                total_signals += result["signals_count"]
            else:
                errors += 1
            total_time += result["execution_time"]
            # Don't persist infra/quota failures — leave unsaved so they retry next run.
            if not result.get("infra_error"):
                result["result_id"] = self.save_result(news_item, result)
            results.append(result)
            pbar.set_postfix_str(f"signals: {total_signals}, errors: {errors}")

        return results, total_signals, total_time, errors

    def _run_parallel(
        self, items_to_process: List[Dict[str, Any]], agent_type: str, workers: int
    ) -> Tuple[List[Dict[str, Any]], int, float, int]:
        results: List[Dict[str, Any]] = []
        total_signals = 0
        total_time = 0.0
        errors = 0
        lock = threading.Lock()

        pbar = tqdm(
            total=len(items_to_process),
            desc=f"Analyzing ({agent_type}, x{workers})",
            unit="news",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self._parallel_worker, item, agent_type, self.enable_langsmith
                ): item
                for item in items_to_process
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                    with lock:
                        if result["success"]:
                            total_signals += result["signals_count"]
                        else:
                            errors += 1
                        total_time += result["execution_time"]
                        results.append(result)
                        pbar.set_postfix_str(f"signals: {total_signals}, errors: {errors}")
                except Exception as e:
                    with lock:
                        errors += 1
                    tqdm.write(f"✗ id={item.get('id')} error: {e}")
                pbar.update(1)

        pbar.close()
        return results, total_signals, total_time, errors

    def close(self):
        """Close connections"""
        if self.agent:
            if hasattr(self.agent, 'close'):
                self.agent.close()
        if self.conn and not self.conn.closed:
            self.conn.close()


def main():
    """Main function with interactive prompts or CLI (e.g. --days 1 --agent complex --yes)."""
    import argparse
    parser = argparse.ArgumentParser(description="Batch news analysis: run agent on news from DB")
    parser.add_argument("--days", type=int, help="Last N days (non-interactive; uses today as end)")
    parser.add_argument("--agent", choices=["simple", "complex", "both"], default="complex", help="Agent to run (default: complex)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--limit", type=int, default=None, help="Max number of news items to analyze")
    parser.add_argument("--recent", action="store_true", help="Take last N news by published date (use with --limit)")
    parser.add_argument("--tradable", action="store_true", help="With --recent: only weekday US market hours (09:30–15:00 ET)")
    parser.add_argument("--workers", type=int, default=1, metavar="N", help="Parallel worker threads (default: 1)")
    parser.add_argument("--market-hours", action="store_true", help="Only today's news in US market hours (ET), default window 10:00–15:00")
    parser.add_argument("--date", type=str, metavar="YYYY-MM-DD", help="With --market-hours: filter by this date (ET)")
    parser.add_argument("--market-open-offset", type=int, default=30, metavar="MIN", help="Minutes after session open (default 30 → from 10:00 ET)")
    parser.add_argument("--market-close-offset", type=int, default=60, metavar="MIN", help="Minutes before session close (default 60 → until 15:00 ET)")
    args = parser.parse_args()

    print("\n" + "="*70)
    print("BATCH NEWS ANALYSIS TOOL")
    print("="*70 + "\n")

    langsmith_enabled = bool(LANGCHAIN_API_KEY or os.getenv("LANGCHAIN_API_KEY"))
    if langsmith_enabled:
        print("✓ LangSmith tracing: ENABLED")
        print(f"  Project: {LANGCHAIN_PROJECT}")
        print()
    else:
        print("⚠️  LangSmith tracing: DISABLED (LANGCHAIN_API_KEY not set)")
        print()

    analyzer = BatchAnalyzer(enable_langsmith=langsmith_enabled)

    try:
        if getattr(args, 'recent', False):
            limit = args.limit or 50
            tradable = getattr(args, 'tradable', False)
            items = analyzer.get_news_recent(limit=limit, tradable_only=tradable)
            if not items:
                hint = " (try without --tradable)" if tradable else ""
                print(f"\n✗ No news found in database{hint}")
                return
            mode = "tradable " if tradable else ""
            print(f"\n✓ Found {len(items)} most recent {mode}news items")
            analyzer.run_batch_analysis(
                start_date=EASTERN_TZ.localize(datetime(2000, 1, 1)),
                end_date=EASTERN_TZ.localize(datetime(2000, 1, 2)),
                agent_type=args.agent,
                news_type=None,
                limit=limit,
                skip_confirm=args.yes,
                news_items_override=items,
                workers=args.workers,
            )
            return
        if getattr(args, 'market_hours', False):
            limit_mh = args.limit or 100
            open_off = getattr(args, 'market_open_offset', 30)
            close_off = getattr(args, 'market_close_offset', 60)
            on_date = datetime.strptime(args.date, "%Y-%m-%d").date() if getattr(args, 'date', None) else None
            items = analyzer.get_news_last_in_market_hours(
                limit=limit_mh,
                open_offset_minutes=open_off,
                close_offset_minutes=close_off,
                on_date=on_date
            )
            if not items:
                day_desc = f"for {args.date}" if on_date else "for today"
                print(f"\n✗ No news found {day_desc} in the specified market hours window (ET)")
                return
            window_desc = f"{args.date if on_date else 'today'}, ET +{open_off}min after open / -{close_off}min before close (→ 10:00–15:00 by default)"
            print(f"\n✓ Found {len(items)} news for {window_desc}")
            analyzer.run_batch_analysis(
                start_date=EASTERN_TZ.localize(datetime(2000, 1, 1)),
                end_date=EASTERN_TZ.localize(datetime(2000, 1, 2)),
                agent_type=args.agent,
                news_type=None,
                limit=limit_mh,
                skip_confirm=args.yes,
                news_items_override=items,
                workers=args.workers,
            )
            return
        if args.days is not None:
            # Last N days = last N*24 hours (from now backwards)
            end_date = datetime.now(EASTERN_TZ)
            start_date = end_date - timedelta(days=args.days)
            news_type = None
            agent_type = args.agent
            limit = args.limit
            if not args.yes:
                response = input(f"Run agent on news for last {args.days * 24} hours? (yes/no): ").strip().lower()
                if response not in ['yes', 'y']:
                    print("Cancelled.")
                    return
            analyzer.run_batch_analysis(
                start_date=start_date,
                end_date=end_date,
                agent_type=agent_type,
                news_type=news_type,
                limit=limit,
                skip_confirm=args.yes,
                workers=args.workers,
            )
            return
        # Interactive
        print("Enter analysis period:")
        start_date_str = input("  Start date (YYYY-MM-DD): ").strip()
        end_date_str = input("  End date (YYYY-MM-DD): ").strip()

        start_date = EASTERN_TZ.localize(datetime.strptime(start_date_str, "%Y-%m-%d"))
        end_date = EASTERN_TZ.localize(datetime.strptime(end_date_str, "%Y-%m-%d"))
        end_date = end_date + timedelta(days=1)

        print("\nFetching available news types/sources...")
        available_types = analyzer.get_available_news_types()
        if available_types:
            print(f"  Available news types/sources ({len(available_types)}):")
            for i, ntype in enumerate(available_types, 1):
                print(f"    {i}. {ntype}")
        else:
            print("  No news types/sources found in database")

        print("\nFilter by news type/source? (leave empty for all)")
        news_type = input("  News type/source: ").strip() or None

        print("\nLimit number of items to analyze? (leave empty for all)")
        limit_str = input("  Limit: ").strip()
        limit = int(limit_str) if limit_str else None

        print("\nSelect agent:")
        print("  1. Simple Agent (fast, single LLM call)")
        print("  2. Complex Agent (comprehensive, multiple tools)")
        print("  3. Both Agents (run both on same news)")
        agent_choice = input("  Choice (1, 2, or 3): ").strip()

        if agent_choice == '1':
            agent_type = 'simple'
        elif agent_choice == '2':
            agent_type = 'complex'
        elif agent_choice == '3':
            agent_type = 'both'
        else:
            agent_type = 'simple'

        analyzer.run_batch_analysis(
            start_date=start_date,
            end_date=end_date,
            agent_type=agent_type,
            news_type=news_type,
            limit=limit
        )

    except KeyboardInterrupt:
        print("\n\n✗ Analysis interrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        analyzer.close()


if __name__ == "__main__":
    main()




