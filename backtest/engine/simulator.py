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
        self.pre_warmup_data: Optional[pd.DataFrame] = None
        self.current_day = 0

        self.vol_history_30d: List[float] = []
        self.avg_vol_lifetime: float = 0.02
        self.warmup_days = 60

        # Cached cadence info — set once in run()
        self.bars_per_day: int = 1
        self.is_intraday: bool = False
        self.annualization_factor: int = 365  # set in run()
        self.rebalance_interval_steps: int = 1
        self.regime_cache_steps: int = 1
        self.refit_interval_steps: int = 180
        self.var_compute_modulo: int = 1

    def load_market_data(self, price_history: pd.DataFrame) -> None:
        self.market_data = price_history

    def run(self, verbose: bool = False, pre_warmup_data: Optional[pd.DataFrame] = None) -> dict[str, Any]:
        if len(self.market_data) < 2:
            raise RuntimeError(f"Simulator: market_data has only {len(self.market_data)} rows; cannot run.")

        # Cadence: infer once and cache. Use median diff so single timestamp glitches don't poison it.
        diffs = self.market_data.index.to_series().diff().dropna()
        median_diff = diffs.median()
        if median_diff <= timedelta(hours=1):
            self.bars_per_day = int(round(timedelta(days=1) / median_diff))
            self.is_intraday = True
        else:
            self.bars_per_day = 1
            self.is_intraday = False

        # Annualization: trading-day-equivalent for crypto = 365 days * bars_per_day
        self.annualization_factor = 365 * self.bars_per_day
        # Rebalance every 7 calendar days regardless of cadence
        self.rebalance_interval_steps = 7 * self.bars_per_day
        # Regime cache: recompute every 24h
        self.regime_cache_steps = self.bars_per_day
        # HMM refit: every 60 days for intraday, every 180 days otherwise
        self.refit_interval_steps = (60 if self.is_intraday else 180) * self.bars_per_day
        # VaR: once per day
        self.var_compute_modulo = self.bars_per_day

        self.pre_warmup_data = pre_warmup_data
        
        # Performance Optimization: Precompute returns history
        if pre_warmup_data is not None and len(pre_warmup_data) > 0:
            full_data = pd.concat([pre_warmup_data, self.market_data])
            self._pre_warmup_len = len(pre_warmup_data)
        else:
            full_data = self.market_data
            self._pre_warmup_len = 0
            
        self._full_returns = full_data.pct_change().fillna(0)
        self._full_crypto_idx = self._full_returns.mean(axis=1)

        if pre_warmup_data is not None and len(pre_warmup_data) >= self.warmup_days:
            idx = self._full_crypto_idx.iloc[:self._pre_warmup_len]
            self.regime_detector.fit(idx)
            start_day = 0
        else:
            warmup_idx = self._full_crypto_idx.iloc[self._pre_warmup_len : self._pre_warmup_len + self.warmup_days]
            if len(warmup_idx) >= 60:
                self.regime_detector.fit(warmup_idx)
            start_day = self.warmup_days

        for day in range(start_day, len(self.market_data)):
            self.current_day = day
            self.step()

            if day >= self.warmup_days and day % self.refit_interval_steps == 0:
                lookback_bars = 504 * self.bars_per_day
                lookback = self.market_data.iloc[max(0, day-lookback_bars):day]
                returns = lookback.pct_change().fillna(0).mean(axis=1)
                if len(returns) >= self.regime_detector.min_observations:
                    self.regime_detector.refit_rolling(returns)

        return self.summary()

    def step(self) -> None:
        date = self.market_data.index[self.current_day]
        prices = self.market_data.iloc[self.current_day]

        # Hard validate price row before anything else.
        if prices.isna().any() or (prices <= 0).any():
            raise RuntimeError(
                f"simulator.step at {date} (idx={self.current_day}): "
                f"invalid prices {prices.to_dict()}"
            )

        # 1. Mark to Market
        if self.current_day > 0:
            new_val = self.portfolio.cash
            for asset in self.assets:
                val = self.portfolio.units[asset] * prices[asset]
                self.portfolio.positions[asset] = val
                new_val += val

            # MTM for derivatives. Margin was deducted from cash at open, so it now lives
            # inside the position. Total equity contribution per derivative = margin + unrealized.
            for pos in self.portfolio.derivative_positions:
                token = pos.market.replace("-PERP", "")
                if token in prices and pos.entry_price > 0:
                    sign = 1.0 if pos.direction == "long" else -1.0
                    pos.unrealized_pnl = pos.notional_usd * (prices[token] / pos.entry_price - 1.0) * sign
                    new_val += pos.margin_usd + pos.unrealized_pnl

            if not np.isfinite(new_val):
                raise RuntimeError(
                    f"simulator.step at {date}: MTM produced non-finite portfolio_value. "
                    f"cash={self.portfolio.cash}, positions={self.portfolio.positions}, "
                    f"prices={prices.to_dict()}"
                )

            self.portfolio.portfolio_value = new_val
            if self.portfolio.portfolio_value > 0:
                for asset in self.assets:
                    self.portfolio.weights[asset] = self.portfolio.positions[asset] / self.portfolio.portfolio_value

        # 2. Risk & Regime
        lookback_bars = 252 * max(1, self.bars_per_day)
        slice_end = self._pre_warmup_len + self.current_day
        slice_start = max(0, slice_end - lookback_bars)
        
        returns_history = self._full_returns.iloc[slice_start:slice_end]
        crypto_idx_window = self._full_crypto_idx.iloc[slice_start:slice_end]

        if len(returns_history) > 10:
            rolling_vol = float(crypto_idx_window.tail(30 * self.bars_per_day).std())
            update_regime = (self.current_day % self.regime_cache_steps == 0)
            if update_regime or not hasattr(self, '_cached_regime_pred'):
                pred_window = crypto_idx_window.tail(30 * self.bars_per_day)
                regime_pred = self.regime_detector.predict(pred_window)
                self._cached_regime_pred = regime_pred
            else:
                regime_pred = self._cached_regime_pred
        else:
            regime_pred = RegimePrediction(current_regime="uncertain", confidence=0.5, crisis_probability_3step=0.1, regime_probabilities={}, transition_probabilities={})
            rolling_vol = 0.02

        if not np.isfinite(rolling_vol) or rolling_vol < 0:
            rolling_vol = 0.02
        self.vol_history_30d.append(rolling_vol)
        # Use a rolling mean of last 90 days, not lifetime
        recent_window = self.bars_per_day * 90
        if len(self.vol_history_30d) > recent_window:
            self.avg_vol_lifetime = float(np.mean(self.vol_history_30d[-recent_window:]))
        else:
            self.avg_vol_lifetime = float(np.mean(self.vol_history_30d))
        
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
            self.recovery.weeks_in_recovery = days_in // 7

            if days_in >= 49:
                self.recovery.exit()

            if self.recovery.check_further_decline(self.portfolio.portfolio_value):
                self.recovery.reset_recovery()
                self.cb.current_level = 2
                current_cb_level = 2

        # 5. Yield (lending on stables, funding on perps)
        daily_yield = self.yield_engine.calculate_yield(
            self.portfolio.portfolio_value,
            self.portfolio.weights,
            pd.Timestamp(date),
            regime_pred.current_regime,
            self.portfolio.derivative_positions,
        )
        step_yield = daily_yield / max(1, self.bars_per_day)
        if not np.isfinite(step_yield):
            step_yield = 0.0
        self.portfolio.portfolio_value += step_yield
        self.portfolio.cash += step_yield

        # 5b. Perp step: funding accrual to cash, MTM, liquidation check, force-close if blown up.
        prices_dict = prices.to_dict()
        funding_pnl = self.hedger.advance_step(
            portfolio=self.portfolio,
            prices=prices_dict,
            date=pd.Timestamp(date),
            bars_per_day=self.bars_per_day,
            regime=regime_pred.current_regime,
            yield_engine=self.yield_engine,
        )

        # 6. Rebalance
        is_emergency = current_cb_level >= 2
        was_emergency = prev_cb_level >= 2
        entered_emergency = is_emergency and not was_emergency
        is_scheduled = (self.current_day % self.rebalance_interval_steps == 0)

        # Append history row BEFORE rebalance so trade volume can be assigned to it.
        var_val = self.history[-1]["var_95_1d"] if self.history else 0.0
        jump_var_val = self.history[-1]["jump_var_95_1d"] if self.history else 0.0
        compute_var = len(returns_history) > 30 and (self.current_day % self.var_compute_modulo == 0)
        if compute_var and self.portfolio.portfolio_value > 0:
            weights_arr = np.array([self.portfolio.weights.get(a, 0.0) for a in self.assets])
            try:
                var_95, _ = compute_historical_var(returns_history.values, weights_arr)
                var_val = var_95 * self.portfolio.portfolio_value
            except Exception:
                pass
            try:
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
            "trade_volume_usd": 0.0,
            "funding_pnl_step": funding_pnl,
        })

        if is_scheduled or entered_emergency:
            self._execute_rebalance(prices, returns_history, current_cb_level, regime_pred.current_regime, rolling_vol)

        # 7. After rebalance, the hedger sets target hedges; do this AFTER spot rebalance so
        # the target depends on the updated spot exposure.
        if is_scheduled or entered_emergency:
            self.hedger.set_target_hedges(
                portfolio=self.portfolio,
                prices=prices_dict,
                regime=regime_pred.current_regime,
                date=pd.Timestamp(date),
                cost_model=self.cost_model,
                rolling_vol=rolling_vol,
            )

    def _execute_rebalance(self, prices: pd.Series, returns_history: pd.DataFrame, cb_level: int, regime: str, rolling_vol: float) -> float:
        # Decide target weights. Emergency: 100% to first available stable.
        if cb_level >= 2:
            target_weights = {a: 0.0 for a in self.assets}
            picked = False
            for stable in ["USDC", "USDT", "DAI"]:
                if stable in self.assets:
                    target_weights[stable] = 1.0
                    picked = True
                    break
            if not picked:
                # No stable in universe — preserve current weights rather than drain.
                logger.warning(f"Emergency rebalance but no stable in universe; keeping current weights")
                target_weights = dict(self.portfolio.weights)
        else:
            try:
                if len(returns_history) > 20:
                    cov = build_covariance(returns_history)
                    max_vol_override = self.recovery.max_volatile_pct if self.recovery.is_active else None

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
                        current_regime=regime,
                        historical_returns=returns_history
                    )
                else:
                    target_weights = {a: 1.0/len(self.assets) for a in self.assets}
            except Exception as e:
                logger.error(f"Rebalance strategy call failed: {e}")
                target_weights = {a: 1.0/len(self.assets) for a in self.assets}

        # Normalize and validate target weights
        w_sum = sum(target_weights.get(a, 0.0) for a in self.assets)
        if w_sum <= 0 or not np.isfinite(w_sum):
            logger.warning(f"Invalid target weights sum={w_sum}; falling back to equal weight")
            target_weights = {a: 1.0/len(self.assets) for a in self.assets}
        else:
            target_weights = {a: target_weights.get(a, 0.0) / w_sum for a in self.assets}

        # Reserve a fraction of portfolio for hedge margin (so set_target_hedges has cash).
        # margin_reserve = hedge_ratio * volatile_weight / leverage
        try:
            hr = self.hedger.config.regime_hedge_ratios.get(regime, 0.5)
            lev = max(1.0, self.hedger.config.target_leverage)
        except Exception:
            hr, lev = 0.5, 3.0
        volatile_weight_target = sum(w for a, w in target_weights.items() if a not in ("USDC", "USDT", "DAI"))
        margin_reserve_frac = min(0.30, (hr * volatile_weight_target) / lev * 1.20)  # 20% headroom; cap 30%
        if margin_reserve_frac > 0:
            scale = 1.0 - margin_reserve_frac
            target_weights = {a: w * scale for a, w in target_weights.items()}

        total_cost = 0.0
        trade_vol = 0.0
        is_emergency = cb_level >= 2

        # Per-asset volatility (annualized) from the returns_history we already computed
        per_asset_vol = {}
        if len(returns_history) > 10:
            stds = returns_history.std() * np.sqrt(max(1, self.annualization_factor))
            for a in self.assets:
                v = stds.get(a, np.nan)
                per_asset_vol[a] = float(v) if np.isfinite(v) else 0.30  # default crypto-vol
        else:
            per_asset_vol = {a: 0.30 for a in self.assets}

        for asset in self.assets:
            old_val = self.portfolio.positions.get(asset, 0.0)
            new_target_val = self.portfolio.portfolio_value * target_weights.get(asset, 0.0)
            delta = new_target_val - old_val
            if abs(delta) < 1.0:
                continue

            direction = "buy" if delta > 0 else "sell"
            trade_size = abs(delta)

            book_depth = self.depth_by_asset.get(asset, 5_000_000)
            vol_haircut = max(0.1, 1.0 - 5.0 * rolling_vol)
            effective_depth = book_depth * vol_haircut

            cost = self.cost_model.estimate_cost(
                trade_size_usd=trade_size,
                asset=asset,
                direction=direction,
                book_depth_usd=effective_depth,
                daily_volume_usd=1e7,
                asset_volatility=per_asset_vol.get(asset, 0.30),
                is_emergency=is_emergency,
            )

            actual_delta = delta * cost.fill_ratio
            self.portfolio.positions[asset] = old_val + actual_delta
            # Buy: cash -= actual_delta + cost (actual_delta > 0 → cash down)
            # Sell: cash -= actual_delta (negative) - cost → cash up minus cost
            self.portfolio.cash -= (actual_delta + cost.total)

            total_cost += cost.total
            trade_vol += abs(actual_delta)

        # Re-derive portfolio_value from ground-truth state.
        deriv_equity = sum(p.margin_usd + p.unrealized_pnl for p in self.portfolio.derivative_positions)
        self.portfolio.portfolio_value = self.portfolio.cash + sum(self.portfolio.positions.values()) + deriv_equity

        if not np.isfinite(self.portfolio.portfolio_value):
            raise RuntimeError(
                f"_execute_rebalance produced non-finite portfolio_value at idx={self.current_day}. "
                f"cash={self.portfolio.cash}, positions={self.portfolio.positions}, deriv_pnl={deriv_unrealized}"
            )

        if self.portfolio.portfolio_value > 0:
            for asset in self.assets:
                self.portfolio.weights[asset] = self.portfolio.positions[asset] / self.portfolio.portfolio_value
                self.portfolio.units[asset] = self.portfolio.positions[asset] / prices[asset] if prices[asset] > 0 else 0.0

        # Cash invariant: if execution costs drove cash slightly negative, absorb proportionally.
        if self.portfolio.cash < -0.01:
            deficit = -self.portfolio.cash
            total_pos = sum(self.portfolio.positions.values())
            if total_pos > 0:
                for asset in self.assets:
                    self.portfolio.positions[asset] -= deficit * (self.portfolio.positions.get(asset, 0.0) / total_pos)
                # Re-derive units to stay consistent
                for asset in self.assets:
                    self.portfolio.units[asset] = self.portfolio.positions[asset] / prices[asset] if prices[asset] > 0 else 0.0
            self.portfolio.cash = 0.0

        # Record trade volume. history was appended in step() *before* this call so it's never empty.
        if self.history:
            self.history[-1]["trade_volume_usd"] = trade_vol
            self.history[-1]["portfolio_value"] = self.portfolio.portfolio_value
            self.history[-1]["cash"] = self.portfolio.cash

        return total_cost

    def summary(self) -> dict[str, Any]:
        if not self.history: return {}
        vals = pd.Series([h["portfolio_value"] for h in self.history])
        if len(vals) < 2 or vals.iloc[0] <= 0:
            return {"total_return_pct": 0.0, "annualized_return": 0.0, "annualized_volatility": 0.0,
                    "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "cb_days": 0, "total_trade_volume": 0.0}
        returns = vals.pct_change().dropna()
        ann = max(1, self.annualization_factor)
        # Use log-return CAGR
        total_ret = float(vals.iloc[-1] / vals.iloc[0])
        n_steps = len(vals)
        ann_return = total_ret ** (ann / n_steps) - 1.0 if total_ret > 0 else -1.0
        ann_vol = float(returns.std() * np.sqrt(ann))
        sharpe = (ann_return - self.risk_free_rate) / ann_vol if ann_vol > 0 else 0.0
        drawdowns = (vals.cummax() - vals) / vals.cummax()

        return {
            "total_return_pct": (total_ret - 1.0) * 100.0,
            "annualized_return": ann_return,
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": float(drawdowns.max()) * 100.0,
            "cb_days": int(sum(1 for h in self.history if h["cb_level"] > 0) / max(1, self.bars_per_day)),
            "total_trade_volume": float(sum(h.get("trade_volume_usd", 0) for h in self.history)),
            "n_steps": n_steps,
            "annualization_factor": ann,
        }
