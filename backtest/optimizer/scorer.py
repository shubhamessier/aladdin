from dataclasses import dataclass
from typing import Any, Tuple, Dict, Optional
import numpy as np

@dataclass
class OptimizationObjectives:
    """
    Multi-objective scoring for treasury strategies.
    All objectives are computed from a single backtest run.
    """
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    total_return_pct: float
    max_drawdown_pct: float
    annualized_volatility: float
    cb_days: int
    net_yield_usd: float
    rebalance_count: int
    cost_drag_annual_pct: float

    def composite_score(self, weights: Optional[Dict[str, float]] = None) -> float:
        """
        Compute a single composite score from all objectives.
        Range: 0-100 (higher is better).
        """
        if weights is None:
            weights = {
                "sharpe": 0.20,
                "sortino": 0.15,
                "calmar": 0.10,
                "max_dd_penalty": 0.25,
                "vol_penalty": 0.15,
                "cb_penalty": 0.10,
                "cost_penalty": 0.05,
            }
        
        score = 0.0
        
        # Reward: Sharpe ratio (clip to [-2, 3], normalize to [0, 100])
        sharpe_score = (np.clip(self.sharpe_ratio, -2, 3) + 2) / 5 * 100
        score += weights["sharpe"] * sharpe_score
        
        # Reward: Sortino ratio (clip to [-2, 4], normalize)
        sortino_score = (np.clip(self.sortino_ratio, -2, 4) + 2) / 6 * 100
        score += weights["sortino"] * sortino_score
        
        # Reward: Calmar ratio (clip to [-1, 3], normalize)
        calmar_score = (np.clip(self.calmar_ratio, -1, 3) + 1) / 4 * 100
        score += weights["calmar"] * calmar_score
        
        # Penalty: Max drawdown (0% DD = 100 score, 50% DD = 0 score)
        dd_score = max(0, 100 * (1 - self.max_drawdown_pct / 0.50))
        score += weights["max_dd_penalty"] * dd_score
        
        # Penalty: Volatility (0% vol = 100, 50% vol = 0)
        vol_score = max(0, 100 * (1 - self.annualized_volatility / 0.50))
        score += weights["vol_penalty"] * vol_score
        
        # Penalty: CB days (0 days = 100, 1460 days = 0)
        cb_score = max(0, 100 * (1 - self.cb_days / 1460))
        score += weights["cb_penalty"] * cb_score
        
        # Penalty: Cost drag (0% = 100, 5% = 0)
        cost_score = max(0, 100 * (1 - self.cost_drag_annual_pct / 0.05))
        score += weights["cost_penalty"] * cost_score
        
        return score

    def passes_hard_constraints(self) -> Tuple[bool, str]:
        """Hard constraints for treasury safety."""
        if self.max_drawdown_pct > 0.40:
            return False, f"Max drawdown {self.max_drawdown_pct:.1%} > 40%"
        if self.annualized_volatility > 0.30:
            return False, f"Volatility {self.annualized_volatility:.1%} > 30%"
        if self.total_return_pct < -30:
            return False, f"Total return {self.total_return_pct:.1f}% < -30%"
        if self.cost_drag_annual_pct > 0.05:
            return False, f"Cost drag {self.cost_drag_annual_pct:.2%} > 5%"
        return True, "Passes"
