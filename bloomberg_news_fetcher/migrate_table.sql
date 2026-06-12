-- Migration script to rename bloomberg_news to news and add source column
-- Run this if you already have the old bloomberg_news table

-- Connect to trade_agent database first:
-- psql -h YOUR_DB_HOST -U admin -d trade_agent

-- Option 1: If you want to keep existing data
ALTER TABLE IF EXISTS bloomberg_news RENAME TO news;
ALTER TABLE IF EXISTS news ADD COLUMN IF NOT EXISTS source VARCHAR(50);
UPDATE news SET source = 'bloomberg' WHERE source IS NULL;

-- Rename indexes
DROP INDEX IF EXISTS idx_bloomberg_news_published;
DROP INDEX IF EXISTS idx_bloomberg_news_fetch_time;
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published DESC);
CREATE INDEX IF NOT EXISTS idx_news_fetch_time ON news(fetch_time DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);

-- Update comments
COMMENT ON TABLE news IS 'News articles fetched from various sources via RSS feed';
COMMENT ON COLUMN news.source IS 'News source (e.g., bloomberg, reuters, etc.)';

-- Option 2: If you want to start fresh (uncomment if needed)
-- DROP TABLE IF EXISTS bloomberg_news CASCADE;
-- Then run create_table.sql

