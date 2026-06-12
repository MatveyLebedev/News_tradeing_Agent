-- Migrate title+summary embedding to target pgvector column for HNSW index use.
-- Run once (e.g. psql -h ... -U ... -d trade_agent -f migrate_embedding_to_target_column.sql).
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;

-- 1) Add target column if missing
ALTER TABLE news ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- 2) HNSW index for fast similarity search
CREATE INDEX IF NOT EXISTS idx_news_embedding ON news USING hnsw (embedding vector_cosine_ops);

-- 3) Backfill: copy from title_summary_vector (JSONB) into embedding (pgvector)
UPDATE news
SET embedding = (title_summary_vector::text)::vector
WHERE title_summary_vector IS NOT NULL
  AND embedding IS NULL;
