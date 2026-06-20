-- Operational log for the long-term agent (Phase 6 ops).
-- Run-level events + errors from the weekly batch / scheduler / grading jobs, so the
-- Telegram stats bot can report "what ran", "what was produced", and "what failed"
-- without scraping container logs. Self-contained (separate from the news_parser_log).

CREATE TABLE IF NOT EXISTS longterm_agent_log (
    id          BIGSERIAL PRIMARY KEY,
    log_time    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    level       VARCHAR(10),        -- INFO | WARNING | ERROR
    event_type  VARCHAR(40),        -- BATCH_START | RUN_OK | SIGNAL | TRADE | RUN_ERROR | GRADE | ...
    ticker      VARCHAR(10),        -- nullable (job-level events have none)
    as_of       DATE,               -- the analysis date the event refers to (nullable)
    message     TEXT,
    details     JSONB
);

CREATE INDEX IF NOT EXISTS idx_ltlog_time   ON longterm_agent_log(log_time DESC);
CREATE INDEX IF NOT EXISTS idx_ltlog_level  ON longterm_agent_log(level);
CREATE INDEX IF NOT EXISTS idx_ltlog_event  ON longterm_agent_log(event_type);
CREATE INDEX IF NOT EXISTS idx_ltlog_ticker ON longterm_agent_log(ticker);

COMMENT ON TABLE longterm_agent_log IS 'Operational log for the long-term agent batch/scheduler/grading jobs';
