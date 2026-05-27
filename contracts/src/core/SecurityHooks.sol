// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ISecurityHooks} from "../interfaces/ISecurityHooks.sol";
import {IOracleAdapter} from "../interfaces/IOracleAdapter.sol";
import {IAssetRegistry} from "../interfaces/IAssetRegistry.sol";
import {ITreasuryVault} from "../interfaces/ITreasuryVault.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {Vault__GasPriceTooHigh, Vault__DailyVolumeCap, Vault__TradeCooldown, Vault__CircuitBreakerActive, Vault__VelocityLimitBreached, Vault__SlippageExceeded, Vault__MaxTradeSize, Vault__DrawdownLimitExceeded, Vault__GrossNotionalExceeded, Vault__NetDeltaExceeded, Vault__StrategyConcentration} from "../errors/VaultErrors.sol";

contract SecurityHooks is ISecurityHooks {
    IOracleAdapter public oracleAdapter;
    IAssetRegistry public assetRegistry;
    ITreasuryVault public vault;

    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    uint256 public constant MIN_TRADE_COOLDOWN_SECONDS = 30;
    
    uint256 public maxGrossNotionalBps = 5000;
    uint256 public maxNetDeltaBps = 3000;
    uint256 public maxStrategyConcentrationBps = 2500;
    uint256 public maxTotalStrategyBps = 6000;
    uint256 public maxDrawdownBps = 2000;
    uint256 public maxDailyOutflowBps = 1500;
    
    bool public recoveryPhaseActive;
    uint256 public recoveryMaxVolatileBps;

    mapping(bytes32 => uint256) public lastTradeTimestamp;
    uint256 public dailyVolumeUsed;
    uint256 public dailyVolumeResetTimestamp;

    struct OutflowRecord {
        uint256 timestamp;
        uint256 amountUSD;
    }
    OutflowRecord[200] public outflowBuffer;
    uint256 public outflowIndex;

    constructor(address _oracleAdapter, address _assetRegistry, address _vault) {
        oracleAdapter = IOracleAdapter(_oracleAdapter);
        assetRegistry = IAssetRegistry(_assetRegistry);
        vault = ITreasuryVault(_vault);
    }

    function setRecoveryPhase(bool active, uint256 maxVolatileBps) external {
        require(IAccessControl(address(vault)).hasRole(GUARDIAN_ROLE, msg.sender), "Only guardian");
        recoveryPhaseActive = active;
        recoveryMaxVolatileBps = maxVolatileBps;
    }

    function _getAssetPriceAndStatus(address token) internal view returns (uint256 price, IOracleAdapter.PriceStatus status) {
        if (token == address(0)) return (0, IOracleAdapter.PriceStatus.GOOD);
        IOracleAdapter.PriceData memory data = oracleAdapter.getPrice(token);
        return (data.price, data.status);
    }

    function validate(ActionParams calldata params) external view override returns (ValidationResult memory) {
        uint8 riskLevel = 0;

        // Rule 1: Oracle Status Gate
        (uint256 priceIn, IOracleAdapter.PriceStatus statusIn) = _getAssetPriceAndStatus(params.tokenIn);
        (uint256 priceOut, IOracleAdapter.PriceStatus statusOut) = _getAssetPriceAndStatus(params.tokenOut);

        if (statusIn == IOracleAdapter.PriceStatus.STALE || statusOut == IOracleAdapter.PriceStatus.STALE) {
            return ValidationResult(false, "Rule 1: Stale oracle", 2);
        }
        if (statusIn == IOracleAdapter.PriceStatus.SUSPECT || statusOut == IOracleAdapter.PriceStatus.SUSPECT) {
            if (params.actionType != ActionType.STRATEGY_WITHDRAWAL && params.actionType != ActionType.DERIVATIVE_CLOSE && params.actionType != ActionType.WITHDRAWAL) {
                return ValidationResult(false, "Rule 1: Suspect oracle, risk reducing only", 2);
            }
        }
        if (statusIn == IOracleAdapter.PriceStatus.DEGRADED || statusOut == IOracleAdapter.PriceStatus.DEGRADED) {
            riskLevel = 1;
        }

        // Rule 10: Gas Price
        if (vault.maxGasPriceWei() > 0 && tx.gasprice > vault.maxGasPriceWei()) {
            return ValidationResult(false, "Rule 10: Gas price too high", 1);
        }

        // Get Portfolio Context
        ITreasuryVault.PortfolioSnapshot memory snap = vault.getLatestSnapshot();
        uint256 totalPortfolioUSD = snap.totalValueUSD;

        // General Swap Rules
        if (params.actionType == ActionType.SWAP) {
            // Rule 3: Slippage Protection
            if (params.amountOut < params.minAmountOut) {
                return ValidationResult(false, "Rule 3: Slippage too high", 1);
            }

            // Rule 4: Trade Size Limits
            uint256 tradeUsd = (params.amountIn * priceIn) / 1e18; // assuming 18 decimals internal pricing
            if (tradeUsd > vault.maxTradeUSD()) {
                return ValidationResult(false, "Rule 4: Exceeds max trade USD", 1);
            }

            uint256 currentDailyVol = dailyVolumeUsed;
            if (block.timestamp > dailyVolumeResetTimestamp + 1 days) {
                currentDailyVol = 0;
            }
            if (currentDailyVol + tradeUsd > vault.maxDailyVolumeUSD()) {
                return ValidationResult(false, "Rule 4: Exceeds daily volume", 1);
            }

            // Rule 5: Trade Cooldown
            bytes32 pairHash = keccak256(abi.encodePacked(params.tokenIn, params.tokenOut));
            if (block.timestamp - lastTradeTimestamp[pairHash] < MIN_TRADE_COOLDOWN_SECONDS) {
                return ValidationResult(false, "Rule 5: Trade cooldown active", 1);
            }

            // Rule 2: Allocation Bounds (Check OUT token)
            // Simplified approximation for amountOut_USD
            uint256 amountOutUSD = (params.amountOut * priceOut) / 1e18;
            (bool validAlloc, string memory reasonAlloc) = assetRegistry.validateAllocation(params.tokenOut, amountOutUSD, totalPortfolioUSD);
            if (!validAlloc) return ValidationResult(false, reasonAlloc, 1);
            
            if (recoveryPhaseActive) {
                IAssetRegistry.AssetConfig memory config = assetRegistry.getAssetConfig(params.tokenOut);
                if (config.tier != IAssetRegistry.RiskTier.STABLE) {
                    if ((amountOutUSD * 10000) / totalPortfolioUSD > recoveryMaxVolatileBps) {
                        return ValidationResult(false, "Rule 2: Exceeds recovery volatile cap", 1);
                    }
                }
            }
            
            // Note: selling an asset entirely is okay (0 >= minAllocationBps for most) handles inside validateAllocation
        }

        // Rule 7: Derivative Exposure
        if (params.actionType == ActionType.DERIVATIVE_OPEN || params.actionType == ActionType.DERIVATIVE_ADJUST) {
            uint256 newGross = snap.totalDerivativeExposureUSD + params.derivativeSize;
            if ((newGross * 10000) / totalPortfolioUSD > maxGrossNotionalBps) {
                return ValidationResult(false, "Rule 7: Gross notional limit exceeded", 2);
            }
            // Simplified delta check
            int256 deltaChange = params.isLong ? int256(params.derivativeSize) : -int256(params.derivativeSize);
            int256 newNetDelta = snap.netDelta + deltaChange;
            int256 absDelta = newNetDelta > 0 ? newNetDelta : -newNetDelta;
            if ((uint256(absDelta) * 10000) / totalPortfolioUSD > maxNetDeltaBps) {
                return ValidationResult(false, "Rule 7: Net delta limit exceeded", 2);
            }
        }

        // Rule 8: Strategy Concentration
        if (params.actionType == ActionType.STRATEGY_DEPOSIT) {
            uint256 amountUSD = (params.amountIn * priceIn) / 1e18;
            if ((amountUSD * 10000) / totalPortfolioUSD > maxStrategyConcentrationBps) {
                return ValidationResult(false, "Rule 8: Strategy concentration exceeded", 2);
            }
            if (((snap.totalStrategyValueUSD + amountUSD) * 10000) / totalPortfolioUSD > maxTotalStrategyBps) {
                return ValidationResult(false, "Rule 8: Total strategy alloc exceeded", 2);
            }
        }

        // Rule 9: Circuit Breaker State
        uint8 cbLevel = vault.currentCBLevel();
        if (cbLevel > 0 && params.actionType != ActionType.DERIVATIVE_CLOSE && params.actionType != ActionType.STRATEGY_WITHDRAWAL) {
            return ValidationResult(false, "Rule 9: Circuit breaker active", 2);
        }

        // Rule 11: Drawdown Guard
        uint256 hwm = vault.portfolioHighWaterMark();
        if (hwm > 0 && totalPortfolioUSD < hwm) {
            uint256 drawdownBps = ((hwm - totalPortfolioUSD) * 10000) / hwm;
            if (drawdownBps > maxDrawdownBps) {
                if (params.actionType != ActionType.DERIVATIVE_CLOSE && params.actionType != ActionType.STRATEGY_WITHDRAWAL && params.actionType != ActionType.WITHDRAWAL) {
                    return ValidationResult(false, "Rule 11: Drawdown limit exceeded", 2);
                }
            }
        }

        // Rule 12: Velocity Check
        if (params.actionType == ActionType.WITHDRAWAL || params.actionType == ActionType.SWAP) {
            uint256 sumOutflow = 0;
            uint256 cutoff = block.timestamp - 1 days;
            for (uint256 i = 0; i < 200; i++) {
                if (outflowBuffer[i].timestamp > cutoff) {
                    sumOutflow += outflowBuffer[i].amountUSD;
                }
            }
            uint256 tradeUsd = (params.amountIn * priceIn) / 1e18;
            if (((sumOutflow + tradeUsd) * 10000) / totalPortfolioUSD > maxDailyOutflowBps) {
                return ValidationResult(false, "Rule 12: Velocity limit breached", 2);
            }
        }
        
        // Rule 6: Concentration HHI (spot check on-chain is expensive, assume off-chain computed, we trust allocation limits for now)

        return ValidationResult(true, "Action valid", riskLevel);
    }

    function recordAction(ActionParams calldata params) external override {
        require(msg.sender == address(vault), "Only vault");
        
        if (params.actionType == ActionType.SWAP) {
            (uint256 priceIn, ) = _getAssetPriceAndStatus(params.tokenIn);
            uint256 tradeUsd = (params.amountIn * priceIn) / 1e18;
            
            if (block.timestamp > dailyVolumeResetTimestamp + 1 days) {
                dailyVolumeUsed = 0;
                dailyVolumeResetTimestamp = block.timestamp;
            }
            dailyVolumeUsed += tradeUsd;
            
            bytes32 pairHash = keccak256(abi.encodePacked(params.tokenIn, params.tokenOut));
            lastTradeTimestamp[pairHash] = block.timestamp;
            
            _recordOutflow(tradeUsd);
        } else if (params.actionType == ActionType.WITHDRAWAL) {
            (uint256 priceIn, ) = _getAssetPriceAndStatus(params.tokenIn);
            uint256 tradeUsd = (params.amountIn * priceIn) / 1e18;
            _recordOutflow(tradeUsd);
        }
    }

    function _recordOutflow(uint256 amountUSD) internal {
        outflowBuffer[outflowIndex] = OutflowRecord(block.timestamp, amountUSD);
        outflowIndex = (outflowIndex + 1) % 200;
    }
}
