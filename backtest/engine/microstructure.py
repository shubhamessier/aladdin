import numpy as np
import pandas as pd
from typing import Dict, Any

class L2MicrostructureSimulator:
    """
    Simulates Central Limit Order Book (CLOB) dynamics using Daily OHLCV data.
    Generates synthetic intraday queue states, spread dynamics, and fill probabilities.
    """
    def __init__(self, base_spread_bps: float = 2.0, base_depth_usd: float = 1e6):
        self.base_spread_bps = base_spread_bps
        self.base_depth_usd = base_depth_usd

    def simulate_execution(
        self,
        target_size_usd: float,
        asset: str,
        daily_volatility: float,
        daily_volume: float,
        is_maker: bool = False
    ) -> Dict[str, float]:
        """
        Simulate an execution against the L2 orderbook.
        Returns the effective slippage and fill ratio.
        """
        if target_size_usd <= 0:
            return {"slippage_bps": 0.0, "fill_ratio": 1.0, "toxicity_bps": 0.0}

        # 1. Spread & Depth Expansion
        # During high volatility, market makers pull liquidity.
        # Spread widens linearly with vol; Depth decays exponentially.
        vol_multiplier = max(1.0, daily_volatility / 0.03)
        current_spread = self.base_spread_bps * vol_multiplier
        current_depth = self.base_depth_usd / (vol_multiplier ** 2)

        slippage_bps = 0.0
        fill_ratio = 1.0
        toxicity_bps = 0.0

        if is_maker:
            # Maker Order Simulation (Queue Aging & Adverse Selection)
            # You only get filled if the market crosses the spread (adverse selection)
            # or through random noise trading.
            
            # Probability of getting filled by uninformed flow
            prob_uninformed_fill = np.exp(-target_size_usd / (current_depth * 0.1))
            
            # Probability of toxic fill (market crashes through your bid)
            prob_toxic = 1.0 - np.exp(-vol_multiplier * 0.5)

            if np.random.random() < prob_toxic:
                # Toxic fill: We capture the maker rebate but suffer immediate adverse selection
                toxicity_bps = current_spread * 2.0  # Market moves against us by 2x spread
                fill_ratio = 1.0 # Toxic flow always fills you completely
            else:
                # Normal fill
                fill_ratio = prob_uninformed_fill
                toxicity_bps = 0.0
        else:
            # Taker Order Simulation (Walking the Book)
            # Pay half the spread immediately
            slippage_bps += current_spread / 2.0
            
            # Market Impact (Walking the book)
            if target_size_usd > current_depth:
                # Exhausted top of book, walk into thinner liquidity
                excess = target_size_usd - current_depth
                impact = (excess / current_depth) * current_spread * 1.5
                slippage_bps += impact
            
            # Takers always get 100% fill if they accept the slippage (assuming sufficient overall ADV)
            fill_ratio = 1.0

        return {
            "slippage_bps": slippage_bps,
            "fill_ratio": fill_ratio,
            "toxicity_bps": toxicity_bps
        }
