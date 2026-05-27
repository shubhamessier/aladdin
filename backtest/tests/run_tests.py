import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parent.parent.parent
if str(root) not in sys.path:
    sys.path.append(str(root))

from backtest.tests.test_data_integrity import (
    test_hyperliquid_ohlcv_format,
    test_stable_fallback_format,
    test_long_duration_fetch
)

if __name__ == "__main__":
    print("Running Data Integrity Tests...")
    
    try:
        print("1. Testing Hyperliquid OHLCV Format (1h)...")
        test_hyperliquid_ohlcv_format()
        print("   SUCCESS")
        
        print("2. Testing Stable Fallback Format...")
        test_stable_fallback_format()
        print("   SUCCESS")
        
        print("3. Testing Long Duration Fetch (1d)...")
        test_long_duration_fetch()
        print("   SUCCESS")
        
        print("\nAll tests passed successfully!")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
