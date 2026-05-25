import pandas as pd
from typing import Dict, Any

LENDING_RATE_SCHEDULE = {
    (2022, 2): 0.02, (2022, 3): 0.015, (2022, 4): 0.02,
    (2023, 1): 0.03, (2023, 2): 0.04, (2023, 3): 0.05, (2023, 4): 0.05,
    (2024, 1): 0.08, (2024, 2): 0.10, (2024, 3): 0.07, (2024, 4): 0.06,
    (2025, 1): 0.05, (2025, 2): 0.04, (2025, 3): 0.04, (2025, 4): 0.04,
    (2026, 1): 0.04, (2026, 2): 0.04,
}

FUNDING_RATE_BY_REGIME = {
    "bull": 0.0005,       # Shorts receive 0.05% per 8h
    "uncertain": 0.0002,
    "crisis": -0.0003,    # Shorts pay 0.03% per 8h
}

class YieldEngine:
    def get_lending_rate(self, date: pd.Timestamp) -> float:
        key = (date.year, (date.month - 1) // 3 + 1)
        return LENDING_RATE_SCHEDULE.get(key, 0.04)

    def calculate_yield(self, portfolio_value: float, cash_pct: float, date: pd.Timestamp, regime: str) -> float:
        """
        Calculates daily yield. 
        Stables (cash) earn lending yield (always positive).
        Volatile assets (1 - cash_pct) could represent hedged positions.
        BUG-11 Fix: Ensure treasury isn't penalized for holding cash in crisis.
        """
        lending_rate = self.get_lending_rate(date)
        
        # Cash portion earns pure lending yield
        daily_lending = (portfolio_value * cash_pct * 0.70) * (lending_rate / 365)
        
        # Volatile portion: if we assume a fraction is hedged (delta neutral carry)
        # funding_rate is per 8h. Annualized = rate * 3 * 365.
        funding_rate_daily = FUNDING_RATE_BY_REGIME.get(regime, 0.0002) * 3
        
        # Only apply funding to 10% of the portfolio (simulated basis trade or hedge)
        # In a real system, this would be computed from DerivativePosition
        daily_funding = (portfolio_value * 0.10) * funding_rate_daily
        
        return daily_lending + daily_funding
