-- SQL script to create news_parser_log table in trade_agent database
-- Run this script to create the logging table

-- Connect to trade_agent database first:
-- psql -h YOUR_DB_HOST -U admin -d trade_agent

CREATE TABLE IF NOT EXISTS news_parser_log (
    id SERIAL PRIMARY KEY,
    log_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    log_level VARCHAR(20),
    event_type VARCHAR(50),
    message TEXT,
    article_link TEXT,
    details JSONB
);

-- Create index on log_time for faster queries
CREATE INDEX IF NOT EXISTS idx_news_parser_log_time ON news_parser_log(log_time DESC);

-- Create index on log_level for filtering
CREATE INDEX IF NOT EXISTS idx_news_parser_log_level ON news_parser_log(log_level);

-- Create index on event_type for filtering
CREATE INDEX IF NOT EXISTS idx_news_parser_log_event ON news_parser_log(event_type);

COMMENT ON TABLE news_parser_log IS 'Logs for Bloomberg news parser operations';
COMMENT ON COLUMN news_parser_log.id IS 'Auto-incrementing log entry ID';
COMMENT ON COLUMN news_parser_log.log_time IS 'Timestamp of the log entry';
COMMENT ON COLUMN news_parser_log.log_level IS 'Log level: INFO, WARNING, ERROR';
COMMENT ON COLUMN news_parser_log.event_type IS 'Type of event: FETCH_START, FETCH_COMPLETE, ARTICLE_FETCHED, ARTICLE_ERROR, DB_SAVE, etc.';
COMMENT ON COLUMN news_parser_log.message IS 'Log message';
COMMENT ON COLUMN news_parser_log.article_link IS 'Article URL if applicable';
COMMENT ON COLUMN news_parser_log.details IS 'Additional details in JSON format';

