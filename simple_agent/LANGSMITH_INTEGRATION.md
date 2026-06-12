# LangSmith Integration for Baseline Agent

## Overview

The Simple Agent (Baseline Agent) now supports LangSmith tracing for observability and monitoring.

## Features

- ✅ Trace name: **"baseline_agent"**
- ✅ Default project: **"baseline-agent"**
- ✅ Tracks full execution (inputs, outputs, timing)
- ✅ Optional - can be enabled/disabled
- ✅ Same tracing infrastructure as complex agent

## Setup

### 1. Install Dependencies

```bash
pip install langsmith
```

Or install all dependencies:
```bash
cd simple_agent
pip install -r requirements.txt
```

### 2. Set Environment Variables

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=your-api-key
export LANGCHAIN_PROJECT=baseline-agent
export LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

## Usage

### Method 1: Enable via Constructor

```python
from simple_agent import SimpleAgent
import os

# Set API key
os.environ["LANGCHAIN_API_KEY"] = "your-api-key"

# Initialize with tracing enabled
agent = SimpleAgent(enable_langsmith=True)

# Run analysis (will be traced)
signals = agent.run(news_text=news_text, timestamp=timestamp)

agent.close()
```

### Method 2: Use Pre-configured Script

```bash
cd simple_agent
python run_with_langsmith.py
```

This script:
- Sets up all environment variables
- Enables LangSmith tracing
- Runs example analysis
- Shows where to view traces

## Viewing Traces

1. Go to: https://smith.langchain.com
2. Select project: **baseline-agent**
3. View traces with name: **baseline_agent**

## Trace Information

Each trace includes:

### Inputs
- `news_text`: The news article text
- `timestamp`: Publication timestamp
- `metadata`: Optional metadata dict

### Outputs
- List of trading signals
- Each signal with: ticker, signal, confidence, reasoning

### Metadata
- Execution time
- Model used (gemini-2.0-flash-exp)
- Temperature setting
- Number of signals generated

### Tags
- Run type: "chain"
- Project: "baseline-agent"

## Comparison with Complex Agent

| Feature | Baseline Agent | Complex Agent |
|---------|----------------|---------------|
| **Trace Name** | baseline_agent | run_trading_agent |
| **Project** | baseline-agent | news-trading-agent |
| **Traces per Run** | 1 | 10-20+ (nodes + tools) |
| **Complexity** | Simple | Detailed (graph structure) |
| **Use Case** | Quick monitoring | Deep debugging |

## Example Output

When running with LangSmith enabled:

```
=== Initializing Simple Trading Agent (Baseline) ===
✓ LLM initialized (Vertex AI): gemini-2.0-flash-exp
  Project: elemental-alloy-455119-p5
  Location: us-central1
  Temperature: 0.0
✓ Simple Agent initialized
✓ LangSmith tracing enabled

======================================================================
ANALYZING NEWS (Simple Agent)
======================================================================
...

✓ View detailed trace at: https://smith.langchain.com
  Project: baseline-agent
```

## Troubleshooting

### Error: "LangSmith tracing requested but LANGCHAIN_API_KEY not set"

**Solution**: Set the API key:
```bash
export LANGCHAIN_API_KEY=your-api-key
```

### Traces not appearing in LangSmith

**Check**:
1. API key is valid
2. `LANGCHAIN_TRACING_V2=true` is set
3. Network connectivity to api.smith.langchain.com
4. Project name is correct

### Want to disable tracing

**Option 1**: Don't pass `enable_langsmith=True`
```python
agent = SimpleAgent()  # Tracing disabled by default
```

**Option 2**: Unset environment variable
```bash
unset LANGCHAIN_TRACING_V2
```

## Benefits

### For Development
- Debug single LLM calls
- Compare different prompts
- Track performance over time
- Identify errors quickly

### For Production
- Monitor agent performance
- Track success rates
- Analyze signal quality
- Compare with complex agent

### For Research
- A/B testing different approaches
- Baseline for improvements
- Performance benchmarking
- Cost analysis

## Integration with Complex Agent

You can run both agents with tracing to compare:

```python
# Baseline agent
os.environ["LANGCHAIN_PROJECT"] = "baseline-agent"
simple_agent = SimpleAgent(enable_langsmith=True)
simple_signals = simple_agent.run(news_text, timestamp)

# Complex agent
os.environ["LANGCHAIN_PROJECT"] = "news-trading-agent"
complex_agent = TradingAgent()
complex_signals = complex_agent.run(news_text, timestamp)

# Compare in LangSmith dashboard
```

## Best Practices

1. **Use consistent project names** - Keep "baseline-agent" for simple agent
2. **Tag runs appropriately** - Add metadata for filtering
3. **Monitor costs** - Track API usage via LangSmith
4. **Compare regularly** - Baseline vs complex agent performance
5. **Archive old traces** - Keep dashboard clean

## Future Enhancements

Possible improvements:
- Add custom tags per run
- Track confidence distribution
- Monitor signal accuracy
- Add cost tracking
- Create custom dashboards
- Set up alerts

## Conclusion

LangSmith tracing provides valuable observability for the baseline agent, enabling:
- ✅ Performance monitoring
- ✅ Debugging and troubleshooting
- ✅ Comparison with complex agent
- ✅ Production monitoring
- ✅ Research and experimentation

The integration is lightweight, optional, and follows the same patterns as the complex agent.

