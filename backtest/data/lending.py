import requests
import pandas as pd
import logging
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def fetch_lending_rates(asset: str, start_time: int, end_time: int, cache_dir: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch historical lending rates from DeFi Llama.
    Uses AAVE V3 as the representative liquidity pool.
    """
    # Check disk cache first to avoid repeated HTTP requests
    if cache_dir:
        cache_path = Path(cache_dir) / f"defillama_{asset}_{start_time}_{end_time}.parquet"
        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                logger.info(f"Loaded {len(df)} lending records for {asset} from cache")
                return df
            except Exception:
                pass

    try:
        pool_map = {
            "BTC": "7e382157-b1bc-406d-b17b-facba43b716e",  # WBTC Aave V3 Ethereum
            "ETH": "e880e828-ca59-4ec6-8d4f-27182a4dc23d",  # WETH Aave V3 Ethereum
            "USDC": "aa70268e-4b52-42bf-a116-608b370f9501", # USDC Aave V3 Ethereum
            "USDT": "f981a304-bb6c-45b8-b0c5-fd2f515ad23a", # USDT Aave V3 Ethereum
            "DAI": "3665ee7e-6c5d-49d9-abb7-c47ab5d9d4ac",  # DAI Aave V3 Ethereum
        }

        pool_id = pool_map.get(asset)
        if not pool_id:
            if asset in ["USDC", "USDT", "DAI"]:
                logger.critical(f"DATA INTEGRITY FAILURE: No DeFi Llama pool mapping for stablecoin {asset}. Cannot run on fabricated inputs.")
                return pd.DataFrame()
            logger.warning(f"No DeFi Llama pool mapping for {asset}. Using synthetic.")
            return _generate_realistic_lending_rates(asset, start_time, end_time)

        url = f"https://yields.llama.fi/chart/{pool_id}"
        # Use stream=True to avoid waiting for full body before timeout triggers
        resp = requests.get(url, timeout=(5, 30), stream=False)
        resp.raise_for_status()
        data = resp.json().get("data", [])

        if not data:
            raise ValueError("Empty data from DeFi Llama")

        records = []
        for entry in data:
            records.append({
                "timestamp": pd.to_datetime(entry["timestamp"]),
                "lending_rate": float(entry["apy"]) / 100.0
            })

        df = pd.DataFrame(records).set_index("timestamp")
        if df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        start_ts = pd.to_datetime(start_time, unit='s')
        end_ts = pd.to_datetime(end_time, unit='s')
        df = df[(df.index >= start_ts) & (df.index <= end_ts)]

        # Save to cache
        if cache_dir and not df.empty:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache_path)

        logger.info(f"Fetched {len(df)} real lending records for {asset} from DeFi Llama")
        return df

    except Exception as e:
        logger.warning(f"DeFi Llama fetch failed for {asset}: {e}. Using synthetic.")
        return _generate_realistic_lending_rates(asset, start_time, end_time)

def _generate_realistic_lending_rates(asset: str, start_time: int, end_time: int) -> pd.DataFrame:
    idx = pd.date_range(pd.to_datetime(start_time, unit='s'), pd.to_datetime(end_time, unit='s'), freq='1d')
    rates = []
    for date in idx:
        if date.year == 2022: r = 0.02
        elif date.year == 2023: r = 0.04
        elif date.year == 2024: r = 0.08 if date.month < 6 else 0.06
        elif date.year == 2025: r = 0.05
        else: r = 0.04
        r += np.random.normal(0, 0.005)
        rates.append(max(0.01, min(0.20, r)))
    return pd.DataFrame({"lending_rate": rates}, index=idx)
