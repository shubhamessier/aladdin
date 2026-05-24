from pydantic import BaseModel
from typing import Dict, Tuple
from .portfolio import PortfolioState

class YieldConfig(BaseModel):
    lending_rates: Dict[str, float] = {}  # token -> Annualized Rate (e.g. 0.05 for 5%)
    staking_rates: Dict[str, float] = {}  # token -> Annualized Rate
    funding_rates: Dict[str, float] = {}  # symbol -> Daily funding rate (e.g. 0.0001 for 1 bps)
    basis_rates: Dict[str, float] = {}    # symbol -> Annualized basis yield

class YieldEngine:
    def __init__(self, config: YieldConfig):
        self.config = config
        
    def simulate_yield(
        self, 
        state: PortfolioState, 
        prices: Dict[str, float], 
        dt_days: float
    ) -> Tuple[Dict[str, float], float]:
        """
        Simulates yield over dt_days.
        Returns:
            token_yields: Dict[str, float] of yield in token amounts
            usd_yield: float of yield that is directly in USD (like funding P&L)
        """
        token_yields: Dict[str, float] = {}
        usd_yield = 0.0
        
        # 1. Lending Yield for Cash (assuming cash is USD/USDC)
        cash_rate = self.config.lending_rates.get("USDC", 0.0)
        if cash_rate > 0.0:
            usd_yield += state.cash * (cash_rate * dt_days / 365.0)
            
        # 2. Staking/Lending Yield for Positions
        for token, amount in state.positions.items():
            rate = self.config.staking_rates.get(token, self.config.lending_rates.get(token, 0.0))
            if rate > 0.0:
                token_yields[token] = amount * (rate * dt_days / 365.0)
                
        # 3. Funding & Basis P&L for Derivatives
        for pos in state.derivative_positions:
            # Funding (paid/received in USD)
            # If long, pay funding. If short, receive funding. 
            # Note: positive rate -> longs pay shorts.
            daily_funding = self.config.funding_rates.get(pos.symbol, 0.0)
            direction = 1.0 if pos.is_long else -1.0
            funding_pnl = pos.notional_value * daily_funding * dt_days * (-direction)
            usd_yield += funding_pnl
            
            # Basis P&L (if we're running a basis trade)
            # Typically a basis trade is long spot, short perp. The yield is the convergence.
            basis_rate = self.config.basis_rates.get(pos.symbol, 0.0)
            if basis_rate > 0.0 and not pos.is_long:
                basis_pnl = pos.notional_value * (basis_rate * dt_days / 365.0)
                usd_yield += basis_pnl
                
        return token_yields, usd_yield
