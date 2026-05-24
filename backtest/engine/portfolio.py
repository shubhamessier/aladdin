from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

@dataclass
class DerivativePosition:
    market: str
    direction: str
    notional_usd: float
    entry_price: float
    current_price: float
    margin_usd: float
    unrealized_pnl: float
    cumulative_funding: float
    open_date: datetime
    days_open: int = 0

@dataclass
class PortfolioState:
    timestamp: datetime
    portfolio_value: float
    cash: float
    weights: Dict[str, float] = field(default_factory=dict)
    positions: Dict[str, float] = field(default_factory=dict)  # dollar values per asset
    strategy_allocations: Dict[str, float] = field(default_factory=dict)
    derivative_positions: List[DerivativePosition] = field(default_factory=list)
