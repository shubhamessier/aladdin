from pydantic import BaseModel
from typing import Dict

class CostModelConfig(BaseModel):
    dex_fee_bps: float = 30.0  # 0.3% default
    gas_cost_usd: float = 5.0
    mev_slippage_bps: float = 5.0
    impact_coefficient: float = 0.1
    impact_exponent: float = 0.5

class TransactionCostModel:
    def __init__(self, config: CostModelConfig):
        self.config = config
        
    def estimate_cost(
        self, 
        trade_size_usd: float, 
        asset_volatility: float, 
        daily_volume_usd: float,
        is_maker: bool = False
    ) -> float:
        """
        Estimate the total transaction cost including fees, slippage, and gas.
        Handles zero volume safely.
        """
        if trade_size_usd <= 0.0:
            return 0.0
            
        # 1. DEX Fee
        dex_fee = 0.0 if is_maker else (trade_size_usd * self.config.dex_fee_bps / 10000.0)
        
        # 2. Slippage (Almgren-Chriss style temporary impact)
        slippage_cost = 0.0
        if daily_volume_usd > 0.0:
            volume_fraction = trade_size_usd / daily_volume_usd
            # Impact = sigma * eta * (v/V)^beta
            impact_fraction = asset_volatility * self.config.impact_coefficient * (volume_fraction ** self.config.impact_exponent)
            slippage_cost = trade_size_usd * impact_fraction
            
        # 3. MEV / Additional Slippage
        mev_cost = trade_size_usd * self.config.mev_slippage_bps / 10000.0
        
        # 4. Gas Cost
        gas_cost = self.config.gas_cost_usd
        
        return dex_fee + slippage_cost + mev_cost + gas_cost
