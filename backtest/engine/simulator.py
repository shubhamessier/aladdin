import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

# Add risk engine to path
risk_engine_path = Path(__file__).resolve().parent.parent / "python-risk"
if str(risk_engine_path) not in sys.path:
    sys.path.append(str(risk_engine_path))

from risk_engine.regime_detector import RobustRegimeDetector
from risk_engine.portfolio_optimizer import optimize_risk_parity
from risk_engine.schemas import RegimePrediction, PortfolioWeights, TierConstraint
from risk_engine.var_models import compute_historical_var
from risk_engine.covariance import build_covariance

from backtest.engine.portfolio import PortfolioState
from backtest.engine.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, RecoveryPhase, compute_effective_hwm
from backtest.engine.yield_engine import YieldEngine
from backtest.engine.cost_model import TransactionCostModel, CostModelConfig

class TreasurySimulator:
    def __init__(
        self,
        initial_cash: float,
        start_date: datetime,
        end_date: datetime,
        assets: List[str],
        circuit_breaker_config: CircuitBreakerConfig,
    ):
        self.portfolio = PortfolioState(
            timestamp=start_date,
            portfolio_value=initial_cash,
            cash=initial_cash,
            weights={a: 0.0 for a in assets}
        )
        self.assets = assets
        self.start_date = start_date
        self.end_date = end_date
        self.cb = CircuitBreaker(circuit_breaker_config)
        self.recovery = RecoveryPhase()
        self.yield_engine = YieldEngine()
        self.cost_model = TransactionCostModel(CostModelConfig())
        self.regime_detector = RobustRegimeDetector()
        
        self.history: List[Dict[str, Any]] = []
        self.market_data: pd.DataFrame = pd.DataFrame()
        self.current_day = 0
        
        self.vol_history_30d: List[float] = []
        self.avg_vol_lifetime: float = 0.02

    def load_market_data(self, price_history: pd.DataFrame) -> None:
        self.market_data = price_history

    def run(self, verbose: bool = False) -> dict[str, Any]:
        warmup = 60
        for day in range(len(self.market_data)):
            self.current_day = day
            self.step()
            
            if day >= warmup and day % 30 == 0:
                lookback = self.market_data.iloc[max(0, day-504):day]
                returns = lookback.pct_change().fillna(0).mean(axis=1)
                self.regime_detector.refit_rolling(returns)
                
        return self.summary()

    def step(self) -> None:
        date = self.market_data.index[self.current_day]
        prices = self.market_data.iloc[self.current_day]
        
        if self.current_day > 0:
            prev_prices = self.market_data.iloc[self.current_day - 1]
            new_val = self.portfolio.cash
            for asset, weight in self.portfolio.weights.items():
                if weight > 0:
                    new_val += (self.portfolio.portfolio_value * weight) * (prices[asset] / prev_prices[asset])
            self.portfolio.portfolio_value = new_val

        returns_history = self.market_data.iloc[max(0, self.current_day-252):self.current_day+1].pct_change().fillna(0)
        crypto_idx = returns_history.mean(axis=1)
        regime_pred = self.regime_detector.predict(crypto_idx)
        
        rolling_vol = crypto_idx.tail(30).std() if self.current_day > 10 else 0.02
        self.vol_history_30d.append(rolling_vol)
        self.avg_vol_lifetime = np.mean(self.vol_history_30d)
        
        prev_cb_level = self.cb.current_level
        current_cb_level = self.cb.update(date, self.portfolio.portfolio_value, rolling_vol, self.avg_vol_lifetime)
        
        # Track Effective HWM
        days_since_hwm = (date - self.cb.last_peak_time).days if self.cb.last_peak_time else 0
        eff_hwm = compute_effective_hwm(self.cb.hwm_absolute, self.portfolio.portfolio_value, days_since_hwm, self.cb.config.hwm_decay_halflife_days)

        if prev_cb_level >= 2 and current_cb_level == 1 and not self.recovery.is_active:
            self.recovery.enter(pd.Timestamp(date), self.portfolio.portfolio_value)
            
        if self.recovery.is_active:
            if (pd.Timestamp(date) - self.recovery.entry_date).days % 7 == 0:
                self.recovery.advance_week()
            if self.recovery.check_snap_back(self.portfolio.portfolio_value):
                self.recovery.snap_back()
                self.cb.current_level = 2
                current_cb_level = 2

        daily_yield = self.yield_engine.calculate_yield(self.portfolio.portfolio_value, self.portfolio.cash/self.portfolio.portfolio_value if self.portfolio.portfolio_value > 0 else 1.0, pd.Timestamp(date), regime_pred.current_regime)
        self.portfolio.portfolio_value += daily_yield
        
        if self.current_day % 7 == 0 and current_cb_level < 2:
            try:
                lookback_returns = returns_history.tail(self.current_day if self.current_day < 252 else 252)
                if len(lookback_returns) > 20:
                    cov = build_covariance(lookback_returns)
                    res_weights = optimize_risk_parity(
                        returns=lookback_returns,
                        covariance=cov,
                        assets=self.assets,
                        min_stable_reserve=0.20 if regime_pred.current_regime != 'crisis' else 0.60
                    )
                    target_weights = res_weights.weights
                else:
                    target_weights = {a: 1.0/len(self.assets) for a in self.assets}
            except Exception:
                target_weights = {a: 1.0/len(self.assets) for a in self.assets}

            if self.recovery.is_active:
                max_vol = self.recovery.max_volatile_pct
                total_vol = sum(target_weights[a] for a in self.assets if a not in ['USDC', 'USDT', 'DAI'])
                if total_vol > max_vol:
                    scale = max_vol / total_vol
                    for a in target_weights:
                        if a not in ['USDC', 'USDT', 'DAI']:
                            target_weights[a] *= scale

            total_trade = sum(abs(target_weights.get(a, 0) - self.portfolio.weights.get(a, 0)) for a in self.assets) * self.portfolio.portfolio_value
            cost = self.cost_model.estimate_cost(total_trade, "ETH", "buy", 1e8, 1e7).total
            self.portfolio.portfolio_value -= cost
            self.portfolio.weights = target_weights
            self.portfolio.cash = self.portfolio.portfolio_value * (1 - sum(target_weights.values()))

        self.history.append({
            "timestamp": date,
            "portfolio_value": self.portfolio.portfolio_value,
            "cash": self.portfolio.cash,
            "regime": regime_pred.current_regime,
            "cb_level": current_cb_level,
            "effective_hwm": eff_hwm,
            "recovery_active": self.recovery.is_active,
            "var_95_1d": 0.0,
            "trade_volume_usd": 0.0
        })

    def summary(self) -> dict[str, Any]:
        if not self.history: return {}
        vals = [h["portfolio_value"] for h in self.history]
        total_return = (vals[-1] - vals[0]) / vals[0]
        
        drawdowns = []
        max_val = 0.0
        for v in vals:
            if v > max_val: max_val = v
            drawdowns.append((max_val - v) / max_val if max_val > 0 else 0)
        
        return {
            "total_return_pct": total_return * 100,
            "annualized_return": ((1 + total_return) ** (365 / len(vals)) - 1),
            "sharpe_ratio": (total_return * 100) / (np.std(vals) / np.mean(vals) * 100) if np.mean(vals) > 0 else 0,
            "max_drawdown_pct": max(drawdowns) * 100,
            "cb_days": sum(1 for h in self.history if h["cb_level"] > 0)
        }
