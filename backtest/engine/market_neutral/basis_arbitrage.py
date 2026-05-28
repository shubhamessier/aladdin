from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd
import logging
from backtest.engine.strategies import AllocationStrategy, StrategyConfig

logger = logging.getLogger(__name__)

class BasisArbitrageConfig(StrategyConfig):
    name: str = "Basis Arbitrage"
    entry_funding_apy: float = 0.15 # 15% APY
    exit_funding_apy: float = 0.05 # 5% APY
    max_leverage: float = 1.0

class BasisArbitrageStrategy(AllocationStrategy):
    """
    Delta-neutral Cash and Carry strategy.
    Buys spot and shorts 100% of the notional value when annualized funding is high.
    Closes the trade (0% target hedge, rotates spot to stablecoin) when funding compresses.
    """
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
        # Always run 100% stablecoins initially; spot gets allocated dynamically
        weights = {a: 0.0 for a in asset_names}
        stable_assets = ["USDC", "USDT", "DAI"]
        stables = [a for a in asset_names if a in stable_assets]
        volatiles = [a for a in asset_names if a not in stable_assets]
        
        if not stables:
            return {a: 1.0/max(len(asset_names), 1) for a in asset_names}
            
        for s in stables:
            weights[s] = 1.0 / len(stables)
            
        if yield_engine is None or date is None:
            return weights
            
        # Check funding rates for volatiles
        active_arbs = []
        for v in volatiles:
            try:
                # Get annualized funding rate
                funding_8h = yield_engine.get_funding_rate_8h(v, date, current_regime)
                funding_apy = float(funding_8h) * 3 * 365
                
                # Simple hysteresis
                current_w = current_weights.get(v, 0.0)
                is_active = current_w > 0.01
                
                cfg = self.config
                entry_thresh = getattr(cfg, 'entry_funding_apy', 0.15)
                exit_thresh = getattr(cfg, 'exit_funding_apy', 0.05)
                
                if (not is_active and funding_apy >= entry_thresh) or (is_active and funding_apy > exit_thresh):
                    active_arbs.append(v)
            except Exception:
                pass
                
        if active_arbs:
            # Shift weight from stables to active arbs
            # We must leave ample cash for the short margin! 
            # If we hedge 100% of spot, and leverage is e.g. 1x or 3x, we need margin. 
            # To be ultra-safe (Delta-neutral), allocate 50% to spot, 50% to stable (cash).
            weight_per_arb = 0.50 / len(active_arbs)
            for v in active_arbs:
                weights[v] = weight_per_arb
            
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
        # For basis arbitrage, we ALWAYS want 100% hedge ratio for our spot volatile exposures
        # because the strategy is definitionally Delta Neutral.
        hedges = {}
        for a in asset_names:
            if a not in ["USDC", "USDT", "DAI"]:
                hedges[a] = 1.0 # 100% short
        return hedges