import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime

# Add backtest and risk engine to path
root_path = Path("/home/shubham/Desktop/quant/aladdin")
sys.path.append(str(root_path))
sys.path.append(str(root_path / "python-risk"))

from backtest.engine.simulator import TreasurySimulator
from backtest.engine.circuit_breaker import CircuitBreakerConfig
from backtest.engine.strategies import EqualWeightStrategy, StrategyConfig
from risk_engine.var_models import compute_historical_var

class MockYieldEngine:
    def calculate_yield(self, *args, **kwargs):
        return 0.0

def test_mtm():
    print("--- 2.1 MTM VERIFICATION ---")
    dates = pd.date_range("2023-01-01", periods=3)
    price_data = {"ETH": [2000, 2200, 1800], "USDC": [1.0, 1.0, 1.0]}
    df = pd.DataFrame(price_data, index=dates)
    
    config = StrategyConfig(name="Test")
    strategy = EqualWeightStrategy(config)
    cb_config = CircuitBreakerConfig()
    
    sim = TreasurySimulator(
        initial_cash=20000.0,
        start_date=dates[0],
        end_date=dates[2],
        assets=["ETH", "USDC"],
        circuit_breaker_config=cb_config,
        strategy=strategy
    )
    sim.yield_engine = MockYieldEngine()
    sim.load_market_data(df)
    
    sim.portfolio.weights = {"ETH": 0.5, "USDC": 0.5}
    sim.portfolio.units = {"ETH": 5.0, "USDC": 10000.0}
    sim.portfolio.positions = {"ETH": 10000.0, "USDC": 10000.0}
    sim.portfolio.cash = 0.0
    
    sim.current_day = 1
    sim.step()
    print(f"Day 1 Portfolio Value: {sim.portfolio.portfolio_value}")
    assert abs(sim.portfolio.portfolio_value - 21000.0) < 0.01
    
    sim.current_day = 2
    sim.step()
    print(f"Day 2 Portfolio Value: {sim.portfolio.portfolio_value}")
    assert abs(sim.portfolio.portfolio_value - 19000.0) < 0.01
    print("MTM Verification Passed!")

def test_var_sanity():
    print("\n--- 2.3 VaR SANITY CHECK ---")
    np.random.seed(42)
    eth_returns = np.random.normal(0, 0.04, 1000)
    usdc_returns = np.zeros(1000)
    returns_matrix = np.column_stack([eth_returns, usdc_returns])
    weights = np.array([0.5, 0.5])
    
    var_95, _ = compute_historical_var(returns_matrix, weights)
    var_95_usd = var_95 * 1000000
    
    print(f"VaR 95% 1-day for $1M portfolio: ${var_95_usd:,.2f}")
    assert 25000 < var_95_usd < 45000
    print("VaR Sanity Check Passed!")

if __name__ == "__main__":
    try:
        test_mtm()
        test_var_sanity()
    except Exception as e:
        print(f"VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
