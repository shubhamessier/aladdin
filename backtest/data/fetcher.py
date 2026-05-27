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
        
    def _get_cache_path(self, symbol: str, source: str, start_time: int, end_time: int, interval: str = "1d") -> Path:
        safe_sym = symbol.replace("/", "_").replace("-", "_")
        return self.cache_dir / f"{source}_{safe_sym}_{interval}_{start_time}_{end_time}.parquet"
        
    def fetch_ohlcv(self, symbol: str, start_time: int, end_time: int, source: str = "hyperliquid", interval: str = "1d") -> pd.DataFrame:
        """
        Fetch OHLCV market data for a given symbol and time range.
        Checks cache first, then fetches from the requested source with fallbacks.
        """
        sources_to_try = [source, "binance", "coingecko", "coincap"]
        # Remove duplicates while preserving order
        sources_to_try = list(dict.fromkeys(sources_to_try))

        # Two-pass: check ALL caches before making any HTTP request.
        # This prevents stalling on a slow/rate-limited primary source when a
        # cached fallback source (e.g. binance) already has the data.
        for src in sources_to_try:
            cache_path = self._get_cache_path(symbol, src, start_time, end_time, interval)
            if cache_path.exists():
                logger.info(f"Loading {symbol} data from cache ({src}): {cache_path}")
                try:
                    return pd.read_parquet(cache_path)
                except Exception as e:
                    logger.warning(f"Failed to read cache {cache_path}, refetching... Error: {e}")

        for src in sources_to_try:
            logger.info(f"Fetching {symbol} from {src}...")
            try:
                if src == "hyperliquid":
                    df = self._fetch_hyperliquid(symbol, start_time, end_time, interval)
                elif src == "binance":
                    df = self._fetch_binance(symbol, start_time, end_time, interval)
                elif src == "coingecko":
                    df = self._fetch_coingecko(symbol, start_time, end_time)
                elif src == "coincap":
                    df = self._fetch_coincap(symbol, start_time, end_time)
                else:
                    continue

                if df is not None and not df.empty:
                    df = self._validate_and_clean(df)
                    if not df.empty:
                        try:
                            df.to_parquet(cache_path)
                        except Exception as e:
                            logger.warning(f"Failed to save cache to {cache_path}: {e}")
                        return df
            except Exception as e:
                logger.error(f"Error fetching data for {symbol} from {src}: {e}")
                time.sleep(1.0) # Backoff
                
        # If it's a stable and we failed all sources, return constant 1.0
        if symbol in ["USDC", "USDT", "DAI"]:
            logger.info(f"Using constant 1.0 price for stable {symbol}")
            freq = interval.upper() if interval in ("1d", "1D") else interval
            idx = pd.date_range(pd.to_datetime(start_time, unit='s'), pd.to_datetime(end_time, unit='s'), freq=freq)
            return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)

        return pd.DataFrame()

    def _validate_and_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate data integrity: no negative prices, interpolate missing up to 3 days.
        """
        if df.empty:
            return df

        # Deduplicate index — HL occasionally returns two candles with identical timestamps
        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep='first')]

        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                # Replace negative or zero prices with NaN so they get interpolated
                if col != 'volume':
                    df.loc[df[col] <= 0, col] = np.nan
                else:
                    df.loc[df[col] < 0, col] = np.nan
                
        # Interpolate missing values (limit depends on frequency, using 3 as default for 1d)
        df = df.interpolate(method='linear', limit_direction='forward', limit=3)
        df = df.dropna(subset=['close']) # Ensure close at minimum exists
        return df

    def _fetch_hyperliquid(self, coin: str, start_time: int, end_time: int, interval: str = "1d") -> pd.DataFrame:
        url = "https://api.hyperliquid.xyz/info"
        all_candles = []
        
        # HL interval mapping
        hl_interval = interval
        if interval == "1d": hl_interval = "1d"
        elif interval == "1h": hl_interval = "1h"
        
        chunk_seconds = {
            "1h": 5000 * 3600,
            "1d": 5000 * 86400,
        }.get(hl_interval, 5000 * 3600)
        
        current_start = start_time * 1000
        end_ms = end_time * 1000
        
        MAX_ITERATIONS = 50  # Safety cap: 50 chunks × chunk_seconds is far more than any range
        iteration = 0
        while current_start < end_ms and iteration < MAX_ITERATIONS:
            chunk_end = min(current_start + chunk_seconds * 1000, end_ms)
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": hl_interval,
                    "startTime": current_start,
                    "endTime": chunk_end
                }
            }
            resp = self.session.post(url, json=payload, timeout=12)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break
            all_candles.extend(data)
            new_start = data[-1]["t"] + 1
            if new_start <= current_start:
                break  # Guard against non-advancing timestamp (infinite loop)
            current_start = new_start
            iteration += 1
            time.sleep(0.1)
            
        if not all_candles:
            return pd.DataFrame()
            
        df = pd.DataFrame(all_candles)
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df = df.set_index("timestamp")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        return df

    def _fetch_binance(self, symbol: str, start_time: int, end_time: int, interval: str = "1d") -> pd.DataFrame:
        url = "https://api.binance.com/api/v3/klines"
        all_data: List[List[Any]] = []
        current_start = start_time * 1000
        end_ms = end_time * 1000
        limit = 1000
        
        binance_symbol = symbol.replace("/", "").replace("-", "").upper()
        if not binance_symbol.endswith("USDT") and not binance_symbol.endswith("USD") and symbol not in ["USDC", "USDT", "DAI"]:
            binance_symbol += "USDT"
        if symbol == "USDC": binance_symbol = "USDCUSDT"
            
        while current_start < end_ms:
            params: dict[str, str | int] = {
                "symbol": binance_symbol,
                "interval": interval,
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
            time.sleep(0.1)
            
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

    def fetch_l2_depth_snapshot(self, coin: str) -> dict:
        import time as _time
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "l2Book", "coin": coin}
        for attempt in range(3):
            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code == 429 and attempt < 2:
                _time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        book = resp.json()
        
        bids = [(float(l["px"]), float(l["sz"])) for l in book["levels"][0]]
        asks = [(float(l["px"]), float(l["sz"])) for l in book["levels"][1]]
        
        mid = (bids[0][0] + asks[0][0]) / 2.0
        
        def depth_within_bps(levels, bps, side="ask"):
            threshold = mid * (1 + bps/10000) if side == "ask" else mid * (1 - bps/10000)
            total_usd = 0.0
            for px, sz in levels:
                if (side == "ask" and px <= threshold) or (side == "bid" and px >= threshold):
                    total_usd += px * sz
            return total_usd
        
        return {
            "coin": coin,
            "mid": mid,
            "depth_5bps_usd": depth_within_bps(asks, 5),
            "depth_10bps_usd": depth_within_bps(asks, 10),
            "depth_25bps_usd": depth_within_bps(asks, 25),
            "depth_50bps_usd": depth_within_bps(asks, 50),
            "spread_bps": (asks[0][0] - bids[0][0]) / mid * 10000,
        }

    def _fetch_coingecko(self, symbol: str, start_time: int, end_time: int) -> pd.DataFrame:
        cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "USDC": "usd-coin", "USDT": "tether"}
        sym_upper = symbol.replace("/USD", "").replace("USD", "").upper()
        cg_id = cg_map.get(sym_upper, sym_upper.lower())
        
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart/range"
        params: dict[str, str | int] = {"vs_currency": "usd", "from": start_time, "to": end_time}
        
        response = self.session.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        time.sleep(1.5)
        
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
            
        return df[['close', 'volume']]

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
        return df[['close']]
