-- Results of long-term agent runs (mirrors batch_analysis/agent_results, for the
-- multi-week agent). One row per (agent_version, ticker, as_of). Stores the full
-- reasoning artifacts as JSONB plus the final signal, so runs are auditable and the
-- backtest/grading job can attach realized outcomes later.

CREATE TABLE IF NOT EXISTS longterm_agent_results (
    id              SERIAL PRIMARY KEY,
    agent_name      VARCHAR(50) NOT NULL DEFAULT 'longterm_agent',
    agent_version   VARCHAR(20) NOT NULL,
    run_timestamp   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    ticker          VARCHAR(10) NOT NULL,
    as_of           DATE NOT NULL,
    lookback_days   INT,

    -- reasoning artifacts (one JSONB per stage)
    background_profile  JSONB,
    background_shift    JSONB,
    fundamentals_summary JSONB,
    shift_analogs       JSONB,
    signal              JSONB,         -- final LongTermSignal (direction, conviction, horizon, thesis, invalidation)

    verdict         VARCHAR(20),       -- LONG | SHORT | NEUTRAL | NO_DATA
    conviction      REAL,
    article_count   INT,
    execution_time_seconds FLOAT,

    -- realized outcome (backfilled by the grading job once the horizon matures)
    fwd_return_4w   REAL,
    benchmark_return_4w REAL,
    outcome_filled_at DATE,

    UNIQUE (agent_version, ticker, as_of)
);

CREATE INDEX IF NOT EXISTS idx_lar_ticker  ON longterm_agent_results(ticker, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_lar_version ON longterm_agent_results(agent_version);
CREATE INDEX IF NOT EXISTS idx_lar_verdict ON longterm_agent_results(verdict);

COMMENT ON TABLE longterm_agent_results IS 'Long-term agent run results + reasoning artifacts; outcomes graded later';
