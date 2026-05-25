import numpy as np
import pandas as pd
from typing import Dict, List, Any
import logging

class Order:
    def __init__(self, order_id: str, asset: str, side: str, price: float, size: float, is_maker: bool):
        self.order_id = order_id
        self.asset = asset
        self.side = side
        self.price = price
        self.size = size
        self.remaining_size = size
        self.is_maker = is_maker
        self.status = "OPEN"
        self.timestamp = None

class EventDrivenReplayEngine:
    """
    Simulates tick-level market microstructure over a historical L2 orderbook.
    Replays queues, partial fills, latency, and liquidation cascades.
    """
    def __init__(self, latency_ms: int = 150):
        self.latency_ms = latency_ms
        self.active_orders: Dict[str, Order] = {}
        self.execution_log: List[Dict[str, Any]] = []

    def inject_latency(self) -> float:
        """Simulate stochastic network and matching engine jitter."""
        jitter = np.random.exponential(scale=self.latency_ms * 0.2)
        return self.latency_ms + jitter

    def process_order(self, order: Order, current_book: Dict[str, Any], current_vpin: float) -> None:
        """
        Process an order against the L2 snapshot.
        """
        latency = self.inject_latency()
        order.timestamp = current_book['timestamp'] + latency
        
        # Determine toxicity
        is_toxic = current_vpin > 0.7
        
        if order.is_maker:
            if is_toxic:
                # Toxic sweep: we get filled but the market moves through our price
                fill_price = order.price
                post_fill_drift = order.price * 0.0005 # 5 bps adverse drift
                realized_price = fill_price + post_fill_drift if order.side == "buy" else fill_price - post_fill_drift
                self.record_fill(order, order.size, realized_price, is_toxic=True)
            else:
                # Normal queue aging
                # Simplified: Assume 50% probability of fill at the touch if not toxic
                if np.random.rand() > 0.5:
                    self.record_fill(order, order.size, order.price, is_toxic=False)
        else:
            # Taker: Walk the book
            remaining = order.size
            total_cost = 0.0
            levels = current_book['asks'] if order.side == "buy" else current_book['bids']
            
            for px, sz in levels:
                if remaining <= 0:
                    break
                fill_sz = min(remaining, sz)
                total_cost += fill_sz * px
                remaining -= fill_sz
                
            if remaining < order.size:
                avg_price = total_cost / (order.size - remaining)
                self.record_fill(order, order.size - remaining, avg_price, is_toxic=False)
            
            if remaining > 0:
                # Partial fill
                order.remaining_size = remaining
                order.status = "PARTIAL"

    def record_fill(self, order: Order, fill_size: float, fill_price: float, is_toxic: bool):
        fee_bps = -0.2 if order.is_maker else 3.5
        fee_cost = (fill_size * fill_price) * (fee_bps / 10000)
        
        self.execution_log.append({
            "order_id": order.order_id,
            "asset": order.asset,
            "side": order.side,
            "fill_size": fill_size,
            "fill_price": fill_price,
            "fee_paid": fee_cost,
            "is_maker": order.is_maker,
            "is_toxic": is_toxic,
            "timestamp": order.timestamp
        })
        order.remaining_size -= fill_size
        if order.remaining_size <= 0:
            order.status = "FILLED"

    def get_execution_summary(self) -> pd.DataFrame:
        return pd.DataFrame(self.execution_log)
