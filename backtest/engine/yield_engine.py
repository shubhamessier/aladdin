import pandas as pd
from typing import Dict, Any

LENDING_RATE_SCHEDULE = {
    # (year, quarter): apy
    (2022, 2): 0.015, (2022, 3): 0.02, (2022, 4): 0.025,
    (2023, 1): 0.035, (2023, 2): 0.04, (2023, 3): 0.045, (2023, 4): 0.05,
    (2024, 1): 0.08, (2024, 2): 0.10, (2024, 3): 0.07, (2024, 4): 0.06,
    (2025, 1): 0.055, (2025, 2): 0.05, (2025, 3): 0.05, (2025, 4): 0.05,
    (2026, 1): 0.05, (2026, 2): 0.05,
}

FUNDING_RATES = {
    "bull": {
        "mean_daily": 0.0009,    # 0.03% per 8h ≈ 33% annualized
    },
    "uncertain": {
        "mean_daily": 0.0003,    # 0.01% per 8h ≈ 11% annualized
    },
    "crisis": {
        "mean_daily": -0.0006,   # Shorts pay longs (-0.02% per 8h)
    },
}

class YieldEngine:
    def get_lending_rate(self, date: pd.Timestamp) -> float:
        key = (date.year, (date.month - 1) // 3 + 1)
        return LENDING_RATE_SCHEDULE.get(key, 0.05)

    def calculate_yield(self, portfolio_value: float, cash_pct: float, date: pd.Timestamp, regime: str) -> float:
        lending_rate = self.get_lending_rate(date)
        
        # Hyperliquid Earn approx (6% avg) for 70% of cash
        daily_lending = (portfolio_value * cash_pct * 0.70) * (lending_rate / 365)
        
        # Funding yield for hedged portion (assume 10% of portfolio value is hedged/basis)
        funding_rate_daily = FUNDING_RATES.get(regime, FUNDING_RATES["uncertain"])["mean_daily"]
        
        # Shorts receive funding if mean_daily is positive
        daily_funding = (portfolio_value * 0.10) * funding_rate_daily
        
        return daily_lending + daily_funding
