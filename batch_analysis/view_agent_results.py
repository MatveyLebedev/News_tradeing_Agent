#!/usr/bin/env python3
"""
View Agent Results from Database

Shows analysis results stored in agent_results table
"""
import os
import psycopg2
from pathlib import Path
from datetime import datetime
import json
from typing import Optional

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist


def connect_db():
    """Connect to database"""
    db_config = {
        "host": os.getenv("DB_HOST"),
        "database": os.getenv("DB_NAME", "trade_agent"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": os.getenv("DB_PORT", "5432")
    }
    return psycopg2.connect(**db_config)


def view_recent_runs(limit: int = 10):
    """View recent analysis runs"""
    conn = connect_db()
    cursor = conn.cursor()
    
    query = """
        SELECT 
            run_timestamp,
            agent_name,
            agent_version,
            COUNT(*) as items_analyzed,
            SUM(signals_generated) as total_signals,
            AVG(execution_time_seconds) as avg_time
        FROM agent_results
        GROUP BY run_timestamp, agent_name, agent_version
        ORDER BY run_timestamp DESC
        LIMIT %s
    """
    
    cursor.execute(query, (limit,))
    rows = cursor.fetchall()
    
    print("\n" + "="*70)
    print("RECENT ANALYSIS RUNS")
    print("="*70 + "\n")
    
    if not rows:
        print("No results found")
        return
    
    for row in rows:
        run_time, agent_name, agent_version, items, signals, avg_time = row
        print(f"Run: {run_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Agent: {agent_name} v{agent_version}")
        print(f"  Items: {items}")
        print(f"  Signals: {signals}")
        print(f"  Avg time: {avg_time:.2f}s")
        print()
    
    cursor.close()
    conn.close()


def view_run_details(run_timestamp: Optional[str] = None):
    """View detailed results for a specific run"""
    conn = connect_db()
    cursor = conn.cursor()
    
    if run_timestamp:
        query = """
            SELECT 
                id,
                news_title,
                news_published,
                signals_generated,
                signals_json,
                execution_time_seconds
            FROM agent_results
            WHERE DATE(run_timestamp) = DATE(%s)
            ORDER BY news_published
        """
        cursor.execute(query, (run_timestamp,))
    else:
        # Get most recent run
        query = """
            SELECT 
                id,
                news_title,
                news_published,
                signals_generated,
                signals_json,
                execution_time_seconds
            FROM agent_results
            WHERE run_timestamp = (SELECT MAX(run_timestamp) FROM agent_results)
            ORDER BY news_published
        """
        cursor.execute(query)
    
    rows = cursor.fetchall()
    
    print("\n" + "="*70)
    print("RUN DETAILS")
    print("="*70 + "\n")
    
    if not rows:
        print("No results found")
        return
    
    total_signals = 0
    total_time = 0
    
    for row in rows:
        result_id, title, published, signals_count, signals_json, exec_time = row
        total_signals += signals_count
        total_time += exec_time
        
        print(f"[{result_id}] {title[:60]}")
        print(f"  Published: {published.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Signals: {signals_count}")
        print(f"  Time: {exec_time:.2f}s")
        
        if signals_count > 0 and signals_json:
            signals = json.loads(signals_json)
            for signal in signals:
                ticker = signal.get('ticker', 'N/A')
                action = signal.get('signal', 'N/A').upper()
                conf = signal.get('confidence', 0)
                print(f"    • {ticker}: {action} ({conf:.0%})")
        print()
    
    print("-" * 70)
    print(f"Total items: {len(rows)}")
    print(f"Total signals: {total_signals}")
    print(f"Total time: {total_time:.2f}s")
    print(f"Avg signals/item: {total_signals/len(rows):.2f}")
    print(f"Avg time/item: {total_time/len(rows):.2f}s")
    print()
    
    cursor.close()
    conn.close()


def compare_agents():
    """Compare performance between simple and complex agents"""
    conn = connect_db()
    cursor = conn.cursor()
    
    query = """
        SELECT 
            agent_name,
            agent_version,
            COUNT(*) as total_items,
            SUM(signals_generated) as total_signals,
            AVG(signals_generated) as avg_signals,
            AVG(execution_time_seconds) as avg_time,
            MIN(run_timestamp) as first_run,
            MAX(run_timestamp) as last_run
        FROM agent_results
        GROUP BY agent_name, agent_version
        ORDER BY agent_name
    """
    
    cursor.execute(query)
    rows = cursor.fetchall()
    
    print("\n" + "="*70)
    print("AGENT COMPARISON")
    print("="*70 + "\n")
    
    if not rows:
        print("No results found")
        return
    
    for row in rows:
        agent_name, version, items, total_sig, avg_sig, avg_time, first, last = row
        print(f"{agent_name} v{version}")
        print(f"  Total items analyzed: {items}")
        print(f"  Total signals: {total_sig}")
        print(f"  Avg signals/item: {avg_sig:.2f}")
        print(f"  Avg time/item: {avg_time:.2f}s")
        print(f"  First run: {first.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Last run: {last.strftime('%Y-%m-%d %H:%M:%S')}")
        print()
    
    cursor.close()
    conn.close()


def main():
    """Main menu"""
    print("\n" + "="*70)
    print("AGENT RESULTS VIEWER")
    print("="*70 + "\n")
    
    print("Options:")
    print("  1. View recent runs")
    print("  2. View latest run details")
    print("  3. Compare agents")
    print("  4. Exit")
    
    choice = input("\nChoice (1-4): ").strip()
    
    if choice == '1':
        limit = input("How many runs to show? (default: 10): ").strip()
        limit = int(limit) if limit else 10
        view_recent_runs(limit)
    elif choice == '2':
        view_run_details()
    elif choice == '3':
        compare_agents()
    elif choice == '4':
        print("Goodbye!")
    else:
        print("Invalid choice")


if __name__ == "__main__":
    main()




