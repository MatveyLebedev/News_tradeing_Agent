#!/usr/bin/env python3
"""
Database Report Script for trade_agent database
Generates a comprehensive report of all tables, structures, and data
"""
import psycopg2
from tabulate import tabulate
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


def get_table_info(cursor):
    """Get list of all tables"""
    cursor.execute("""
        SELECT 
            tablename,
            pg_size_pretty(pg_total_relation_size('public.'||tablename)) AS size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename;
    """)
    return cursor.fetchall()


def get_table_structure(cursor, table_name):
    """Get table structure"""
    cursor.execute(f"""
        SELECT 
            column_name,
            data_type,
            character_maximum_length,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' 
        AND table_name = '{table_name}'
        ORDER BY ordinal_position;
    """)
    return cursor.fetchall()


def get_table_indexes(cursor, table_name):
    """Get table indexes"""
    cursor.execute(f"""
        SELECT
            indexname,
            indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        AND tablename = '{table_name}'
        ORDER BY indexname;
    """)
    return cursor.fetchall()


def get_row_count(cursor, table_name):
    """Get row count for a table"""
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    return cursor.fetchone()[0]


def get_recent_records(cursor, table_name, limit=5):
    """Get recent records from table"""
    try:
        # Check if table has id column
        cursor.execute(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = '{table_name}' 
            AND column_name IN ('id', 'created_at', 'log_time', 'fetch_time')
            LIMIT 1
        """)
        order_col = cursor.fetchone()
        
        if order_col:
            order_by = order_col[0]
            cursor.execute(f"SELECT * FROM {table_name} ORDER BY {order_by} DESC LIMIT {limit}")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            return columns, rows
    except:
        pass
    return None, None


def generate_report():
    """Generate comprehensive database report"""
    conn = get_connection()
    cursor = conn.cursor()
    
    print("=" * 100)
    print("DATABASE REPORT: trade_agent")
    print("=" * 100)
    print()
    
    # Get database size
    cursor.execute("""
        SELECT pg_size_pretty(pg_database_size('trade_agent'))
    """)
    db_size = cursor.fetchone()[0]
    print(f"Database Size: {db_size}")
    print()
    
    # Get all tables
    tables = get_table_info(cursor)
    
    print("=" * 100)
    print("TABLES OVERVIEW")
    print("=" * 100)
    print()
    print(tabulate(tables, headers=['Table Name', 'Size'], tablefmt='grid'))
    print()
    
    # Detailed info for each table
    for table_name, table_size in tables:
        print("=" * 100)
        print(f"TABLE: {table_name}")
        print("=" * 100)
        print()
        
        # Row count
        row_count = get_row_count(cursor, table_name)
        print(f"Total Records: {row_count}")
        print(f"Table Size: {table_size}")
        print()
        
        # Structure
        print("--- STRUCTURE ---")
        structure = get_table_structure(cursor, table_name)
        headers = ['Column', 'Type', 'Max Length', 'Nullable', 'Default']
        print(tabulate(structure, headers=headers, tablefmt='grid'))
        print()
        
        # Indexes
        print("--- INDEXES ---")
        indexes = get_table_indexes(cursor, table_name)
        if indexes:
            for idx_name, idx_def in indexes:
                print(f"  • {idx_name}")
                print(f"    {idx_def}")
                print()
        else:
            print("  No indexes")
            print()
        
        # Recent records
        if row_count > 0:
            print("--- RECENT RECORDS (Last 5) ---")
            columns, rows = get_recent_records(cursor, table_name, limit=5)
            if columns and rows:
                print(tabulate(rows, headers=columns, tablefmt='grid'))
            else:
                print("  Unable to fetch records")
            print()
        
        print()
    
    cursor.close()
    conn.close()
    
    print("=" * 100)
    print("END OF REPORT")
    print("=" * 100)


if __name__ == "__main__":
    try:
        generate_report()
    except Exception as e:
        print(f"Error generating report: {e}")
        import traceback
        traceback.print_exc()

