// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IStrategy} from "../interfaces/IStrategy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {
    Strategy__NotActive,
    Vault__InsufficientMargin,
    Strategy__InvalidParameters,
    Strategy__HedgeBroken,
    Vault__ZeroAmount
} from "../errors/VaultErrors.sol";

// Mock Interfaces
interface IHyperliquidPerp {
    function depositMargin(uint256 amount) external;
    function withdrawMargin(uint256 amount) external;
    function openPosition(address market, bool isLong, uint256 sizeUSD) external returns (bytes32 positionId);
    function closePosition(bytes32 positionId) external returns (int256 pnl);
    function getPositionValue(bytes32 positionId) external view returns (int256);
    function getFundingRate(address market) external view returns (int256);
    function getMarkPrice(address market) external view returns (uint256);
}

interface ISpotExchange {
    function buySpot(address token, uint256 amountIn) external returns (uint256 amountOut);
    function sellSpot(address token, uint256 amountIn) external returns (uint256 amountOut);
    function getSpotPrice(address token) external view returns (uint256);
}

contract BasisTradeStrategy is IStrategy, AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant VAULT_ROLE = keccak256("VAULT_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    address public immutable override underlyingToken; // USDC
    IHyperliquidPerp public immutable perpExchange;
    ISpotExchange public immutable spotExchange;
    
    bool public override isActive = true;
    uint256 public override maxCapacity = 5_000_000e18;
    
    uint256 public totalCapitalDeposited;
    int256 public cumulativeFunding;
    
    uint256 public constant BASIS_DIVERGENCE_THRESHOLD_BPS = 300; // 3%

    struct BasisTrade {
        address spotToken;
        address perpMarket;
        uint256 spotAmount;
        bytes32 perpPositionId;
        uint256 initialSpotPrice;
        uint256 initialPerpPrice;
        uint256 notionalUSD;
    }

    mapping(bytes32 => BasisTrade) public activeTrades;
    bytes32[] public tradeIds;

    constructor(address _underlyingToken, address _perpExchange, address _spotExchange) {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        underlyingToken = _underlyingToken;
        perpExchange = IHyperliquidPerp(_perpExchange);
        spotExchange = ISpotExchange(_spotExchange);
    }

    function deposit(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (!isActive) revert Strategy__NotActive();
        if (amount == 0) revert Vault__ZeroAmount();
        
        IERC20(underlyingToken).safeTransferFrom(msg.sender, address(this), amount);
        totalCapitalDeposited += amount;
        return amount;
    }

    function withdraw(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        uint256 balance = IERC20(underlyingToken).balanceOf(address(this));
        if (amount > balance) revert Vault__InsufficientMargin(amount, balance);
        
        totalCapitalDeposited -= amount;
        IERC20(underlyingToken).safeTransfer(msg.sender, amount);
        return amount;
    }

    function withdrawAll() external override onlyRole(VAULT_ROLE) returns (uint256) {
        uint256 amount = IERC20(underlyingToken).balanceOf(address(this));
        totalCapitalDeposited -= amount;
        IERC20(underlyingToken).safeTransfer(msg.sender, amount);
        return amount;
    }

    function harvest() external override returns (int256 netPnL) {
        netPnL = cumulativeFunding;
        cumulativeFunding = 0;
        return netPnL;
    }

    // Guardian Actions
    function openBasisTrade(address spotToken, address perpMarket, uint256 capitalUSD, uint256 leverage) external onlyRole(GUARDIAN_ROLE) returns (bytes32) {
        uint256 balance = IERC20(underlyingToken).balanceOf(address(this));
        if (capitalUSD > balance) revert Vault__InsufficientMargin(capitalUSD, balance);
        if (leverage == 0) revert Strategy__InvalidParameters();
        
        uint256 spotPortion = capitalUSD / 2;
        uint256 perpPortion = capitalUSD - spotPortion;
        uint256 notionalUSD = perpPortion * leverage;
        
        // 1. Buy spot
        IERC20(underlyingToken).forceApprove(address(spotExchange), spotPortion);
        uint256 spotOut = spotExchange.buySpot(spotToken, spotPortion);
        uint256 currentSpotPrice = spotExchange.getSpotPrice(spotToken);
        
        // 2. Open short perp
        IERC20(underlyingToken).forceApprove(address(perpExchange), perpPortion);
        perpExchange.depositMargin(perpPortion);
        bytes32 positionId = perpExchange.openPosition(perpMarket, false, notionalUSD);
        uint256 currentPerpPrice = perpExchange.getMarkPrice(perpMarket);
        
        // Generate trade ID
        bytes32 tradeId = keccak256(abi.encodePacked(spotToken, perpMarket, block.timestamp));
        
        activeTrades[tradeId] = BasisTrade({
            spotToken: spotToken,
            perpMarket: perpMarket,
            spotAmount: spotOut,
            perpPositionId: positionId,
            initialSpotPrice: currentSpotPrice,
            initialPerpPrice: currentPerpPrice,
            notionalUSD: notionalUSD
        });
        tradeIds.push(tradeId);
        
        return tradeId;
    }

    function closeBasisTrade(bytes32 tradeId) public onlyRole(GUARDIAN_ROLE) {
        BasisTrade memory trade = activeTrades[tradeId];
        if (trade.spotToken == address(0)) revert Strategy__InvalidParameters();
        
        // 1. Close perp
        int256 perpPnl = perpExchange.closePosition(trade.perpPositionId);
        
        // Assume withdraw all free margin for this position (mock)
        // In reality we'd withdraw exactly what was tied up + pnl
        uint256 perpMarginInitial = trade.notionalUSD / 2; // Approximating based on 2x assumption if not tracking exactly
        // Just mocking withdraw margin:
        // perpExchange.withdrawMargin(...);
        
        // 2. Sell spot
        IERC20(trade.spotToken).forceApprove(address(spotExchange), trade.spotAmount);
        uint256 usdcOut = spotExchange.sellSpot(trade.spotToken, trade.spotAmount);
        
        // Funding/PnL would be collected here in reality
        
        delete activeTrades[tradeId];
        _removeTradeId(tradeId);
    }
    
    function checkAndCloseIfBroken(bytes32 tradeId) external onlyRole(GUARDIAN_ROLE) {
        BasisTrade memory trade = activeTrades[tradeId];
        if (trade.spotToken == address(0)) revert Strategy__InvalidParameters();
        
        uint256 currentSpotPrice = spotExchange.getSpotPrice(trade.spotToken);
        uint256 currentPerpPrice = perpExchange.getMarkPrice(trade.perpMarket);
        
        // % change = |new - old| * 10000 / old
        uint256 spotDiffBps;
        if (currentSpotPrice > trade.initialSpotPrice) {
            spotDiffBps = ((currentSpotPrice - trade.initialSpotPrice) * 10000) / trade.initialSpotPrice;
        } else {
            spotDiffBps = ((trade.initialSpotPrice - currentSpotPrice) * 10000) / trade.initialSpotPrice;
        }
        
        uint256 perpDiffBps;
        if (currentPerpPrice > trade.initialPerpPrice) {
            perpDiffBps = ((currentPerpPrice - trade.initialPerpPrice) * 10000) / trade.initialPerpPrice;
        } else {
            perpDiffBps = ((trade.initialPerpPrice - currentPerpPrice) * 10000) / trade.initialPerpPrice;
        }
        
        uint256 basisDivergence;
        if (spotDiffBps > perpDiffBps) {
            basisDivergence = spotDiffBps - perpDiffBps;
        } else {
            basisDivergence = perpDiffBps - spotDiffBps;
        }
        
        if (basisDivergence > BASIS_DIVERGENCE_THRESHOLD_BPS) {
            closeBasisTrade(tradeId);
            revert Strategy__HedgeBroken(spotDiffBps, perpDiffBps, BASIS_DIVERGENCE_THRESHOLD_BPS);
        }
    }

    function _removeTradeId(bytes32 tradeId) internal {
        uint256 length = tradeIds.length;
        for (uint256 i = 0; i < length; i++) {
            if (tradeIds[i] == tradeId) {
                tradeIds[i] = tradeIds[length - 1];
                tradeIds.pop();
                break;
            }
        }
    }

    function estimatedTotalAssets() external view override returns (uint256) {
        uint256 total = IERC20(underlyingToken).balanceOf(address(this));
        
        for (uint256 i = 0; i < tradeIds.length; i++) {
            BasisTrade memory trade = activeTrades[tradeIds[i]];
            // Spot value
            uint256 currentSpotPrice = spotExchange.getSpotPrice(trade.spotToken);
            uint256 spotValue = (trade.spotAmount * currentSpotPrice) / 1e18; // assuming 1e18 decimals
            
            // Perp MTM
            int256 perpMTM = perpExchange.getPositionValue(trade.perpPositionId);
            
            // Add margin + MTM + spot
            uint256 perpMargin = trade.notionalUSD / 2; // rough assumption based on split
            
            total += spotValue;
            if (perpMTM > 0) {
                total += perpMargin + uint256(perpMTM);
            } else {
                uint256 absMTM = uint256(-perpMTM);
                if (perpMargin > absMTM) {
                    total += (perpMargin - absMTM);
                }
            }
        }
        
        return total;
    }

    function estimatedAPY() external view override returns (int256) {
        return 8e18; // 8% target basis yield
    }

    function riskScore() external pure override returns (uint8) {
        return 35; // Moderate risk
    }

    function liquidationValue() external view override returns (uint256) {
        return this.estimatedTotalAssets();
    }

    function getGreeks() external view override returns (
        int256 delta,
        int256 gamma,
        int256 vega,
        int256 theta
    ) {
        delta = 0;
        theta = 0;

        for (uint256 i = 0; i < tradeIds.length; i++) {
            BasisTrade memory trade = activeTrades[tradeIds[i]];
            // Spot is long delta, perp is short delta (should be ~0)
            uint256 currentSpotPrice = spotExchange.getSpotPrice(trade.spotToken);
            uint256 spotNotional = (trade.spotAmount * currentSpotPrice) / 1e18;
            
            delta += int256(spotNotional);
            delta -= int256(trade.notionalUSD);
            
            int256 fundingRate = perpExchange.getFundingRate(trade.perpMarket);
            theta += (fundingRate * int256(trade.notionalUSD)) / 1e18;
        }
        
        return (delta, 0, 0, theta);
    }

    function maxDrawdownHistorical() external pure override returns (uint256) {
        return 500; // 5%
    }

    function sharpeRatio30d() external pure override returns (int256) {
        return 4e18; 
    }

    function setActive(bool _active) external onlyRole(DEFAULT_ADMIN_ROLE) {
        isActive = _active;
    }
}
