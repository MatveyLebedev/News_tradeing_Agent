-- SQL script to create news table in trade_agent database
-- Run this script once before starting the news fetcher

-- Connect to trade_agent database first:
-- psql -h YOUR_DB_HOST -U admin -d trade_agent

CREATE TABLE IF NOT EXISTS news (
    id SERIAL UNIQUE NOT NULL,
    link TEXT PRIMARY KEY,
    source VARCHAR(50),
    published TIMESTAMP,
    fetch_time TIMESTAMP,
    title TEXT,
    summary TEXT,
    full_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index on published date for faster queries
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published DESC);

-- Create index on fetch_time for tracking
CREATE INDEX IF NOT EXISTS idx_news_fetch_time ON news(fetch_time DESC);

-- Create index on source for filtering
CREATE INDEX IF NOT EXISTS idx_news_source ON news(source);

COMMENT ON TABLE news IS 'News articles fetched from various sources via RSS feed';
COMMENT ON COLUMN news.id IS 'Auto-incrementing ID for joins with other tables';
COMMENT ON COLUMN news.link IS 'Article URL (primary key)';
COMMENT ON COLUMN news.source IS 'News source (e.g., bloomberg, reuters, etc.)';
COMMENT ON COLUMN news.published IS 'Original publication date from RSS feed';
COMMENT ON COLUMN news.fetch_time IS 'Timestamp when the article was fetched (Moscow time)';
COMMENT ON COLUMN news.title IS 'Article title';
COMMENT ON COLUMN news.summary IS 'Article summary from RSS feed';
COMMENT ON COLUMN news.full_text IS 'Full article text (if available)';

