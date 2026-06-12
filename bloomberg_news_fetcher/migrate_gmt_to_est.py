#!/usr/bin/env python3
"""
Migration script to convert existing GMT/UTC timestamps to EST in the news table.

This script:
1. Assumes existing timestamps in the database are stored as GMT/UTC (naive timestamps)
2. Converts them to EST using PostgreSQL timezone functions
3. Updates both 'published' and 'fetch_time' columns

Run this script once to migrate existing data.
"""

import psycopg2
from config import DB_CONFIG

def migrate_timestamps():
    """
    Convert existing GMT/UTC timestamps to EST in the news table.
    Assumes existing timestamps are in GMT/UTC and converts them to EST.
    """
    try:
        print("Connecting to database...")
        conn = psycopg2.connect(
            host=DB_CONFIG['host'],
            database=DB_CONFIG['database'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            port=DB_CONFIG['port']
        )
        cursor = conn.cursor()
        
        # First, check how many records we have
        cursor.execute("SELECT COUNT(*) FROM news")
        total_count = cursor.fetchone()[0]
        print(f"Found {total_count} records in news table")
        
        if total_count == 0:
            print("No records to migrate. Exiting.")
            cursor.close()
            conn.close()
            return
        
        # Show sample of current data
        print("\nSample of current timestamps (first 5 records):")
        cursor.execute("""
            SELECT id, title, published, fetch_time 
            FROM news 
            ORDER BY id 
            LIMIT 5
        """)
        for row in cursor.fetchall():
            print(f"  ID: {row[0]}, Published: {row[2]}, Fetch Time: {row[3]}")
        
        # Ask for confirmation
        print("\n⚠️  WARNING: This will update all timestamps in the news table.")
        print("Assuming existing timestamps are in GMT/UTC, converting to EST.")
        response = input("Continue? (yes/no): ")
        
        if response.lower() != 'yes':
            print("Migration cancelled.")
            cursor.close()
            conn.close()
            return
        
        print("\nConverting timestamps from GMT/UTC to EST...")
        
        # Convert published timestamps: assume they're in UTC, convert to EST
        # PostgreSQL: AT TIME ZONE 'UTC' treats the timestamp as UTC, then converts to EST
        cursor.execute("""
            UPDATE news
            SET published = (published AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York'
            WHERE published IS NOT NULL
        """)
        published_updated = cursor.rowcount
        print(f"  Updated {published_updated} 'published' timestamps")
        
        # Convert fetch_time timestamps: assume they're in UTC, convert to EST
        cursor.execute("""
            UPDATE news
            SET fetch_time = (fetch_time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York'
            WHERE fetch_time IS NOT NULL
        """)
        fetch_time_updated = cursor.rowcount
        print(f"  Updated {fetch_time_updated} 'fetch_time' timestamps")
        
        # Commit the changes
        conn.commit()
        print("\n✓ Migration completed successfully!")
        
        # Show sample of updated data
        print("\nSample of updated timestamps (first 5 records):")
        cursor.execute("""
            SELECT id, title, published, fetch_time 
            FROM news 
            ORDER BY id 
            LIMIT 5
        """)
        for row in cursor.fetchall():
            print(f"  ID: {row[0]}, Published: {row[2]}, Fetch Time: {row[3]}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"\n✗ Error during migration: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        raise


if __name__ == "__main__":
    print("=" * 80)
    print("GMT/UTC to EST Migration Script")
    print("=" * 80)
    print()
    migrate_timestamps()

