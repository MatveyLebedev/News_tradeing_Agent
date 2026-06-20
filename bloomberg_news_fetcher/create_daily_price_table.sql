-- Daily OHLCV bars for the long-term agent.
--
-- The intraday `historical_data_<ticker>` tables only go back ~3.7 months (and are 1-min),
-- which isn't enough for 180-day technicals (50/200 MA) or multi-week backtests. This single
-- table holds daily bars backfilled from yfinance (period="2y") and appended daily.
--
-- One table for all tickers (unlike the per-ticker 1-min tables) — daily data is small.

CREATE TABLE IF NOT EXISTS stock_prices_daily (
    id         SERIAL PRIMARY KEY,
    ticker     VARCHAR(10) NOT NULL,
    date       DATE NOT NULL,
    open       DECIMAL(14, 4),
    high       DECIMAL(14, 4),
    low        DECIMAL(14, 4),
    close      DECIMAL(14, 4),
    adj_close  DECIMAL(14, 4),
    volume     BIGINT,
    fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_spd_ticker      ON stock_prices_daily(ticker, date DESC);
CREATE INDEX IF NOT EXISTS idx_spd_date        ON stock_prices_daily(date DESC);

COMMENT ON TABLE stock_prices_daily IS 'Daily OHLCV bars (yfinance) for long-term technicals, priced-in checks, and backtests';
