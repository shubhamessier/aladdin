from pydantic import BaseModel
from typing import Dict, List, Any
from .portfolio import PortfolioState

class HedgingConfig(BaseModel):
    # Regime to hedge ratio (e.g. 'bull': 0.2, 'uncertain': 0.5, 'crisis': 0.8)
    regime_hedge_ratios: Dict[str, float] = {"bull": 0.2, "uncertain": 0.5, "crisis": 0.8}
    target_leverage: float = 2.0
    basis_trade_allocation_pct: float = 0.10  # Max 10% of portfolio for basis trades
    min_hedge_adjustment_usd: float = 1000.0  # Avoid micro adjustments

class HedgingEngine:
    def __init__(self, config: HedgingConfig):
        self.config = config
        
    def calculate_hedge_adjustments(
        self,
        state: PortfolioState,
        prices: Dict[str, float],
        current_regime: str
    ) -> List[Dict[str, Any]]:
        """
        Calculate required derivative position adjustments to maintain target delta.
        Returns a list of dicts describing actions.
        """
        target_ratio = self.config.regime_hedge_ratios.get(current_regime, 0.5)
        
        # 1. Calculate current spot delta (exposure)
        spot_delta_usd: Dict[str, float] = {}
        for token, amount in state.positions.items():
            # Exclude stablecoins from hedging
            if token in prices and token not in ["USDC", "USDT", "DAI"]:
                # amount is already in USD per PortfolioState definition
                spot_delta_usd[token] = amount
                
        # 2. Calculate current derivative delta
        deriv_delta_usd: Dict[str, float] = {}
        for pos in state.derivative_positions:
            token = pos.market.replace("-PERP", "") 
            direction = 1.0 if pos.direction == "long" else -1.0
            delta = pos.notional_usd * direction
            deriv_delta_usd[token] = deriv_delta_usd.get(token, 0.0) + delta
            
        # 3. Calculate target derivative delta and adjustments
        actions = []
        for token, spot_exposure in spot_delta_usd.items():
            # We want to hedge a ratio of the spot exposure. Hedge is short (negative delta).
            target_deriv_delta = - (spot_exposure * target_ratio)
            current_deriv_delta = deriv_delta_usd.get(token, 0.0)
            
            delta_diff = target_deriv_delta - current_deriv_delta
            
            if abs(delta_diff) > self.config.min_hedge_adjustment_usd:
                actions.append({
                    "action": "adjust_hedge",
                    "symbol": f"{token}-PERP",
                    "delta_adjustment_usd": delta_diff, # negative means short more
                    "target_leverage": self.config.target_leverage
                })
                
        return actions

    def calculate_basis_trades(
        self,
        state: PortfolioState,
        prices: Dict[str, float],
        funding_rates: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """
        Identify opportunities for basis trades (long spot + short perp) if funding is highly positive.
        """
        actions = []
        total_value = state.get_total_value(prices)
        max_basis_capital = total_value * self.config.basis_trade_allocation_pct
        
        # Simple logic: if annualized funding > 10%, allocate capital.
        for symbol, daily_rate in funding_rates.items():
            annualized_rate = daily_rate * 365.0
            if annualized_rate > 0.10: 
                token = symbol.replace("-PERP", "")
                if token in prices:
                    actions.append({
                        "action": "open_basis_trade",
                        "symbol": symbol,
                        "token": token,
                        "capital_to_allocate": max_basis_capital / max(1, len(funding_rates)),
                        "annualized_yield_estimate": annualized_rate
                    })
        return actions
