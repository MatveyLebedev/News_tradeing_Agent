# Bloomberg News Fetcher - Timezone Update

## Summary

The Bloomberg news fetcher has been updated to use **US Eastern Time (America/New_York)** for all timestamps, ensuring consistency with the trading agent.

---

## Changes Made

### 1. **news_fetcher.py** ✅

**Before:**
```python
# Moscow timezone
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Used Moscow time for fetch_time
fetch_time = datetime.now(MOSCOW_TZ)

# Fallback to Moscow time on parse error
return datetime.now(MOSCOW_TZ)
```

**After:**
```python
# US Eastern Time timezone (for consistency with trading agent and US market hours)
EASTERN_TZ = pytz.timezone('America/New_York')

# Use Eastern Time for fetch_time (consistent with trading agent)
fetch_time = datetime.now(EASTERN_TZ)

# Return current Eastern Time if parsing fails
return datetime.now(EASTERN_TZ)
```

**Key Updates:**
- Changed timezone from Moscow to US Eastern Time
- Updated `parse_published_date()` to convert RSS feed timestamps to ET
- Added timezone conversion for all parsed dates
- Updated display format to show timezone (e.g., "2024-11-05 14:30:00 EST")
- Added timezone logging in fetch start message

### 2. **config.py** ✅

**Added:**
```python
# Timezone: All timestamps stored in US Eastern Time (America/New_York)
# This is consistent with:
# - US market hours (9:30 AM - 4:00 PM ET)
# - Trading agent timezone
# - Most financial news timestamps
TIMEZONE = "America/New_York"
```

---

## How It Works Now

### Timestamp Flow

```
RSS Feed (UTC or other timezone)
    ↓
parse_published_date()
    ↓
Convert to Eastern Time
    ↓
Store in Database (ET)
    ↓
Trading Agent reads (already ET)
```

### Example

**RSS Feed provides:**
```
Published: Wed, 05 Nov 2024 18:30:00 GMT (UTC)
```

**News Fetcher converts to:**
```
Published: 2024-11-05 13:30:00 EST (or EDT depending on DST)
```

**Database stores:**
```sql
published: 2024-11-05 13:30:00-05:00 (EST)
-- or --
published: 2024-11-05 13:30:00-04:00 (EDT during daylight saving)
```

**Trading Agent reads:**
```python
timestamp: datetime(2024, 11, 5, 13, 30, tzinfo=pytz.timezone('America/New_York'))
# Already in Eastern Time, no conversion needed!
```

---

## Benefits

### ✅ Consistency
- News timestamps match trading agent timezone
- No timezone conversion needed when agent reads news
- All timestamps in same timezone across system

### ✅ Market Hours Alignment
- News timestamps align with US market hours
- Easy to determine if news published during trading hours
- Proper market hours validation (9:30 AM - 4:00 PM ET)

### ✅ Correctness
- Automatic DST handling (EST ↔ EDT)
- Proper timezone-aware datetime objects
- No ambiguous naive datetimes

### ✅ Data Integrity
- Historical news timestamps remain accurate
- Time-aware queries work correctly
- No data leakage from timezone confusion

---

## Database Impact

### Existing Data

If you have existing news in the database with Moscow timezone:

**Option 1: Keep existing data as-is**
- New data will be in Eastern Time
- Trading agent handles both (converts to ET)
- Mixed timezones in database

**Option 2: Convert existing data (recommended)**
```sql
-- Convert existing Moscow time to Eastern Time
UPDATE news
SET published = published AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'America/New_York'
WHERE published < NOW();

-- Verify conversion
SELECT id, title, published 
FROM news 
ORDER BY published DESC 
LIMIT 5;
```

### Database Timezone Setting

Recommended PostgreSQL timezone settings:

```sql
-- Option 1: Keep database in UTC (recommended)
ALTER DATABASE trade_agent SET timezone = 'UTC';
-- Fetcher stores in ET, database converts to UTC for storage

-- Option 2: Set database to Eastern Time
ALTER DATABASE trade_agent SET timezone = 'America/New_York';
-- Fetcher stores in ET, database stores in ET

-- Check current setting
SHOW timezone;
```

---

## Testing

### Test the Updated Fetcher

```bash
# Run once to test
cd bloomberg_news_fetcher
python news_fetcher.py
```

**Expected output:**
```
Fetching Bloomberg Technology News via RSS...

⏰ Using timezone: US Eastern Time (America/New_York)

[1/10] Apple announces new iPhone...
Published: 2024-11-05 14:30:00 EST (ET)
Link: https://...
Fetching full article text...
✓ Full text fetched (1234 chars)
✓ Saved to database
```

### Verify Database Storage

```sql
-- Check recent news timestamps
SELECT 
    title,
    published,
    EXTRACT(TIMEZONE FROM published) as tz_offset,
    published AT TIME ZONE 'America/New_York' as et_time
FROM news
ORDER BY published DESC
LIMIT 5;
```

---

## Migration Guide

### For Running Fetcher

1. **Stop the current fetcher**:
```bash
# If running in Docker
docker-compose -f docker-compose-news.yml down

# If running as script
# Press Ctrl+C to stop
```

2. **No configuration changes needed**:
   - The code now uses Eastern Time automatically
   - No environment variables to update

3. **Restart the fetcher**:
```bash
# Docker
docker-compose -f docker-compose-news.yml up -d

# Or run directly
python auto_news_fetcher.py --now
```

4. **Verify it's working**:
```bash
# Check logs
docker-compose -f docker-compose-news.yml logs -f

# Or check database
psql -h YOUR_DB_HOST -U admin -d trade_agent -c "SELECT title, published FROM news ORDER BY published DESC LIMIT 3;"
```

### For Existing Data (Optional)

If you want to convert existing Moscow time data:

```sql
-- Backup first!
CREATE TABLE news_backup AS SELECT * FROM news;

-- Convert timestamps
UPDATE news
SET published = published AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'America/New_York',
    fetch_time = fetch_time AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'America/New_York';

-- Verify
SELECT 
    COUNT(*) as total_news,
    MIN(published) as earliest,
    MAX(published) as latest,
    EXTRACT(TIMEZONE FROM published) as tz_offset
FROM news;
```

---

## Compatibility

### Trading Agent ✅
- **Fully compatible** - both use Eastern Time
- No changes needed to trading agent code
- Time-aware queries work correctly

### Database ✅
- **Works with any database timezone** (UTC, ET, etc.)
- PostgreSQL handles timezone conversion automatically
- TIMESTAMPTZ columns store timezone info

### Docker Container ✅
- **Container timezone doesn't matter**
- Fetcher explicitly uses Eastern Time via pytz
- Consistent regardless of container TZ setting

---

## Summary

✅ **News fetcher now uses US Eastern Time**  
✅ **All timestamps stored in ET**  
✅ **Consistent with trading agent**  
✅ **Aligns with US market hours**  
✅ **Automatic DST handling**  
✅ **No configuration changes needed**  
✅ **Backward compatible**  

The news fetcher is now fully aligned with the trading agent's timezone handling!



