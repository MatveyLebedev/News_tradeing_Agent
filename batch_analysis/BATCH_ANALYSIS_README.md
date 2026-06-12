# Batch News Analysis Tool

Comprehensive tool for running agents on multiple news items and tracking results.

## Features

✅ **Count news** for selected period  
✅ **Filter by news type** (optional)  
✅ **Select agent** (simple or complex)  
✅ **Confirmation** before running analysis  
✅ **Store results** in database table with agent info  
✅ **View results** and compare agent performance  

## Files

1. **`create_agent_results_table.sql`** - SQL schema for results table
2. **`batch_analyze_news.py`** - Main batch analysis script
3. **`view_agent_results.py`** - View and compare results
4. **`BATCH_ANALYSIS_README.md`** - This file

## Database Table: `agent_results`

Stores all analysis results with the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | SERIAL | Primary key |
| `run_timestamp` | TIMESTAMP | When analysis was run |
| `agent_name` | VARCHAR(50) | 'simple_agent' or 'complex_agent' |
| `agent_version` | VARCHAR(20) | Version of agent (e.g., '1.0.0') |
| `news_id` | INTEGER | Foreign key to news table |
| `news_title` | TEXT | Title of analyzed news |
| `news_published` | TIMESTAMP | When news was published |
| `signals_generated` | INTEGER | Number of signals generated |
| `signals_json` | JSONB | Full signal data in JSON format |
| `execution_time_seconds` | FLOAT | How long analysis took |
| `news_type` | VARCHAR(50) | Type of news (if categorized) |

## Usage

### 1. Create Database Table

First, create the results table:

```bash
# From project root
psql -h YOUR_DB_HOST -U admin -d trade_agent -f batch_analysis/create_agent_results_table.sql

# Or from batch_analysis directory
cd batch_analysis
psql -h YOUR_DB_HOST -U admin -d trade_agent -f create_agent_results_table.sql
```

Or the script will create it automatically on first run.

### 2. Run Batch Analysis

```bash
cd batch_analysis
python batch_analyze_news.py
```

Or from project root:
```bash
python batch_analysis/batch_analyze_news.py
```

The script will prompt you for:

1. **Date range** - Start and end dates (YYYY-MM-DD)
2. **News type filter** - Optional filter by type (leave empty for all)
3. **Limit** - Optional limit on number of items (leave empty for all)
4. **Agent selection** - Choose simple (1) or complex (2) agent

Example session:

```
======================================================================
BATCH NEWS ANALYSIS TOOL
======================================================================

Enter analysis period:
  Start date (YYYY-MM-DD): 2025-11-01
  End date (YYYY-MM-DD): 2025-11-30

Filter by news type? (leave empty for all)
  News type: 

Limit number of items to analyze? (leave empty for all)
  Limit: 10

Select agent:
  1. Simple Agent (fast, single LLM call)
  2. Complex Agent (comprehensive, multiple tools)
  Choice (1 or 2): 1

======================================================================
BATCH NEWS ANALYSIS
======================================================================

Found 150 news items
Period: 2025-11-01 to 2025-11-30
Limiting analysis to 10 items (of 150)

Fetching 10 news items...
✓ Retrieved 10 news items

Preview of news items:
----------------------------------------------------------------------
1. Apple announces breakthrough M4 chip...
   Published: 2025-11-05 10:30:00-05:00
2. Microsoft expands AI capabilities...
   Published: 2025-11-06 14:15:00-05:00
...

======================================================================
Agent: SIMPLE
Items to analyze: 10
======================================================================

Proceed with analysis? (yes/no): yes

Initializing agent...
✓ Initialized simple_agent v1.0.0

======================================================================
RUNNING ANALYSIS
======================================================================

[1/10] Analyzing: Apple announces breakthrough M4 chip...
  ✓ Generated 1 signal(s) in 2.34s

[2/10] Analyzing: Microsoft expands AI capabilities...
  ✓ Generated 2 signal(s) in 2.56s

...

======================================================================
ANALYSIS COMPLETE
======================================================================
Items analyzed: 10
Total signals: 15
Average signals per item: 1.50
Total time: 25.43s
Average time per item: 2.54s

✓ Results saved to database table: agent_results
======================================================================
```

### 3. View Results

```bash
cd batch_analysis
python view_agent_results.py
```

Or from project root:
```bash
python batch_analysis/view_agent_results.py
```

Options:

1. **View recent runs** - Shows summary of recent analysis runs
2. **View latest run details** - Shows detailed results for most recent run
3. **Compare agents** - Compares performance between simple and complex agents
4. **Exit**

Example output:

```
======================================================================
RECENT ANALYSIS RUNS
======================================================================

Run: 2025-11-28 15:30:45
  Agent: simple_agent v1.0.0
  Items: 10
  Signals: 15
  Avg time: 2.54s

Run: 2025-11-27 10:15:22
  Agent: complex_agent v1.0.0
  Items: 5
  Signals: 12
  Avg time: 15.32s
```

## Query Examples

### Get all results for a specific agent

```sql
SELECT * FROM agent_results
WHERE agent_name = 'simple_agent'
ORDER BY run_timestamp DESC;
```

### Compare signal generation rates

```sql
SELECT 
    agent_name,
    AVG(signals_generated) as avg_signals,
    COUNT(*) as total_runs
FROM agent_results
GROUP BY agent_name;
```

### Find news that generated most signals

```sql
SELECT 
    news_title,
    news_published,
    signals_generated,
    signals_json
FROM agent_results
WHERE signals_generated > 0
ORDER BY signals_generated DESC
LIMIT 10;
```

### Get signals for specific ticker

```sql
SELECT 
    news_title,
    news_published,
    agent_name,
    signals_json
FROM agent_results
WHERE signals_json::text LIKE '%AAPL%'
ORDER BY news_published DESC;
```

## Advanced Usage

### Run with Python API

```python
from batch_analysis.batch_analyze_news import BatchAnalyzer
from datetime import datetime
import pytz

analyzer = BatchAnalyzer()

# Define period
eastern = pytz.timezone('US/Eastern')
start_date = eastern.localize(datetime(2025, 11, 1))
end_date = eastern.localize(datetime(2025, 11, 30))

# Run analysis
analyzer.run_batch_analysis(
    start_date=start_date,
    end_date=end_date,
    agent_type='simple',  # or 'complex'
    news_type=None,       # optional filter
    limit=10              # optional limit
)

analyzer.close()
```

### Custom Queries

```python
import psycopg2

conn = psycopg2.connect(
    host="YOUR_DB_HOST",
    database="trade_agent",
    user="admin",
    password="YOUR_DB_PASSWORD"
)

cursor = conn.cursor()

# Your custom query
query = """
    SELECT agent_name, COUNT(*) 
    FROM agent_results 
    GROUP BY agent_name
"""

cursor.execute(query)
results = cursor.fetchall()

for row in results:
    print(row)

cursor.close()
conn.close()
```

## Performance Metrics

The tool tracks:

- **Execution time** - How long each analysis takes
- **Signal count** - Number of signals generated per news item
- **Success rate** - Percentage of successful analyses
- **Agent comparison** - Simple vs Complex agent performance

## Use Cases

### 1. Backtesting

Analyze historical news to see what signals would have been generated:

```bash
python batch_analyze_news.py
# Enter: 2025-10-01 to 2025-10-31
# Agent: simple
```

### 2. Agent Comparison

Run both agents on same dataset:

```bash
# Run 1: Simple agent
python batch_analyze_news.py
# Select simple agent

# Run 2: Complex agent  
python batch_analyze_news.py
# Select complex agent

# Compare
python view_agent_results.py
# Option 3: Compare agents
```

### 3. Performance Testing

Test agent performance on large datasets:

```bash
python batch_analyze_news.py
# Enter large date range
# Set limit: 100
```

### 4. News Type Analysis

Analyze specific types of news:

```bash
python batch_analyze_news.py
# News type: earnings
# or: merger, product_launch, etc.
```

## Troubleshooting

### Error: "Table does not exist"

Run the SQL script to create the table:

```bash
# From project root
psql -h YOUR_DB_HOST -U admin -d trade_agent -f batch_analysis/create_agent_results_table.sql

# Or from batch_analysis directory
cd batch_analysis
psql -h YOUR_DB_HOST -U admin -d trade_agent -f create_agent_results_table.sql
```

### Error: "No news found"

Check your date range and ensure news exists in database:

```sql
SELECT COUNT(*), MIN(published), MAX(published) 
FROM news;
```

### Error: "Agent initialization failed"

Ensure the agent modules are available:

```bash
# For simple agent
cd simple_agent && python -c "from simple_agent import SimpleAgent"

# For complex agent
cd news_agent && python -c "from news_agent import TradingAgent"
```

## Best Practices

1. **Start small** - Test with limit=10 before running on large datasets
2. **Use confirmation** - Always review before running analysis
3. **Monitor performance** - Check execution times and adjust limits
4. **Compare agents** - Run both agents on same data to compare
5. **Archive old results** - Periodically clean up old results if needed

## Future Enhancements

Possible improvements:

- [ ] Add parallel processing for faster batch analysis
- [ ] Export results to CSV/JSON
- [ ] Add visualization dashboard
- [ ] Email notifications when batch completes
- [ ] Scheduled batch runs (cron jobs)
- [ ] Real-time progress tracking
- [ ] Filter by signal confidence threshold
- [ ] Group results by ticker

## Conclusion

This batch analysis tool provides a comprehensive way to:
- ✅ Test agents on historical data
- ✅ Compare agent performance
- ✅ Track results over time
- ✅ Analyze large datasets efficiently
- ✅ Store results for future analysis

Perfect for backtesting, performance evaluation, and agent development! 🚀




