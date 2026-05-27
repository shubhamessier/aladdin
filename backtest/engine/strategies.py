import sys
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import numpy as np
import logging

from backtest.engine.constants import MARKET_CAP_PRIORS, DEFAULT_RISK_AVERSION

logger = logging.getLogger(__name__)

_risk_engine_path = Path(__file__).resolve().parent.parent.parent / "python-risk"
if str(_risk_engine_path) not in sys.path:
    sys.path.append(str(_risk_engine_path))

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
            
            diff = volatile_sum - new_volatile_sum
            stable_fallback = next((s for s in ["USDC", "USDT", "DAI"] if s in weights), None)
            if stable_fallback:
                new_weights[stable_fallback] = new_weights.get(stable_fallback, 0.0) + diff
            else:
                total = sum(new_weights.values())
                if total > 0:
                    new_weights = {k: v / total for k, v in new_weights.items()}
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
        try:
            from risk_engine.portfolio_optimizer import optimize_risk_parity
            from risk_engine.schemas import TierConstraint
            
            min_stable = 0.20 if current_regime != 'crisis' else 0.60
            if max_volatile_override is not None:
                min_stable = max(min_stable, 1.0 - max_volatile_override)
                
            stable_indices = [i for i, a in enumerate(asset_names) if a in ["USDC", "USDT", "DAI"]]
            tier_constraints = []
            if stable_indices:
                tier_constraints.append(TierConstraint(asset_indices=stable_indices, min_total=min_stable, max_total=1.0))
            
            bounds = [(0.0, 0.35) for _ in asset_names]
            
            res = optimize_risk_parity(covariance_matrix, bounds, tier_constraints)
            weights = {name: float(w) for name, w in zip(asset_names, res.weights)}
        except Exception:
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
            volatile_pct = 1.0
            
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
        try:
            from risk_engine.portfolio_optimizer import optimize_mean_variance
            # Min variance = Mean variance with expected returns = 0 vector
            N = len(asset_names)
            expected_zeros = np.zeros(N)
            bounds = [(0.0, 1.0) for _ in range(N)]
            
            res = optimize_mean_variance(expected_zeros, covariance_matrix, bounds)
            weights = {name: float(w) for name, w in zip(asset_names, res.weights)}
        except Exception:
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
        try:
            from risk_engine.portfolio_optimizer import optimize_black_litterman
            from risk_engine.schemas import View
            
            # Use CAPM equilibrium as prior
            mkt_weights = np.array([MARKET_CAP_PRIORS.get(a, 0.01) for a in asset_names])
            mkt_weights = mkt_weights / mkt_weights.sum()
            
            # For now, no specific views (P/Q), just equilibrium
            # This can be expanded to inject analyst views
            res = optimize_black_litterman(
                covariance=covariance_matrix,
                market_caps=mkt_weights,
                views=[],
                risk_aversion=getattr(self.config, 'risk_aversion', DEFAULT_RISK_AVERSION),
                tau=getattr(self.config, 'tau', 0.05),
                bounds=[(0.0, 1.0) for _ in asset_names]
            )
            weights = {name: float(w) for name, w in zip(asset_names, res.weights)}
        except Exception as e:
            logger.error(f"BL optimization failed: {e}. Falling back to EW.")
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
        if current_regime == "bull":
            volatile_target = 0.60
        elif current_regime == "crisis":
            volatile_target = 0.10
        else:
            volatile_target = 0.30
            
        if max_volatile_override is not None:
            volatile_target = min(volatile_target, max_volatile_override)
            
        stable_assets = ["USDC", "USDT", "DAI"]
        stables_present = [a for a in asset_names if a in stable_assets]
        volatiles_present = [a for a in asset_names if a not in stable_assets]
        
        if not volatiles_present:
            return {a: 1.0/len(stables_present) for a in stables_present} if stables_present else {}

        vol_indices = [i for i, a in enumerate(asset_names) if a in volatiles_present]
        sub_cov = covariance_matrix[np.ix_(vol_indices, vol_indices)]
        vols = np.sqrt(np.diag(sub_cov))
        inv_vols = 1.0 / np.maximum(vols, 1e-6)
        vol_weights_arr = inv_vols / np.sum(inv_vols)
        
        weights = {}
        for i, a in enumerate(volatiles_present):
            weights[a] = float(vol_weights_arr[i] * volatile_target)
            
        stable_total = 1.0 - sum(weights.values())
        if stables_present:
            for s in stables_present:
                weights[s] = stable_total / len(stables_present)
        else:
            total = sum(weights.values())
            if total > 0:
                for a in weights:
                    weights[a] /= total

        return weights
