# Market-Neutral Strategies: 4-Hour Simulation Plan

## Objective
To achieve a Sharpe ratio > 3.0 by eliminating directional market risk (Beta) and harvesting pure, market-neutral Alpha.

## Phase 1: Engine Modifications
The current `simulator.py` and `hedger.py` expect a long-only spot allocation that is optionally partially hedged. I will update the `AllocationStrategy` to explicitly output a `target_hedges` dictionary. If `target_hedges` is provided, the Hedging Engine will respect it directly rather than falling back to regime-based fractional hedging.

## Phase 2: Strategy 1 - Delta-Neutral Basis Arbitrage (Cash & Carry)
*   **The Logic:** Monitor the rolling 24-hour funding rate for all perp markets. When the annualized funding rate exceeds a specific entry threshold (e.g., > 15% APY), buy the spot asset and simultaneously short 100% of the notional value on the perp market.
*   **The Risk:** Execution slippage, liquidation risk if the basis suddenly widens exponentially, and opportunity cost of capital.
*   **Parameter Sweeps:**
    *   `entry_funding_apy`: 10%, 15%, 20%
    *   `exit_funding_apy`: 5%, 0%, -5%
    *   `max_leverage`: 1x, 2x, 3x

## Phase 3: Strategy 3 - Statistical Arbitrage (Pairs Trading)
*   **The Logic:** Calculate the spread (price ratio or difference) between highly correlated assets (e.g., BTC and ETH). Calculate the rolling z-score of this spread. When the z-score > 2.0 (ETH is overvalued relative to BTC), short ETH-PERP and long BTC-PERP. When z-score < -2.0, reverse. Close the trade when the z-score reverts to 0.
*   **The Risk:** Structural breaks (correlations decoupling forever), and heavy margin utilization.
*   **Parameter Sweeps:**
    *   `z_score_entry`: 1.5, 2.0, 2.5
    *   `z_score_exit`: 0.5, 0.0, -0.5
    *   `lookback_window`: 7 days, 14 days, 30 days

## Phase 4: Strategy 4 - Volatility Selling (Short Gamma)
*   **The Logic:** While we don't have options data, we can proxy short volatility by dynamically adjusting liquidity provision (acting as a maker). We will provide liquidity (via limit orders modeled through taker fee rebates) during periods of high mean-reverting chop, and pull liquidity during trending breakouts.
*   **Parameter Sweeps:** Bollinger Band width thresholds, ADX (Average Directional Index) filters.

## Phase 5: Execution & Autonomous Optimization
1.  Implement the strategies in Python.
2.  Run individual 4-year backtest smoke tests.
3.  Launch Grid and Random Search pipelines over the parameter spaces.
4.  Capture PnL, Sharpe, Max Drawdown, and Liquidations.
5.  Refine, rewrite underperforming logic, and converge on the global maximum Sharpe.