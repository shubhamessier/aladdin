import numpy as np
from pydantic import BaseModel
from dataclasses import dataclass

@dataclass
class TradeCost:
    dex_fee: float
    impact_cost: float
    permanent_impact_cost: float
    gas_cost: float
    mev_cost: float
    total: float
    total_bps: float

class CostModelConfig(BaseModel):
    # Hyperliquid blended fee (Maker 0.2bp, Taker 2.5bp -> 70/30 split ≈ 0.9bp)
    dex_fee_bps: float = 0.9
    # Slippage calibrated to $100k trade depth
    slippage_bps_per_100k: dict = {
        "ETH": 2.0,
        "BTC": 1.5,
        "SOL": 4.0,
        "USDC": 0.0,
        "USDT": 0.1,
        "DAI": 0.5,
    }
    impact_coefficient: float = 0.1
    permanent_impact_coeff: float = 0.05
    gas_cost_per_trade_usd: float = 0.50 # HyperEVM gas
    mev_threshold_usd: float = 1000000.0 # No MEV on Hyperliquid sequencer
    mev_cost_bps: float = 0.0
    twap_threshold_usd: float = 250000.0

class TransactionCostModel:
    def __init__(self, config: CostModelConfig):
        self.config = config

    def estimate_cost(
        self,
        trade_size_usd: float,
        asset: str,
        direction: str,
        pool_liquidity_usd: float,
        daily_volume_usd: float,
        asset_volatility: float = 0.03
    ) -> TradeCost:
        if trade_size_usd <= 0:
            return TradeCost(0,0,0,0,0,0,0)
            
        dex_fee = trade_size_usd * self.config.dex_fee_bps / 10000
        
        # Real-world Hyperliquid slippage approximation
        base_slippage = self.config.slippage_bps_per_100k.get(asset, 2.0)
        # Scaled by size: $1M trade has 10x impact of $100k? No, depth is non-linear.
        # Approximation: slippage_bps = base_bps * (size / 100k) ^ 0.7
        size_factor = (trade_size_usd / 100000.0) ** 0.7
        impact_bps = base_slippage * size_factor
        impact_cost = trade_size_usd * (impact_bps / 10000)
        
        permanent_cost = 0.0 # Minimal info leakage on large CLOB
        gas_cost = self.config.gas_cost_per_trade_usd
        mev_cost = 0.0
        
        total = dex_fee + impact_cost + permanent_cost + gas_cost + mev_cost
        return TradeCost(
            dex_fee=dex_fee,
            impact_cost=impact_cost,
            permanent_impact_cost=permanent_cost,
            gas_cost=gas_cost,
            mev_cost=mev_cost,
            total=total,
            total_bps=total / trade_size_usd * 10000 if trade_size_usd > 0 else 0
        )
