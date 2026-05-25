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
    dex_fee_bps: float = 5.0
    impact_coefficient: float = 0.1
    permanent_impact_coeff: float = 0.05
    gas_cost_per_trade_usd: float = 2.0
    mev_threshold_usd: float = 25000.0
    mev_cost_bps: float = 5.0
    twap_threshold_usd: float = 100000.0

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
            
        # FLAW-04: USDC/USDT have near-zero slippage
        effective_impact_coeff = self.config.impact_coefficient
        effective_mev_bps = self.config.mev_cost_bps
        
        if asset in ["USDC", "USDT", "DAI"]:
            effective_impact_coeff *= 0.05 # 95% reduction for stables
            effective_mev_bps *= 0.1       # Stables less prone to sandwiching
            
        dex_fee = trade_size_usd * self.config.dex_fee_bps / 10000
        adv = max(daily_volume_usd, 1e6)
        participation_rate = trade_size_usd / adv
        
        temporary_impact = effective_impact_coeff * asset_volatility * np.sqrt(participation_rate)
        impact_cost = trade_size_usd * temporary_impact
        
        permanent_impact = self.config.permanent_impact_coeff * (trade_size_usd / adv) ** 0.6
        if asset in ["USDC", "USDT", "DAI"]:
            permanent_impact *= 0.1
        permanent_cost = trade_size_usd * permanent_impact
        
        gas_cost = self.config.gas_cost_per_trade_usd
        
        mev_cost = 0.0
        if trade_size_usd > self.config.mev_threshold_usd:
            mev_cost = trade_size_usd * effective_mev_bps / 10000
            if trade_size_usd > self.config.twap_threshold_usd:
                mev_cost *= 0.3
        
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
