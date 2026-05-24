import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

class DataFetcher:
    """
    DataFetcher handles multi-source historical market data acquisition with
    robust pagination, rate limiting, and local disk caching.
    """
    
    def __init__(self, cache_dir: str | Path = "backtest/cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        
    def _get_cache_path(self, symbol: str, source: str, start_time: int, end_time: int) -> Path:
        safe_sym = symbol.replace("/", "_").replace("-", "_")
        return self.cache_dir / f"{source}_{safe_sym}_{start_time}_{end_time}.parquet"
        
    def fetch_ohlcv(self, symbol: str, start_time: int, end_time: int, source: str = "binance") -> pd.DataFrame:
        """
        Fetch OHLCV market data for a given symbol and time range.
        Checks cache first, then fetches from the requested source.
        """
        cache_path = self._get_cache_path(symbol, source, start_time, end_time)
        if cache_path.exists():
            logger.info(f"Loading {symbol} data from cache: {cache_path}")
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                logger.warning(f"Failed to read cache {cache_path}, refetching... Error: {e}")
            
        logger.info(f"Fetching {symbol} from {source}...")
        
        try:
            if source == "binance":
                df = self._fetch_binance(symbol, start_time, end_time)
            elif source == "coingecko":
                df = self._fetch_coingecko(symbol, start_time, end_time)
            elif source == "coincap":
                df = self._fetch_coincap(symbol, start_time, end_time)
            else:
                raise ValueError(f"Unknown source: {source}")
        except Exception as e:
            logger.error(f"Error fetching data for {symbol} from {source}: {e}")
            df = pd.DataFrame()
            
        df = self._validate_and_clean(df)
        
        if not df.empty:
            try:
                df.to_parquet(cache_path)
            except Exception as e:
                logger.warning(f"Failed to save cache to {cache_path}: {e}")
                
        return df

    def _validate_and_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate data integrity: no negative prices, interpolate missing up to 3 days.
        """
        if df.empty:
            return df
            
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                # Replace negative or zero prices with NaN so they get interpolated
                if col != 'volume':
                    df.loc[df[col] <= 0, col] = np.nan
                else:
                    df.loc[df[col] < 0, col] = np.nan
                
        # Interpolate missing values up to 3 days (assuming daily data, limit=3)
        df = df.interpolate(method='linear', limit_direction='forward', limit=3)
        df = df.dropna(subset=['close']) # Ensure close at minimum exists
        return df

    def _fetch_binance(self, symbol: str, start_time: int, end_time: int) -> pd.DataFrame:
        url = "https://api.binance.com/api/v3/klines"
        all_data: List[List[Any]] = []
        current_start = start_time * 1000
        end_ms = end_time * 1000
        limit = 1000
        
        binance_symbol = symbol.replace("/", "").replace("-", "").upper()
        if not binance_symbol.endswith("USDT") and not binance_symbol.endswith("USD"):
            binance_symbol += "USDT"
            
        while current_start < end_ms:
            params: dict[str, str | int] = {
                "symbol": binance_symbol,
                "interval": "1d",
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data:
                break
                
            all_data.extend(data)
            current_start = data[-1][0] + 1
            time.sleep(0.1)  # Respect Binance rate limits
            
        if not all_data:
            return pd.DataFrame()
            
        df = pd.DataFrame(all_data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]

    def _fetch_coingecko(self, symbol: str, start_time: int, end_time: int) -> pd.DataFrame:
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "USDC": "usd-coin", "USDT": "tether"}
        sym_upper = symbol.replace("/USD", "").replace("USD", "").upper()
        cg_id = cg_map.get(sym_upper, sym_upper.lower())
        
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart/range"
        params: dict[str, str | int] = {"vs_currency": "usd", "from": start_time, "to": end_time}
        
        response = self.session.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        time.sleep(1.0)  # CoinGecko has strict rate limits
        
        if 'prices' not in data or not data['prices']:
            return pd.DataFrame()
            
        df = pd.DataFrame(data['prices'], columns=['timestamp', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        if 'total_volumes' in data and data['total_volumes']:
            volumes = pd.DataFrame(data['total_volumes'], columns=['timestamp', 'volume'])
            volumes['timestamp'] = pd.to_datetime(volumes['timestamp'], unit='ms')
            volumes.set_index('timestamp', inplace=True)
            df = df.join(volumes)
        else:
            df['volume'] = 0.0
            
        df['open'] = df['close']
        df['high'] = df['close']
        df['low'] = df['close']
        return df[['open', 'high', 'low', 'close', 'volume']]

    def _fetch_coincap(self, symbol: str, start_time: int, end_time: int) -> pd.DataFrame:
        cc_map = {"BTC": "bitcoin", "ETH": "ethereum", "USDC": "usd-coin", "USDT": "tether"}
        sym_upper = symbol.replace("/USD", "").replace("USD", "").upper()
        cg_id = cc_map.get(sym_upper, sym_upper.lower())
        
        url = f"https://api.coincap.io/v2/assets/{cg_id}/history"
        params: dict[str, str | int] = {
            "interval": "d1",
            "start": start_time * 1000,
            "end": end_time * 1000
        }
        
        response = self.session.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        time.sleep(0.5)
        
        if 'data' not in data or not data['data']:
            return pd.DataFrame()
            
        df = pd.DataFrame(data['data'])
        df['timestamp'] = pd.to_datetime(df['time'], unit='ms')
        df['close'] = pd.to_numeric(df['priceUsd'])
        df.set_index('timestamp', inplace=True)
        df['open'] = df['close']
        df['high'] = df['close']
        df['low'] = df['close']
        df['volume'] = 0.0
        return df[['open', 'high', 'low', 'close', 'volume']]
