# V3 Audit Fixes - Solidity, Python, and TypeScript Integration

This document logs the resolutions for the outstanding catastrophic (A), major (B), and architectural (C) issues outlined in the deep audit pass (`more-issues.md`).

## A-Tier: Solidity Catastrophic Fixes
* **A1 (Portfolio Snapshot):** Implemented `getPortfolioSnapshot` in `AssetRegistry.sol` to iterate over active tokens, fetch on-chain balances from the vault, price them via the oracle, and compute valid allocation BPS. Division-by-zero risk in `SecurityHooks` has been eliminated.
* **A2 (Update Snapshot Access):** `_updatePortfolioSnapshot` in `TreasuryVault` was made internal. It is now called at the end of both `executeSwap` and `executeBatchActions`. A separate `updatePortfolioSnapshot` function was added with `onlyRole(KEEPER_ROLE)`.
* **A3 (Router Whitelist & Allowance Reset):** Uncommented the `$.whitelistedRouters` check in `TreasuryVault.sol`. Added `forceApprove(router, 0)` immediately after external calls to completely eliminate the risk of lingering allowances leading to guardian-key drain.
* **A4 (OracleAdapter Price Status):** Changed `OracleAdapter.getPrice` to dynamically compute `PriceStatus.STALE` if `lastTwapUpdate` exceeds `MAX_STALENESS_SECONDS`, rather than permanently returning `PriceStatus.GOOD`.
* **A5 (OracleAdapter Access Control):** Added `onlyRole(GOVERNOR_ROLE)` to `setFeeds` using the `IAssetRegistry` access control interface, preventing arbitrary EOAs from repointing feeds.
* **A6 (Withdraw Controls):** Rewrote the `withdraw` function to use `SecurityHooks.validate`. Added support for threshold-based withdrawal queuing and a `claimWithdrawal` function, securing large withdrawals with a timelock.
* **A7 (UUPS Upgrade Security):** Modified `_authorizeUpgrade` to require the `TIMELOCK_ROLE` instead of `DEFAULT_ADMIN_ROLE`, closing the instant upgrade exploit vector.

## B-Tier: Python Major Risk/Quant Failures
* **B1 & B2 (Solidity CB HWM & Level Decay):** Ported the Python HWM decay logic into Solidity's `_computeEffectiveHWM`. Added a 30-day grace period and computed the decay on the gap. Updated `decayCBLevel` to require consecutive stable days or 60 forced days.
* **B8 (HMM Rank Transform Bias):** Replaced the stateless `inverse_normal_transform` with a stateful `_transform` inside `RobustRegimeDetector` to map test data against a preserved empirical CDF.
* **B9 (Black-Litterman Omega Calculation):** Removed the incorrect `tau` multiplier on the confidence branch in `portfolio_optimizer.py`, respecting Idzorek's method.
* **B10 (Student-t MC Variance Inflation):** Scaled Student-t innovations in `monte_carlo.py` by `sqrt((df-2)/df)` to unitize intrinsic variance.
* **B11 (Rescaled Volatility):** Renamed parameters to `annualized_volatilities` and adjusted downstream callers, dropping the redundant `/sqrt(252)` inside the simulator block.
* **B12 (Recovery Week Counter):** Removed the modulo-based `advance_week` accumulator. Replaced with `days_in // 7` continuous calculation to prevent artificial inflation on hourly simulation ticks.
* **B13 & B14 (Optimizer Non-Convergence & Infeasibility):** Updated strategy scripts to explicitly verify `res.converged` and fall back to inverse volatility. Adjusted stablecoin minimums dynamically to ensure the risk-parity SLSQP constraints are actually feasible during crisis regimes.
* **B15 & B16 (CB Peak Updates & Cooldowns):** Debounced HWM candidate updates with a 1-day minimum dwell time in `circuit_breaker.py` to match the asymmetry of the 30-day decay.

## C-Tier: Guardian Service / Architecture
* **C1 (Guardian integration with Vault):** Connected the `EventSourcingLedger` to the vault via an `initializeFromVault` function that decodes the real `getPortfolioSnapshot` via RPC call, removing the $1M hardcoded stub.
* **C2 (Nonce Race Condition):** Added a mutex (`nonceMutex`) into `TransactionManager`'s `getNextNonce()` sequence to correctly queue parallel actions submitted by the orchestrator via `Promise.all`.
* **C3 (Sequence Gap Recovery):** Refactored the `recoverFromSnapshot` method to resume using an authoritative external `seqId` rather than `Date.now()`.
* **C4 (Daily Volume & Gas Split-Brain):** Refactored `SecurityHooks.sol` to fetch constants dynamically via `vault.maxDailyVolumeUSD()`, `vault.maxTradeUSD()`, and `vault.maxGasPriceWei()`. The guardian's `writer.ts` now also fetches the max gas price directly from the Vault rather than hardcoding 100 Gwei.
* **C7 (Emergency Pause):** Added explicit `pause` and `unpause` admin endpoints onto `TreasuryVault.sol` allowing `EMERGENCY_ROLE` to freeze the vault.

## Phase 3 Upgrades: Alpha Injection & Simulator Performance
* **Predictive Alpha Views:** Upgraded `BlackLittermanStrategy` to generate predictive cross-sectional momentum views rather than just defaulting to market equilibrium. By passing `historical_returns` to `generate_target_weights`, the optimizer now successfully shifts capital toward momentum leaders while shorting laggards.
* **Simulation Speed:** The `simulator.py` step loop previously sliced pandas DataFrames on every iteration (35,000+ operations), creating massive memory allocation overhead. The history arrays are now fully pre-calculated in a numpy/pandas vector upon `run()`, drastically reducing the `step()` latency.

The combined system is now significantly more robust, aligned across Python and Solidity simulation tiers, and capable of operating as a real integration loop with the guardian service.
