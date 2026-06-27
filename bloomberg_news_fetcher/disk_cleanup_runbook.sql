-- ============================================================================
-- DISK CLEANUP RUNBOOK — trade_agent (Postgres)
-- ----------------------------------------------------------------------------
-- Reclaims the post-deploy disk spike and bounds future growth. Run section by
-- section against the prod DB (psql -h <host> -U <user> -d trade_agent).
-- Read the comments — VACUUM FULL takes an exclusive lock; prefer pg_repack.
-- The code-side fixes (scheduled graph cleanup, retention purge, no redundant
-- price indexes) are already in the repo; this file is the DB-side counterpart.
-- ============================================================================


-- === PHASE 0 — MEASURE (read-only) ==========================================
-- Total size + biggest relations + dead tuples:
SELECT pg_size_pretty(pg_database_size(current_database())) AS db_total;

SELECT relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total,
       pg_size_pretty(pg_relation_size(relid))       AS heap,
       n_dead_tup
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 15;

-- Index sizes on `news` (the GIN indexes are the one-time spike):
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'news'
ORDER BY pg_relation_size(indexrelid) DESC;

-- Real row counts (replace the earlier estimates):
SELECT count(*) AS n_news,
       count(title_summary_vector) AS legacy_ts,
       count(full_text_vector)     AS legacy_ft,
       count(embedding)            AS pgvec
FROM news;

SELECT pg_size_pretty(pg_total_relation_size('graph_event_embeddings')) AS event_embeddings,
       pg_size_pretty(pg_total_relation_size('graph_story_centroids'))  AS story_centroids;


-- === PHASE 1 — RECLAIM the one-time step on `news` ==========================
-- GATE: the legacy title+summary vector must already be backfilled into the
-- pgvector `embedding` column. This MUST return 0 before dropping. If not,
-- run migrate_embedding_to_target_column.sql first.
SELECT count(*) AS not_backfilled
FROM news
WHERE embedding IS NULL AND title_summary_vector IS NOT NULL;

-- Optional safety dump of the doomed columns (shell, not psql):
--   pg_dump -t news --column-inserts trade_agent | gzip > news_backup_$(date +%F).sql.gz

-- Drop the legacy JSONB columns. This immediately frees the two GIN indexes
-- (idx_news_title_summary_vector, idx_news_full_text_vector).
ALTER TABLE news DROP COLUMN IF EXISTS title_summary_vector;
ALTER TABLE news DROP COLUMN IF EXISTS full_text_vector;

-- Reclaim the heap (dropped-column bytes + backfill dead tuples). DROP COLUMN is
-- catalog-only; the heap shrinks only on rewrite. Pick ONE:
--   (a) ONLINE, no long lock (preferred):   pg_repack -t news -d trade_agent
--   (b) Maintenance window (~1x table free disk, ACCESS EXCLUSIVE lock):
--       stop the news-fetcher container first, then:
VACUUM (FULL, ANALYZE) news;
--       docker compose start news-fetcher


-- === PHASE 3 — PREVENT recurrence ===========================================

-- 3a. Drop the redundant per-ticker price indexes across all 120 tables.
--     UNIQUE(timestamp) already provides a btree; idx_*_timestamp duplicated it,
--     and idx_*_fetch_time was never queried. (price_fetcher.py no longer creates
--     these, so this drop is permanent.) The UNIQUE auto-index is named
--     "<table>_timestamp_key" and is intentionally NOT matched below.
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT indexname
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND tablename LIKE 'historical_data_%'
      AND indexname LIKE 'idx_%'
      AND (indexname LIKE '%_timestamp' OR indexname LIKE '%_fetch_time')
  LOOP
    EXECUTE format('DROP INDEX IF EXISTS public.%I', r.indexname);
    RAISE NOTICE 'dropped %', r.indexname;
  END LOOP;
END $$;

-- 3b. Tighter autovacuum on the hot tables so dead tuples never accumulate
--     enough to need another manual VACUUM FULL.
ALTER TABLE news
  SET (autovacuum_vacuum_scale_factor = 0.02,
       autovacuum_vacuum_cost_limit  = 2000);

ALTER TABLE graph_event_embeddings
  SET (autovacuum_vacuum_scale_factor = 0.05);

-- 3c. (Optional) immediate one-off purge of old event embeddings to match the
--     EVENT_EMBEDDING_RETENTION_DAYS the scheduler now enforces daily (default 365):
-- DELETE FROM graph_event_embeddings WHERE event_date < (CURRENT_DATE - 365);
