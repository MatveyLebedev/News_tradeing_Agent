import psycopg2
from psycopg2 import Error
import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not required if using system env vars
except Exception:
    pass  # Silently fail if .env doesn't exist

# Database configuration - from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432")
}


def list_databases():
    """List all databases on the PostgreSQL server"""
    try:
        # Connect to postgres database to list all databases
        config = DB_CONFIG.copy()
        config['database'] = 'postgres'
        
        connection = psycopg2.connect(**config)
        connection.autocommit = True
        cursor = connection.cursor()
        
        print("=" * 80)
        print("POSTGRESQL DATABASES")
        print("=" * 80)
        
        cursor.execute("""
            SELECT datname, pg_size_pretty(pg_database_size(datname)) as size
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname;
        """)
        
        databases = cursor.fetchall()
        
        print(f"\nFound {len(databases)} database(s):\n")
        print(f"{'Database Name':<30} {'Size':>15}")
        print("-" * 80)
        
        for db_name, size in databases:
            print(f"{db_name:<30} {size:>15}")
        
        cursor.close()
        connection.close()
        
        return [db[0] for db in databases]
        
    except Error as e:
        print(f"Error listing databases: {e}")
        return []


def list_tables(database):
    """List all tables in a specific database"""
    try:
        config = DB_CONFIG.copy()
        config['database'] = database
        
        connection = psycopg2.connect(**config)
        cursor = connection.cursor()
        
        print(f"\n{'='*80}")
        print(f"TABLES IN DATABASE: {database}")
        print(f"{'='*80}")
        
        # Get all tables
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
            print("\nNo tables found.")
        else:
            print(f"\nFound {len(tables)} table(s):\n")
            print(f"{'Schema':<15} {'Table Name':<40} {'Size':>15}")
            print("-" * 80)
            
            for schema, table_name, size in tables:
                print(f"{schema:<15} {table_name:<40} {size:>15}")
        
        # Get row counts for each table
        if tables:
            print(f"\n{'='*80}")
            print("TABLE ROW COUNTS")
            print(f"{'='*80}\n")
            print(f"{'Schema':<15} {'Table Name':<40} {'Rows':>15}")
            print("-" * 80)
            
            for schema, table_name, _ in tables:
                try:
                    cursor.execute(f'SELECT COUNT(*) FROM {schema}.{table_name};')
                    count = cursor.fetchone()[0]
                    print(f"{schema:<15} {table_name:<40} {count:>15,}")
                except Exception as e:
                    print(f"{schema:<15} {table_name:<40} {'Error':>15}")
        
        cursor.close()
        connection.close()
        
    except Error as e:
        print(f"Error accessing database '{database}': {e}")


def show_table_details(database, table_name, schema='public'):
    """Show detailed information about a specific table"""
    try:
        config = DB_CONFIG.copy()
        config['database'] = database
        
        connection = psycopg2.connect(**config)
        cursor = connection.cursor()
        
        print(f"\n{'='*80}")
        print(f"TABLE DETAILS: {schema}.{table_name}")
        print(f"{'='*80}")
        
        # Get columns
        cursor.execute("""
            SELECT 
                column_name,
                data_type,
                character_maximum_length,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position;
        """, (schema, table_name))
        
        columns = cursor.fetchall()
        
        print(f"\nColumns ({len(columns)}):\n")
        print(f"{'Column':<25} {'Type':<20} {'Nullable':<10} {'Default':<20}")
        print("-" * 80)
        
        for col_name, data_type, max_len, nullable, default in columns:
            type_str = data_type
            if max_len:
                type_str += f"({max_len})"
            default_str = str(default)[:20] if default else ""
            print(f"{col_name:<25} {type_str:<20} {nullable:<10} {default_str:<20}")
        
        # Get row count
        cursor.execute(f'SELECT COUNT(*) FROM {schema}.{table_name};')
        count = cursor.fetchone()[0]
        print(f"\nTotal rows: {count:,}")
        
        # Show sample data
        if count > 0:
            cursor.execute(f'SELECT * FROM {schema}.{table_name} LIMIT 3;')
            rows = cursor.fetchall()
            
            print(f"\nSample data (first {len(rows)} rows):")
            print("-" * 80)
            for row in rows:
                print(row)
        
        cursor.close()
        connection.close()
        
    except Error as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='PostgreSQL Database and Table Explorer')
    parser.add_argument('--databases', action='store_true', help='List all databases')
    parser.add_argument('--tables', metavar='DB', help='List tables in specified database')
    parser.add_argument('--details', nargs=2, metavar=('DB', 'TABLE'), help='Show table details')
    parser.add_argument('--all', action='store_true', help='Show databases and all tables')
    
    args = parser.parse_args()
    
    if args.all:
        # List all databases and their tables
        databases = list_databases()
        for db in databases:
            list_tables(db)
    elif args.databases:
        # Just list databases
        list_databases()
    elif args.tables:
        # List tables in specific database
        list_tables(args.tables)
    elif args.details:
        # Show details for specific table
        show_table_details(args.details[0], args.details[1])
    else:
        # Default: show everything for 'news' database
        print("=" * 80)
        print("POSTGRESQL SERVER INFORMATION")
        print("=" * 80)
        print(f"Host: {DB_CONFIG['host']}")
        print(f"Port: {DB_CONFIG['port']}")
        print(f"User: {DB_CONFIG['user']}")
        
        databases = list_databases()
        
        # Show tables for 'news' database if it exists
        if 'news' in databases:
            list_tables('news')
        
        print("\n" + "=" * 80)
        print("USAGE EXAMPLES")
        print("=" * 80)
        print("  python db_info.py --databases              # List all databases")
        print("  python db_info.py --tables news            # List tables in 'news' database")
        print("  python db_info.py --details news articles  # Show details for 'articles' table")
        print("  python db_info.py --all                    # Show everything")






