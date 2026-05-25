import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

# Add risk engine to path
risk_engine_path = Path(__file__).resolve().parent.parent.parent / "python-risk"
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
from backtest.engine.strategies import AllocationStrategy

class TreasurySimulator:
    def __init__(
        self,
        initial_cash: float,
        start_date: datetime,
        end_date: datetime,
        assets: List[str],
        circuit_breaker_config: CircuitBreakerConfig,
        strategy: AllocationStrategy
    ):
        self.portfolio = PortfolioState(
            timestamp=start_date,
            portfolio_value=initial_cash,
            cash=initial_cash,
            weights={a: 0.0 for a in assets},
            units={a: 0.0 for a in assets},
            positions={a: 0.0 for a in assets}
        )
        self.assets = assets
        self.start_date = start_date
        self.end_date = end_date
        self.cb = CircuitBreaker(circuit_breaker_config)
        self.recovery = RecoveryPhase()
        self.yield_engine = YieldEngine()
        self.cost_model = TransactionCostModel(CostModelConfig())
        self.regime_detector = RobustRegimeDetector(min_observations=60)
        self.strategy = strategy
        
        self.history: List[Dict[str, Any]] = []
        self.market_data: pd.DataFrame = pd.DataFrame()
        self.current_day = 0
        
        self.vol_history_30d: List[float] = []
        self.avg_vol_lifetime: float = 0.02
        self.warmup_days = 60

    def load_market_data(self, price_history: pd.DataFrame) -> None:
        self.market_data = price_history

    def run(self, verbose: bool = False) -> dict[str, Any]:
        if not self.market_data.empty:
            initial_idx = self.market_data.iloc[:self.warmup_days].pct_change().fillna(0).mean(axis=1)
            if len(initial_idx) >= 60:
                self.regime_detector.fit(initial_idx)

        for day in range(len(self.market_data)):
            self.current_day = day
            self.step()
            
            if day >= self.warmup_days and day % 30 == 0:
                # FLAW-01: No look-ahead. End at current_day (which was just completed).
                lookback = self.market_data.iloc[max(0, day-504):day+1]
                returns = lookback.pct_change().fillna(0).mean(axis=1)
                self.regime_detector.refit_rolling(returns)
                
        return self.summary()

    def step(self) -> None:
        date = self.market_data.index[self.current_day]
        prices = self.market_data.iloc[self.current_day]
        
        # 1. Mark to Market
        if self.current_day > 0:
            new_val = self.portfolio.cash
            for asset in self.assets:
                val = self.portfolio.units[asset] * prices[asset]
                self.portfolio.positions[asset] = val
                new_val += val
            self.portfolio.portfolio_value = new_val
            
            if self.portfolio.portfolio_value > 0:
                for asset in self.assets:
                    self.portfolio.weights[asset] = self.portfolio.positions[asset] / self.portfolio.portfolio_value

        # 2. Risk & Regime (End at T-1 to avoid look-ahead bias)
        # Using data up to yesterday to decide today's actions.
        lookback_end = self.current_day # This is index for Today. iloc[:current_day] gives up to T-1.
        returns_history = self.market_data.iloc[max(0, lookback_end-252):lookback_end].pct_change().fillna(0)
        
        if len(returns_history) > 10:
            crypto_idx = returns_history.mean(axis=1)
            regime_pred = self.regime_detector.predict(crypto_idx)
            rolling_vol = crypto_idx.tail(30).std()
        else:
            regime_pred = RegimePrediction(current_regime="uncertain", confidence=0.5, crisis_probability_3step=0.1, regime_probabilities={}, transition_probabilities={})
            rolling_vol = 0.02
            
        self.vol_history_30d.append(rolling_vol)
        self.avg_vol_lifetime = np.mean(self.vol_history_30d)
        
        # 3. Update CB
        prev_cb_level = self.cb.current_level
        current_cb_level = self.cb.update(date, self.portfolio.portfolio_value, rolling_vol, self.avg_vol_lifetime)
        
        days_since_hwm = (date - self.cb.last_peak_time).days if self.cb.last_peak_time else 0
        eff_hwm = compute_effective_hwm(self.cb.hwm_absolute, self.portfolio.portfolio_value, days_since_hwm, self.cb.config.hwm_decay_halflife_days)

        # 4. Recovery Management
        if prev_cb_level >= 2 and current_cb_level == 1 and not self.recovery.is_active:
            self.recovery.enter(pd.Timestamp(date), self.portfolio.portfolio_value)
            
        if self.recovery.is_active:
            days_in = (pd.Timestamp(date) - self.recovery.entry_date).days
            if days_in >= 49:
                self.recovery.exit()
            elif days_in > 0 and days_in % 7 == 0:
                self.recovery.advance_week()
            
            if self.recovery.check_further_decline(self.portfolio.portfolio_value):
                self.recovery.reset_recovery()
                self.cb.current_level = 2
                current_cb_level = 2

        # 5. Yield
        cash_pct = self.portfolio.cash/self.portfolio.portfolio_value if self.portfolio.portfolio_value > 0 else 1.0
        daily_yield = self.yield_engine.calculate_yield(self.portfolio.portfolio_value, cash_pct, pd.Timestamp(date), regime_pred.current_regime)
        self.portfolio.portfolio_value += daily_yield
        self.portfolio.cash += daily_yield
        
        # 6. Rebalance
        # Rebalance every 7 days OR immediately upon entering L2+ (emergency)
        entered_emergency = (current_cb_level >= 2 and prev_cb_level < 2)
        is_scheduled = (self.current_day % 7 == 0)
        
        trade_vol = 0.0
        if is_scheduled or entered_emergency:
            if current_cb_level >= 2:
                # Emergency de-risk: Move to stables
                target_weights = {a: 0.0 for a in self.assets}
                # Prefer USDC then USDT then DAI
                for stable in ["USDC", "USDT", "DAI"]:
                    if stable in self.assets:
                        target_weights[stable] = 1.0
                        break
            else:
                try:
                    if len(returns_history) > 20:
                        cov = build_covariance(returns_history)
                        max_vol_override = self.recovery.max_volatile_pct if self.recovery.is_active else None

                        _mkt_cap = {"BTC": 0.65, "ETH": 0.25, "USDC": 0.08, "USDT": 0.07, "DAI": 0.05}
                        _mkt_w = np.array([_mkt_cap.get(a, 1.0/len(self.assets)) for a in self.assets])
                        _mkt_w = _mkt_w / _mkt_w.sum()
                        _eq_returns = 2.5 * (cov @ _mkt_w)
                        expected_returns_dict = {a: float(r) for a, r in zip(self.assets, _eq_returns)}

                        target_weights = self.strategy.generate_target_weights(
                            current_weights=self.portfolio.weights,
                            expected_returns=expected_returns_dict,
                            covariance_matrix=cov,
                            asset_names=self.assets,
                            max_volatile_override=max_vol_override,
                            current_regime=regime_pred.current_regime
                        )
                    else:
                        target_weights = {a: 1.0/len(self.assets) for a in self.assets}
                except Exception:
                    target_weights = {a: 1.0/len(self.assets) for a in self.assets}

            # Update units and handle costs
            for asset in self.assets:
                old_val = self.portfolio.positions.get(asset, 0.0)
                new_target_val = self.portfolio.portfolio_value * target_weights.get(asset, 0.0)
                
                # Estimate cost per asset (Flaw-04 fix in TCM)
                trade_size = abs(new_target_val - old_val)
                trade_vol += trade_size
                cost = self.cost_model.estimate_cost(trade_size, asset, "buy", 1e8, 1e7).total
                self.portfolio.portfolio_value -= cost
            
            self.portfolio.cash = self.portfolio.portfolio_value * (1.0 - sum(target_weights.values()))
            
            for asset in self.assets:
                asset_val = self.portfolio.portfolio_value * target_weights.get(asset, 0.0)
                self.portfolio.units[asset] = asset_val / prices[asset] if prices[asset] > 0 else 0
                self.portfolio.positions[asset] = asset_val
            
            self.portfolio.weights = target_weights

        # 7. Risk Metrics (Use returns_history for VaR - no look-ahead)
        var_val = 0.0
        if len(returns_history) > 30:
            weights_arr = np.array([self.portfolio.weights.get(a, 0.0) for a in self.assets])
            var_95, _ = compute_historical_var(returns_history.values, weights_arr)
            var_val = var_95 * self.portfolio.portfolio_value

        self.history.append({
            "timestamp": date,
            "portfolio_value": self.portfolio.portfolio_value,
            "cash": self.portfolio.cash,
            "regime": regime_pred.current_regime,
            "cb_level": current_cb_level,
            "effective_hwm": eff_hwm,
            "recovery_active": self.recovery.is_active,
            "var_95_1d": var_val,
            "trade_volume_usd": trade_vol
        })

    def summary(self) -> dict[str, Any]:
        if not self.history: return {}
        vals = pd.Series([h["portfolio_value"] for h in self.history])
        returns = vals.pct_change().dropna()
        ann_return = ((1 + (vals.iloc[-1]-vals.iloc[0])/vals.iloc[0]) ** (365 / len(vals)) - 1)
        ann_vol = returns.std() * np.sqrt(252)
        sharpe = (ann_return - 0.05) / ann_vol if ann_vol > 0 else 0 # Fixed rf to 5%
        drawdowns = (vals.cummax() - vals) / vals.cummax()
        
        return {
            "total_return_pct": ((vals.iloc[-1] / vals.iloc[0]) - 1) * 100,
            "annualized_return": ann_return,
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": drawdowns.max() * 100,
            "cb_days": sum(1 for h in self.history if h["cb_level"] > 0),
            "total_trade_volume": sum(h["trade_volume_usd"] for h in self.history)
        }
