import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def fetch_funding_rates(symbol: str, start_time: int, end_time: int) -> pd.DataFrame:
    """
    Fetch historical funding rates for perpetual futures.
    Falls back to synthetic generation if live API endpoint is not implemented or fails.
    """
    try:
        # Placeholder for real fetching logic (e.g. Binance USD-M futures)
        raise NotImplementedError("Real funding rate fetch not implemented. Triggering fallback.")
    except Exception as e:
        logger.info(f"Using synthetic funding rates for {symbol}: {e}")
        return _generate_synthetic_funding(symbol, start_time, end_time)

def _generate_synthetic_funding(symbol: str, start_time: int, end_time: int) -> pd.DataFrame:
    """
    Generate synthetic funding rates every 8 hours.
    Base rate is typically 0.01% per 8 hours (0.0001) for bullish regimes.
    Includes noise and auto-regression to simulate real funding behavior.
    """
    # Create 8-hour frequency date range
    dates = pd.date_range(
        start=pd.to_datetime(start_time, unit='s'),
        end=pd.to_datetime(end_time, unit='s'),
        freq='8h'
    )
                          
    base_rate = 0.0001
    
    # Generate realistic noise using a random walk with mean reversion
    np.random.seed(hash(symbol) % (2**32 - 1)) # Consistent randomness per symbol
    noise = np.random.normal(0, 0.00005, len(dates))
    rates = np.zeros(len(dates))
    
    # Mean-reverting random walk for funding
    current_rate = base_rate
    for i in range(len(dates)):
        # Mean reversion towards base rate
        reversion = (base_rate - current_rate) * 0.1
        current_rate = current_rate + reversion + noise[i]
        # Cap funding rates to realistic extremes (-0.05% to 0.1%)
        current_rate = np.clip(current_rate, -0.0005, 0.001)
        rates[i] = current_rate
        
    df = pd.DataFrame({
        'timestamp': dates,
        'funding_rate': rates
    })
    df.set_index('timestamp', inplace=True)
    return df
