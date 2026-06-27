-- SQL script to create the `news` table in the trade_agent database.
-- Run once before starting the news fetcher:
--   psql -h <db-host> -U <user> -d trade_agent -f create_table.sql
-- Fresh installs need ONLY this script — it already includes the pgvector
-- `embedding` column + HNSW index. (migrate_embedding_to_target_column.sql is
-- only for upgrading older databases that stored embeddings as JSONB.)

-- pgvector extension — required for the `embedding` column / similarity search.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS news (
    id SERIAL UNIQUE NOT NULL,
    link TEXT PRIMARY KEY,
    source VARCHAR(50),
    published TIMESTAMP,
    fetch_time TIMESTAMP,
    title TEXT,
    summary TEXT,
    full_text TEXT,
    embedding vector(1536),          -- title+summary embedding (pgvector)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add the embedding column to pre-existing tables that lack it (idempotent).
ALTER TABLE news ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published DESC);
CREATE INDEX IF NOT EXISTS idx_news_fetch_time ON news(fetch_time DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);
-- HNSW index for fast cosine similarity search on the embedding.
CREATE INDEX IF NOT EXISTS idx_news_embedding ON news USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE news IS 'News articles fetched from various sources via RSS feed';
COMMENT ON COLUMN news.id IS 'Auto-incrementing ID for joins with other tables';
COMMENT ON COLUMN news.link IS 'Article URL (primary key)';
COMMENT ON COLUMN news.source IS 'News source (e.g., bloomberg-technology, cnbc-top, marketwatch-top)';
COMMENT ON COLUMN news.published IS 'Original publication date from RSS feed, stored in US Eastern (America/New_York)';
COMMENT ON COLUMN news.fetch_time IS 'Timestamp when the article was fetched, stored in US Eastern (America/New_York)';
COMMENT ON COLUMN news.title IS 'Article title';
COMMENT ON COLUMN news.summary IS 'Article summary from RSS feed';
COMMENT ON COLUMN news.full_text IS 'Full article text (if available)';
COMMENT ON COLUMN news.embedding IS '1536-dim title+summary embedding (gemini-embedding-001 via OpenRouter)';
