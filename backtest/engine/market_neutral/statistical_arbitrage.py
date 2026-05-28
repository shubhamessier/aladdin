from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd
import logging
from backtest.engine.strategies import AllocationStrategy, StrategyConfig

logger = logging.getLogger(__name__)

class StatArbConfig(StrategyConfig):
    name: str = "Statistical Arbitrage"
    z_score_entry: float = 2.0
    z_score_exit: float = 0.0
    lookback_days: int = 14

class StatArbStrategy(AllocationStrategy):
    """
    Pairs trading strategy. 
    If asset A diverges significantly from asset B relative to historical norm, 
    go long A and short B (or vice versa).
    """
    def __init__(self, config: StrategyConfig):
        super().__init__(config)
        self.active_pairs = {} # 'asset': position multiplier (+1 = long, -1 = short)

    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain",
        historical_returns: Optional[pd.DataFrame] = None,
        date: Optional[pd.Timestamp] = None,
        yield_engine: Optional[Any] = None
    ) -> Dict[str, float]:
        weights = {a: 0.0 for a in asset_names}
        stable_assets = ["USDC", "USDT", "DAI"]
        stables = [a for a in asset_names if a in stable_assets]
        volatiles = [a for a in asset_names if a not in stable_assets]
        
        if not stables:
            return {a: 1.0/max(len(asset_names), 1) for a in asset_names}
            
        for s in stables:
            weights[s] = 1.0 / len(stables)
            
        if historical_returns is None or len(historical_returns) < 14 * 24:
            return weights
            
        cfg = self.config
        lookback = getattr(cfg, 'lookback_days', 14) * 24
        z_entry = getattr(cfg, 'z_score_entry', 2.0)
        z_exit = getattr(cfg, 'z_score_exit', 0.0)
        
        # Pairs trading BTC and ETH for simplicity
        if "BTC" in volatiles and "ETH" in volatiles:
            hist_btc = historical_returns["BTC"].tail(lookback)
            hist_eth = historical_returns["ETH"].tail(lookback)
            
            # Cumulative returns over lookback
            cum_btc = (1 + hist_btc).cumprod()
            cum_eth = (1 + hist_eth).cumprod()
            
            spread = cum_eth - cum_btc
            mean_spread = spread.mean()
            std_spread = spread.std()
            
            if std_spread > 0:
                current_z = (spread.iloc[-1] - mean_spread) / std_spread
                
                # ETH is overvalued relative to BTC (Short ETH, Long BTC) -> long spot BTC
                if current_z > z_entry:
                    self.active_pairs = {"BTC": 1.0, "ETH": -1.0}
                # ETH is undervalued relative to BTC (Long ETH, Short BTC) -> long spot ETH
                elif current_z < -z_entry:
                    self.active_pairs = {"BTC": -1.0, "ETH": 1.0}
                elif abs(current_z) < z_exit:
                    self.active_pairs = {}
                    
        # Allocate spot to BOTH assets (we need spot to short against, or we rely on hedger)
        # For simplicity, we allocate spot equally to both to allow the hedger to short one
        if self.active_pairs:
            weight_per_leg = 0.25 # 25% to BTC, 25% to ETH -> 50% total. 50% remains as Cash for margin.
            weights["BTC"] = weight_per_leg
            weights["ETH"] = weight_per_leg
            for s in stables:
                weights[s] = 0.50 / len(stables)
                
        return self._apply_volatile_override(weights, max_volatile_override)

    def generate_target_hedges(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain",
        historical_returns: Optional[pd.DataFrame] = None,
        date: Optional[pd.Timestamp] = None,
        yield_engine: Optional[Any] = None
    ) -> Optional[Dict[str, float]]:
        hedges = {}
        for a in asset_names:
            if a not in ["USDC", "USDT", "DAI"]:
                if self.active_pairs.get(a) == -1.0:
                    hedges[a] = 1.0 # 100% short
                else:
                    hedges[a] = 0.0 # 0% short (long side)
        return hedges