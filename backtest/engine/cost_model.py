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
    latency_cost: float
    toxicity_cost: float
    total: float
    total_bps: float
    fill_ratio: float

class CostModelConfig(BaseModel):
    # Hyperliquid actual fees
    maker_fee_bps: float = 0.2
    taker_fee_bps: float = 3.5
    maker_fraction_normal: float = 0.70   # 70% maker on scheduled rebalances
    maker_fraction_emergency: float = 0.0 # 100% taker on CB-triggered de-risk
    
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
    base_latency_ms: float = 150.0
    latency_penalty_bps_per_100ms: float = 1.5
    toxicity_alpha: float = 0.15 # 15% probability of toxic fill taking 5bps

class TransactionCostModel:
    def __init__(self, config: CostModelConfig):
        self.config = config

    def estimate_cost(
        self,
        trade_size_usd: float,
        asset: str,
        direction: str,
        book_depth_usd: float = 5_000_000, # Realistic HL depth
        daily_volume_usd: float = 1e7,
        asset_volatility: float = 0.03,
        is_emergency: bool = False
    ) -> TradeCost:
        if trade_size_usd <= 0:
            return TradeCost(0,0,0,0,0,0,0,0,0,1.0)
            
        maker_frac = 0.0 if is_emergency else self.config.maker_fraction_normal
        taker_frac = 1.0 - maker_frac
        
        blended_fee_bps = (
            maker_frac * self.config.maker_fee_bps +
            taker_frac * self.config.taker_fee_bps
        )
        dex_fee = trade_size_usd * blended_fee_bps / 10000
        
        # Real-world Hyperliquid slippage approximation
        base_slippage = self.config.slippage_bps_per_100k.get(asset, 2.0)
        
        # Sell during downturn crosses spread against you
        direction_multiplier = 1.3 if direction == "sell" and is_emergency else 1.0
        
        # Scaled by size: nonlinear depth
        size_factor = (trade_size_usd / 100000.0) ** 0.7
        vol_multiplier = max(1.0, asset_volatility / 0.03) * direction_multiplier
        
        # FLAW-04: USDC/USDT have near-zero slippage
        if asset in ["USDC", "USDT", "DAI"]:
            vol_multiplier *= 0.1
            
        # Nonlinear depth: slippage explodes when trade exceeds available depth
        depth_ratio = trade_size_usd / max(book_depth_usd, 1.0)
        if depth_ratio > 0.5:
            vol_multiplier *= (1.0 + 3.0 * (depth_ratio - 0.5))
            
        impact_bps = base_slippage * size_factor * vol_multiplier
        impact_cost = trade_size_usd * (impact_bps / 10000)
        
        permanent_cost = 0.0 
        gas_cost = self.config.gas_cost_per_trade_usd
        
        # Microstructure Additions (Latency & Toxicity)
        latency_cost = trade_size_usd * (self.config.base_latency_ms / 100.0) * (self.config.latency_penalty_bps_per_100ms / 10000.0)
        
        # Toxic fill
        prob_toxic = min(0.9, self.config.toxicity_alpha * vol_multiplier)
        toxicity_cost = trade_size_usd * prob_toxic * (5.0 / 10000.0) 
        
        # Partial Fill modeling
        fill_ratio = 1.0
        if trade_size_usd > (book_depth_usd * 0.5) and asset_volatility > 0.05:
            fill_ratio = max(0.2, 1.0 - (depth_ratio))
            
        total = dex_fee + impact_cost + permanent_cost + gas_cost + latency_cost + toxicity_cost
        return TradeCost(
            dex_fee=dex_fee,
            impact_cost=impact_cost,
            permanent_impact_cost=permanent_cost,
            gas_cost=gas_cost,
            mev_cost=0.0,
            latency_cost=latency_cost,
            toxicity_cost=toxicity_cost,
            total=total,
            total_bps=total / trade_size_usd * 10000 if trade_size_usd > 0 else 0,
            fill_ratio=fill_ratio
        )
