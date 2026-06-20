-- Cache of per-week background-profiling map outputs.
--
-- Consecutive backtest cohorts (weekly as_of dates) share almost all their weekly
-- buckets, so the same (ticker, week) would otherwise be re-summarized by the LLM once
-- per cohort. Caching the map step keyed by (ticker, week_start, agent_version) means
-- each week is summarized ONCE — ~5-10x cheaper replay AND reproducible labels (the
-- cached sentiment is fixed, removing the run-to-run map variance).
--
-- A bucket is only cached once it is COMPLETE (its week has fully elapsed before as_of);
-- the in-progress final bucket of a run is never cached (it would change as news arrives).

CREATE TABLE IF NOT EXISTS bucket_summary_cache (
    ticker        VARCHAR(10) NOT NULL,
    week_start    DATE NOT NULL,
    agent_version VARCHAR(20) NOT NULL,
    data          JSONB NOT NULL,          -- the PeriodBucketSummary payload
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (ticker, week_start, agent_version)
);

COMMENT ON TABLE bucket_summary_cache IS 'Cached per-week map-step summaries; makes the replay cheap and reproducible';
