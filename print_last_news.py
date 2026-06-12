#!/usr/bin/env python3
"""
Script to print the last 10 news articles from the database
"""
import psycopg2
from bloomberg_news_fetcher.config import DB_CONFIG


def get_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=DB_CONFIG['host'],
        database=DB_CONFIG['database'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        port=DB_CONFIG['port']
    )


def print_last_news(limit=30):
    """Print the last N news articles from the database"""
    conn = get_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT id, title, published, fetch_time, created_at, source, link, summary
        FROM news 
        ORDER BY published DESC 
        LIMIT %s
    """
    
    cursor.execute(query, (limit,))
    news_items = cursor.fetchall()
    
    if not news_items:
        print("No news articles found in the database.")
        cursor.close()
        conn.close()
        return
    
    print("=" * 80)
    print(f"LAST {len(news_items)} NEWS ARTICLES (ordered by published date)")
    print("=" * 80)
    print()
    
    for idx, (news_id, title, published, fetch_time, created_at, source, link, summary) in enumerate(news_items, 1):
        print(f"{idx}. [{news_id}] {title or 'No title'}")
        if published:
            print(f"   Published:    {published}")
        if fetch_time:
            print(f"   Fetch Time:   {fetch_time}")
        if created_at:
            print(f"   Created At:   {created_at}")
        if source:
            print(f"   Source: {source}")
        if link:
            print(f"   Link: {link}")
        if summary:
            # Truncate summary if too long
            summary_display = summary[:500] + "..." if len(summary) > 500 else summary
            print(f"   Summary: {summary_display}")
        print("-" * 80)
    
    cursor.close()
    conn.close()


if __name__ == "__main__":
    print_last_news(30)

