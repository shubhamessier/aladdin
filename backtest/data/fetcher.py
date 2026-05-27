import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)


_INTERVAL_TO_PANDAS = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1D", "3d": "3D", "1w": "1W",
}

_INTERVAL_TO_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _pandas_freq_alias(interval: str) -> str:
    """Map exchange interval ('1h', '1d', ...) to a pandas freq alias."""
    if interval not in _INTERVAL_TO_PANDAS:
        raise ValueError(f"Unsupported interval '{interval}'")
    return _INTERVAL_TO_PANDAS[interval]


def _interval_ms(interval: str) -> int:
    if interval not in _INTERVAL_TO_MS:
        raise ValueError(f"Unsupported interval '{interval}'")
    return _INTERVAL_TO_MS[interval]

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
        The returned DataFrame is reindexed to the requested cadence and forward-filled
        across short gaps. Empty df is returned if the source has no data at all.
        """
        # Hyperliquid candle history is unreliable for windows >1 week; prefer Binance
        # for OHLCV. Keep HL as a fallback only.
        if source == "hyperliquid":
            source = "binance"
        # CoinGecko now requires an API key and CoinCap shut down; only binance/HL are live.
        sources_to_try = [source, "binance", "hyperliquid"]
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
                        df = self._reindex_to_grid(df, start_time, end_time, interval)
                    if not df.empty:
                        # Stablecoins occasionally have multi-day gaps on Binance USDC/USDT pairs.
                        # The peg means we can safely pad gaps with the last observation.
                        if symbol in ("USDC", "USDT", "DAI"):
                            df = self._pad_stable(df, start_time, end_time, interval)
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
            freq = _pandas_freq_alias(interval)
            idx = pd.date_range(pd.to_datetime(start_time, unit='s'), pd.to_datetime(end_time, unit='s'), freq=freq)
            return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)

        return pd.DataFrame()

    def _validate_and_clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate data integrity: dedup, coerce numeric, replace non-positive prices
        with NaN. Returns df with index sorted; gap-filling happens in _reindex_to_grid.
        """
        if df.empty:
            return df

        # Make sure we have a DatetimeIndex sorted ascending
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError(f"_validate_and_clean expects DatetimeIndex, got {type(df.index).__name__}")
        df = df.sort_index()
        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep='first')]

        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                if col != 'volume':
                    df.loc[df[col] <= 0, col] = np.nan
                else:
                    df.loc[df[col] < 0, col] = np.nan

        df = df.dropna(subset=['close'])
        return df

    def _pad_stable(self, df: pd.DataFrame, start_time: int, end_time: int, interval: str) -> pd.DataFrame:
        """Stablecoin-specific pad: fill any remaining NaN with the last known value
        (or 1.0 if the head is also NaN). Used only for USDC/USDT/DAI."""
        try:
            freq = _pandas_freq_alias(interval)
        except ValueError:
            return df
        start_ts = pd.to_datetime(start_time, unit='s').floor(freq)
        end_ts = pd.to_datetime(end_time, unit='s').floor(freq)
        target_idx = pd.date_range(start_ts, end_ts, freq=freq, inclusive='both')
        df = df.reindex(target_idx).ffill().bfill()
        # If still NaN (entire series missing), peg to 1.0
        for col in ('open', 'high', 'low', 'close'):
            if col in df.columns:
                df[col] = df[col].fillna(1.0)
        if 'volume' in df.columns:
            df['volume'] = df['volume'].fillna(0.0)
        return df

    def _reindex_to_grid(self, df: pd.DataFrame, start_time: int, end_time: int, interval: str) -> pd.DataFrame:
        """
        Reindex df to the requested cadence grid from start_time to end_time inclusive.
        The grid is anchored to UTC and floored to the interval boundary so it aligns
        with what exchanges actually emit (hourly = XX:00:00 UTC).
        Forward-fills short gaps (up to 24 grid steps).
        """
        if df.empty:
            return df
        try:
            freq = _pandas_freq_alias(interval)
        except ValueError:
            return df

        # Both source and target on the same floor-aligned grid.
        df = df.copy()
        df.index = df.index.floor(freq)
        df = df[~df.index.duplicated(keep='first')].sort_index()

        # Anchor target_idx to the same floor.
        start_ts = pd.to_datetime(start_time, unit='s').floor(freq)
        end_ts = pd.to_datetime(end_time, unit='s').floor(freq)
        target_idx = pd.date_range(start_ts, end_ts, freq=freq, inclusive='both')
        if len(target_idx) == 0:
            return df

        df = df.reindex(target_idx)
        df = df.ffill(limit=24).bfill(limit=24)
        if 'close' in df.columns and df['close'].isna().any():
            n_missing = int(df['close'].isna().sum())
            logger.warning(
                f"_reindex_to_grid: {n_missing}/{len(df)} bars still NaN after fill; "
                "data source has multi-day gaps. Dropping NaN-only rows."
            )
            df = df.dropna(subset=['close'])
        return df

    def _fetch_hyperliquid(self, coin: str, start_time: int, end_time: int, interval: str = "1d") -> pd.DataFrame:
        url = "https://api.hyperliquid.xyz/info"
        all_candles = []

        if interval not in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
            raise ValueError(f"Hyperliquid does not support interval '{interval}'")

        interval_ms = _interval_ms(interval)
        # HL caps at ~5000 candles per call
        chunk_ms = 5000 * interval_ms

        current_start = start_time * 1000
        end_ms = end_time * 1000

        MAX_ITERATIONS = 200
        iteration = 0
        while current_start < end_ms and iteration < MAX_ITERATIONS:
            chunk_end = min(current_start + chunk_ms, end_ms)
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": current_start,
                    "endTime": chunk_end,
                },
            }
            resp = self.session.post(url, json=payload, timeout=12)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break
            all_candles.extend(data)
            # Advance by ONE full interval past the last candle's open time so we
            # don't request the same candle again with a sub-second offset.
            new_start = int(data[-1]["t"]) + interval_ms
            if new_start <= current_start:
                break  # Guard against non-advancing pagination
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
        # Dedup duplicates by index (HL occasionally returns overlapping rows)
        df = df[~df.index.duplicated(keep="first")].sort_index()
        return df

    def _fetch_binance(self, symbol: str, start_time: int, end_time: int, interval: str = "1d") -> pd.DataFrame:
        # USDT has no spot pair on Binance; quote against itself doesn't exist.
        # Pin to 1.0 at the requested cadence instead of attempting a fetch.
        if symbol == "USDT":
            freq = _pandas_freq_alias(interval)
            idx = pd.date_range(pd.to_datetime(start_time, unit='s'),
                                pd.to_datetime(end_time, unit='s'), freq=freq)
            return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)

        url = "https://api.binance.com/api/v3/klines"
        all_data: List[List[Any]] = []
        interval_ms = _interval_ms(interval)
        current_start = start_time * 1000
        end_ms = end_time * 1000
        limit = 1000

        binance_symbol = symbol.replace("/", "").replace("-", "").upper()
        if not binance_symbol.endswith("USDT") and not binance_symbol.endswith("USD") and symbol not in ["USDC", "USDT", "DAI"]:
            binance_symbol += "USDT"
        if symbol == "USDC":
            binance_symbol = "USDCUSDT"

        while current_start < end_ms:
            params: dict[str, str | int] = {
                "symbol": binance_symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": limit,
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data:
                break

            all_data.extend(data)
            # Advance to next bar boundary, not +1ms
            new_start = int(data[-1][0]) + interval_ms
            if new_start <= current_start:
                break
            current_start = new_start
            time.sleep(0.05)

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore',
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.set_index('timestamp').sort_index()
        df = df[~df.index.duplicated(keep='first')]
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
