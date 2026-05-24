from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

@dataclass
class DerivativePosition:
    symbol: str
    size: float  # Positive for long, negative for short
    entry_price: float
    current_price: float
    margin: float
    leverage: float
    is_long: bool
    
    @property
    def notional_value(self) -> float:
        return abs(self.size) * self.current_price
        
    @property
    def unrealized_pnl(self) -> float:
        price_diff = self.current_price - self.entry_price
        direction = 1.0 if self.is_long else -1.0
        return abs(self.size) * price_diff * direction

    @property
    def liquidation_price(self) -> float:
        if self.leverage <= 0:
            return 0.0
        direction = 1.0 if self.is_long else -1.0
        margin_per_unit = self.entry_price / self.leverage
        # Simple liquidation price calculation, excluding maintenance margin for simplicity
        return max(0.0, self.entry_price - (margin_per_unit * direction))

@dataclass
class PortfolioState:
    timestamp: datetime
    cash: float
    positions: Dict[str, float] = field(default_factory=dict)  # token -> amount
    derivative_positions: List[DerivativePosition] = field(default_factory=list)
    strategy_allocations: Dict[str, float] = field(default_factory=dict) # strategy -> usd value
    
    def get_total_value(self, prices: Dict[str, float]) -> float:
        value = self.cash
        for token, amount in self.positions.items():
            if token in prices:
                value += amount * prices[token]
        
        for pos in self.derivative_positions:
            if pos.symbol in prices:
                pos.current_price = prices[pos.symbol]
            # Value of a derivative is the margin posted plus unrealized PNL
            value += max(0.0, pos.margin + pos.unrealized_pnl)
            
        for strategy, alloc in self.strategy_allocations.items():
            value += alloc
            
        return value
