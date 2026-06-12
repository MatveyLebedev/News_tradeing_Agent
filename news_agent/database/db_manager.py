"""
Database manager with time-aware queries and connection pooling
All timestamps use US Eastern Time (America/New_York) for consistency with US market hours
"""
import psycopg2
from psycopg2 import pool, Error
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import pandas as pd
from contextlib import contextmanager
import pytz
import numpy as np


class DBManager:
    """Database manager with connection pooling and time-aware queries"""
    
    # US Eastern Time timezone (handles EST/EDT automatically)
    EASTERN_TZ = pytz.timezone('America/New_York')
    
    def __init__(self, db_config: Dict[str, str], min_conn: int = 1, max_conn: int = 10):
        """
        Initialize database connection pool
        
        All timestamps will be handled in US Eastern Time (America/New_York)
        
        Args:
            db_config: Database configuration dict
            min_conn: Minimum connections in pool
            max_conn: Maximum connections in pool
        """
        self.db_config = db_config
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                min_conn,
                max_conn,
                **db_config
            )
            if self.connection_pool:
                print(f"✓ Database connection pool created (min={min_conn}, max={max_conn})")
                print(f"✓ Using timezone: America/New_York (US Eastern Time)")
        except Error as e:
            print(f"✗ Error creating connection pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """Context manager for getting database connection from pool"""
        connection = None
        try:
            connection = self.connection_pool.getconn()
            yield connection
        except Error as e:
            error_msg = str(e)
            # Only print connection pool errors, not query execution errors
            # Query errors should be handled by the calling method
            if "connection" in error_msg.lower() or "pool" in error_msg.lower():
                print(f"Error getting connection: {e}")
            raise
        finally:
            if connection:
                self.connection_pool.putconn(connection)
    
    @classmethod
    def ensure_eastern_time(cls, dt: datetime) -> datetime:
        """
        Convert datetime to US Eastern Time (timezone-aware)
        
        Args:
            dt: datetime (naive or aware)
            
        Returns:
            timezone-aware datetime in US Eastern Time
        """
        if dt.tzinfo is None:
            # Naive datetime - assume it's already in Eastern Time
            return cls.EASTERN_TZ.localize(dt)
        else:
            # Already timezone-aware - convert to Eastern Time
            return dt.astimezone(cls.EASTERN_TZ)
    
    def validate_timestamp(self, timestamp: datetime) -> datetime:
        """
        Validate that timestamp is not in the future (data leakage protection)
        Converts to US Eastern Time for consistency
        
        Args:
            timestamp: Timestamp to validate (naive or aware)
            
        Returns:
            Timezone-aware datetime in US Eastern Time
            
        Raises:
            ValueError: If timestamp is in the future
        """
        # Convert to Eastern Time
        timestamp_et = self.ensure_eastern_time(timestamp)
        current_time_et = datetime.now(self.EASTERN_TZ)
        
        if timestamp_et > current_time_et:
            raise ValueError(
                f"Timestamp {timestamp_et.strftime('%Y-%m-%d %H:%M:%S %Z')} is in the future. "
                f"Current time: {current_time_et.strftime('%Y-%m-%d %H:%M:%S %Z')}. "
                f"This would cause data leakage."
            )
        
        return timestamp_et
    
    def get_historical_prices(
        self,
        ticker: str,
        timestamp: datetime,
        lookback_days: int = 1
    ) -> Optional[pd.DataFrame]:
        """
        Get historical price data before timestamp (time-aware)
        All timestamps handled in US Eastern Time
        
        Args:
            ticker: Stock ticker symbol
            timestamp: Reference timestamp (only data before this is returned)
            lookback_days: Number of days to look back
            
        Returns:
            DataFrame with columns: date, open, high, low, close, volume
        """
        # Validate and convert to Eastern Time
        timestamp_et = self.validate_timestamp(timestamp)
        
        table_name = f"historical_data_{ticker.lower()}"
        start_date = timestamp_et - timedelta(days=lookback_days)
        
        query = f"""
            SELECT timestamp, open, high, low, close, volume
            FROM {table_name}
            WHERE timestamp >= %s AND timestamp < %s
            ORDER BY timestamp ASC;
        """
        
        try:
            with self.get_connection() as conn:
                df = pd.read_sql_query(
                    query,
                    conn,
                    params=(start_date, timestamp_et)
                )
                # Ensure date column is timezone-aware (Eastern Time)
                # Database stores naive timestamps in EST, so localize as EST
                if not df.empty:
                    # Rename timestamp to date for consistency with rest of codebase
                    df = df.rename(columns={'timestamp': 'date'})
                    df['date'] = pd.to_datetime(df['date'])
                    # If naive, assume it's in EST (database stores EST as naive)
                    if df['date'].dt.tz is None:
                        df['date'] = df['date'].dt.tz_localize('America/New_York')
                    else:
                        df['date'] = df['date'].dt.tz_convert('America/New_York')
                return df if not df.empty else None
        except Error as e:
            print(f"Error fetching historical prices for {ticker}: {e}")
            return None
    
    def get_price_at_time(
        self,
        ticker: str,
        timestamp: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Get the closest price data before timestamp
        All timestamps handled in US Eastern Time
        
        Args:
            ticker: Stock ticker symbol
            timestamp: Reference timestamp (naive or aware)
            
        Returns:
            Dict with price data or None
        """
        # Validate and convert to Eastern Time
        timestamp_et = self.validate_timestamp(timestamp)
        
        table_name = f"historical_data_{ticker.lower()}"
        
        query = f"""
            SELECT timestamp, open, high, low, close, volume
            FROM {table_name}
            WHERE timestamp < %s
            ORDER BY timestamp DESC
            LIMIT 1;
        """
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (timestamp_et,))
                    result = cursor.fetchone()
                    
                    if result:
                        return {
                            'date': result[0],  # Keep 'date' key for consistency with rest of codebase
                            'open': float(result[1]),
                            'high': float(result[2]),
                            'low': float(result[3]),
                            'close': float(result[4]),
                            'volume': int(result[5])
                        }
                    return None
        except Error as e:
            print(f"Error fetching price at time for {ticker}: {e}")
            return None
    
    def get_price_range(
        self,
        ticker: str,
        start_time: datetime,
        end_time: datetime
    ) -> Optional[pd.DataFrame]:
        """
        Get price data within a specific time range
        All timestamps handled in US Eastern Time
        
        Args:
            ticker: Stock ticker symbol
            start_time: Start timestamp (naive or aware)
            end_time: End timestamp (naive or aware)
            
        Returns:
            DataFrame with price data
        """
        # Validate and convert both timestamps to Eastern Time
        end_time_et = self.validate_timestamp(end_time)
        start_time_et = self.ensure_eastern_time(start_time)
        
        table_name = f"historical_data_{ticker.lower()}"
        
        query = f"""
            SELECT timestamp, open, high, low, close, volume
            FROM {table_name}
            WHERE timestamp >= %s AND timestamp <= %s
            ORDER BY timestamp ASC;
        """
        
        try:
            with self.get_connection() as conn:
                df = pd.read_sql_query(
                    query,
                    conn,
                    params=(start_time_et, end_time_et)
                )
                # Ensure date column is timezone-aware (Eastern Time)
                # Database stores naive timestamps in EST, so localize as EST
                if not df.empty:
                    # Rename timestamp to date for consistency with rest of codebase
                    df = df.rename(columns={'timestamp': 'date'})
                    df['date'] = pd.to_datetime(df['date'])
                    # If naive, assume it's in EST (database stores EST as naive)
                    if df['date'].dt.tz is None:
                        df['date'] = df['date'].dt.tz_localize('America/New_York')
                    else:
                        df['date'] = df['date'].dt.tz_convert('America/New_York')
                return df if not df.empty else None
        except Error as e:
            print(f"Error fetching price range for {ticker}: {e}")
            return None
    
    def table_exists(self, ticker: str) -> bool:
        """
        Check if historical data table exists for ticker
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            True if table exists, False otherwise
        """
        table_name = f"historical_data_{ticker.lower()}"
        
        query = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = %s
            );
        """
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (table_name,))
                    return cursor.fetchone()[0]
        except Error as e:
            print(f"Error checking table existence for {ticker}: {e}")
            return False
    
    def get_news_by_id(self, news_id: int) -> Optional[Dict[str, Any]]:
        """
        Get news article by database ID
        
        Args:
            news_id: Database ID of news article
            
        Returns:
            Dict with news data or None
        """
        query = """
            SELECT id, link, published, title, summary, full_text
            FROM news
            WHERE id = %s;
        """
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (news_id,))
                    result = cursor.fetchone()
                    
                    if result:
                        return {
                            'id': result[0],
                            'link': result[1],
                            'published': result[2],
                            'title': result[3],
                            'summary': result[4],
                            'full_text': result[5]
                        }
                    return None
        except Error as e:
            error_msg = str(e)
            # Handle missing table gracefully
            if "does not exist" in error_msg or "relation" in error_msg.lower():
                # Table doesn't exist - this is expected in some setups
                return None
            else:
                # Other database errors - print for debugging
                print(f"Error fetching news by ID {news_id}: {e}")
                return None
    
    def get_news_by_link(self, url: str) -> Optional[Dict[str, Any]]:
        """Get news article by its URL link."""
        query = """
            SELECT id, link, published, title, summary, full_text
            FROM news
            WHERE link = %s;
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (url,))
                    result = cursor.fetchone()
                    if result:
                        return {
                            'id': result[0],
                            'link': result[1],
                            'published': result[2],
                            'title': result[3],
                            'summary': result[4],
                            'full_text': result[5],
                        }
                    return None
        except Error as e:
            error_msg = str(e)
            if "does not exist" in error_msg or "relation" in error_msg.lower():
                return None
            print(f"Error fetching news by link {url}: {e}")
            return None

    def get_recent_news(
        self,
        timestamp: datetime,
        hours: int = 24,
        ticker: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent news before timestamp
        All timestamps handled in US Eastern Time
        
        Args:
            timestamp: Reference timestamp (naive or aware)
            hours: Hours to look back
            ticker: Optional ticker filter
            
        Returns:
            List of news articles
        """
        # Validate and convert to Eastern Time
        timestamp_et = self.validate_timestamp(timestamp)
        
        start_time = timestamp_et - timedelta(hours=hours)
        
        # Basic query
        query = """
            SELECT id, title, summary, published
            FROM news
            WHERE published >= %s AND published < %s
        """
        params = [start_time, timestamp_et]
        
        # Add ticker filter if provided (full_text может отсутствовать в схеме)
        ticker_pattern = f"%{ticker}%" if ticker else None
        if ticker:
            query += " AND (title ILIKE %s OR summary ILIKE %s OR full_text ILIKE %s)"
            params.extend([ticker_pattern, ticker_pattern, ticker_pattern])

        query += " ORDER BY published DESC;"

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    results = cursor.fetchall()
                    return [
                        {'id': row[0], 'title': row[1], 'summary': row[2], 'published': row[3]}
                        for row in results
                    ]
        except Error as e:
            error_msg = str(e)
            if "full_text" in error_msg and "does not exist" in error_msg and ticker:
                # Колонки full_text нет — фильтр только по title и summary
                query_fallback = """
                    SELECT id, title, summary, published FROM news
                    WHERE published >= %s AND published < %s
                    AND (title ILIKE %s OR summary ILIKE %s)
                    ORDER BY published DESC;
                """
                try:
                    with self.get_connection() as conn:
                        with conn.cursor() as cursor:
                            cursor.execute(query_fallback, [start_time, timestamp_et, ticker_pattern, ticker_pattern])
                            results = cursor.fetchall()
                            return [{'id': r[0], 'title': r[1], 'summary': r[2], 'published': r[3]} for r in results]
                except Error:
                    return []
            if "does not exist" in error_msg or "relation" in error_msg.lower():
                return []
            print(f"Error fetching recent news: {e}")
            return []
    
    def save_news_embedding(self, news_id: int, embedding: List[float]) -> bool:
        """Save pgvector embedding for a news article."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE news SET embedding = %s::vector WHERE id = %s;",
                        (embedding, news_id)
                    )
                    conn.commit()
                    return True
        except Error as e:
            print(f"Error saving embedding for news {news_id}: {e}")
            return False

    def vector_similarity_search(
        self,
        query_embedding: List[float],
        timestamp: datetime,
        ticker: Optional[str] = None,
        limit: int = 5,
        min_time_diff_hours: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Time-aware vector similarity search using pgvector (column 'embedding', HNSW index).
        All timestamps handled in US Eastern Time.
        """
        timestamp_et = self.validate_timestamp(timestamp)
        min_timestamp = timestamp_et - timedelta(hours=min_time_diff_hours)

        vec_str = '[' + ','.join(map(str, query_embedding)) + ']'
        params_base: list = [min_timestamp]
        if ticker:
            tp = f"%{ticker}%"
            params_base.extend([tp, tp])
        ticker_cond = " AND (title ILIKE %s OR summary ILIKE %s)" if ticker else ""

        sql = f"""
            SELECT id, title, summary, published,
                   1 - (embedding <=> '{vec_str}'::vector) AS similarity
            FROM news
            WHERE published < %s AND embedding IS NOT NULL{ticker_cond}
            ORDER BY embedding <=> '{vec_str}'::vector
            LIMIT %s
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params_base + [limit])
                    rows = cursor.fetchall()
                    return [
                        {'id': r[0], 'title': r[1], 'summary': r[2],
                         'published': r[3], 'similarity': float(r[4])}
                        for r in rows
                    ]
        except Error as e:
            if "does not exist" in str(e) or "relation" in str(e).lower():
                return []
            print(f"Error in vector similarity search: {e}")
            return []
    
    def close_all_connections(self):
        """Close all connections in the pool"""
        if self.connection_pool:
            self.connection_pool.closeall()
            print("✓ All database connections closed")



