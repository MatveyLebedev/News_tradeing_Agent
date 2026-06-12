-- Table for storing agent analysis results
CREATE TABLE IF NOT EXISTS agent_results (
    id SERIAL PRIMARY KEY,
    
    -- Run metadata
    run_timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    agent_name VARCHAR(50) NOT NULL,  -- 'simple_agent' or 'complex_agent'
    agent_version VARCHAR(20) NOT NULL,  -- e.g., '1.0.0'
    
    -- News analyzed
    news_id INTEGER NOT NULL,
    news_title TEXT,
    news_published TIMESTAMP WITH TIME ZONE,
    
    -- Analysis results
    signals_generated INTEGER DEFAULT 0,
    signals_json JSONB,  -- Full signal data
    
    -- Performance metrics
    execution_time_seconds FLOAT,
    
    -- Filtering info
    news_type VARCHAR(50),  -- Optional: type of news if categorized
    
    -- Indexes for faster queries
    CONSTRAINT fk_news FOREIGN KEY (news_id) REFERENCES news(id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_agent_results_run_timestamp ON agent_results(run_timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_results_agent_name ON agent_results(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_results_news_id ON agent_results(news_id);
CREATE INDEX IF NOT EXISTS idx_agent_results_news_published ON agent_results(news_published);

-- Comments
COMMENT ON TABLE agent_results IS 'Stores results from agent analysis runs for comparison and tracking';
COMMENT ON COLUMN agent_results.agent_name IS 'Name of agent: simple_agent or complex_agent';
COMMENT ON COLUMN agent_results.agent_version IS 'Version of agent used for analysis';
COMMENT ON COLUMN agent_results.signals_json IS 'Full JSON array of generated signals';




