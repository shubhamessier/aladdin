import requests
import pandas as pd
import logging
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def fetch_funding_rates(coin: str, start_time: int, end_time: int, cache_dir: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch real 8-hour funding history from Hyperliquid.
    Falls back to synthetic only if API fails.
    """
    # Check disk cache first
    if cache_dir:
        cache_path = Path(cache_dir) / f"funding_{coin}_{start_time}_{end_time}.parquet"
        if cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                logger.info(f"Loaded {len(df)} funding records for {coin} from cache")
                return df
            except Exception:
                pass

    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_time * 1000,
        "endTime": end_time * 1000
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            raise ValueError("Empty funding history response")

        records = []
        for entry in data:
            records.append({
                "timestamp": pd.to_datetime(entry["time"], unit="ms"),
                "funding_rate": float(entry["fundingRate"]),
                "premium": float(entry.get("premium", 0.0)),
            })

        df = pd.DataFrame(records).set_index("timestamp")

        # Save to cache
        if cache_dir and not df.empty:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache_path)

        logger.info(f"Fetched {len(df)} real funding records for {coin}")
        return validate_funding(df, coin)

    except Exception as e:
        logger.warning(f"HL funding fetch failed for {coin}: {e}. Generating fallback synthetic data.")
        return _generate_synthetic_funding(coin, start_time, end_time)

def validate_funding(df: pd.DataFrame, coin: str) -> pd.DataFrame:
    KNOWN_BOUNDS = (-0.00075, 0.00375)
    outliers = df[(df["funding_rate"] < KNOWN_BOUNDS[0]) | (df["funding_rate"] > KNOWN_BOUNDS[1])]
    if len(outliers) > 0:
        logger.warning(f"{coin}: {len(outliers)} funding outliers beyond known bounds")
    return df

def _generate_synthetic_funding(coin: str, start_time: int, end_time: int) -> pd.DataFrame:
    idx = pd.date_range(pd.to_datetime(start_time, unit='s'), pd.to_datetime(end_time, unit='s'), freq='8h')
    rates = np.random.normal(0.0001, 0.00005, len(idx))
    df = pd.DataFrame({"funding_rate": rates}, index=idx)
    return df
