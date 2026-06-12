#!/usr/bin/env python3
"""
Test that Simple Agent output format matches Complex Agent format
"""
import os
from datetime import datetime
import pytz
from simple_agent import SimpleAgent


def test_output_format():
    """Test that output format matches expected format"""
    
    print("\n" + "="*70)
    print("TESTING OUTPUT FORMAT")
    print("="*70 + "\n")
    
    # Initialize agent
    agent = SimpleAgent()
    
    # Simple test news
    news_text = "Apple announces breakthrough M4 chip with revolutionary performance improvements."
    timestamp = datetime(2025, 11, 28, 14, 30)
    timestamp = pytz.timezone('US/Eastern').localize(timestamp)
    
    try:
        # Run analysis
        signals = agent.run(news_text=news_text, timestamp=timestamp)
        
        # Validate format
        print("\n" + "="*70)
        print("FORMAT VALIDATION")
        print("="*70)
        
        assert isinstance(signals, list), "❌ Output must be a list"
        print("✓ Output is a list")
        
        for i, signal in enumerate(signals, 1):
            print(f"\nSignal {i}:")
            
            # Check it's a dict
            assert isinstance(signal, dict), f"❌ Signal {i} must be a dict"
            print(f"  ✓ Is a dict")
            
            # Check required keys
            required_keys = ["ticker", "signal", "confidence", "reasoning"]
            for key in required_keys:
                assert key in signal, f"❌ Signal {i} missing key: {key}"
                print(f"  ✓ Has key: {key}")
            
            # Check types
            assert isinstance(signal["ticker"], str), f"❌ ticker must be string"
            print(f"  ✓ ticker is string: {signal['ticker']}")
            
            assert signal["signal"] in ["buy", "sell"], f"❌ signal must be 'buy' or 'sell'"
            print(f"  ✓ signal is valid: {signal['signal']}")
            
            assert isinstance(signal["confidence"], (int, float)), f"❌ confidence must be number"
            assert 0.0 <= signal["confidence"] <= 1.0, f"❌ confidence must be 0-1"
            print(f"  ✓ confidence is valid: {signal['confidence']}")
            
            assert isinstance(signal["reasoning"], str), f"❌ reasoning must be string"
            assert len(signal["reasoning"]) > 0, f"❌ reasoning must not be empty"
            print(f"  ✓ reasoning is valid: {signal['reasoning'][:50]}...")
        
        print("\n" + "="*70)
        print("✓ ALL FORMAT CHECKS PASSED")
        print("="*70 + "\n")
        
        # Show example output
        if signals:
            print("Example output format:")
            print(signals[0])
        
    finally:
        agent.close()


if __name__ == "__main__":
    test_output_format()

