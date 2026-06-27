"""
One-shot, idempotent database maintenance, run automatically at fetcher startup.

Automates the disk-reclaim runbook (disk_cleanup_runbook.sql) so it no longer has
to be applied by hand:
  * drop the legacy JSONB embedding columns + their GIN indexes — GATED on the
    pgvector backfill being complete, so un-backfilled data is never dropped;
  * drop the redundant per-ticker price indexes;
  * tighten autovacuum on the hot tables;
  * reclaim heap with VACUUM FULL, but only when something was actually dropped
    (so ordinary restarts stay cheap), or when DB_VACUUM_FULL_ON_START=true.

Every step is idempotent — after the first successful run they become no-ops.
Master switch: DB_MAINTENANCE_ON_START (default true).
"""
import os
import time

import psycopg2
from psycopg2 import sql

from config import DB_MAINTENANCE_ON_START, DB_VACUUM_FULL_ON_START


def _connect():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=int(os.getenv("DB_PORT", 5432)),
    )
    conn.autocommit = True  # required for VACUUM; DDL runs fine in autocommit too
    return conn


def _column_exists(cur, table, column):
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def _table_exists(cur, table):
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
        (table,),
    )
    return cur.fetchone() is not None


def _drop_legacy_news_vectors(cur, verbose):
    """Drop title_summary_vector / full_text_vector (+ their GIN indexes).

    Gated: if the title+summary vector is not yet fully backfilled into the
    pgvector `embedding` column, skip the drop entirely (never lose data).
    Returns True if any column was dropped this run.
    """
    if _column_exists(cur, "news", "title_summary_vector"):
        cur.execute(
            """
            SELECT count(*) FROM news
            WHERE embedding IS NULL AND title_summary_vector IS NOT NULL
            """
        )
        not_backfilled = cur.fetchone()[0]
        if not_backfilled:
            print(
                f"⚠ Skipping legacy-column drop: {not_backfilled} rows not yet "
                f"backfilled into news.embedding "
                f"(run migrate_embedding_to_target_column.sql first)."
            )
            return False

    dropped = False
    for col in ("title_summary_vector", "full_text_vector"):
        if _column_exists(cur, "news", col):
            cur.execute(
                sql.SQL("ALTER TABLE news DROP COLUMN IF EXISTS {}").format(
                    sql.Identifier(col)
                )
            )
            dropped = True
            if verbose:
                print(f"✓ Dropped legacy column news.{col} (+ its GIN index)")
    return dropped


def _drop_redundant_price_indexes(cur, verbose):
    """Drop the redundant per-ticker price indexes across all historical_data_* tables.

    idx_*_timestamp duplicates the UNIQUE(timestamp) btree; idx_*_fetch_time is
    never queried. The UNIQUE auto-index ("<table>_timestamp_key") is not matched.
    """
    cur.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename LIKE 'historical_data_%'
          AND indexname LIKE 'idx_%'
          AND (indexname LIKE '%_timestamp' OR indexname LIKE '%_fetch_time')
        """
    )
    names = [r[0] for r in cur.fetchall()]
    for name in names:
        cur.execute(
            sql.SQL("DROP INDEX IF EXISTS public.{}").format(sql.Identifier(name))
        )
    if names and verbose:
        print(f"✓ Dropped {len(names)} redundant per-ticker price indexes")
    return len(names)


def _tune_autovacuum(cur, verbose):
    cur.execute(
        "ALTER TABLE news SET "
        "(autovacuum_vacuum_scale_factor = 0.02, autovacuum_vacuum_cost_limit = 2000)"
    )
    if _table_exists(cur, "graph_event_embeddings"):
        cur.execute(
            "ALTER TABLE graph_event_embeddings SET "
            "(autovacuum_vacuum_scale_factor = 0.05)"
        )
    if verbose:
        print("✓ Autovacuum tuned on hot tables")


def run_startup_db_maintenance(verbose=True):
    """Idempotent reclaim run once at startup. Safe to call on every boot."""
    if not DB_MAINTENANCE_ON_START:
        return
    try:
        conn = _connect()
    except Exception as e:
        print(f"⚠️  DB maintenance skipped (no connection): {e}")
        return
    try:
        with conn.cursor() as cur:
            if verbose:
                print("\n🧹 Startup DB maintenance...")
            dropped = _drop_legacy_news_vectors(cur, verbose)
            _drop_redundant_price_indexes(cur, verbose)
            _tune_autovacuum(cur, verbose)

            # Heap reclaim. Runs before the fetch loop starts, so the brief
            # ACCESS EXCLUSIVE lock on `news` is benign. Only fire the heavy
            # VACUUM FULL when we actually dropped something (≈ once, post-deploy)
            # or when explicitly forced — otherwise a cheap VACUUM ANALYZE.
            if dropped or DB_VACUUM_FULL_ON_START:
                t0 = time.time()
                if verbose:
                    print("  VACUUM FULL news (reclaiming heap, holds a brief lock)...")
                cur.execute("VACUUM (FULL, ANALYZE) news")
                if verbose:
                    print(f"✓ VACUUM FULL news done in {time.time() - t0:.1f}s")
            else:
                cur.execute("VACUUM (ANALYZE) news")
                if verbose:
                    print("✓ VACUUM ANALYZE news (no heavy reclaim needed)")
        if verbose:
            print("✓ Startup DB maintenance complete\n")
    except Exception as e:
        print(f"⚠️  DB maintenance error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    run_startup_db_maintenance(verbose=True)
