import pandas as pd
from typing import Dict, Any, List

LENDING_RATE_SCHEDULE = {
    # (year, quarter): apy
    (2022, 2): 0.015, (2022, 3): 0.02, (2022, 4): 0.025,
    (2023, 1): 0.035, (2023, 2): 0.04, (2023, 3): 0.045, (2023, 4): 0.05,
    (2024, 1): 0.08, (2024, 2): 0.10, (2024, 3): 0.07, (2024, 4): 0.06,
    (2025, 1): 0.055, (2025, 2): 0.05, (2025, 3): 0.05, (2025, 4): 0.05,
    (2026, 1): 0.05, (2026, 2): 0.05,
}

# Real-world funding averages
FUNDING_RATES = {
    "bull": 0.0003, # 0.03% per 8h
    "uncertain": 0.0001,
    "crisis": 0.0004, # Earn funding in crisis (BUG-5-04 fix)
}

class YieldEngine:
    def __init__(self, funding_series: Dict[str, pd.Series] = None, lending_series: Dict[str, pd.Series] = None):
        self.funding_series = funding_series or {}
        self.lending_series = lending_series or {}

    def get_lending_rate(self, asset: str, date: pd.Timestamp) -> float:
        series = self.lending_series.get(asset)
        if series is not None and not series.empty:
            val = series.asof(date)
            if pd.notna(val):
                return float(val)

        # Fallback to hardcoded schedule if real data missing or date precedes series
        key = (date.year, (date.month - 1) // 3 + 1)
        return LENDING_RATE_SCHEDULE.get(key, 0.05)

    def get_funding_rate_8h(self, asset: str, date: pd.Timestamp, regime: str = "uncertain") -> float:
        series = self.funding_series.get(asset)
        if series is not None and not series.empty:
            val = series.asof(date)
            if pd.notna(val):
                return float(val)

        # Fallback to regime-based averages
        return FUNDING_RATES.get(regime, FUNDING_RATES["uncertain"])

    def calculate_yield(
        self, 
        portfolio_value: float, 
        weights: Dict[str, float], 
        date: pd.Timestamp, 
        regime: str,
        derivative_positions: List[Any],
        lending_fraction: float = 0.95 # Higher fraction as we're modeling deployed yield
    ) -> float:
        total_yield = 0.0
        
        # 1. Lending yield on stablecoin allocations (Fix Real Data Audit #3)
        stable_assets = ["USDC", "USDT", "DAI"]
        for asset in stable_assets:
            weight = weights.get(asset, 0.0)
            if weight > 0:
                rate = self.get_lending_rate(asset, date)
                total_yield += (portfolio_value * weight * lending_fraction) * (rate / 365)
        
        # 2. Funding yield from derivative positions (Fix Real Data Audit #4)
        for pos in derivative_positions:
            asset = pos.market.replace("-PERP", "")
            rate_8h = self.get_funding_rate_8h(asset, date, regime)
            
            # pos.direction "long" pays funding if rate > 0
            direction = 1.0 if pos.direction == "long" else -1.0
            payment = pos.notional_usd * (rate_8h * 3) * (-direction) # 3 intervals per day
            total_yield += payment
        
        return total_yield
