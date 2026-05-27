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
            # HL has no funding history before mid-2023. Stitch synthetic for missing range.
            logger.info(f"HL returned no funding for {coin} window; using synthetic for full window")
            df = _generate_synthetic_funding(coin, start_time, end_time)
            if cache_dir and not df.empty:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
                df.to_parquet(cache_path)
            return df

        records = []
        for entry in data:
            records.append({
                "timestamp": pd.to_datetime(entry["time"], unit="ms"),
                "funding_rate": float(entry["fundingRate"]),
                "premium": float(entry.get("premium", 0.0)),
            })

        df = pd.DataFrame(records).set_index("timestamp").sort_index()
        df = df[~df.index.duplicated(keep="first")]

        # Stitch synthetic onto the pre-HL portion if the requested window starts earlier
        first_real = df.index[0]
        start_ts = pd.to_datetime(start_time, unit='s')
        if first_real > start_ts + pd.Timedelta(hours=12):
            synth = _generate_synthetic_funding(coin, start_time, int(first_real.timestamp()))
            if not synth.empty:
                synth = synth[synth.index < first_real]
                df = pd.concat([synth, df]).sort_index()
                logger.info(f"Stitched {len(synth)} synthetic funding bars before {first_real} for {coin}")

        # Save to cache
        if cache_dir and not df.empty:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache_path)

        logger.info(f"Fetched {len(df)} funding records for {coin} (real + stitched)")
        return validate_funding(df, coin)

    except Exception as e:
        logger.warning(f"HL funding fetch failed for {coin}: {e}. Generating fallback synthetic data.")
        return _generate_synthetic_funding(coin, start_time, end_time)

def validate_funding(df: pd.DataFrame, coin: str) -> pd.DataFrame:
    KNOWN_BOUNDS = (-0.00375, 0.00375)
    outliers = df[(df["funding_rate"] < KNOWN_BOUNDS[0]) | (df["funding_rate"] > KNOWN_BOUNDS[1])]
    if len(outliers) > 0:
        logger.warning(f"{coin}: clipped {len(outliers)} funding outliers beyond {KNOWN_BOUNDS}")
        df["funding_rate"] = df["funding_rate"].clip(*KNOWN_BOUNDS)
    return df

def _generate_synthetic_funding(coin: str, start_time: int, end_time: int) -> pd.DataFrame:
    """
    Per-asset synthetic funding when HL has no real data (pre-HL-launch dates).
    Means calibrated to historical perp funding by asset; stables get zero.
    Crisis periods (Q2 2022, Q3 2024) drift negative — longs underwater.
    """
    if coin in ("USDC", "USDT", "DAI"):
        return pd.DataFrame()  # stables have no perp; caller should skip them

    asset_mean_8h = {
        "BTC": 0.000100,
        "ETH": 0.000110,
        "SOL": 0.000150,
        "DOGE": 0.000180,
        "AVAX": 0.000130,
    }.get(coin, 0.000100)
    asset_std_8h = {
        "BTC": 0.000080,
        "ETH": 0.000100,
        "SOL": 0.000160,
    }.get(coin, 0.000100)

    rng = np.random.default_rng(seed=abs(hash(coin)) % (2**32))
    idx = pd.date_range(pd.to_datetime(start_time, unit='s'),
                        pd.to_datetime(end_time, unit='s'),
                        freq='8h', inclusive='both')
    n = len(idx)
    rates = rng.normal(asset_mean_8h, asset_std_8h, n)
    # Inject historical regime shifts (crisis windows negative)
    for i, ts in enumerate(idx):
        if ts.year == 2022 and ts.month in (5, 6, 7, 11):
            rates[i] -= 0.00030
        elif ts.year == 2024 and ts.month == 8:
            rates[i] -= 0.00025
    df = pd.DataFrame({"funding_rate": rates}, index=idx)
    return df
