import pandas as pd
from datetime import datetime, timedelta
from backtest.data.fetcher import DataFetcher

def test_hyperliquid_ohlcv_format():
    """Test if Hyperliquid 1h OHLCV data has correct format and columns."""
    fetcher = DataFetcher(cache_dir="backtest/cache/test")
    end_time = int(datetime.now().timestamp())
    start_time = end_time - (24 * 3600 * 5) # 5 days ago
    
    # Fetch BTC (volatile)
    df = fetcher.fetch_ohlcv("BTC", start_time, end_time, interval="1h", source="hyperliquid")
    
    assert not df.empty, "BTC data should not be empty"
    assert all(col in df.columns for col in ['open', 'high', 'low', 'close', 'volume']), "Missing OHLCV columns"
    assert isinstance(df.index, pd.DatetimeIndex), "Index must be DatetimeIndex"
    
    # Check for variance in price (ensure it's real data, not flat 1.0)
    assert df['close'].nunique() > 1, "BTC price should not be constant"
    
    # Check interval consistency (should be approx 1h)
    diffs = df.index.to_series().diff().dropna()
    assert (diffs == pd.Timedelta(hours=1)).all(), "Interval is not 1h"

def test_stable_fallback_format():
    """Test if stable fallback (USDC) returns 1.0 if API fails, but still maintains format."""
    fetcher = DataFetcher(cache_dir="backtest/cache/test")
    end_time = int(datetime.now().timestamp())
    start_time = end_time - (24 * 3600 * 2) # 2 days ago
    
    # Fetch USDC
    df = fetcher.fetch_ohlcv("USDC", start_time, end_time, interval="1h", source="hyperliquid")
    
    assert not df.empty, "USDC data should not be empty"
    assert (df['close'] == 1.0).all() or df['close'].nunique() > 1, "USDC should be 1.0 or real data"
    assert isinstance(df.index, pd.DatetimeIndex), "Index must be DatetimeIndex"

def test_long_duration_fetch():
    """Test if fetching a long duration (1 year) works and paginates correctly."""
    fetcher = DataFetcher(cache_dir="backtest/cache/test")
    end_date = datetime(2024, 1, 1)
    start_date = datetime(2023, 1, 1)
    
    df = fetcher.fetch_ohlcv("ETH", int(start_date.timestamp()), int(end_date.timestamp()), interval="1d", source="hyperliquid")
    
    assert not df.empty, "ETH data should not be empty for long duration"
    assert len(df) >= 360, f"Expected ~365 days, got {len(df)}"
    assert df.index.min() <= pd.Timestamp(start_date) + pd.Timedelta(days=1)
    assert df.index.max() >= pd.Timestamp(end_date) - pd.Timedelta(days=1)
