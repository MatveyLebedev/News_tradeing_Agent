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

-- NaN is a VALID `numeric` value in Postgres, so a poisoned bar passes both NOT NULL and
-- every `close IS NOT NULL` filter in the readers, then detonates far downstream — at an
-- int()/round(), or at a JSONB write, which Postgres refuses outright. That is exactly how
-- 2026-07-24 cost a full weekly cohort: yfinance returned Close=NaN for 131 tickers, the
-- fetcher stored it verbatim, and the Sunday batch lost 49 of 64 tickers plus every
-- decision it did manage to make.
--
-- The writer now drops non-finite bars (daily_price_fetcher.save_daily), but this is the
-- guarantee that does not depend on remembering: any writer, ever. NULL still passes —
-- "we have no close for this bar" is a legitimate, readable state; NaN is not.
DO $$
BEGIN
    ALTER TABLE stock_prices_daily ADD CONSTRAINT stock_prices_daily_no_nan
        CHECK (open      <> 'NaN'::numeric AND high  <> 'NaN'::numeric
           AND low       <> 'NaN'::numeric AND close <> 'NaN'::numeric
           AND adj_close <> 'NaN'::numeric);
EXCEPTION
    WHEN duplicate_object THEN NULL;   -- already applied
END $$;

COMMENT ON TABLE stock_prices_daily IS 'Daily OHLCV bars (yfinance) for long-term technicals, priced-in checks, and backtests';
