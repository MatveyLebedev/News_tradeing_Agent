"""Long-term trading agent — news-background analysis over a period + fundamentals.

Companion to news_agent (the intraday event-driven agent). Analyzes a company's
whole news flow over 60–90 days, detects narrative-background shifts, cross-checks
against point-in-time fundamentals, and emits a multi-week signal.
"""
