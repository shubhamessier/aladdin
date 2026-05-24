from pydantic import BaseModel
from typing import List, Tuple
from datetime import datetime

class CircuitBreakerConfig(BaseModel):
    window_seconds: int = 3600  # 1 hour
    l1_drop_threshold: float = 0.10  # 10%
    l2_drop_threshold: float = 0.20  # 20%
    l3_drop_threshold: float = 0.30  # 30%
    max_drawdown_threshold: float = 0.20 # 20%

class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.history: List[Tuple[datetime, float]] = []
        self.peak_value: float = 0.0
        self.current_level: int = 0
        
    def update(self, current_time: datetime, current_value: float) -> int:
        """
        Update the circuit breaker with the latest portfolio value.
        Returns the current circuit breaker level (0, 1, 2, 3).
        """
        if current_value > self.peak_value:
            self.peak_value = current_value
            
        self.history.append((current_time, current_value))
        
        # Prune history older than window
        cutoff_time = current_time.timestamp() - self.config.window_seconds
        self.history = [h for h in self.history if h[0].timestamp() >= cutoff_time]
        
        if not self.history:
            return self.current_level
            
        # Check window drop
        oldest_value = self.history[0][1]
        if oldest_value > 0.0:
            drop = (oldest_value - current_value) / oldest_value
            if drop >= self.config.l3_drop_threshold:
                self.current_level = max(self.current_level, 3)
            elif drop >= self.config.l2_drop_threshold:
                self.current_level = max(self.current_level, 2)
            elif drop >= self.config.l1_drop_threshold:
                self.current_level = max(self.current_level, 1)
                
        # Check maximum drawdown
        if self.peak_value > 0.0:
            drawdown = (self.peak_value - current_value) / self.peak_value
            if drawdown >= self.config.max_drawdown_threshold:
                # Trigger L2 circuit breaker for max drawdown if not already higher
                self.current_level = max(self.current_level, 2)
                
        return self.current_level
        
    def reset_level(self, manual_override: bool = False) -> None:
        """
        Decreasing CB level requires explicit guardian action (manual_override).
        Reduces the level by 1.
        """
        if manual_override and self.current_level > 0:
            self.current_level -= 1
