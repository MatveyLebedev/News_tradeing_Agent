#!/usr/bin/env python3
"""
Script to check if the agent converts timezone when reading from database
"""
import psycopg2
import pytz
from datetime import datetime
from news_agent.database.db_manager import DBManager
from news_agent.agent_config import AgentConfig

# Connect to database to check what timezone timestamps are returned in
db_config = AgentConfig.get_db_config()
conn = psycopg2.connect(**db_config)
cursor = conn.cursor()

print("=" * 80)
print("DATABASE TIMEZONE CONVERSION CHECK")
print("=" * 80)
print()

# Check database timezone setting
cursor.execute("SHOW timezone;")
db_timezone = cursor.fetchone()[0]
print(f"📅 Database Timezone Setting: {db_timezone}")
print()

# Get a sample news article to see what type of timestamp is returned
cursor.execute("""
    SELECT id, published, fetch_time, created_at
    FROM news 
    ORDER BY published DESC 
    LIMIT 1;
""")
result = cursor.fetchone()

if result:
    news_id, published, fetch_time, created_at = result
    print(f"📰 Sample News Article (ID: {news_id}):")
    print(f"   published type: {type(published)}")
    print(f"   published value: {published}")
    print(f"   published tzinfo: {published.tzinfo if hasattr(published, 'tzinfo') else 'N/A'}")
    print()
    
    if fetch_time:
        print(f"   fetch_time type: {type(fetch_time)}")
        print(f"   fetch_time value: {fetch_time}")
        print(f"   fetch_time tzinfo: {fetch_time.tzinfo if hasattr(fetch_time, 'tzinfo') else 'N/A'}")
        print()
    
    if created_at:
        print(f"   created_at type: {type(created_at)}")
        print(f"   created_at value: {created_at}")
        print(f"   created_at tzinfo: {created_at.tzinfo if hasattr(created_at, 'tzinfo') else 'N/A'}")
        print()
    
    # Check what DBManager returns
    print("🔍 Testing DBManager.get_recent_news():")
    db_manager = DBManager(db_config)
    test_timestamp = datetime.now(pytz.timezone('America/New_York'))
    recent_news = db_manager.get_recent_news(test_timestamp, hours=24*30)  # Last 30 days
    
    if recent_news:
        sample = recent_news[0]
        published_from_db = sample.get('published')
        print(f"   Returned published type: {type(published_from_db)}")
        print(f"   Returned published value: {published_from_db}")
        print(f"   Returned published tzinfo: {published_from_db.tzinfo if hasattr(published_from_db, 'tzinfo') else 'N/A'}")
        print()
        
        # Check if conversion is needed
        if hasattr(published_from_db, 'tzinfo'):
            if published_from_db.tzinfo is None:
                print("   ⚠️  WARNING: Timestamp is naive (no timezone)")
                print("   ⚠️  Agent assumes it's already in Eastern Time")
            else:
                print(f"   ✓ Timestamp is timezone-aware: {published_from_db.tzinfo}")
                if published_from_db.tzinfo.zone != 'America/New_York':
                    print(f"   ⚠️  WARNING: Timezone is {published_from_db.tzinfo.zone}, not Eastern Time")
                    print("   ⚠️  Agent should convert this to Eastern Time")
                else:
                    print("   ✓ Timestamp is already in Eastern Time")
    
    db_manager.connection_pool.closeall()
else:
    print("No news articles found in database")

# Check column types
print()
print("📋 Checking Column Types:")
cursor.execute("""
    SELECT column_name, data_type, datetime_precision
    FROM information_schema.columns
    WHERE table_name = 'news' 
    AND column_name IN ('published', 'fetch_time', 'created_at');
""")
columns = cursor.fetchall()
for col_name, data_type, precision in columns:
    print(f"   {col_name}: {data_type}")

cursor.close()
conn.close()

print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print("✅ Agent converts timestamps TO Eastern Time when:")
print("   - Querying with WHERE clauses (converts input timestamps)")
print("   - Reading price data (converts returned timestamps)")
print()
print("⚠️  For news 'published' timestamps:")
print("   - Depends on database column type (TIMESTAMP vs TIMESTAMPTZ)")
print("   - If TIMESTAMP (naive): Agent assumes it's already ET")
print("   - If TIMESTAMPTZ (aware): psycopg2 returns with timezone")
print("   - Agent may need to convert if not already ET")
print("=" * 80)






























