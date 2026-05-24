import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Any
from pydantic import BaseModel

class CircuitBreakerConfig(BaseModel):
    window_seconds: int = 3600
    l1_drop_threshold: float = 0.10
    l2_drop_threshold: float = 0.20
    l3_drop_threshold: float = 0.35
    hwm_decay_halflife_days: int = 90
    max_drawdown_threshold: float = 0.20

@dataclass
class RecoveryPhase:
    """Tracks the graduated re-entry state."""
    is_active: bool = False
    entry_date: Optional[pd.Timestamp] = None
    entry_portfolio_value: float = 0.0
    weeks_in_recovery: int = 0
    snap_back_count: int = 0
    
    @property
    def max_volatile_pct(self) -> float:
        if not self.is_active:
            return 1.0
        if self.weeks_in_recovery <= 2:
            return 0.10
        elif self.weeks_in_recovery <= 4:
            return 0.20
        elif self.weeks_in_recovery <= 6:
            return 0.35
        else:
            return 0.50
    
    @property
    def snap_back_threshold(self) -> float:
        if self.weeks_in_recovery <= 2:
            return 0.03
        elif self.weeks_in_recovery <= 4:
            return 0.05
        else:
            return 0.07
    
    def check_snap_back(self, current_value: float) -> bool:
        if not self.is_active:
            return False
        drop = (self.entry_portfolio_value - current_value) / self.entry_portfolio_value
        return drop > self.snap_back_threshold
    
    def advance_week(self) -> None:
        self.weeks_in_recovery += 1
    
    def enter(self, date: pd.Timestamp, portfolio_value: float) -> None:
        self.is_active = True
        self.entry_date = date
        self.entry_portfolio_value = portfolio_value
        self.weeks_in_recovery = 0
    
    def exit(self) -> None:
        self.is_active = False
        self.entry_date = None
    
    def snap_back(self) -> None:
        self.is_active = False
        self.snap_back_count += 1

def compute_effective_hwm(
    hwm_absolute: float,
    current_value: float,
    days_since_hwm: int,
    decay_halflife_days: int = 90,
) -> float:
    if current_value >= hwm_absolute:
        return current_value
    if days_since_hwm < 30:
        return hwm_absolute
    effective_days = days_since_hwm - 30
    decay_factor = 0.5 ** (effective_days / decay_halflife_days)
    gap = hwm_absolute - current_value
    return current_value + (gap * decay_factor)

class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.current_level: int = 0
        self.hwm_absolute: float = 0.0
        self.last_peak_time: Optional[datetime] = None
        self.cb_no_further_drop_since: Optional[datetime] = None
        self.history: List[Tuple[datetime, float]] = []

    def update(self, current_time: datetime, current_value: float, rolling_vol: float, avg_vol: float) -> int:
        if current_value > self.hwm_absolute:
            self.hwm_absolute = current_value
            self.last_peak_time = current_time
            self.cb_no_further_drop_since = current_time
            
        days_since_hwm = (current_time - self.last_peak_time).days if self.last_peak_time else 0
        eff_hwm = compute_effective_hwm(self.hwm_absolute, current_value, days_since_hwm, self.config.hwm_decay_halflife_days)
        
        drop_pct = (eff_hwm - current_value) / eff_hwm if eff_hwm > 0 else 0
        
        warranted = 0
        if drop_pct >= self.config.l3_drop_threshold: warranted = 3
        elif drop_pct >= self.config.l2_drop_threshold: warranted = 2
        elif drop_pct >= self.config.l1_drop_threshold: warranted = 1
        
        if warranted > self.current_level:
            self.current_level = warranted
            self.cb_no_further_drop_since = current_time
        elif self.current_level > warranted:
            # Check for decay
            stable_days = (current_time - self.cb_no_further_drop_since).days if self.cb_no_further_drop_since else 0
            vol_ratio = rolling_vol / max(avg_vol, 1e-6)
            
            can_decay = False
            if self.current_level == 3 and stable_days >= 14 and vol_ratio < 2.0: can_decay = True
            elif self.current_level == 2 and stable_days >= 21 and vol_ratio < 1.5: can_decay = True
            elif self.current_level == 1 and stable_days >= 14 and vol_ratio < 1.5: can_decay = True
            
            if can_decay:
                self.current_level -= 1
                
        return self.current_level
