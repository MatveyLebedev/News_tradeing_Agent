#!/usr/bin/env python3
"""
Script to view news parser logs from the database
"""
import psycopg2
from datetime import datetime, timedelta
import json
import argparse
from config import DB_CONFIG


def get_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=DB_CONFIG['host'],
        database=DB_CONFIG['database'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        port=DB_CONFIG['port']
    )


def format_log_entry(log):
    """Format a single log entry for display"""
    log_id, log_time, log_level, event_type, message, article_link, details = log
    
    # Color codes for log levels
    colors = {
        'INFO': '\033[92m',     # Green
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',    # Red
    }
    reset = '\033[0m'
    
    color = colors.get(log_level, '')
    
    print(f"{color}[{log_time}] {log_level:8} | {event_type:20}{reset}")
    print(f"  Message: {message}")
    if article_link:
        print(f"  Article: {article_link}")
    if details:
        try:
            details_obj = json.loads(details) if isinstance(details, str) else details
            print(f"  Details: {json.dumps(details_obj, indent=2)}")
        except:
            print(f"  Details: {details}")
    print("-" * 80)


def view_logs(limit=50, log_level=None, event_type=None, hours=None):
    """
    View logs from the database
    
    Args:
        limit: Number of logs to display
        log_level: Filter by log level (INFO, WARNING, ERROR)
        event_type: Filter by event type
        hours: Only show logs from last N hours
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # Build query
    query = "SELECT id, log_time, log_level, event_type, message, article_link, details FROM news_parser_log"
    conditions = []
    params = []
    
    if log_level:
        conditions.append("log_level = %s")
        params.append(log_level.upper())
    
    if event_type:
        conditions.append("event_type = %s")
        params.append(event_type.upper())
    
    if hours:
        conditions.append("log_time >= NOW() - INTERVAL '%s hours'")
        params.append(hours)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY log_time DESC LIMIT %s"
    params.append(limit)
    
    cursor.execute(query, params)
    logs = cursor.fetchall()
    
    if not logs:
        print("No logs found matching the criteria.")
        cursor.close()
        conn.close()
        return
    
    print("=" * 80)
    print(f"NEWS PARSER LOGS (showing {len(logs)} entries)")
    print("=" * 80)
    print()
    
    for log in logs:
        format_log_entry(log)
    
    cursor.close()
    conn.close()


def get_log_statistics():
    """Get statistics about logs"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # === LAST 24 HOURS STATISTICS (PRIORITY) ===
    # Total articles fetched in last 24 hours
    cursor.execute("""
        SELECT COUNT(*) 
        FROM news_parser_log 
        WHERE event_type = 'DB_SAVE' 
        AND log_time >= NOW() - INTERVAL '24 hours'
    """)
    fetched_last_day = cursor.fetchone()[0]
    
    # Errors in last 24 hours
    cursor.execute("""
        SELECT COUNT(*) 
        FROM news_parser_log 
        WHERE log_level = 'ERROR' 
        AND log_time >= NOW() - INTERVAL '24 hours'
    """)
    errors_last_day = cursor.fetchone()[0]
    
    # Skipped articles in last 24 hours
    cursor.execute("""
        SELECT COUNT(*) 
        FROM news_parser_log 
        WHERE event_type = 'DB_SKIP' 
        AND log_time >= NOW() - INTERVAL '24 hours'
    """)
    skipped_last_day = cursor.fetchone()[0]
    
    # Fetch operations in last 24 hours
    cursor.execute("""
        SELECT COUNT(*) 
        FROM news_parser_log 
        WHERE event_type = 'FETCH_COMPLETE' 
        AND log_time >= NOW() - INTERVAL '24 hours'
    """)
    fetch_operations_last_day = cursor.fetchone()[0]
    
    # === OVERALL STATISTICS ===
    # Total count
    cursor.execute("SELECT COUNT(*) FROM news_parser_log")
    total = cursor.fetchone()[0]
    
    # Count by level
    cursor.execute("""
        SELECT log_level, COUNT(*) 
        FROM news_parser_log 
        GROUP BY log_level 
        ORDER BY COUNT(*) DESC
    """)
    by_level = cursor.fetchall()
    
    # Count by event type
    cursor.execute("""
        SELECT event_type, COUNT(*) 
        FROM news_parser_log 
        GROUP BY event_type 
        ORDER BY COUNT(*) DESC
        LIMIT 10
    """)
    by_event = cursor.fetchall()
    
    print("=" * 80)
    print("LOG STATISTICS")
    print("=" * 80)
    
    # Display last 24 hours stats FIRST (most important)
    print("\n" + "🔥 LAST 24 HOURS" + " " + "-" * 62)
    print(f"  Articles Fetched:     {fetched_last_day:6}")
    print(f"  Articles Skipped:     {skipped_last_day:6}")
    print(f"  Errors:               {errors_last_day:6}")
    print(f"  Fetch Operations:     {fetch_operations_last_day:6}")
    
    print("\n" + "📊 OVERALL STATISTICS" + " " + "-" * 56)
    print(f"  Total log entries: {total}")
    
    print("\n--- By Log Level (All Time) ---")
    for level, count in by_level:
        print(f"  {level:10} {count:6} entries")
    
    print("\n--- Top Event Types (All Time) ---")
    for event, count in by_event:
        print(f"  {event:25} {count:6} entries")
    
    print("\n" + "=" * 80)
    
    cursor.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='View Bloomberg news parser logs'
    )
    parser.add_argument(
        '--limit', '-l',
        type=int,
        default=50,
        help='Number of log entries to display (default: 50)'
    )
    parser.add_argument(
        '--level',
        choices=['INFO', 'WARNING', 'ERROR'],
        help='Filter by log level'
    )
    parser.add_argument(
        '--event',
        help='Filter by event type (e.g., FETCH_START, DB_SAVE, ARTICLE_ERROR)'
    )
    parser.add_argument(
        '--hours',
        type=int,
        help='Only show logs from last N hours'
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Show log statistics instead of logs'
    )
    
    args = parser.parse_args()
    
    if args.stats:
        get_log_statistics()
    else:
        view_logs(
            limit=args.limit,
            log_level=args.level,
            event_type=args.event,
            hours=args.hours
        )

