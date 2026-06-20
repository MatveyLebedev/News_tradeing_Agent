-- Fundamentals store for the long-term trading agent.
--
-- Two sources land in the same table:
--   * 'edgar'    — historical, point-in-time as-filed statements (true PIT via the
--                  10-Q/10-K `filed` date). Backfilled once, then topped up.
--   * 'yfinance' — current snapshots (valuation ratios/margins, analyst estimates,
--                  earnings dates) captured weekly. Each snapshot is keyed by
--                  `fetch_date`, which builds a forward PIT archive over time.
--
-- The agent reads this table with an as-of filter:
--   EDGAR rows:    WHERE filed_date <= :as_of   (never see a statement before it was filed)
--   yfinance rows: WHERE fetch_date <= :as_of   (use the snapshot known at the time)
--
-- `data` holds the normalized payload as JSONB so metric definitions can evolve in the
-- agent's read-side tool without re-backfilling. `dedup_key` is computed in Python and
-- gives a version-independent ON CONFLICT target (NULLs in a multi-col UNIQUE are treated
-- as distinct across Postgres versions, so we avoid relying on that).

CREATE TABLE IF NOT EXISTS fundamentals (
    id            SERIAL PRIMARY KEY,
    ticker        VARCHAR(10) NOT NULL,
    source        VARCHAR(20) NOT NULL,   -- 'edgar' | 'yfinance'
    kind          VARCHAR(40) NOT NULL,   -- edgar: income_statement|balance_sheet|cash_flow
                                          -- yfinance: info|earnings_dates|analyst_estimates|eps_revisions
    period_end    DATE,                   -- statement period end (NULL for yfinance snapshots)
    fiscal_period VARCHAR(10),            -- 'Q1'..'Q4' | 'FY' (NULL for snapshots)
    filed_date    DATE,                   -- EDGAR: original filing date = the point-in-time key
    fetch_date    DATE NOT NULL,          -- when we pulled it (snapshot key for yfinance)
    dedup_key     TEXT NOT NULL UNIQUE,   -- ticker|source|kind|period_end|fiscal_period|filed_date|fetch_date
    data          JSONB NOT NULL,
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker        ON fundamentals(ticker);
CREATE INDEX IF NOT EXISTS idx_fundamentals_lookup        ON fundamentals(ticker, kind, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_fundamentals_filed         ON fundamentals(ticker, filed_date DESC);
CREATE INDEX IF NOT EXISTS idx_fundamentals_fetch         ON fundamentals(ticker, kind, fetch_date DESC);

COMMENT ON TABLE  fundamentals          IS 'Point-in-time company fundamentals: EDGAR as-filed statements + weekly yfinance snapshots';
COMMENT ON COLUMN fundamentals.filed_date IS 'EDGAR original filing date — use as-of gate for backtests (filed_date <= as_of)';
COMMENT ON COLUMN fundamentals.fetch_date IS 'Snapshot date — use as-of gate for yfinance rows (fetch_date <= as_of)';
COMMENT ON COLUMN fundamentals.data       IS 'Normalized JSONB payload; derived metrics computed at read time in the agent';
