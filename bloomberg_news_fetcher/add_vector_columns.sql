-- Add vector columns to news table for embeddings
-- Run this script once to add vector storage

-- Connect to trade_agent database first:
-- psql -h YOUR_DB_HOST -U admin -d trade_agent

-- Add JSONB columns for storing embeddings
ALTER TABLE news ADD COLUMN IF NOT EXISTS title_summary_vector JSONB;
ALTER TABLE news ADD COLUMN IF NOT EXISTS full_text_vector JSONB;

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_news_title_summary_vector ON news USING gin(title_summary_vector);
CREATE INDEX IF NOT EXISTS idx_news_full_text_vector ON news USING gin(full_text_vector);

-- Add comments
COMMENT ON COLUMN news.title_summary_vector IS 'Vector embedding of title + summary (768 dimensions from Vertex AI text-embedding-004)';
COMMENT ON COLUMN news.full_text_vector IS 'Vector embedding of full_text (768 dimensions from Vertex AI text-embedding-004)';

