# Security Threat Model

This document outlines the threat landscape, identified attack vectors, mitigations, and residual risks for the Autonomous Treasury Management System.

## 1. Oracle Manipulation

### Flash Loan & Multi-Block Attacks
- **Threat**: Attacker uses a flash loan or large capital over multiple blocks to skew AMM spot prices, tricking the vault into executing trades at bad rates.
- **Probability**: Medium
- **Impact**: High (Capital loss due to bad execution)
- **Mitigation**: `OracleAdapter.sol` aggregates 3 sources (Chainlink, Pyth, TWAP). TWAP acts as a low-pass filter. Cross-deviation checks reject prices if sources diverge significantly. `SecurityHooks.sol` enforces max slippage on execution.
- **Detection**: Guardian flags `Oracle__PriceSuspect` events. State shifts to WARNING.
- **Residual Risk**: Zero-day vulnerabilities in Chainlink or Pyth infrastructure.

### Oracle Censorship / Liveness Failure
- **Threat**: Network congestion or coordinated censorship prevents oracle updates, leading to stale prices.
- **Probability**: Low
- **Impact**: Medium (System halts trading)
- **Mitigation**: Guardian checks staleness (`maxStalenessSeconds`). If stale, system shifts to DEGRADED and halts risk-increasing operations.
- **Detection**: Guardian multi-call returns stale timestamps.
- **Residual Risk**: Market crashes while system is halted, preventing necessary hedges.

## 2. Governance Attacks

### Flash Loan Voting & Delegate Buying
- **Threat**: Attacker acquires significant voting power temporarily to pass malicious proposals (e.g., whitelist a scam token, change allocation bounds to 100%).
- **Probability**: Low (Depends on DAO token distribution)
- **Impact**: Critical (Total loss of funds)
- **Mitigation**: `GovernanceModule.sol` implements a mandatory timelock delay before execution. Max value caps on proposals. Guardian monitors pending proposals.
- **Detection**: Guardian reads `getPendingProposals()`.
- **Residual Risk**: Apathy among honest voters during the timelock window.

## 3. MEV (Miner Extractable Value)

### Sandwich Attacks
- **Threat**: Searchers front-run and back-run the vault's trades, capturing the allowed slippage.
- **Probability**: High
- **Impact**: Low/Medium (Slow bleed of capital)
- **Mitigation**: `TWAP Engine` slices trades into smaller sizes, reducing sandwich profitability. `SecurityHooks.sol` enforces tight slippage bounds. Use of private mempools (if available on HyperEVM) or Hyperliquid native integrations.
- **Detection**: Post-trade slippage analysis in the Guardian.
- **Residual Risk**: Unavoidable minor slippage on large market moves.

### JIT Liquidity & Time-Bandit Attacks
- **Threat**: Manipulation of AMM liquidity just before a vault trade.
- **Probability**: Low
- **Impact**: Medium
- **Mitigation**: On-chain TWAP pricing and strict execution limits.
- **Detection**: Deviation between expected execution price and actual price.
- **Residual Risk**: Sophisticated validators reordering blocks.

## 4. Smart Contract Exploits

### Reentrancy
- **Threat**: Attacker calls back into the vault during an external call before state is updated.
- **Probability**: Low (Standard mitigations applied)
- **Impact**: Critical
- **Mitigation**: OpenZeppelin `ReentrancyGuardUpgradeable` used on all state-mutating functions. State changes (CEI pattern) strictly precede external calls.
- **Detection**: Failed execution logs, unexpected reverts.
- **Residual Risk**: Complex cross-contract reentrancy via strategies.

### Access Control & Overflow
- **Threat**: Unauthorized actor calls restricted functions; integer overflows in math libraries.
- **Probability**: Low
- **Impact**: Critical
- **Mitigation**: Strong RBAC (`AccessControlUpgradeable`). `FixedPointMath.sol` and `BasisPointMath.sol` thoroughly tested with 512-bit math for overflows.
- **Detection**: Contract reverts on unauthorized access.
- **Residual Risk**: Logic errors in custom math implementations.

## 5. Operational Failures

### Guardian Key Compromise
- **Threat**: Attacker steals the Guardian's hot wallet private key.
- **Probability**: Low
- **Impact**: Medium (Cannot drain funds directly, but can force bad trades or DOS)
- **Mitigation**: Guardian key is constrained by `SecurityHooks.sol` (cannot transfer funds out, only trade whitelisted assets within bounds). Vault can pause Guardian via Keeper or Governance.
- **Detection**: Unscheduled or out-of-bound trade attempts.
- **Residual Risk**: Attacker triggers max allowed slippage continuously.

### Infrastructure Failure (Risk Engine / Node Offline)
- **Threat**: Python Risk Engine or RPC node goes down.
- **Probability**: Medium
- **Impact**: Low
- **Mitigation**: Guardian shifts to DEGRADED state. Uses cached VaR metrics or fallback conservative weights. Can switch RPC endpoints dynamically.
- **Detection**: Circuit breaker in Risk Engine Client opens.
- **Residual Risk**: Stale risk metrics during a volatile market event.

## 6. Economic Threats

### Stablecoin Depeg
- **Threat**: A core stablecoin (e.g., USDC, USDT) loses its peg.
- **Probability**: Low/Medium
- **Impact**: High
- **Mitigation**: Off-chain `StablecoinPegMonitor` tracks price vs $1.00 and rate of change. Triggers WARNING or CRITICAL state. Vault auto-swaps to healthy stablecoins. Minimum stable reserve enforced by `SecurityHooks.sol`.
- **Detection**: Oracle price deviation >10 bps.
- **Residual Risk**: Contagion affecting all stablecoins simultaneously.

### Liquidity Crisis & Correlated Crash
- **Threat**: Market-wide crash where all asset correlations go to 1, and DEX liquidity dries up.
- **Probability**: Low
- **Impact**: High
- **Mitigation**: `RegimeDetector` identifies crisis state early. L1/L2/L3 Circuit Breakers halt trading or force emergency deleveraging. `PerpHedgingStrategy` provides downside protection independent of spot liquidity.
- **Detection**: Circuit breaker trips based on portfolio High Water Mark drops.
- **Residual Risk**: Inability to execute hedges due to lack of counterparty liquidity on Hyperliquid.
