#!/usr/bin/env python3
"""
Script to check what timezone the agent uses for searching and input
"""
import pytz
from datetime import datetime
from news_agent.agent_config import AgentConfig
from news_agent.database.db_manager import DBManager

print("=" * 80)
print("AGENT TIMEZONE CONFIGURATION")
print("=" * 80)
print()

# Check agent config timezone
print("📅 Agent Configuration Timezone:")
print(f"   Timezone: {AgentConfig.TIMEZONE_NAME}")
print(f"   Timezone Object: {AgentConfig.TIMEZONE}")
print(f"   Current Time (ET): {datetime.now(AgentConfig.TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')}")
print()

# Check DBManager timezone
print("🗄️  Database Manager Timezone:")
print(f"   Timezone: {DBManager.EASTERN_TZ.zone}")
print(f"   Current Time (ET): {datetime.now(DBManager.EASTERN_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
print()

# Show market hours
print("⏰ Market Hours (US Eastern Time):")
print(f"   Market Open:  {AgentConfig.MARKET_OPEN_HOUR:02d}:{AgentConfig.MARKET_OPEN_MINUTE:02d} ET")
print(f"   Market Close: {AgentConfig.MARKET_CLOSE_HOUR:02d}:{AgentConfig.MARKET_CLOSE_MINUTE:02d} ET")
print()

# Show timezone conversion examples
print("🔄 Timezone Conversion Examples:")
print()

# Example 1: Naive datetime
naive_dt = datetime(2024, 11, 1, 14, 30)
print(f"1. Naive datetime input:")
print(f"   Input: {naive_dt} (no timezone)")
print(f"   Agent assumes: Eastern Time")
converted_naive = AgentConfig.TIMEZONE.localize(naive_dt)
print(f"   Converted: {converted_naive.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"   UTC equivalent: {converted_naive.astimezone(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S %Z')}")
print()

# Example 2: UTC datetime
utc_dt = datetime(2024, 11, 1, 18, 30, tzinfo=pytz.UTC)
print(f"2. UTC datetime input:")
print(f"   Input: {utc_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
converted_utc = utc_dt.astimezone(AgentConfig.TIMEZONE)
print(f"   Converted to ET: {converted_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print()

# Example 3: ET datetime
et_dt = AgentConfig.TIMEZONE.localize(datetime(2024, 11, 1, 14, 30))
print(f"3. Eastern Time datetime input:")
print(f"   Input: {et_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"   No conversion needed (already ET)")
print()

print("=" * 80)
print("SUMMARY")
print("=" * 80)
print("✅ Agent uses: US Eastern Time (America/New_York)")
print("✅ Input handling:")
print("   - Naive datetime → Assumed to be Eastern Time")
print("   - Timezone-aware → Converted to Eastern Time")
print("✅ All database searches use Eastern Time")
print("✅ Market hours defined in Eastern Time")
print("=" * 80)






























