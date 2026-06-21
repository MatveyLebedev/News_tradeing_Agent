-- Content-addressed LLM response cache (Backtest Plan B0).
-- Key = sha256(model+params+prompt+schema). Because the key includes the full prompt
-- (which contains the news/financial text), a hit only occurs for a byte-identical call —
-- so the cache never returns a stale answer for changed inputs. Record-then-replay makes
-- backtests reproducible and cheap. Safe to clear/TRUNCATE at any time (re-records on miss).

CREATE TABLE IF NOT EXISTS longterm_llm_cache (
    key        TEXT PRIMARY KEY,      -- sha256(llm_string + 0x1f + prompt)
    llm        TEXT,                  -- short llm_string prefix (model/params) for debugging
    response   TEXT,                  -- langchain_core.load.dumps(list[Generation])
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llmcache_created ON longterm_llm_cache(created_at);

COMMENT ON TABLE longterm_llm_cache IS 'Content-addressed LLM response cache for reproducible/cheap backtests (B0)';
