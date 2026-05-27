from dataclasses import dataclass, field
from typing import Any
import numpy as np

@dataclass
class ParamSpec:
    """Specification for a single tunable parameter."""
    name: str
    description: str
    default: Any
    min_val: Any
    max_val: Any
    step: Any
    param_type: str = "float"           # float, int, categorical
    category: str = "general"
    affects_strategies: list[str] = field(default_factory=lambda: ["all"])
    
    def grid_values(self) -> list[Any]:
        if self.param_type == "categorical":
            assert isinstance(self.min_val, list)
            return self.min_val
        elif self.param_type == "int":
            return list(range(int(self.min_val), int(self.max_val) + 1, int(self.step)))
        else:
            return list(np.arange(self.min_val, self.max_val + self.step / 2, self.step))

PARAM_SPACE: list[ParamSpec] = [
    # ──── CIRCUIT BREAKER ────
    ParamSpec("cb_level1_drop_pct", "Drop % for CB L1", 0.10, 0.05, 0.20, 0.025, category="circuit_breaker"),
    ParamSpec("cb_level2_drop_pct", "Drop % for CB L2", 0.20, 0.10, 0.35, 0.05, category="circuit_breaker"),
    ParamSpec("cb_level3_drop_pct", "Drop % for CB L3", 0.35, 0.20, 0.50, 0.05, category="circuit_breaker"),
    ParamSpec("hwm_decay_halflife_days", "Days for HWM to decay 50%", 90, 30, 365, 30, "int", "circuit_breaker"),
    ParamSpec("cb_decay_stable_days_l3", "Stable days to decay L3->L2", 14, 7, 30, 7, "int", "circuit_breaker"),
    ParamSpec("cb_decay_stable_days_l2", "Stable days to decay L2->L1", 21, 7, 45, 7, "int", "circuit_breaker"),
    ParamSpec("cb_decay_vol_ratio_threshold", "Vol ratio threshold for CB decay", 1.5, 1.0, 3.0, 0.25, category="circuit_breaker"),
    
    # ──── RECOVERY PHASE ────
    ParamSpec("recovery_week1_max_volatile_pct", "Max volatile in weeks 1-2", 0.10, 0.05, 0.25, 0.05, category="recovery"),
    ParamSpec("recovery_week3_max_volatile_pct", "Max volatile in weeks 3-4", 0.20, 0.10, 0.35, 0.05, category="recovery"),
    ParamSpec("recovery_week5_max_volatile_pct", "Max volatile in weeks 5-6", 0.35, 0.20, 0.50, 0.05, category="recovery"),
    ParamSpec("post_recovery_caution_days", "Caution days post-recovery", 30, 0, 60, 15, "int", "recovery"),
    
    # ──── ALLOCATION ────
    ParamSpec("min_stable_reserve_pct", "Min stablecoin", 0.20, 0.10, 0.40, 0.05, category="allocation"),
    ParamSpec("max_volatile_pct", "Max volatile", 0.50, 0.30, 0.70, 0.05, category="allocation"),
    
    # ──── REBALANCING ────
    ParamSpec("drift_threshold_pct", "L1 drift threshold", 0.05, 0.02, 0.15, 0.01, category="rebalancing"),
    ParamSpec("rebalance_cooldown_days", "Min days between rebalances", 3, 1, 21, 1, "int", "rebalancing"),
    
    # ──── HEDGING ────
    ParamSpec("hedge_ratio_uncertain", "Target hedge in uncertain", 0.40, 0.10, 0.60, 0.05, category="hedging"),
    ParamSpec("hedge_ratio_crisis", "Target hedge in crisis", 0.80, 0.50, 1.00, 0.10, category="hedging"),
    
    # ──── COVARIANCE & REGIME ────
    ParamSpec("covariance_lookback_days", "Rolling window for covariance", 252, 60, 504, 63, "int", "covariance"),
    ParamSpec("regime_fit_window_days", "Rolling window for HMM fitting", 504, 252, 756, 126, "int", "regime"),

    # ──── EXECUTION (F2A) ────
    ParamSpec("maker_fraction", "Fraction of trades as maker", 0.70, 0.30, 0.95, 0.05, category="execution"),
    ParamSpec("emergency_taker_fee_bps", "Taker fee bps for CB events", 3.5, 2.0, 5.0, 0.5, category="execution"),
    ParamSpec("slippage_depth_usd", "Assumed book depth for slippage", 5e6, 1e6, 50e6, 1e6, category="execution"),
    
    # ──── YIELD (F2A) ────
    ParamSpec("basis_trade_pct", "Portfolio fraction in basis trades", 0.0, 0.0, 0.20, 0.02, category="yield"),
    ParamSpec("lending_fraction_of_cash", "Fraction of cash lent out", 0.70, 0.40, 0.95, 0.05, category="yield"),
    
    # ──── REGIME (F2A) ────
    ParamSpec("hmm_sticky_alpha", "HMM sticky prior strength", 10.0, 2.0, 30.0, 2.0, category="regime"),
    ParamSpec("crisis_3step_threshold", "Crisis prob threshold for emergency", 0.30, 0.10, 0.70, 0.05, category="regime"),
    
    # ──── VOLATILITY SCALING (F2A) ────
    ParamSpec("vol_target_annualized", "Target portfolio vol", 0.12, 0.06, 0.25, 0.01, category="vol_scaling"),
    ParamSpec("vol_lookback_days", "Vol estimation window", 21, 10, 63, 5, "int", category="vol_scaling"),
]
