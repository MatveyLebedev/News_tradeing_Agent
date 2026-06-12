# Timezone Handling Guide

## Overview

All timestamps in the News Trading Agent use **US Eastern Time (America/New_York)** for consistency with US stock market hours.

## Why Eastern Time?

- **US Market Hours**: NYSE/NASDAQ operate 9:30 AM - 4:00 PM ET
- **Financial News**: Most US financial news uses ET timestamps
- **Daylight Saving**: The `America/New_York` timezone automatically handles EST/EDT transitions
- **Trader Friendly**: Easier for US traders to interpret

## How It Works

### 1. Input Timestamps

When you provide a timestamp to the agent:

```python
from datetime import datetime
from news_agent import TradingAgent

agent = TradingAgent()

# Option 1: Naive datetime (assumed to be Eastern Time)
timestamp = datetime(2024, 11, 1, 14, 30)  # Interpreted as 2:30 PM ET
signals = agent.run(news_text, timestamp)

# Option 2: Timezone-aware (converted to Eastern Time)
import pytz
utc_tz = pytz.UTC
timestamp_utc = datetime(2024, 11, 1, 18, 30, tzinfo=utc_tz)  # 6:30 PM UTC
signals = agent.run(news_text, timestamp_utc)  # Converted to 2:30 PM ET (EDT)
```

### 2. Database Queries

All database queries enforce Eastern Time:

```python
# Example internal query
query = """
    SELECT * FROM historical_data_aapl
    WHERE date < %s  -- %s is in Eastern Time
"""
```

The `DBManager` class handles timezone conversion automatically:
- Naive timestamps → localized to Eastern Time
- Aware timestamps → converted to Eastern Time
- All comparisons happen in Eastern Time

### 3. Market Hours Validation

Market hours are defined in Eastern Time:

```python
# From agent_config.py
MARKET_OPEN_HOUR = 9      # 9:30 AM ET
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16    # 4:00 PM ET
MARKET_CLOSE_MINUTE = 0
```

News published outside these hours or in the last hour before close are excluded from price impact calculations.

## Database Configuration

### PostgreSQL Timezone

Set your database timezone to **UTC** (recommended) or **America/New_York**:

```sql
-- Option 1: UTC (recommended for multi-region deployments)
ALTER DATABASE news SET timezone = 'UTC';

-- Option 2: Eastern Time
ALTER DATABASE news SET timezone = 'America/New_York';

-- Verify
SHOW timezone;
```

### Column Types

Use `TIMESTAMPTZ` for timezone-aware columns:

```sql
CREATE TABLE bloomberg_news (
    id SERIAL PRIMARY KEY,
    published TIMESTAMPTZ,  -- Stores with timezone info
    fetch_time TIMESTAMPTZ,
    ...
);
```

## Docker Configuration

### Container Timezone

Set container to match database or use UTC:

```yaml
# docker-compose.yml
services:
  news-agent:
    environment:
      # Option 1: Match database
      - TZ=America/New_York
      
      # Option 2: UTC (agent handles conversion)
      - TZ=UTC
```

## Timezone Conversion Examples

### Example 1: Different Container/DB Timezones

**Scenario**: Container in UTC, Database in UTC, Agent uses ET

```python
# Container time: 2024-11-01 18:30:00 UTC
# News timestamp (naive): 2024-11-01 14:30:00

# Agent assumes naive timestamp is ET
timestamp_et = eastern_tz.localize(datetime(2024, 11, 1, 14, 30))
# Result: 2024-11-01 14:30:00 EDT (UTC-4)
#       = 2024-11-01 18:30:00 UTC

# Database stores in UTC
db_timestamp = timestamp_et.astimezone(pytz.UTC)
# Query: WHERE date < '2024-11-01 18:30:00+00'
```

### Example 2: Timezone-Aware Input

**Scenario**: User provides UTC timestamp

```python
import pytz

# User provides UTC timestamp
timestamp_utc = datetime(2024, 11, 1, 18, 30, tzinfo=pytz.UTC)

# Agent converts to ET
timestamp_et = timestamp_utc.astimezone(pytz.timezone('America/New_York'))
# Result: 2024-11-01 14:30:00 EDT

# All internal operations use timestamp_et
```

### Example 3: Daylight Saving Time

**Scenario**: EST vs EDT handling

```python
import pytz
eastern = pytz.timezone('America/New_York')

# Winter (EST = UTC-5)
winter_dt = eastern.localize(datetime(2024, 1, 15, 14, 30))
print(winter_dt)  # 2024-01-15 14:30:00 EST
print(winter_dt.astimezone(pytz.UTC))  # 2024-01-15 19:30:00 UTC

# Summer (EDT = UTC-4)
summer_dt = eastern.localize(datetime(2024, 7, 15, 14, 30))
print(summer_dt)  # 2024-07-15 14:30:00 EDT
print(summer_dt.astimezone(pytz.UTC))  # 2024-07-15 18:30:00 UTC
```

## Best Practices

### ✅ Do

1. **Use timezone-aware datetimes** when possible:
```python
import pytz
eastern = pytz.timezone('America/New_York')
timestamp = eastern.localize(datetime(2024, 11, 1, 14, 30))
```

2. **Be explicit about timezone** in logs/output:
```python
print(f"Timestamp: {timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}")
# Output: Timestamp: 2024-11-01 14:30:00 EDT
```

3. **Test with different timezones**:
```python
# Test with UTC input
utc_timestamp = datetime(2024, 11, 1, 18, 30, tzinfo=pytz.UTC)
signals = agent.run(news_text, utc_timestamp)

# Test with naive (ET assumed)
naive_timestamp = datetime(2024, 11, 1, 14, 30)
signals = agent.run(news_text, naive_timestamp)
```

### ❌ Don't

1. **Don't use datetime.now() without timezone**:
```python
# Bad
timestamp = datetime.now()  # Ambiguous - what timezone?

# Good
timestamp = datetime.now(pytz.timezone('America/New_York'))
```

2. **Don't mix timezones in queries**:
```python
# Bad - comparing different timezones
if timestamp > datetime.now():  # Different timezones!

# Good - both in same timezone
eastern = pytz.timezone('America/New_York')
if timestamp_et > datetime.now(eastern):
```

3. **Don't assume UTC**:
```python
# Bad assumption
# "All timestamps are UTC" - Not true for this agent!

# Correct understanding
# "All timestamps are converted to Eastern Time"
```

## Troubleshooting

### Issue: Wrong price data returned

**Symptom**: Query returns unexpected data

**Cause**: Timezone mismatch between input and database

**Solution**:
```python
# Check what timezone your input is in
print(f"Input timezone: {timestamp.tzinfo}")

# Check database timezone
# In psql: SHOW timezone;

# Ensure conversion is correct
eastern = pytz.timezone('America/New_York')
timestamp_et = timestamp.astimezone(eastern) if timestamp.tzinfo else eastern.localize(timestamp)
print(f"Eastern Time: {timestamp_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
```

### Issue: "Data leakage" error when it shouldn't trigger

**Symptom**: Validation fails for valid historical timestamp

**Cause**: Timezone offset causing future comparison

**Solution**:
```python
# Make sure you're comparing same timezone
eastern = pytz.timezone('America/New_York')
current_et = datetime.now(eastern)
timestamp_et = eastern.localize(timestamp) if timestamp.tzinfo is None else timestamp.astimezone(eastern)

print(f"Current: {current_et}")
print(f"Timestamp: {timestamp_et}")
print(f"Is past: {timestamp_et < current_et}")
```

### Issue: Market hours not working correctly

**Symptom**: News during market hours marked as outside hours

**Cause**: Timestamp not in Eastern Time when checking hours

**Solution**:
```python
# Ensure timestamp is in Eastern Time before checking
eastern = pytz.timezone('America/New_York')
if published_time.tzinfo is None:
    published_time = eastern.localize(published_time)
else:
    published_time = published_time.astimezone(eastern)

# Now check market hours (both in ET)
market_open = published_time.replace(hour=9, minute=30)
market_close = published_time.replace(hour=16, minute=0)
is_market_hours = market_open <= published_time <= market_close
```

## Testing

Test timezone handling with the verification script:

```python
# test_timezone.py
from datetime import datetime
import pytz
from news_agent import TradingAgent

eastern = pytz.timezone('America/New_York')
utc = pytz.UTC

# Test 1: Naive datetime (assumed ET)
print("\nTest 1: Naive datetime")
naive_dt = datetime(2024, 10, 1, 14, 30)
agent = TradingAgent()
try:
    signals = agent.run("Test news", naive_dt)
    print("✓ Naive datetime handled correctly")
except Exception as e:
    print(f"✗ Error: {e}")

# Test 2: UTC timestamp
print("\nTest 2: UTC timestamp")
utc_dt = datetime(2024, 10, 1, 18, 30, tzinfo=utc)
try:
    signals = agent.run("Test news", utc_dt)
    print("✓ UTC timestamp converted correctly")
except Exception as e:
    print(f"✗ Error: {e}")

# Test 3: Already ET timestamp
print("\nTest 3: ET timestamp")
et_dt = eastern.localize(datetime(2024, 10, 1, 14, 30))
try:
    signals = agent.run("Test news", et_dt)
    print("✓ ET timestamp handled correctly")
except Exception as e:
    print(f"✗ Error: {e}")

# Test 4: Future timestamp (should fail)
print("\nTest 4: Future timestamp")
future_dt = eastern.localize(datetime(2030, 1, 1, 10, 0))
try:
    signals = agent.run("Test news", future_dt)
    print("✗ Future timestamp not rejected!")
except ValueError as e:
    print(f"✓ Future timestamp correctly rejected: {e}")

agent.close()
```

## Summary

- **All timestamps → US Eastern Time (America/New_York)**
- **Naive datetime → Assumed to be ET**
- **Aware datetime → Converted to ET**
- **Market hours → Defined in ET**
- **Database → Stores in UTC or ET (agent handles conversion)**
- **Docker container → Can be any timezone (agent handles conversion)**

This consistent timezone handling prevents data leakage and ensures accurate market hours validation.



