#!/usr/bin/env python3
"""
Test ticker validation - ensure non-US market tickers are filtered
"""
import os
from simple_agent import SimpleAgent


def test_ticker_validation():
    """Test that _is_us_market_ticker correctly identifies US vs non-US tickers"""
    
    print("\n" + "="*70)
    print("TESTING TICKER VALIDATION")
    print("="*70 + "\n")
    
    agent = SimpleAgent()
    
    # Test US market tickers (should return True)
    us_tickers = [
        "AAPL",
        "MSFT",
        "GOOGL",
        "TSLA",
        "NVDA",
        "AMD",
        "META",
        "AMZN",
        "CME",
        "JBLU",
    ]
    
    # Test non-US market tickers (should return False)
    non_us_tickers = [
        ("AIR.PA", "Paris - Airbus"),
        ("LUN.TO", "Toronto - Lundin Mining"),
        ("VOD.L", "London - Vodafone"),
        ("SAP.DE", "Germany - SAP"),
        ("0700.HK", "Hong Kong - Tencent"),
        ("7203.T", "Tokyo - Toyota"),
        ("BHP.AX", "Australia - BHP"),
        ("NESN.SW", "Switzerland - Nestle"),
        ("UCG.MI", "Italy - UniCredit"),
        ("ASML.AS", "Amsterdam - ASML"),
        ("NOK.OL", "Oslo - Nokia"),
        ("NOVO.CO", "Copenhagen - Novo Nordisk"),
        ("VALE.SA", "Brazil - Vale"),
        ("BABA.NZ", "New Zealand"),
        ("DBS.SI", "Singapore - DBS"),
        ("005930.KS", "Korea - Samsung"),
        ("2330.TW", "Taiwan - TSMC"),
    ]
    
    print("Testing US market tickers (should be VALID):")
    print("-" * 70)
    all_passed = True
    
    for ticker in us_tickers:
        is_us = agent._is_us_market_ticker(ticker)
        status = "✓" if is_us else "✗"
        if not is_us:
            all_passed = False
        print(f"  {status} {ticker:10s} - {'VALID' if is_us else 'INVALID (ERROR!)'}")
    
    print("\nTesting non-US market tickers (should be INVALID):")
    print("-" * 70)
    
    for ticker, description in non_us_tickers:
        is_us = agent._is_us_market_ticker(ticker)
        status = "✓" if not is_us else "✗"
        if is_us:
            all_passed = False
        print(f"  {status} {ticker:15s} - {'FILTERED' if not is_us else 'NOT FILTERED (ERROR!)'} ({description})")
    
    print("\n" + "="*70)
    if all_passed:
        print("✓ ALL TESTS PASSED")
    else:
        print("✗ SOME TESTS FAILED")
    print("="*70 + "\n")
    
    agent.close()
    
    return all_passed


if __name__ == "__main__":
    success = test_ticker_validation()
    exit(0 if success else 1)

