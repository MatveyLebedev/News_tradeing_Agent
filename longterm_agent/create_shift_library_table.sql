-- Shift library for the long-term agent's analogical ("similar past shifts") reasoning.
--
-- Each row is one detected news-background shift: its fingerprint (direction, magnitude,
-- causal event types, novelty, fundamentals posture) plus — backfilled later by the
-- grading job — its realized forward return. The `similar_shift_analysis` step searches
-- this table for analogs of the current shift and reads their outcomes as a base rate.
--
-- The library is POPULATED BY THE AGENT ITSELF (backfill_shift_library.py replays past
-- `as_of` dates and records every detected shift), not seeded by hand. A shift is computed
-- from raw news/graph/price data, so an empty table is fine at cold start.
--
-- agent_version is load-bearing: the rows are products of a specific detector. When the
-- detection logic changes you regenerate under a new version (old rows stay for audit /
-- comparison), and similarity search filters to one version so fingerprints are comparable.
-- Mirrors the agent_name/agent_version convention in agent_results.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS shift_library (
    id              SERIAL PRIMARY KEY,

    -- provenance / versioning
    agent_name      VARCHAR(50) NOT NULL DEFAULT 'longterm_agent',
    agent_version   VARCHAR(20) NOT NULL,        -- detector version that produced this row
    detected_at     TIMESTAMP DEFAULT NOW(),

    -- what + when
    ticker          VARCHAR(10) NOT NULL,
    as_of           DATE NOT NULL,               -- analysis date the shift was detected at
    inflection_date DATE,                         -- estimated start of the shift

    -- shift fingerprint (the similarity query keys on these)
    direction       VARCHAR(10),                 -- 'positive' | 'negative'
    magnitude_z     REAL,                        -- sentiment break z-score
    shift_window_days INT,
    causal_event_types TEXT[],                   -- e.g. {regulatory, trade_policy}
    novelty         REAL,
    attention_spike_ratio REAL,
    fundamentals_context JSONB,                  -- improving/deteriorating + valuation snapshot
    shift_embedding VECTOR(1536),                -- semantic fingerprint for similarity search
    features        JSONB,                       -- full extensible feature payload

    -- realized outcomes (NULL until the grading job fills them once the horizon matures)
    fwd_return_2w   REAL,
    fwd_return_4w   REAL,
    fwd_return_12w  REAL,
    benchmark_return_4w REAL,                     -- SPY over same window → excess return
    outcome_filled_at DATE,

    -- one shift per ticker/date PER VERSION: re-running a new version adds rows,
    -- re-running the same version is idempotent.
    UNIQUE (agent_version, ticker, as_of)
);

CREATE INDEX IF NOT EXISTS idx_shift_library_ticker   ON shift_library(ticker, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_shift_library_version  ON shift_library(agent_version);
CREATE INDEX IF NOT EXISTS idx_shift_library_ungraded ON shift_library(outcome_filled_at) WHERE outcome_filled_at IS NULL;
-- Vector ANN index for similarity search (cosine); requires pgvector >= 0.5 for HNSW.
CREATE INDEX IF NOT EXISTS idx_shift_library_embedding
    ON shift_library USING hnsw (shift_embedding vector_cosine_ops);

COMMENT ON TABLE  shift_library              IS 'Detected news-background shifts + realized outcomes; powers analogical reasoning';
COMMENT ON COLUMN shift_library.agent_version IS 'Detector version — regenerate under a new version when detection logic changes; search filters by version';
COMMENT ON COLUMN shift_library.as_of         IS 'Analysis date; detection used only data <= as_of (no lookahead)';
COMMENT ON COLUMN shift_library.fwd_return_4w IS 'Realized 4-week forward return, backfilled by the grading job after the horizon matures';
