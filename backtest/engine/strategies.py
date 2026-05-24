from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import numpy as np

class StrategyConfig(BaseModel):
    name: str
    target_volatility: Optional[float] = None
    max_drawdown_limit: Optional[float] = None
    rebalance_frequency_days: int = 1
    
class RiskParityConfig(StrategyConfig):
    name: str = "Risk Parity"
    use_historical_cov: bool = True
    
class RegimeAdaptiveConfig(StrategyConfig):
    name: str = "Regime-Adaptive"
    hmm_window_days: int = 365
    bull_target_vol: float = 0.40
    bear_target_vol: float = 0.15
    
class BlackLittermanConfig(StrategyConfig):
    name: str = "Black-Litterman"
    risk_aversion: float = 2.5
    tau: float = 0.05
    
class MinVarianceConfig(StrategyConfig):
    name: str = "Min Variance"
    shrinkage_intensity: float = 0.5
    
class StaticConservativeConfig(StrategyConfig):
    name: str = "Static Conservative"
    stablecoin_allocation_pct: float = 0.80
    volatile_allocation_pct: float = 0.20
    
class BuyAndHoldConfig(StrategyConfig):
    name: str = "Buy & Hold"
    initial_allocations: Dict[str, float] = Field(default_factory=dict)
    
class EqualWeightConfig(StrategyConfig):
    name: str = "Equal Weight"
    
class AllocationStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config
        
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        """
        Generate target weights. Must be implemented by subclasses.
        """
        raise NotImplementedError

class RiskParityStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        # Inverse volatility as a simple fallback approximation of risk parity
        vols = np.sqrt(np.diag(covariance_matrix))
        inv_vols = 1.0 / np.maximum(vols, 1e-6)
        weights = inv_vols / np.sum(inv_vols)
        return {name: float(w) for name, w in zip(asset_names, weights)}

class EqualWeightStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        n = len(asset_names)
        if n == 0:
            return {}
        w = 1.0 / n
        return {name: w for name in asset_names}

class BuyAndHoldStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        # Returns current weights (no rebalancing)
        return current_weights

class StaticConservativeStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        # Simple static allocation prioritizing stablecoins
        stable_assets = ["USDC", "USDT", "DAI"]
        stables_present = [a for a in asset_names if a in stable_assets]
        volatiles_present = [a for a in asset_names if a not in stable_assets]
        
        weights: dict[str, float] = {}
        if not stables_present and not volatiles_present:
            return weights
            
        stable_pct = getattr(self.config, 'stablecoin_allocation_pct', 0.8)
        volatile_pct = getattr(self.config, 'volatile_allocation_pct', 0.2)
        
        if stables_present:
            w_stable = stable_pct / len(stables_present)
            for s in stables_present:
                weights[s] = w_stable
        else:
            volatile_pct = 1.0 # fallback
            
        if volatiles_present:
            w_vol = volatile_pct / len(volatiles_present)
            for v in volatiles_present:
                weights[v] = w_vol
                
        return weights

class MinVarianceStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        # Approximate with inverse covariance 
        try:
            inv_cov = np.linalg.inv(covariance_matrix)
            ones = np.ones(len(asset_names))
            weights = inv_cov @ ones
            weights = weights / np.sum(weights)
            # Clip negative weights for simple long-only
            weights = np.maximum(weights, 0)
            sum_w = np.sum(weights)
            if sum_w > 0:
                weights = weights / sum_w
            else:
                weights = np.ones(len(asset_names)) / len(asset_names)
        except np.linalg.LinAlgError:
            # Fallback to inverse volatility if singular
            vols = np.sqrt(np.diag(covariance_matrix))
            inv_vols = 1.0 / np.maximum(vols, 1e-6)
            weights = inv_vols / np.sum(inv_vols)
            
        return {name: float(w) for name, w in zip(asset_names, weights)}

class BlackLittermanStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        # Using the expected returns generated by the BL model from risk engine
        try:
            # simple target: w ~ inv(Cov) * Expected_Returns
            inv_cov = np.linalg.inv(covariance_matrix)
            mu = np.array([expected_returns.get(name, 0.0) for name in asset_names])
            weights = inv_cov @ mu
            # Normalize and clip
            weights = np.maximum(weights, 0)
            sum_w = np.sum(weights)
            if sum_w > 0:
                weights = weights / sum_w
            else:
                weights = np.ones(len(asset_names)) / len(asset_names)
        except np.linalg.LinAlgError:
            weights = np.ones(len(asset_names)) / len(asset_names)
            
        return {name: float(w) for name, w in zip(asset_names, weights)}

class RegimeAdaptiveStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str]
    ) -> Dict[str, float]:
        # This wrapper expects an external signal for the current regime
        # Fallback to risk-parity structurally
        vols = np.sqrt(np.diag(covariance_matrix))
        inv_vols = 1.0 / np.maximum(vols, 1e-6)
        weights = inv_vols / np.sum(inv_vols)
        return {name: float(w) for name, w in zip(asset_names, weights)}
