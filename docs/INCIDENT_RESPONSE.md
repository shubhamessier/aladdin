# Incident Response Plan

This document details the procedures for handling various critical events and failures within the Autonomous Treasury Management System.

## 1. Guardian Enters SHUTDOWN State

**Trigger**: Node crash, RPC completely offline, out of gas, or unhandled fatal exception in the orchestrator.
- **Immediate Actions**:
  1. Acknowledge the PagerDuty/Slack alert.
  2. Verify if the process is actually dead or stuck.
  3. Restart the Guardian service using `pm2` or systemd.
- **Investigation Steps**:
  1. Check the application logs (`~/.pm2/logs/guardian-service-error.log`).
  2. Check the RPC node status and API rate limits.
  3. Check the hot wallet gas token balance.
- **Resolution Steps**:
  1. If RPC is down, switch to a backup endpoint in `.env` and restart.
  2. If out of gas, fund the hot wallet and restart.
  3. If a bug caused the crash, manually intervene (pause contract if necessary) until patched.
- **Post-Mortem Required**: Yes.

## 2. Circuit Breaker Triggers (L2 or L3)

**Trigger**: Portfolio High Water Mark drops by >15% (L2) or >25% (L3) within the configured window.
- **Immediate Actions**:
  1. System automatically halts risk-increasing trades (L2) or all non-emergency trades (L3).
  2. Acknowledge the alert.
- **Investigation Steps**:
  1. Determine the cause of the drop: broad market crash, specific asset collapse, or oracle bug?
  2. Check on-chain prices vs centralized exchanges.
  3. Evaluate the effectiveness of current hedges (if any).
- **Resolution Steps**:
  1. Let the system manage the deleveraging process if functioning correctly.
  2. Once the market stabilizes and the drop is no longer worsening, a Keeper/Admin must manually call `reduceCBLevel()` after the cooldown period to restore normal operations.
- **Post-Mortem Required**: Yes (for L3), Optional (for L2).

## 3. Stablecoin Depeg

**Trigger**: Off-chain monitor detects deviation >2% (CRITICAL) or >0.5% (WARNING) for a core stablecoin.
- **Immediate Actions**:
  1. The Guardian will automatically attempt to swap out of the depegging asset (subject to liquidity and slippage bounds).
- **Investigation Steps**:
  1. Verify the depeg is real (check Curve pools, Binance, etc.) and not an oracle glitch.
  2. Assess the available liquidity on HyperEVM DEXs.
- **Resolution Steps**:
  1. If liquidity is drying up and slippage is exceeding bounds, the Guardian will halt swapping.
  2. DAO may need to pass an emergency proposal to bridge assets back to L1 or Arbitrum if HyperEVM liquidity is insufficient.
- **Post-Mortem Required**: Yes.

## 4. Oracle Goes Fully Stale

**Trigger**: No oracle source (Chainlink, Pyth, TWAP) has updated within `maxStalenessSeconds`.
- **Immediate Actions**:
  1. Guardian transitions to `DEGRADED` state. No trades will be executed.
- **Investigation Steps**:
  1. Check Hyperliquid/HyperEVM network status (is the chain halted?).
  2. Check Chainlink/Pyth status pages.
- **Resolution Steps**:
  1. Wait for network recovery. The Guardian will automatically resume `HEALTHY` state once prices update.
  2. If prolonged, DAO may vote to change the `OracleAdapter` configuration or manually push prices if supported.
- **Post-Mortem Required**: No (unless it caused a missed hedge resulting in loss).

## 5. Strategy Loses Money Unexpectedly

**Trigger**: A strategy's `estimatedTotalAssets()` drops without a corresponding drop in the underlying asset's market price.
- **Immediate Actions**:
  1. Manually pause the specific strategy via `StrategyManager.pauseStrategy(address)`.
- **Investigation Steps**:
  1. Investigate the strategy's target protocol (e.g., Aave fork exploited? LP position suffered extreme impermanent loss? Funding rate inverted sharply?).
  2. Review the smart contract transactions leading up to the loss.
- **Resolution Steps**:
  1. Call `withdrawAll()` from the strategy if funds are still recoverable.
  2. Deactivate the strategy permanently if the underlying protocol is compromised.
- **Post-Mortem Required**: Yes.

## 6. Contract Upgrade Needed Urgently

**Trigger**: A zero-day vulnerability is discovered in the vault or a core dependency.
- **Immediate Actions**:
  1. Pause the `TreasuryVault` via the emergency multisig (bypasses timelock).
- **Investigation Steps**:
  1. Validate the exploit vector.
  2. Draft the patch.
- **Resolution Steps**:
  1. Since it's a UUPS proxy, deploy the new implementation contract.
  2. Propose the upgrade via the `GovernanceModule`.
  3. (If emergency, use the emergency upgrade path if the DAO constitution allows bypassing the timelock).
- **Post-Mortem Required**: Yes.

## 7. Governance Attack in Progress

**Trigger**: A malicious proposal is queued in the `GovernanceModule`.
- **Immediate Actions**:
  1. Identify the malicious actors and the proposal payload.
- **Investigation Steps**:
  1. Determine how they acquired the voting power (flash loan, delegate buying, compromised whale wallet).
- **Resolution Steps**:
  1. Rally honest DAO members to vote NO or VETO the proposal before the timelock expires.
  2. If the DAO has a security council multisig with veto power, exercise it immediately.
- **Post-Mortem Required**: Yes.

## Post-Mortem Template
Every incident requiring a post-mortem must complete the following document and share it with the DAO:
1. **Incident Summary**: What happened, when, and who responded.
2. **Impact**: Financial loss, downtime, reputational damage.
3. **Timeline**: Chronological sequence of events (with transaction hashes).
4. **Root Cause Analysis**: The technical or economic failure that allowed the incident.
5. **Action Items**: Concrete steps (code changes, parameter updates, process improvements) to prevent recurrence, with assignees and deadlines.
