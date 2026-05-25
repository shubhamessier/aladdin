import numpy as np
import pandas as pd
from typing import List, Dict

class TSMOMRiskParityOptimizer:
    """
    Time-Series Momentum (TSMOM) Risk Parity Overlay.
    Unlike classic Risk Parity which implicitly buys losers and sells winners
    (because losers drop in price and usually increase in vol, lowering their weight),
    this overlays a trend score to maintain momentum exposure while balancing risk.
    """
    
    def __init__(self, target_volatility: float = 0.15):
        self.target_volatility = target_volatility

    def calculate_trend_score(self, prices: pd.DataFrame) -> pd.Series:
        """
        Calculate a composite trend score using 20d/60d momentum and 50d/200d MA crosses.
        Score ranges roughly around 1.0 (neutral), >1 (bullish), <1 (bearish).
        """
        # 20d and 60d returns
        mom_20 = prices.pct_change(20).iloc[-1]
        mom_60 = prices.pct_change(60).iloc[-1]
        
        # Moving averages
        ma_50 = prices.rolling(50).mean().iloc[-1]
        ma_200 = prices.rolling(200).mean().iloc[-1]
        
        # Trend indicators (1 if positive, -1 if negative)
        trend_ma = np.where(prices.iloc[-1] > ma_50, 1, -1) + np.where(ma_50 > ma_200, 1, -1)
        trend_mom = np.where(mom_20 > 0, 1, -1) + np.where(mom_60 > 0, 1, -1)
        
        # Composite raw score (-4 to +4)
        raw_score = trend_ma + trend_mom
        
        # Scale to a multiplier (e.g., -4 -> 0.5x, 0 -> 1.0x, +4 -> 1.5x)
        # Using a sigmoid-like mapping or simple linear scaling
        trend_multiplier = 1.0 + (raw_score / 8.0) # Range: [0.5, 1.5]
        
        return pd.Series(trend_multiplier, index=prices.columns).fillna(1.0)

    def optimize(self, returns: pd.DataFrame, prices: pd.DataFrame, cov_matrix: np.ndarray, asset_names: List[str]) -> Dict[str, float]:
        """
        Calculates TSMOM-adjusted Risk Parity weights.
        weight = (trend_score / volatility) / sum(trend_score / volatility)
        """
        vols = np.sqrt(np.diag(cov_matrix))
        # Handle zero vol
        vols = np.maximum(vols, 1e-6)
        
        trend_scores = self.calculate_trend_score(prices)
        
        # Align arrays
        trend_arr = np.array([trend_scores.get(asset, 1.0) for asset in asset_names])
        
        # TSMOM Risk Parity formula
        raw_weights = trend_arr / vols
        weights = raw_weights / np.sum(raw_weights)
        
        return {asset: float(weight) for asset, weight in zip(asset_names, weights)}
