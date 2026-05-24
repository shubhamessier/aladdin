// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

error Vault__Paused();
error Vault__NotWhitelisted(address token);
error Vault__AllocationExceeded(address token, uint256 postAllocationBps, uint256 maxBps);
error Vault__StableReserveBreach(uint256 postStableBps, uint256 minRequiredBps);
error Vault__SlippageExceeded(uint256 expected, uint256 actual, uint256 maxSlippageBps);
error Vault__DeadlineExpired(uint256 deadline, uint256 currentTimestamp);
error Vault__DailyVolumeCap(uint256 requested, uint256 remaining);
error Vault__TradeCooldown(bytes32 pair, uint256 nextAllowed, uint256 currentTimestamp);
error Vault__CircuitBreakerActive(uint8 level);
error Vault__GasPriceTooHigh(uint256 gasPrice, uint256 maxAllowed);
error Vault__InsufficientBalance(address token, uint256 requested, uint256 available);
error Vault__InsufficientMargin(uint256 required, uint256 available);
error Vault__LiquidationTooClose(uint256 liquidationPrice, uint256 currentPrice, uint256 minDistanceBps);
error Vault__MaxTradeSize(uint256 tradeUSD, uint256 maxUSD);
error Vault__VelocityLimitBreached(uint256 outflowLast24h, uint256 maxAllowed);
error Vault__DrawdownLimitExceeded(uint256 currentDrawdownBps, uint256 maxBps);
error Vault__ZeroAmount();
error Vault__SameToken();
error Vault__BatchActionFailed(uint256 index, bytes reason);
error Vault__WithdrawalQueued(uint256 requestId, uint256 executeAfter);
error Vault__WithdrawalNotReady(uint256 requestId, uint256 executeAfter);
error Vault__ExternalCallFailed(address target, bytes4 selector, bytes reason);
error Vault__GrossNotionalExceeded(uint256 postNotional, uint256 maxNotional);
error Vault__NetDeltaExceeded(int256 postDelta, int256 maxAbsDelta);
error Vault__StrategyConcentration(address strategy, uint256 postBps, uint256 maxBps);

// Strategy Specific Errors
error Strategy__NotActive();
error Strategy__UtilizationTooHigh(uint256 utilizationBps, uint256 maxBps);
error Strategy__Shortfall(uint256 expected, uint256 actual);
error Strategy__InvalidParameters();
error Strategy__HedgeBroken(uint256 spotDiff, uint256 perpDiff, uint256 threshold);
error Strategy__NotImplemented();
