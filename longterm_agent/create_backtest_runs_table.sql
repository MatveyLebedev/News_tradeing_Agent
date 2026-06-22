-- Walk-forward backtest results (Backtest Plan B1). DELIBERATELY separate from the live
-- longterm_agent_results table so a backtest NEVER pollutes live signals or the forward
-- paper-trade stats. One row per (backtest_id, ticker, as_of, horizon_days): the pipeline
-- runs once per (ticker, as_of); a row is written for each measurement horizon so +2/+4/+12w
-- can be graded independently. `backtest_id` namespaces a whole walk-forward sweep so two
-- configs can be A/B-compared; `provenance` stamps what produced it (git rev, models, etc.).

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              SERIAL PRIMARY KEY,
    backtest_id     TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    provenance      JSONB,                 -- config.provenance(): git rev, models, temp, cache…
    agent_version   VARCHAR(20),

    ticker          VARCHAR(10) NOT NULL,
    as_of           DATE NOT NULL,
    horizon_days    INT NOT NULL,

    -- decision (same across a row's horizons; the pipeline ran once per ticker/as_of)
    direction       VARCHAR(10),           -- LONG | SHORT | NEUTRAL | NO_DATA
    conviction      REAL,
    traded          BOOLEAN DEFAULT FALSE,  -- directional AND passed the risk gate
    shift_flag      BOOLEAN,                -- a background shift fired (deterministic)
    shift_score     REAL,                   -- continuous shift strength 0-1
    regime          VARCHAR(10),            -- bull | bear | flat (SPY trailing return at as_of)
    signal          JSONB,                  -- the (filtered or raw) LongTermSignal

    -- realized outcome (backfilled by the grading pass once the horizon matures)
    fwd_return      REAL,                   -- stock return over the horizon
    benchmark_return REAL,                  -- SPY return over the horizon
    excess          REAL,                   -- signed strategy excess (LONG: stk−SPY, SHORT: SPY−stk)
    outcome_filled_at DATE,

    UNIQUE (backtest_id, ticker, as_of, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_btr_id    ON backtest_runs(backtest_id);
CREATE INDEX IF NOT EXISTS idx_btr_grade ON backtest_runs(backtest_id, outcome_filled_at);

COMMENT ON TABLE backtest_runs IS 'Walk-forward backtest results (B1); isolated from live longterm_agent_results';
