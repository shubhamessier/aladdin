import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Add risk engine to path
risk_engine_path = Path(__file__).resolve().parent.parent.parent / "python-risk"
if str(risk_engine_path) not in sys.path:
    sys.path.append(str(risk_engine_path))

from risk_engine.regime_detector import RobustRegimeDetector
from risk_engine.portfolio_optimizer import optimize_risk_parity
from risk_engine.schemas import RegimePrediction, PortfolioWeights, TierConstraint
from risk_engine.var_models import compute_historical_var, compute_jump_diffusion_var
from risk_engine.covariance import build_covariance

from backtest.engine.portfolio import PortfolioState, DerivativePosition
from backtest.engine.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, RecoveryPhase, compute_effective_hwm
from backtest.engine.yield_engine import YieldEngine
from backtest.engine.cost_model import TransactionCostModel, CostModelConfig
from backtest.engine.strategies import AllocationStrategy
from backtest.engine.hedger import HedgingEngine, HedgingConfig
from backtest.engine.constants import MARKET_CAP_PRIORS, DEFAULT_RISK_AVERSION

class TreasurySimulator:
    def __init__(
        self,
        initial_cash: float,
        start_date: datetime,
        end_date: datetime,
        assets: List[str],
        circuit_breaker_config: CircuitBreakerConfig,
        strategy: AllocationStrategy,
        risk_free_rate: float = 0.05,
        yield_engine: Optional[YieldEngine] = None,
        depth_by_asset: Optional[Dict[str, float]] = None
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
        self.yield_engine = yield_engine or YieldEngine()
        self.cost_model = TransactionCostModel(CostModelConfig())
        self.hedger = HedgingEngine(HedgingConfig())
        self.regime_detector = RobustRegimeDetector(min_observations=60)
        self.strategy = strategy
        self.risk_free_rate = risk_free_rate
        self.depth_by_asset = depth_by_asset or {}
        
        self.history: List[Dict[str, Any]] = []
        self.market_data: pd.DataFrame = pd.DataFrame()
        self.current_day = 0
        
        self.vol_history_30d: List[float] = []
        self.avg_vol_lifetime: float = 0.02
        self.warmup_days = 60

    def load_market_data(self, price_history: pd.DataFrame) -> None:
        self.market_data = price_history

    def run(self, verbose: bool = False, pre_warmup_data: Optional[pd.DataFrame] = None) -> dict[str, Any]:
        start_day = 0
        if pre_warmup_data is not None and len(pre_warmup_data) >= self.warmup_days:
            idx = pre_warmup_data.pct_change().fillna(0).mean(axis=1)
            self.regime_detector.fit(idx)
        else:
            warmup_idx = self.market_data.iloc[:self.warmup_days].pct_change().fillna(0).mean(axis=1)
            if len(warmup_idx) >= 60:
                self.regime_detector.fit(warmup_idx)
            start_day = self.warmup_days

        # Determine data frequency once for the whole run
        is_hourly_run = False
        if len(self.market_data) > 1:
            diff = self.market_data.index[1] - self.market_data.index[0]
            is_hourly_run = diff <= timedelta(hours=1)
        # Refit every 60 days for hourly (1440 steps), every 180 days for daily
        refit_interval = 1440 if is_hourly_run else 180

        for day in range(start_day, len(self.market_data)):
            self.current_day = day
            self.step()

            if day >= self.warmup_days and day % refit_interval == 0:
                lookback = self.market_data.iloc[max(0, day-504):day]
                returns = lookback.pct_change().fillna(0).mean(axis=1)
                if len(returns) >= self.regime_detector.min_observations:
                    self.regime_detector.refit_rolling(returns)

        return self.summary()

    def step(self) -> None:
        date = self.market_data.index[self.current_day]
        prices = self.market_data.iloc[self.current_day]
        
        # Determine frequency
        if len(self.market_data) > 1:
            diff = self.market_data.index[1] - self.market_data.index[0]
            is_hourly = diff <= timedelta(hours=1)
        else:
            is_hourly = False

        # 1. Mark to Market
        if self.current_day > 0:
            new_val = self.portfolio.cash
            for asset in self.assets:
                val = self.portfolio.units[asset] * prices[asset]
                self.portfolio.positions[asset] = val
                new_val += val
            
            # MTM for hedges (FIX 11)
            for pos in self.portfolio.derivative_positions:
                token = pos.market.replace("-PERP", "")
                if token in prices:
                    # Mark-to-market unrealized PnL
                    pos.unrealized_pnl = pos.notional_usd * (prices[token] / pos.entry_price - 1) * (1 if pos.direction == "long" else -1)
                    new_val += pos.unrealized_pnl
                    
            self.portfolio.portfolio_value = new_val
            
            if self.portfolio.portfolio_value > 0:
                for asset in self.assets:
                    self.portfolio.weights[asset] = self.portfolio.positions[asset] / self.portfolio.portfolio_value

        # 2. Risk & Regime
        # BUG-5-02 & FIX 5: Strict look-ahead prevention
        lookback_end = self.current_day
        returns_history = self.market_data.iloc[max(0, lookback_end-252):lookback_end].pct_change().fillna(0)

        if len(returns_history) > 10:
            crypto_idx = returns_history.mean(axis=1)
            rolling_vol = crypto_idx.tail(30).std()
            # Regime changes on multi-day timescales; cache to avoid per-step HMM inference
            # Daily: recompute weekly (every 7 steps); Hourly: recompute daily (every 24 steps)
            regime_cache_interval = 7 if not is_hourly else 24
            update_regime = (self.current_day % regime_cache_interval == 0)
            if update_regime or not hasattr(self, '_cached_regime_pred'):
                # Pass only last 30 observations; HMM only needs recent context to classify current state
                pred_window = crypto_idx.tail(30)
                regime_pred = self.regime_detector.predict(pred_window)
                self._cached_regime_pred = regime_pred
            else:
                regime_pred = self._cached_regime_pred
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

        # 4. Recovery Management (FIX 9)
        if prev_cb_level >= 2 and current_cb_level == 1 and not self.recovery.is_active:
            self.recovery.enter(pd.Timestamp(date), self.portfolio.portfolio_value)
            
        if self.recovery.is_active:
            self.recovery.update_peak(self.portfolio.portfolio_value)
            days_in = (pd.Timestamp(date) - self.recovery.entry_date).days
            if days_in >= 49:
                self.recovery.exit()
            elif days_in > 0 and days_in % 7 == 0:
                self.recovery.advance_week()
            
            if self.recovery.check_further_decline(self.portfolio.portfolio_value):
                self.recovery.reset_recovery()
                self.cb.current_level = 2
                current_cb_level = 2

        # 5. Yield (FIX 8 & Audit #3)
        daily_yield = self.yield_engine.calculate_yield(
            self.portfolio.portfolio_value, 
            self.portfolio.weights, 
            pd.Timestamp(date), 
            regime_pred.current_regime,
            self.portfolio.derivative_positions
        )
        # Yield is daily, so if hourly, divide by 24
        step_yield = daily_yield / 24 if is_hourly else daily_yield
        self.portfolio.portfolio_value += step_yield
        self.portfolio.cash += step_yield
        
        # 6. Rebalance
        # BUG-5-03: Maintain de-risk while CB is active
        is_emergency = current_cb_level >= 2
        was_emergency = prev_cb_level >= 2
        entered_emergency = is_emergency and not was_emergency
        
        # Scheduled rebalance every 7 days (or 168 hours)
        rebalance_interval = 168 if is_hourly else 7
        is_scheduled = (self.current_day % rebalance_interval == 0)
        
        if is_scheduled or entered_emergency:
            self._execute_rebalance(prices, returns_history, current_cb_level, regime_pred.current_regime, rolling_vol)

        # 6.5 Hedging (FIX 11 & Audit #4) - Check every step for drift
        prices_dict = prices.to_dict()
        hedge_actions = self.hedger.calculate_hedge_adjustments(self.portfolio, prices_dict, regime_pred.current_regime)
        
        # Simple execution for backtest: update derivative_positions to match target
        for action in hedge_actions:
            market = action["symbol"]
            delta = action["delta_adjustment_usd"]
            
            # Find existing position
            pos = next((p for p in self.portfolio.derivative_positions if p.market == market), None)
            
            if pos:
                # Update notional. Negative delta means shorting more.
                # In this simple model, we assume delta matches notional change.
                # Since hedges are shorts, notional_usd is always positive, direction is 'short'.
                new_notional = pos.notional_usd + (-delta if pos.direction == "short" else delta)
                if new_notional < 100: # Close if too small
                    self.portfolio.derivative_positions.remove(pos)
                else:
                    pos.notional_usd = new_notional
            else:
                # Open new position (always short for hedging spot)
                if delta < 0: # negative means want to be short
                    self.portfolio.derivative_positions.append(DerivativePosition(
                        market=market,
                        direction="short",
                        notional_usd=abs(delta),
                        entry_price=prices_dict.get(market.replace("-PERP", ""), 1.0),
                        current_price=prices_dict.get(market.replace("-PERP", ""), 1.0),
                        margin_usd=abs(delta) / 2.0, # 2x leverage
                        unrealized_pnl=0.0,
                        cumulative_funding=0.0,
                        open_date=date
                    ))

        # 7. Risk Metrics — compute at most once per day (skip intermediate hourly steps)
        var_val = self.history[-1]["var_95_1d"] if self.history else 0.0
        jump_var_val = self.history[-1]["jump_var_95_1d"] if self.history else 0.0
        compute_var = len(returns_history) > 30 and (not is_hourly or self.current_day % 24 == 0)
        if compute_var:
            weights_arr = np.array([self.portfolio.weights.get(a, 0.0) for a in self.assets])
            var_95, _ = compute_historical_var(returns_history.values, weights_arr)
            var_val = var_95 * self.portfolio.portfolio_value

            try:
                # FIX 6: Vectorized jump diffusion var
                j_var_95, _ = compute_jump_diffusion_var(returns_history.values, weights_arr, seed=self.current_day)
                jump_var_val = j_var_95 * self.portfolio.portfolio_value
            except Exception:
                jump_var_val = var_val

        self.history.append({
            "timestamp": date,
            "portfolio_value": self.portfolio.portfolio_value,
            "cash": self.portfolio.cash,
            "regime": regime_pred.current_regime,
            "cb_level": current_cb_level,
            "effective_hwm": eff_hwm,
            "recovery_active": self.recovery.is_active,
            "var_95_1d": var_val,
            "jump_var_95_1d": jump_var_val,
            "trade_volume_usd": 0.0
        })

    def _execute_rebalance(self, prices: pd.Series, returns_history: pd.DataFrame, cb_level: int, regime: str, rolling_vol: float) -> float:
        if cb_level >= 2:
            target_weights = {a: 0.0 for a in self.assets}
            # Find first available stable
            for stable in ["USDC", "USDT", "DAI"]:
                if stable in self.assets:
                    target_weights[stable] = 1.0
                    break
        else:
            try:
                if len(returns_history) > 20:
                    cov = build_covariance(returns_history)
                    max_vol_override = self.recovery.max_volatile_pct if self.recovery.is_active else None

                    # BUG-5-08 & Audit #7: Use dynamic equilibrium returns
                    # This could be improved by fetching real-time market caps
                    mkt_weights = np.array([MARKET_CAP_PRIORS.get(a, 0.01) for a in self.assets])
                    mkt_weights = mkt_weights / mkt_weights.sum()
                    eq_returns = DEFAULT_RISK_AVERSION * (cov @ mkt_weights)
                    expected_returns_dict = {a: float(r) for a, r in zip(self.assets, eq_returns)}

                    target_weights = self.strategy.generate_target_weights(
                        current_weights=self.portfolio.weights,
                        expected_returns=expected_returns_dict,
                        covariance_matrix=cov,
                        asset_names=self.assets,
                        max_volatile_override=max_vol_override,
                        current_regime=regime
                    )
                else:
                    target_weights = {a: 1.0/len(self.assets) for a in self.assets}
            except Exception as e:
                logger.error(f"Rebalance failed: {e}")
                target_weights = {a: 1.0/len(self.assets) for a in self.assets}

        total_cost = 0.0
        trade_vol = 0.0
        is_emergency = cb_level >= 2
        
        # FIX 1: Double-entry accounting
        for asset in self.assets:
            old_val = self.portfolio.positions.get(asset, 0.0)
            new_target_val = self.portfolio.portfolio_value * target_weights.get(asset, 0.0)
            delta = new_target_val - old_val
            
            if abs(delta) < 1.0: continue
            
            direction = "buy" if delta > 0 else "sell"
            trade_size = abs(delta)
            
            # FIX 2 & Audit #5: Direction-aware costs with dynamic depth
            book_depth = self.depth_by_asset.get(asset, 5_000_000)
            # Conservative haircut during high vol
            vol_haircut = max(0.1, 1.0 - 5.0 * rolling_vol)
            effective_depth = book_depth * vol_haircut
            
            cost = self.cost_model.estimate_cost(trade_size, asset, direction, effective_depth, 1e7, is_emergency=is_emergency)
            
            actual_delta = delta * cost.fill_ratio
            self.portfolio.positions[asset] = old_val + actual_delta
            self.portfolio.cash -= (actual_delta + cost.total)
            
            total_cost += cost.total
            trade_vol += abs(actual_delta)
            
        # Re-calc value from ground truth (FIX 1)
        deriv_pnl = sum(p.unrealized_pnl for p in self.portfolio.derivative_positions)
        self.portfolio.portfolio_value = self.portfolio.cash + sum(self.portfolio.positions.values()) + deriv_pnl
        if self.portfolio.portfolio_value > 0:
            for asset in self.assets:
                self.portfolio.weights[asset] = self.portfolio.positions[asset] / self.portfolio.portfolio_value
                self.portfolio.units[asset] = self.portfolio.positions[asset] / prices[asset] if prices[asset] > 0 else 0
        
        # Assert cash >= 0 (strictly enforced)
        if self.portfolio.cash < -0.01:
            logger.warning(
                f"REBALANCE ACCOUNTING FAILURE: Cash went negative ({self.portfolio.cash:.2f} USD). "
                f"Portfolio Value: {self.portfolio.portfolio_value:.2f}. "
                "This usually indicates transaction costs exceeded available cash during emergency de-risk. "
                "Adjusting portfolio value to absorb loss."
            )
            # portfolio_value was already set to cash + positions at line above.
            # Absorb the deficit proportionally from positions to maintain the invariant
            # pv = cash + positions after zeroing cash.
            deficit = -self.portfolio.cash
            total_pos = sum(self.portfolio.positions.values())
            if total_pos > 0:
                for asset in self.assets:
                    self.portfolio.positions[asset] -= deficit * (
                        self.portfolio.positions.get(asset, 0.0) / total_pos
                    )
            self.portfolio.cash = 0.0
            
        if self.history:
            self.history[-1]["trade_volume_usd"] = trade_vol
            
        return total_cost

    def summary(self) -> dict[str, Any]:
        if not self.history: return {}
        vals = pd.Series([h["portfolio_value"] for h in self.history])
        returns = vals.pct_change().dropna()
        ann_return = ((1 + (vals.iloc[-1]-vals.iloc[0])/vals.iloc[0]) ** (365 / len(vals)) - 1)
        ann_vol = returns.std() * np.sqrt(252)
        sharpe = (ann_return - self.risk_free_rate) / ann_vol if ann_vol > 0 else 0
        drawdowns = (vals.cummax() - vals) / vals.cummax()
        
        return {
            "total_return_pct": ((vals.iloc[-1] / vals.iloc[0]) - 1) * 100,
            "annualized_return": ann_return,
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": drawdowns.max() * 100,
            "cb_days": sum(1 for h in self.history if h["cb_level"] > 0),
            "total_trade_volume": sum(h.get("trade_volume_usd", 0) for h in self.history)
        }
