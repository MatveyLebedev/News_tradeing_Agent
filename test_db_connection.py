#!/usr/bin/env python3
"""
Test PostgreSQL Database Connection

Tests connection using credentials from .env file
"""
import os
import sys
from pathlib import Path
import psycopg2
from psycopg2 import Error, OperationalError

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded .env from {env_path}")
    else:
        print(f"⚠️  .env file not found at {env_path}")
        print("   Using system environment variables instead")
except ImportError:
    print("⚠️  python-dotenv not installed. Using system environment variables")
except Exception as e:
    print(f"⚠️  Could not load .env: {e}")

# Get database configuration from environment
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME", "trade_agent"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT")
}


def check_config():
    """Check if all required config values are set"""
    print("\n" + "=" * 70)
    print("DATABASE CONFIGURATION CHECK")
    print("=" * 70)
    
    required = ["host", "user", "password"]
    missing = []
    
    for key in required:
        value = DB_CONFIG.get(key)
        if not value:
            missing.append(key)
            print(f"❌ {key.upper()}: Not set")
        else:
            # Mask password for security
            display_value = value if key != "password" else "*" * len(value)
            print(f"✓ {key.upper()}: {display_value}")
    
    # Optional fields
    print(f"✓ DATABASE: {DB_CONFIG.get('database', 'trade_agent')}")
    print(f"✓ PORT: {DB_CONFIG.get('port', '5432')}")
    
    if missing:
        print(f"\n❌ Missing required configuration: {', '.join(missing)}")
        print("   Please set these in your .env file:")
        for key in missing:
            print(f"   {key.upper()}=your_value")
        return False
    
    print("\n✓ All required configuration present")
    return True


def test_connection():
    """Test database connection"""
    print("\n" + "=" * 70)
    print("TESTING DATABASE CONNECTION")
    print("=" * 70)
    
    if not check_config():
        return False
    
    connection = None
    try:
        print(f"\nConnecting to {DB_CONFIG['host']}:{DB_CONFIG['port']}...")
        print(f"Database: {DB_CONFIG['database']}")
        print(f"User: {DB_CONFIG['user']}")
        
        connection = psycopg2.connect(**DB_CONFIG)
        
        # Test query
        cursor = connection.cursor()
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()
        
        cursor.execute("SELECT current_database(), current_user, inet_server_addr(), inet_server_port();")
        db_info = cursor.fetchone()
        
        print("\n" + "=" * 70)
        print("✓ CONNECTION SUCCESSFUL!")
        print("=" * 70)
        print(f"\nPostgreSQL Version: {db_version[0]}")
        print(f"Current Database: {db_info[0]}")
        print(f"Current User: {db_info[1]}")
        print(f"Server Address: {db_info[2]}")
        print(f"Server Port: {db_info[3]}")
        
        # Test basic query
        cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';")
        table_count = cursor.fetchone()[0]
        print(f"\nPublic Tables: {table_count}")
        
        cursor.close()
        return True
        
    except OperationalError as e:
        print("\n" + "=" * 70)
        print("❌ CONNECTION FAILED (Operational Error)")
        print("=" * 70)
        print(f"\nError: {e}")
        print("\nPossible issues:")
        print("  - Database server is not running")
        print("  - Incorrect host/port")
        print("  - Network/firewall blocking connection")
        print("  - Database does not exist")
        return False
        
    except Error as e:
        print("\n" + "=" * 70)
        print("❌ CONNECTION FAILED (Database Error)")
        print("=" * 70)
        print(f"\nError: {e}")
        print("\nPossible issues:")
        print("  - Incorrect username or password")
        print("  - User does not have permission")
        print("  - Database authentication failed")
        return False
        
    except Exception as e:
        print("\n" + "=" * 70)
        print("❌ CONNECTION FAILED (Unexpected Error)")
        print("=" * 70)
        print(f"\nError: {e}")
        print(f"Type: {type(e).__name__}")
        return False
        
    finally:
        if connection:
            connection.close()
            print("\n✓ Connection closed")


def list_databases():
    """List all databases on the server"""
    print("\n" + "=" * 70)
    print("LISTING DATABASES")
    print("=" * 70)
    
    # Connect to postgres database to list all databases
    config = DB_CONFIG.copy()
    config['database'] = 'postgres'
    
    try:
        connection = psycopg2.connect(**config)
        cursor = connection.cursor()
        
        cursor.execute("""
            SELECT datname, pg_size_pretty(pg_database_size(datname)) as size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname;
        """)
        
        databases = cursor.fetchall()
        
        print(f"\nFound {len(databases)} database(s):\n")
        print(f"{'Database Name':<30} {'Size':>15}")
        print("-" * 50)
        
        for db_name, size in databases:
            marker = " ← Current" if db_name == DB_CONFIG['database'] else ""
            print(f"{db_name:<30} {size:>15}{marker}")
        
        cursor.close()
        connection.close()
        
    except Exception as e:
        print(f"\n❌ Error listing databases: {e}")


def list_tables():
    """List tables in current database"""
    print("\n" + "=" * 70)
    print(f"LISTING TABLES IN DATABASE: {DB_CONFIG['database']}")
    print("=" * 70)
    
    try:
        connection = psycopg2.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        cursor.execute("""
            SELECT 
                schemaname,
                tablename,
                pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
            FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schemaname, tablename;
        """)
        
        tables = cursor.fetchall()
        
        if not tables:
            print("\nNo tables found in public schema")
        else:
            print(f"\nFound {len(tables)} table(s):\n")
            print(f"{'Schema':<15} {'Table Name':<40} {'Size':>15}")
            print("-" * 75)
            
            for schema, table_name, size in tables:
                print(f"{schema:<15} {table_name:<40} {size:>15}")
        
        cursor.close()
        connection.close()
        
    except Exception as e:
        print(f"\n❌ Error listing tables: {e}")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("POSTGRESQL DATABASE CONNECTION TEST")
    print("=" * 70)
    
    # Test connection
    if test_connection():
        # If connection successful, show additional info
        import argparse
        
        parser = argparse.ArgumentParser(description='Test PostgreSQL connection')
        parser.add_argument('--list-databases', action='store_true', help='List all databases')
        parser.add_argument('--list-tables', action='store_true', help='List tables in current database')
        parser.add_argument('--all', action='store_true', help='Show all information')
        
        args = parser.parse_args()
        
        if args.all or args.list_databases:
            list_databases()
        
        if args.all or args.list_tables:
            list_tables()
        
        print("\n" + "=" * 70)
        print("✓ TEST COMPLETE")
        print("=" * 70)
    else:
        print("\n" + "=" * 70)
        print("❌ TEST FAILED - Please check your configuration")
        print("=" * 70)
        sys.exit(1)
