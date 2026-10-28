#!/usr/bin/env python3
"""
DB cleanup analyzer for the `trade_agent` Postgres database.

Read-only by default: connects, reports total DB size, per-table size/rows,
and prints TAILORED cleanup SQL for the regenerable caches, the append-only
logs, and the heavy source columns -- WITHOUT executing anything.

    python3 db_cleanup_report.py              # report + suggested SQL (safe)
    python3 db_cleanup_report.py --apply-caches   # TRUNCATE the regenerable caches only
    python3 db_cleanup_report.py --logs-days 30   # also delete log rows older than 30d

Nothing destructive runs unless you pass an --apply* / --logs-days flag.
Credentials are read from .env (DB_HOST/DB_NAME/DB_USER/DB_PASSWORD/DB_PORT).
"""
import os, sys, argparse
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Regenerable caches: re-created on miss. Clearing them costs the next run some
# LLM time/money but loses NO source data. (See each table's DDL comment.)
CACHE_TABLES = ["longterm_llm_cache", "bucket_summary_cache"]
# Append-only operational logs: keep recent rows, prune old ones by log_time.
LOG_TABLES = {
    "news_parser_log":   "log_time",
    "longterm_agent_log": "log_time",
}
# NEVER suggest cleanup for these (Phase 11): longterm_run_snapshots IS the future
# training set (~100-200 MB/year by design — keep forever); macro_series is tiny PIT
# history that cannot be re-fetched first-release once lost; trace_labels are that
# training set's targets, and Langfuse is rebuilt FROM them.
KEEP_FOREVER = ["longterm_run_snapshots", "macro_series", "trace_labels", "trace_label_syncs"]


def conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", "5432"), connect_timeout=10,
    )


def report(cur):
    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
    print(f"\nTotal database size: {cur.fetchone()[0]}\n")
    cur.execute("""
        SELECT relname,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
               pg_total_relation_size(c.oid) AS bytes,
               c.reltuples::bigint AS approx_rows
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog','information_schema')
        ORDER BY pg_total_relation_size(c.oid) DESC;
    """)
    rows = cur.fetchall()
    print(f"{'TABLE':<38}{'SIZE':>12}{'APPROX ROWS':>16}")
    print("-" * 66)
    for name, pretty, _b, approx in rows:
        print(f"{name:<38}{pretty:>12}{approx:>16,}")
    print(f"\nTotal user tables: {len(rows)}")
    return {r[0] for r in rows}


def suggest(cur, existing, logs_days):
    print("\n" + "=" * 66)
    print("CLEANUP SUGGESTIONS (review before running)")
    print("=" * 66)

    print("\n-- 1) Regenerable caches -- safe to TRUNCATE (re-fills on miss):")
    for t in CACHE_TABLES:
        if t in existing:
            cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{t}'))")
            print(f"TRUNCATE {t};        -- currently {cur.fetchone()[0]}")

    print(f"\n-- 2) Operational logs -- delete rows older than {logs_days} days:")
    for t, col in LOG_TABLES.items():
        if t in existing:
            print(f"DELETE FROM {t} WHERE {col} < NOW() - INTERVAL '{logs_days} days';")

    if "news" in existing:
        print("\n-- 3) Heaviest source column: news.full_text on OLD articles.")
        print("--    Null it out for articles older than 180d (keeps title/summary/metadata):")
        print("UPDATE news SET full_text = NULL "
              "WHERE published < NOW() - INTERVAL '180 days' AND full_text IS NOT NULL;")

    if "graph_story_centroids" in existing:
        print("\n-- 4) Closed/inactive story centroids (1536-d vectors):")
        print("DELETE FROM graph_story_centroids WHERE status <> 'active' "
              "AND last_updated < NOW() - INTERVAL '90 days';")

    keep = [t for t in KEEP_FOREVER if t in existing]
    if keep:
        print(f"\n-- 5) KEEP FOREVER (never suggest cleanup): {', '.join(keep)}")
        print("--    snapshots = the future training set; macro_series = first-release PIT")
        print("--    history that cannot be re-fetched once lost.")

    print("\n-- NOTE: DELETE/TRUNCATE marks space reusable but does NOT shrink the")
    print("--       files on disk. To return space to the OS, afterwards run:")
    print("--         VACUUM (FULL, ANALYZE) <table>;   (locks the table briefly)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-caches", action="store_true",
                    help="TRUNCATE the regenerable cache tables")
    ap.add_argument("--logs-days", type=int, default=None,
                    help="delete log rows older than N days")
    args = ap.parse_args()

    try:
        c = conn()
    except Exception as e:
        print("Could not connect:", str(e).splitlines()[0])
        print("(If this times out, the server firewall may be blocking this IP.)")
        sys.exit(1)

    c.autocommit = True
    cur = c.cursor()
    existing = report(cur)
    suggest(cur, existing, args.logs_days or 30)

    if args.apply_caches:
        for t in CACHE_TABLES:
            if t in existing:
                cur.execute(f"TRUNCATE {t}")
                print(f"[applied] TRUNCATE {t}")
    if args.logs_days:
        for t, col in LOG_TABLES.items():
            if t in existing:
                cur.execute(f"DELETE FROM {t} WHERE {col} < NOW() - INTERVAL '{args.logs_days} days'")
                print(f"[applied] pruned {cur.rowcount:,} rows from {t}")
    c.close()


if __name__ == "__main__":
    main()
