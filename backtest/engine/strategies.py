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
        
    def _apply_volatile_override(
        self, 
        weights: Dict[str, float], 
        max_volatile_override: Optional[float]
    ) -> Dict[str, float]:
        """
        Helper to enforce max volatile allocation limit.
        """
        if max_volatile_override is None:
            return weights
            
        stable_assets = ["USDC", "USDT", "DAI"]
        volatile_sum = sum(w for name, w in weights.items() if name not in stable_assets)
        
        if volatile_sum > max_volatile_override:
            scale = max_volatile_override / volatile_sum if volatile_sum > 0 else 0.0
            new_weights = {}
            new_volatile_sum = 0.0
            for name, w in weights.items():
                if name not in stable_assets:
                    new_weights[name] = w * scale
                    new_volatile_sum += new_weights[name]
                else:
                    new_weights[name] = w
            
            # Re-allocate the difference to USDC
            diff = volatile_sum - new_volatile_sum
            new_weights["USDC"] = new_weights.get("USDC", 0.0) + diff
            return new_weights
            
        return weights

    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
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
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
    ) -> Dict[str, float]:
        # Inverse volatility as a simple fallback approximation of risk parity
        vols = np.sqrt(np.diag(covariance_matrix))
        inv_vols = 1.0 / np.maximum(vols, 1e-6)
        weights_arr = inv_vols / np.sum(inv_vols)
        weights = {name: float(w) for name, w in zip(asset_names, weights_arr)}
        return self._apply_volatile_override(weights, max_volatile_override)

class EqualWeightStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
    ) -> Dict[str, float]:
        n = len(asset_names)
        if n == 0:
            return {}
        w = 1.0 / n
        weights = {name: w for name in asset_names}
        return self._apply_volatile_override(weights, max_volatile_override)

class BuyAndHoldStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
    ) -> Dict[str, float]:
        # Returns current weights (no rebalancing)
        return self._apply_volatile_override(current_weights, max_volatile_override)

class StaticConservativeStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
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
        
        if max_volatile_override is not None:
            volatile_pct = min(volatile_pct, max_volatile_override)
            stable_pct = 1.0 - volatile_pct
        
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
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
    ) -> Dict[str, float]:
        # Approximate with inverse covariance 
        try:
            inv_cov = np.linalg.inv(covariance_matrix)
            ones = np.ones(len(asset_names))
            weights_arr = inv_cov @ ones
            weights_arr = weights_arr / np.sum(weights_arr)
            # Clip negative weights for simple long-only
            weights_arr = np.maximum(weights_arr, 0)
            sum_w = np.sum(weights_arr)
            if sum_w > 0:
                weights_arr = weights_arr / sum_w
            else:
                weights_arr = np.ones(len(asset_names)) / len(asset_names)
        except np.linalg.LinAlgError:
            # Fallback to inverse volatility if singular
            vols = np.sqrt(np.diag(covariance_matrix))
            inv_vols = 1.0 / np.maximum(vols, 1e-6)
            weights_arr = inv_vols / np.sum(inv_vols)
            
        weights = {name: float(w) for name, w in zip(asset_names, weights_arr)}
        return self._apply_volatile_override(weights, max_volatile_override)

class BlackLittermanStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
    ) -> Dict[str, float]:
        # Using the expected returns generated by the BL model from risk engine
        try:
            # simple target: w ~ inv(Cov) * Expected_Returns
            inv_cov = np.linalg.inv(covariance_matrix)
            mu = np.array([expected_returns.get(name, 0.0) for name in asset_names])
            weights_arr = inv_cov @ mu
            # Normalize and clip
            weights_arr = np.maximum(weights_arr, 0)
            sum_w = np.sum(weights_arr)
            if sum_w > 0:
                weights_arr = weights_arr / sum_w
            else:
                weights_arr = np.ones(len(asset_names)) / len(asset_names)
        except np.linalg.LinAlgError:
            weights_arr = np.ones(len(asset_names)) / len(asset_names)
            
        weights = {name: float(w) for name, w in zip(asset_names, weights_arr)}
        return self._apply_volatile_override(weights, max_volatile_override)

class RegimeAdaptiveStrategy(AllocationStrategy):
    def generate_target_weights(
        self,
        current_weights: Dict[str, float],
        expected_returns: Dict[str, float],
        covariance_matrix: np.ndarray,
        asset_names: List[str],
        max_volatile_override: Optional[float] = None,
        current_regime: str = "uncertain"
    ) -> Dict[str, float]:
        # Branch on regime as suggested in BUG-14
        if current_regime == "bull":
            volatile_target = 0.60
        elif current_regime == "crisis":
            volatile_target = 0.10
        else:  # uncertain or bear
            volatile_target = 0.30
            
        if max_volatile_override is not None:
            volatile_target = min(volatile_target, max_volatile_override)
            
        stable_assets = ["USDC", "USDT", "DAI"]
        stables_present = [a for a in asset_names if a in stable_assets]
        volatiles_present = [a for a in asset_names if a not in stable_assets]
        
        if not volatiles_present:
            return {a: 1.0/len(stables_present) for a in stables_present} if stables_present else {}

        # Risk-parity for volatile assets
        vol_indices = [i for i, a in enumerate(asset_names) if a in volatiles_present]
        sub_cov = covariance_matrix[np.ix_(vol_indices, vol_indices)]
        vols = np.sqrt(np.diag(sub_cov))
        inv_vols = 1.0 / np.maximum(vols, 1e-6)
        vol_weights_arr = inv_vols / np.sum(inv_vols)
        
        weights = {}
        for i, a in enumerate(volatiles_present):
            weights[a] = float(vol_weights_arr[i] * volatile_target)
            
        # Distribute remaining to stables
        stable_total = 1.0 - sum(weights.values())
        if stables_present:
            for s in stables_present:
                weights[s] = stable_total / len(stables_present)
        else:
            # Re-scale volatile to 100% if no stables
            total = sum(weights.values())
            if total > 0:
                for a in weights:
                    weights[a] /= total

        return weights
