-- One-time backfill of the pgvector `embedding` column for databases that
-- PREVIOUSLY stored embeddings as JSONB (legacy `title_summary_vector`).
--
-- Fresh installs created with create_table.sql already have `embedding` + the
-- HNSW index and do NOT need this script. Running it on a fresh DB is a safe
-- no-op (the backfill is guarded on the legacy column still existing).
--
--   psql -h <db-host> -U <user> -d trade_agent -f migrate_embedding_to_target_column.sql

-- Ensure the target column + index exist (idempotent; mirrors create_table.sql).
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE news ADD COLUMN IF NOT EXISTS embedding vector(1536);
CREATE INDEX IF NOT EXISTS idx_news_embedding ON news USING hnsw (embedding vector_cosine_ops);

-- Backfill from the legacy JSONB column ONLY if it still exists, so this is a
-- safe no-op on fresh databases that never had `title_summary_vector`.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'news' AND column_name = 'title_summary_vector'
  ) THEN
    UPDATE news
    SET embedding = (title_summary_vector::text)::vector
    WHERE title_summary_vector IS NOT NULL
      AND embedding IS NULL;
  END IF;
END $$;

-- After backfill, drop the legacy JSONB columns + their GIN indexes to reclaim
-- space (this is also done automatically by db_maintenance.py at startup):
--   ALTER TABLE news DROP COLUMN IF EXISTS title_summary_vector;
--   ALTER TABLE news DROP COLUMN IF EXISTS full_text_vector;
--   VACUUM (FULL, ANALYZE) news;   -- needs ~1x table size free disk
