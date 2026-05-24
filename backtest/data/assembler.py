import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Union

import pandas as pd

from backtest.data.fetcher import DataFetcher
from backtest.data.funding import fetch_funding_rates
from backtest.data.lending import fetch_lending_rates

logger = logging.getLogger(__name__)

@dataclass
class MarketData:
    """
    Master container for historical market data streams used in backtesting.
    """
    prices: Dict[str, pd.DataFrame]
    funding_rates: Dict[str, pd.DataFrame]
    lending_rates: Dict[str, pd.DataFrame]


def assemble_market_data(
    symbols: List[str], 
    start_time: int, 
    end_time: int, 
    cache_dir: Union[str, Path] = "backtest/cache",
    price_source: str = "binance"
) -> MarketData:
    """
    Master function to assemble all required market data for a given set of symbols.
    Aggregates prices, funding rates, and lending rates into a unified MarketData object.
    
    :param symbols: List of ticker symbols (e.g., ['BTC/USD', 'ETH/USD', 'USDC/USD'])
    :param start_time: Unix timestamp (seconds) for start
    :param end_time: Unix timestamp (seconds) for end
    :param cache_dir: Directory to save/load cached parquets
    :param price_source: API source for price data ('binance', 'coingecko', 'coincap')
    """
    fetcher = DataFetcher(cache_dir=cache_dir)
    
    prices: Dict[str, pd.DataFrame] = {}
    funding: Dict[str, pd.DataFrame] = {}
    lending: Dict[str, pd.DataFrame] = {}
    
    for symbol in symbols:
        logger.info(f"Assembling data for {symbol}...")
        
        # 1. Fetch OHLCV Price Data
        df_price = fetcher.fetch_ohlcv(symbol, start_time, end_time, source=price_source)
        if df_price.empty:
            logger.warning(f"Could not retrieve price data for {symbol}. Skipping.")
            continue
        prices[symbol] = df_price
        
        # 2. Fetch Funding Rates (Applicable for perp modeling)
        df_funding = fetch_funding_rates(symbol, start_time, end_time)
        funding[symbol] = df_funding
        
        # 3. Fetch Lending Rates (Applicable for yield modeling)
        df_lending = fetch_lending_rates(symbol, start_time, end_time)
        lending[symbol] = df_lending
        
    logger.info("Market data assembly complete.")
    
    return MarketData(
        prices=prices,
        funding_rates=funding,
        lending_rates=lending
    )
