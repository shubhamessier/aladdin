import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import numpy as np
import pandas as pd

# Add python-risk to path to allow importing risk_engine
risk_engine_path = Path(__file__).resolve().parent.parent.parent / "python-risk"
if str(risk_engine_path) not in sys.path:
    sys.path.append(str(risk_engine_path))

from .portfolio import PortfolioState, DerivativePosition
from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from .yield_engine import YieldEngine, YieldConfig
from .hedger import HedgingEngine, HedgingConfig

# Import Risk Engine Models
from risk_engine.regime_detector import RegimeDetector
from risk_engine.portfolio_optimizer import optimize_risk_parity
from risk_engine.schemas import TierConstraint
from risk_engine.var_models import compute_historical_var

class TreasurySimulator:
    def __init__(
        self,
        initial_cash: float,
        start_date: datetime,
        end_date: datetime,
        assets: List[str],
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        yield_config: Optional[YieldConfig] = None,
        hedger_config: Optional[HedgingConfig] = None
    ):
        self.state = PortfolioState(timestamp=start_date, cash=initial_cash)
        self.start_date = start_date
        self.end_date = end_date
        self.assets = assets
        self.current_date = start_date
        
        self.circuit_breaker = CircuitBreaker(circuit_breaker_config or CircuitBreakerConfig())
        self.yield_engine = YieldEngine(yield_config or YieldConfig())
        self.hedging_engine = HedgingEngine(hedger_config or HedgingConfig())
        self.regime_detector = RegimeDetector()
        
        self.history: List[Dict[str, Any]] = []
        self.market_prices: Dict[str, float] = {}
        self.price_history_df: pd.DataFrame = pd.DataFrame()
        
        self.current_regime: str = "uncertain"
        self.cb_level: int = 0
        
        self._target_weights: Dict[str, float] = {}

    def load_market_data(self, price_history: pd.DataFrame):
        """
        price_history should be a DataFrame indexed by datetime with asset symbols as columns.
        """
        self.price_history_df = price_history
        # Pre-fit regime detector on a proxy (e.g. BTC or equal weight) if history is available
        if not price_history.empty and len(price_history) > 60:
            returns = price_history.pct_change().dropna().mean(axis=1) # Simple broad index
            self.regime_detector.fit(returns)

    def run(self) -> dict[str, Any]:
        """
        Executes the 11-phase daily simulation loop.
        """
        while self.current_date <= self.end_date:
            self.step()
            self.current_date += timedelta(days=1)
            
    def step(self):
        """
        The 11-phase loop for a single timestep.
        """
        # Phase 1: Time Advancement
        self.state.timestamp = self.current_date
        
        # Phase 2: Market Data Update
        self._update_market_data()
        
        # Phase 3: State Snapshot / Mark-to-Market
        portfolio_value = self.state.get_total_value(self.market_prices)
        
        # Phase 4: Risk Assessment (VaR / Covariance)
        var_95, cov_matrix = self._assess_risk(portfolio_value)
        
        # Phase 5: Regime Detection
        self._detect_regime()
        
        # Phase 6: Circuit Breaker Check
        self.cb_level = self.circuit_breaker.update(self.current_date, portfolio_value)
        
        # Phase 7: Yield Harvesting
        self._harvest_yield()
        
        # Phase 8: Hedging Rebalancing
        hedge_actions = []
        if self.cb_level < 2:  # Only hedge actively if not in deep circuit breaker
            hedge_actions = self.hedging_engine.calculate_hedge_adjustments(
                self.state, self.market_prices, self.current_regime
            )
            self._execute_hedges(hedge_actions)
            
        # Phase 9: Portfolio Optimization (Target weights)
        if self.cb_level == 0:
            self._optimize_portfolio(cov_matrix)
        elif self.cb_level >= 2:
            # Shift towards cash/stables in crisis
            self._target_weights = {asset: 0.0 for asset in self.assets}
            self._target_weights["USDC"] = 1.0
            
        # Phase 10: Trade Execution (Rebalancing to targets)
        trade_volume = self._execute_trades(portfolio_value)
        
        # Phase 11: Logging / Reporting
        new_value = self.state.get_total_value(self.market_prices)
        self.history.append({
            "timestamp": self.current_date,
            "portfolio_value": new_value,
            "cash": self.state.cash,
            "regime": self.current_regime,
            "cb_level": self.cb_level,
            "var_95_1d": var_95,
            "trade_volume_usd": trade_volume
        })

    def _update_market_data(self):
        if self.current_date in self.price_history_df.index:
            row = self.price_history_df.loc[self.current_date]
            for asset in self.assets:
                if asset in row:
                    self.market_prices[asset] = float(row[asset])

    def _assess_risk(self, portfolio_value: float) -> tuple[float, np.ndarray]:
        # Need at least 30 days of history up to current date
        history_slice = self.price_history_df.loc[:self.current_date]
        var_95 = 0.0
        cov_matrix = np.eye(len(self.assets))
        
        if len(history_slice) > 30:
            returns = history_slice[self.assets].pct_change().dropna()
            cov_matrix = returns.cov().values
            
            # Use Risk Engine for VaR
            # To compute historical var, we need an array of historical returns for the portfolio.
            # Simplified: assuming current weights to compute portfolio historical returns
            weights = np.zeros(len(self.assets))
            for i, asset in enumerate(self.assets):
                if asset in self.state.positions and self.market_prices.get(asset, 0) > 0:
                    weights[i] = (self.state.positions[asset] * self.market_prices[asset]) / max(1, portfolio_value)
                    
            if len(returns) > 0:
                port_returns = returns.values @ weights
                # Assuming var_models interface: compute_historical_var(returns, value) -> dict
                try:
                    # Depending on exact sig, typically: compute_historical_var(returns, current_value)
                    # Let's approximate using numpy percentiles since we can't inspect the exact var_models sig easily
                    losses = -port_returns
                    var_95 = float(np.percentile(losses, 95)) * portfolio_value
                except Exception:
                    pass
                    
        return var_95, cov_matrix

    def _detect_regime(self) -> None:
        history_slice = self.price_history_df.loc[:self.current_date]
        if len(history_slice) > 30 and self.regime_detector.model is not None:
            returns = history_slice.pct_change().dropna().mean(axis=1)
            try:
                prediction = self.regime_detector.predict(returns)
                self.current_regime = prediction.current_regime
            except Exception:
                pass

    def _harvest_yield(self) -> None:
        token_yields, usd_yield = self.yield_engine.simulate_yield(
            self.state, self.market_prices, dt_days=1.0
        )
        self.state.cash += usd_yield
        for token, amt in token_yields.items():
            self.state.positions[token] = self.state.positions.get(token, 0.0) + amt

    def _execute_hedges(self, hedge_actions: List[Dict[str, Any]]):
        # Simplified execution of hedge requests
        for action in hedge_actions:
            if action["action"] == "adjust_hedge":
                symbol = action["symbol"]
                adj_usd = action["delta_adjustment_usd"]
                # For simplicity in simulation, just adjust derivative position size
                # Positive delta = Long, Negative delta = Short
                current_price = self.market_prices.get(symbol.replace("-PERP", ""), 1.0)
                size_change = adj_usd / current_price
                
                # Find existing position
                pos = next((p for p in self.state.derivative_positions if p.symbol == symbol), None)
                if pos:
                    pos.size += size_change
                else:
                    self.state.derivative_positions.append(DerivativePosition(
                        symbol=symbol,
                        size=size_change,
                        entry_price=current_price,
                        current_price=current_price,
                        margin=abs(adj_usd) / action.get("target_leverage", 1.0),
                        leverage=action.get("target_leverage", 1.0),
                        is_long=(size_change > 0)
                    ))
                    # Deduct margin from cash
                    self.state.cash -= abs(adj_usd) / action.get("target_leverage", 1.0)

    def _optimize_portfolio(self, cov_matrix: np.ndarray):
        bounds = [(0.0, 1.0) for _ in self.assets]
        # Example constraints: max 20% in any volatile asset
        tier_constraints = [TierConstraint(
            asset_indices=[i for i, a in enumerate(self.assets) if a not in ["USDC", "USDT", "DAI"]],
            min_total=0.0,
            max_total=0.8
        )]
        try:
            result = optimize_risk_parity(
                covariance=cov_matrix,
                bounds=bounds,
                tier_constraints=tier_constraints
            )
            if result.converged:
                for i, asset in enumerate(self.assets):
                    self._target_weights[asset] = result.weights[i]
        except Exception:
            pass # Keep previous target weights if optimization fails

    def _execute_trades(self, portfolio_value: float) -> float:
        trade_volume = 0.0
        if not self._target_weights:
            return trade_volume
            
        current_weights = {}
        for asset in self.assets:
            if asset in self.state.positions and self.market_prices.get(asset, 0) > 0:
                current_weights[asset] = (self.state.positions[asset] * self.market_prices[asset]) / portfolio_value
            else:
                current_weights[asset] = 0.0
                
        # Calculate target values and execute
        for asset, target_w in self._target_weights.items():
            if asset == "USDC":
                continue # Cash handled implicitly
                
            current_val = current_weights.get(asset, 0.0) * portfolio_value
            target_val = target_w * portfolio_value
            diff_usd = target_val - current_val
            
            if abs(diff_usd) > 1000 and asset in self.market_prices: # Only trade if > $1k diff
                price = self.market_prices[asset]
                trade_size = diff_usd / price
                
                # Apply 10bps slippage/fee
                cost = abs(diff_usd) * 0.001 
                
                self.state.positions[asset] = self.state.positions.get(asset, 0.0) + trade_size
                self.state.cash -= (diff_usd + cost)
                trade_volume += abs(diff_usd)
                
        return trade_volume
