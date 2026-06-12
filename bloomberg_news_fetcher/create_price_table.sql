-- SQL script to create stock_prices table in trade_agent database
-- Stores 1-minute historical price data for top US tickers

-- Connect to trade_agent database first:
-- psql -h YOUR_DB_HOST -U admin -d trade_agent

CREATE TABLE IF NOT EXISTS stock_prices (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open DECIMAL(12, 4),
    high DECIMAL(12, 4),
    low DECIMAL(12, 4),
    close DECIMAL(12, 4),
    volume BIGINT,
    fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Unique constraint to prevent duplicate entries
    UNIQUE(ticker, timestamp)
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_stock_prices_ticker ON stock_prices(ticker);
CREATE INDEX IF NOT EXISTS idx_stock_prices_timestamp ON stock_prices(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_stock_prices_ticker_timestamp ON stock_prices(ticker, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_stock_prices_fetch_time ON stock_prices(fetch_time DESC);

COMMENT ON TABLE stock_prices IS '1-minute historical price data for top 50 US market tickers';
COMMENT ON COLUMN stock_prices.ticker IS 'Stock ticker symbol';
COMMENT ON COLUMN stock_prices.timestamp IS 'Price timestamp (1-minute interval)';
COMMENT ON COLUMN stock_prices.open IS 'Opening price';
COMMENT ON COLUMN stock_prices.high IS 'High price';
COMMENT ON COLUMN stock_prices.low IS 'Low price';
COMMENT ON COLUMN stock_prices.close IS 'Close price';
COMMENT ON COLUMN stock_prices.volume IS 'Trading volume';
COMMENT ON COLUMN stock_prices.fetch_time IS 'When data was fetched (Moscow time)';

