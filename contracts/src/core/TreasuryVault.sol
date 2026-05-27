// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {AccessControlUpgradeable} from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import {ReentrancyGuardUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {ITreasuryVault} from "../interfaces/ITreasuryVault.sol";
import {IAssetRegistry} from "../interfaces/IAssetRegistry.sol";
import {ISecurityHooks} from "../interfaces/ISecurityHooks.sol";
import {IOracleAdapter} from "../interfaces/IOracleAdapter.sol";
import "../errors/VaultErrors.sol";

contract TreasuryVault is Initializable, UUPSUpgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable, ITreasuryVault {
    using SafeERC20 for IERC20;

    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");
    bytes32 public constant GOVERNOR_ROLE = keccak256("GOVERNOR_ROLE");
    bytes32 public constant EMERGENCY_ROLE = keccak256("EMERGENCY_ROLE");
    bytes32 public constant KEEPER_ROLE = keccak256("KEEPER_ROLE");

    bytes32 private constant TreasuryVaultStorageLocation = 0xc059c4b7c6c40db27928b5d5d8fb86550dfbbf11e646271c6d376263dfbe9000;

    struct TreasuryVaultStorage {
        mapping(address => bool) whitelistedTokens;
        mapping(address => AssetLedger) assetLedgers;
        mapping(address => bool) whitelistedRouters;
        mapping(address => bool) whitelistedStrategies;
        mapping(bytes32 => DerivativePosition) derivativePositions;
        bytes32[] activeDerivativeKeys;
        
        PortfolioSnapshot latestSnapshot;
        PortfolioSnapshot[720] snapshotHistory;
        uint256 snapshotIndex;
        uint256 snapshotCount;
        uint256 portfolioHighWaterMark;
        
        uint256 hwmAbsolute;
        uint256 hwmEffective;
        uint256 hwmLastUpdatedTimestamp;
        uint256 hwmDecayHalflifeSeconds;
        uint256 cbLevelSetTimestamp;
        uint256 cbNoFurtherDropSince;
        uint256 cbConsecutiveStableDays;
        
        uint256 cbLevel1Bps;
        uint256 cbLevel2Bps;
        uint256 cbLevel3Bps;
        uint256 cbWindowSeconds;
        uint8 currentCBLevel;
        uint256 cbActivatedAt;
        
        uint256 maxSlippageBps;
        uint256 maxTradeUSD;
        uint256 maxDailyVolumeUSD;
        uint256 dailyVolumeUsed;
        uint256 dailyVolumeResetBlock;
        uint256 minTradeCooldownSeconds;
        mapping(bytes32 => uint256) lastTradeTimestamp;
        
        uint256 largeWithdrawalThreshold;
        uint256 withdrawalTimelockSeconds;
        mapping(uint256 => WithdrawalRequest) withdrawalQueue;
        uint256 nextWithdrawalId;
        
        uint256 maxGasPriceWei;
        
        IAssetRegistry assetRegistry;
        ISecurityHooks securityHooks;
        IOracleAdapter oracleAdapter;
        
        bool paused;
    }

    event ExternalCallFailed(address indexed target, bytes4 selector, bytes reason);
    event CircuitBreakerTriggered(uint8 level, uint256 currentValue, uint256 refValue, uint256 dropBps);

    function _getTreasuryVaultStorage() private pure returns (TreasuryVaultStorage storage $) {
        assembly {
            $.slot := TreasuryVaultStorageLocation
        }
    }

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        address _assetRegistry,
        address _securityHooks,
        address _oracleAdapter
    ) initializer public {
        __UUPSUpgradeable_init();
        __AccessControl_init();
        __ReentrancyGuard_init();

        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);

        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        $.assetRegistry = IAssetRegistry(_assetRegistry);
        $.securityHooks = ISecurityHooks(_securityHooks);
        $.oracleAdapter = IOracleAdapter(_oracleAdapter);

        $.cbLevel1Bps = 1000;
        $.cbLevel2Bps = 2000;
        $.cbLevel3Bps = 3500;
        $.cbWindowSeconds = 3600;
        
        $.hwmDecayHalflifeSeconds = 90 days;
        $.hwmLastUpdatedTimestamp = block.timestamp;
        
        $.maxSlippageBps = 100;
        $.maxTradeUSD = 500_000e18;
        $.maxDailyVolumeUSD = 2_000_000e18;
        $.maxGasPriceWei = 100_000_000_000; // 100 gwei
    }

    function _authorizeUpgrade(address newImplementation) internal override onlyRole(keccak256("TIMELOCK_ROLE")) {}

    function deposit(address token, uint256 amount) external nonReentrant {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();
        if(!$.whitelistedTokens[token]) revert Vault__NotWhitelisted(token);
        if(amount == 0) revert Vault__ZeroAmount();
        
        uint256 balBefore = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
        uint256 actualAmount = IERC20(token).balanceOf(address(this)) - balBefore;
        
        $.assetLedgers[token].freeBalance += actualAmount;
        $.assetLedgers[token].cumulativeDeposits += actualAmount;
        $.assetLedgers[token].lastUpdatedBlock = block.number;
        
        _updatePortfolioSnapshot();
    }

    function withdraw(address token, uint256 amount) external nonReentrant returns (uint256 requestId) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();
        if($.assetLedgers[token].freeBalance < amount) revert Vault__InsufficientBalance(token, amount, $.assetLedgers[token].freeBalance);
        
        ISecurityHooks.ActionParams memory params = ISecurityHooks.ActionParams({
            actionType: ISecurityHooks.ActionType.WITHDRAWAL,
            caller: msg.sender,
            tokenIn: address(0),
            tokenOut: token,
            amountIn: 0,
            amountOut: amount,
            minAmountOut: amount,
            strategy: address(0),
            market: address(0),
            isLong: false,
            derivativeSize: 0,
            additionalData: ""
        });
        
        ISecurityHooks.ValidationResult memory res = $.securityHooks.validate(params);
        if(!res.allowed) revert(res.reason);

        IOracleAdapter.PriceData memory priceData = $.oracleAdapter.getPrice(token);
        IAssetRegistry.AssetConfig memory config = $.assetRegistry.getAssetConfig(token);
        
        uint256 valUSD = 0;
        if (config.decimals > 0 && priceData.price > 0) {
            valUSD = (amount * priceData.price) / (10 ** config.decimals);
        }

        if ($.largeWithdrawalThreshold > 0 && valUSD > $.largeWithdrawalThreshold) {
            uint256 id = ++$.nextWithdrawalId;
            $.withdrawalQueue[id] = WithdrawalRequest({
                depositor: msg.sender,
                token: token,
                amount: amount,
                unlockTimestamp: block.timestamp + $.withdrawalTimelockSeconds,
                isExecuted: false,
                isCancelled: false
            });
            $.assetLedgers[token].pendingWithdrawals += amount;
            $.assetLedgers[token].freeBalance -= amount; // Lock the funds
            $.securityHooks.recordAction(params);
            _updatePortfolioSnapshot();
            return id;
        }
        
        $.assetLedgers[token].freeBalance -= amount;
        $.assetLedgers[token].cumulativeWithdrawals += amount;
        $.assetLedgers[token].lastUpdatedBlock = block.number;

        $.securityHooks.recordAction(params);
        _updatePortfolioSnapshot();
        
        IERC20(token).safeTransfer(msg.sender, amount);
        return 0; // Immediate execution
    }

    function claimWithdrawal(uint256 requestId) external nonReentrant {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();
        
        WithdrawalRequest storage req = $.withdrawalQueue[requestId];
        require(req.depositor == msg.sender, "Vault__NotOwner");
        require(!req.isExecuted, "Vault__AlreadyExecuted");
        require(!req.isCancelled, "Vault__Cancelled");
        require(block.timestamp >= req.unlockTimestamp, "Vault__Timelocked");
        
        req.isExecuted = true;
        
        $.assetLedgers[req.token].pendingWithdrawals -= req.amount;
        $.assetLedgers[req.token].cumulativeWithdrawals += req.amount;
        $.assetLedgers[req.token].lastUpdatedBlock = block.number;
        
        IERC20(req.token).safeTransfer(msg.sender, req.amount);
    }

    function executeSwap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        uint256 deadline,
        bytes calldata routeData
    ) external nonReentrant onlyRole(GUARDIAN_ROLE) returns (uint256 amountOut) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();
        if(block.timestamp > deadline) revert Vault__DeadlineExpired(deadline, block.timestamp);
        if(amountIn == 0) revert Vault__ZeroAmount();
        if(tokenIn == tokenOut) revert Vault__SameToken();
        if($.maxGasPriceWei > 0 && tx.gasprice > $.maxGasPriceWei) revert Vault__GasPriceTooHigh(tx.gasprice, $.maxGasPriceWei);
        if($.assetLedgers[tokenIn].freeBalance < amountIn) revert Vault__InsufficientBalance(tokenIn, amountIn, $.assetLedgers[tokenIn].freeBalance);

        // Extract router from routeData (assuming first 20 bytes is router address for simplicity or it's a fixed router config)
        // Here we mock a call, but normally we'd decode router
        address router = address(bytes20(routeData[0:20])); 
        require($.whitelistedRouters[router], "Vault__NotWhitelisted");

        ISecurityHooks.ActionParams memory params = ISecurityHooks.ActionParams({
            actionType: ISecurityHooks.ActionType.SWAP,
            caller: msg.sender,
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            amountIn: amountIn,
            amountOut: minAmountOut,
            minAmountOut: minAmountOut,
            strategy: address(0),
            market: address(0),
            isLong: false,
            derivativeSize: 0,
            additionalData: routeData
        });
        
        ISecurityHooks.ValidationResult memory res = $.securityHooks.validate(params);
        if(!res.allowed) revert(res.reason);

        // Pre-swap balances
        uint256 balInBefore = IERC20(tokenIn).balanceOf(address(this));
        uint256 balOutBefore = IERC20(tokenOut).balanceOf(address(this));

        // Approvals and Call
        IERC20(tokenIn).safeIncreaseAllowance(router, amountIn);
        
        (bool success, bytes memory returnData) = router.call(routeData[20:]);
        
        IERC20(tokenIn).forceApprove(router, 0);

        if (!success) {
            bytes4 selector = 0;
            if (routeData.length >= 24) selector = bytes4(routeData[20:24]);
            emit ExternalCallFailed(router, selector, returnData);
            revert Vault__ExternalCallFailed(router, selector, returnData);
        }

        uint256 balInAfter = IERC20(tokenIn).balanceOf(address(this));
        uint256 balOutAfter = IERC20(tokenOut).balanceOf(address(this));

        uint256 actualAmountIn = balInBefore - balInAfter;
        uint256 actualAmountOut = balOutAfter - balOutBefore;

        if(actualAmountOut < minAmountOut) revert Vault__SlippageExceeded(minAmountOut, actualAmountOut, $.maxSlippageBps);

        // Update ledgers
        $.assetLedgers[tokenIn].freeBalance -= actualAmountIn;
        $.assetLedgers[tokenIn].lastUpdatedBlock = block.number;
        
        $.assetLedgers[tokenOut].freeBalance += actualAmountOut;
        $.assetLedgers[tokenOut].lastUpdatedBlock = block.number;

        $.securityHooks.recordAction(params);
        _updatePortfolioSnapshot();
        
        return actualAmountOut;
    }

    function executeBatchActions(Action[] calldata actions) external nonReentrant onlyRole(GUARDIAN_ROLE) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();

        for(uint256 i = 0; i < actions.length; i++) {
            Action calldata a = actions[i];
            
            ISecurityHooks.ActionParams memory params = ISecurityHooks.ActionParams({
                actionType: ISecurityHooks.ActionType(a.actionType),
                caller: msg.sender,
                tokenIn: a.tokenIn,
                tokenOut: a.tokenOut,
                amountIn: a.amountIn,
                amountOut: a.minAmountOut,
                minAmountOut: a.minAmountOut,
                strategy: a.target,
                market: a.target,
                isLong: a.isLong,
                derivativeSize: a.derivativeSize,
                additionalData: a.data
            });

            ISecurityHooks.ValidationResult memory res = $.securityHooks.validate(params);
            if(!res.allowed) revert Vault__BatchActionFailed(i, bytes(res.reason));

            if(params.actionType == ISecurityHooks.ActionType.SWAP) {
                require($.whitelistedRouters[a.target], "Vault__NotWhitelisted");
                
                // Pre-swap balances
                uint256 balInBefore = IERC20(a.tokenIn).balanceOf(address(this));
                uint256 balOutBefore = IERC20(a.tokenOut).balanceOf(address(this));

                IERC20(a.tokenIn).safeIncreaseAllowance(a.target, a.amountIn);
                
                (bool success, bytes memory returnData) = a.target.call(a.data);
                
                IERC20(a.tokenIn).forceApprove(a.target, 0);

                if (!success) {
                    revert Vault__BatchActionFailed(i, returnData);
                }

                uint256 balInAfter = IERC20(a.tokenIn).balanceOf(address(this));
                uint256 balOutAfter = IERC20(a.tokenOut).balanceOf(address(this));

                uint256 actualAmountIn = balInBefore - balInAfter;
                uint256 actualAmountOut = balOutAfter - balOutBefore;

                if(actualAmountOut < a.minAmountOut) revert Vault__SlippageExceeded(a.minAmountOut, actualAmountOut, $.maxSlippageBps);

                $.assetLedgers[a.tokenIn].freeBalance -= actualAmountIn;
                $.assetLedgers[a.tokenOut].freeBalance += actualAmountOut;
            } else {
                 // For mock completeness, other actions just pass
            }
            
            $.securityHooks.recordAction(params);
        }

        _updatePortfolioSnapshot();
    }

    function updatePortfolioSnapshot() external onlyRole(KEEPER_ROLE) {
        _updatePortfolioSnapshot();
    }

    function _updatePortfolioSnapshot() internal {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        IAssetRegistry.SnapshotData memory data = $.assetRegistry.getPortfolioSnapshot();
        
        uint256 totalVolatile = 0;
        uint256 totalStable = 0;
        
        for(uint256 i = 0; i < data.assets.length; i++) {
            if(data.assets[i].tier == IAssetRegistry.RiskTier.STABLE) {
                totalStable += data.assets[i].valueUSD;
            } else {
                totalVolatile += data.assets[i].valueUSD;
            }
        }
        
        uint256 totalDeriv = 0;
        int256 netDelta = 0;
        for(uint256 i = 0; i < $.activeDerivativeKeys.length; i++) {
            DerivativePosition memory pos = $.derivativePositions[$.activeDerivativeKeys[i]];
            if(pos.sizeUSD > 0) {
                totalDeriv += pos.sizeUSD;
                netDelta += pos.isLong ? int256(pos.sizeUSD) : -int256(pos.sizeUSD);
            }
        }
        
        PortfolioSnapshot memory snap = PortfolioSnapshot({
            totalValueUSD: data.totalPortfolioUSD,
            totalStableValueUSD: totalStable,
            totalVolatileValueUSD: totalVolatile,
            totalStrategyValueUSD: 0, // Mock: strategy integration
            totalDerivativeExposureUSD: totalDeriv,
            netDelta: netDelta,
            timestamp: block.timestamp,
            blockNumber: block.number
        });
        
        $.latestSnapshot = snap;
        $.snapshotHistory[$.snapshotIndex] = snap;
        $.snapshotIndex = ($.snapshotIndex + 1) % 720;
        if($.snapshotCount < 720) $.snapshotCount++;
        
        if(data.totalPortfolioUSD > $.portfolioHighWaterMark) {
            $.portfolioHighWaterMark = data.totalPortfolioUSD;
        }
        
        if(data.totalPortfolioUSD > $.hwmAbsolute) {
            $.hwmAbsolute = data.totalPortfolioUSD;
            $.hwmLastUpdatedTimestamp = block.timestamp;
        }
        $.hwmEffective = _computeEffectiveHWM(data.totalPortfolioUSD);
        
        if(data.totalPortfolioUSD < $.cbNoFurtherDropSince || $.cbNoFurtherDropSince == 0) {
            $.cbNoFurtherDropSince = data.totalPortfolioUSD;
            $.cbConsecutiveStableDays = 0;
        } else if (block.timestamp >= $.cbLevelSetTimestamp + ($.cbConsecutiveStableDays + 1) * 1 days) {
            $.cbConsecutiveStableDays++;
        }
        
        _checkCircuitBreaker(data.totalPortfolioUSD);
    }

    function _computeEffectiveHWM(uint256 currentValue) internal view returns (uint256) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if ($.hwmAbsolute == 0 || currentValue >= $.hwmAbsolute) return currentValue;
        
        uint256 elapsed = block.timestamp - $.hwmLastUpdatedTimestamp;
        uint256 gracePeriod = 30 days;
        if (elapsed <= gracePeriod) {
            return $.hwmAbsolute;
        }
        
        uint256 decayElapsed = elapsed - gracePeriod;
        uint256 gap = $.hwmAbsolute - currentValue;
        
        uint256 halflives = decayElapsed / $.hwmDecayHalflifeSeconds;
        if (halflives >= 64) return currentValue; 
        
        uint256 decayedGap = gap >> halflives;
        uint256 remainder = decayElapsed % $.hwmDecayHalflifeSeconds;
        if (remainder > 0) {
            decayedGap = decayedGap - (decayedGap * remainder) / (2 * $.hwmDecayHalflifeSeconds);
        }
        
        return currentValue + decayedGap;
    }

    function decayCBLevel() external onlyRole(GUARDIAN_ROLE) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if ($.currentCBLevel == 0) return;
        
        uint256 daysSinceActivated = (block.timestamp - $.cbActivatedAt) / 1 days;
        if (daysSinceActivated >= 60) {
            $.currentCBLevel = 0;
            $.cbLevelSetTimestamp = block.timestamp;
            return;
        }

        uint256 requiredDays = 7;
        if ($.currentCBLevel == 2) requiredDays = 14;
        
        if ($.cbConsecutiveStableDays < requiredDays) revert Vault__CBDecayTooSoon();
        
        $.currentCBLevel--;
        $.cbLevelSetTimestamp = block.timestamp;
        $.cbConsecutiveStableDays = 0;
    }

    function _checkCircuitBreaker(uint256 currentValue) internal {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.snapshotCount == 0) return;
        
        uint256 targetTime = block.timestamp > $.cbWindowSeconds ? block.timestamp - $.cbWindowSeconds : 0;
        
        uint256 refValue = currentValue;
        for(uint256 i = 1; i <= $.snapshotCount; i++) {
            uint256 idx = ($.snapshotIndex + 720 - i) % 720;
            if($.snapshotHistory[idx].timestamp <= targetTime) {
                refValue = $.snapshotHistory[idx].totalValueUSD;
                break;
            }
        }
        
        uint256 windowDropBps = 0;
        if(currentValue < refValue && refValue > 0) {
            windowDropBps = ((refValue - currentValue) * 10000) / refValue;
        }

        uint256 hwmDropBps = 0;
        if (currentValue < $.hwmEffective && $.hwmEffective > 0) {
            hwmDropBps = (($.hwmEffective - currentValue) * 10000) / $.hwmEffective;
        }
        
        uint8 newLevel = $.currentCBLevel;
        
        if(windowDropBps >= $.cbLevel3Bps || hwmDropBps >= 3500) newLevel = 3;
        else if((windowDropBps >= $.cbLevel2Bps || hwmDropBps >= 2000) && $.currentCBLevel < 3) newLevel = 2;
        else if((windowDropBps >= $.cbLevel1Bps || hwmDropBps >= 1000) && $.currentCBLevel < 2) newLevel = 1;
        
        if(newLevel > $.currentCBLevel) {
            $.currentCBLevel = newLevel;
            $.cbActivatedAt = block.timestamp;
            $.cbLevelSetTimestamp = block.timestamp;
            $.cbNoFurtherDropSince = currentValue;
            $.cbConsecutiveStableDays = 0;
            
            uint256 triggerDrop = windowDropBps > hwmDropBps ? windowDropBps : hwmDropBps;
            uint256 triggerRef = windowDropBps > hwmDropBps ? refValue : $.hwmEffective;
            emit CircuitBreakerTriggered(newLevel, currentValue, triggerRef, triggerDrop);
        }
    }

    function openDerivativePosition(address market, bool isLong, uint256 sizeUSD, uint256 leverage) external nonReentrant onlyRole(GUARDIAN_ROLE) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();
        
        uint256 requiredMargin = sizeUSD / leverage;
        address usdc = address(0); // Assume USDC address fetched from somewhere, mocking for now
        // if($.assetLedgers[usdc].freeBalance < requiredMargin) revert Vault__InsufficientMargin(requiredMargin, $.assetLedgers[usdc].freeBalance);
        
        bytes32 posKey = keccak256(abi.encode(market, isLong));
        
        DerivativePosition memory pos = $.derivativePositions[posKey];
        if(pos.sizeUSD == 0) {
            $.activeDerivativeKeys.push(posKey);
        }
        
        pos.market = market;
        pos.isLong = isLong;
        pos.sizeUSD += sizeUSD;
        pos.margin += requiredMargin;
        pos.entryPrice = 0; // Fetch from oracle
        pos.openTimestamp = block.timestamp;
        pos.lastUpdateBlock = block.number;
        
        $.derivativePositions[posKey] = pos;
        
        // Mock transfer margin
    }

    function closeDerivativePosition(bytes32 positionId) external nonReentrant onlyRole(GUARDIAN_ROLE) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        if($.paused) revert Vault__Paused();
        
        DerivativePosition memory pos = $.derivativePositions[positionId];
        if(pos.sizeUSD > 0) {
            // Mock settle
            delete $.derivativePositions[positionId];
            
            // Remove from active keys
            for(uint256 i = 0; i < $.activeDerivativeKeys.length; i++) {
                if($.activeDerivativeKeys[i] == positionId) {
                    $.activeDerivativeKeys[i] = $.activeDerivativeKeys[$.activeDerivativeKeys.length - 1];
                    $.activeDerivativeKeys.pop();
                    break;
                }
            }
        }
    }

    function currentCBLevel() external view override returns (uint8) {
        return _getTreasuryVaultStorage().currentCBLevel;
    }

    function portfolioHighWaterMark() external view override returns (uint256) {
        return _getTreasuryVaultStorage().portfolioHighWaterMark;
    }

    function maxDailyVolumeUSD() external view override returns (uint256) {
        return _getTreasuryVaultStorage().maxDailyVolumeUSD;
    }

    function maxTradeUSD() external view override returns (uint256) {
        return _getTreasuryVaultStorage().maxTradeUSD;
    }

    function maxGasPriceWei() external view override returns (uint256) {
        return _getTreasuryVaultStorage().maxGasPriceWei;
    }

    function maxSlippageBps() external view override returns (uint256) {
        return _getTreasuryVaultStorage().maxSlippageBps;
    }

    // Admin configuration
    function whitelistToken(address token, bool isWhitelisted) external onlyRole(GOVERNOR_ROLE) {
        TreasuryVaultStorage storage $ = _getTreasuryVaultStorage();
        $.whitelistedTokens[token] = isWhitelisted;
    }

    function pause() external onlyRole(keccak256("EMERGENCY_ROLE")) {
        _getTreasuryVaultStorage().paused = true;
    }

    function unpause() external onlyRole(GOVERNOR_ROLE) {
        _getTreasuryVaultStorage().paused = false;
    }

    function getAssetLedger(address token) external view returns (AssetLedger memory) {
        return _getTreasuryVaultStorage().assetLedgers[token];
    }

    function getLatestSnapshot() external view returns (PortfolioSnapshot memory) {
        return _getTreasuryVaultStorage().latestSnapshot;
    }

    function getDerivativePosition(bytes32 positionKey) external view returns (DerivativePosition memory) {
        return _getTreasuryVaultStorage().derivativePositions[positionKey];
    }
}
