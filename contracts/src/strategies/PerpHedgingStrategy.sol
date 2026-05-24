// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IStrategy} from "../interfaces/IStrategy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {
    Strategy__NotActive,
    Vault__InsufficientMargin,
    Strategy__InvalidParameters
} from "../errors/VaultErrors.sol";

// Mock Hyperliquid Interface
interface IHyperliquidPerp {
    function depositMargin(uint256 amount) external;
    function withdrawMargin(uint256 amount) external;
    function openPosition(address market, bool isLong, uint256 sizeUSD) external returns (bytes32 positionId);
    function adjustPosition(bytes32 positionId, int256 sizeDeltaUSD) external;
    function closePosition(bytes32 positionId) external returns (int256 pnl);
    function getPositionValue(bytes32 positionId) external view returns (int256);
    function getFundingRate(address market) external view returns (int256);
}

contract PerpHedgingStrategy is IStrategy, AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant VAULT_ROLE = keccak256("VAULT_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    address public immutable override underlyingToken; // USDC margin
    IHyperliquidPerp public immutable exchange;
    
    bool public override isActive = true;
    uint256 public override maxCapacity = 5_000_000e18;
    
    uint256 public marginDeposited;
    uint256 public constant MAX_LEVERAGE = 5;
    int256 public cumulativeFunding;

    struct Position {
        address market;
        bool isLong;
        uint256 sizeUSD;
    }

    mapping(bytes32 => Position) public positions;
    bytes32[] public activePositionIds;

    constructor(address _underlyingToken, address _exchange) {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        underlyingToken = _underlyingToken;
        exchange = IHyperliquidPerp(_exchange);
    }

    function deposit(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (!isActive) revert Strategy__NotActive();
        IERC20(underlyingToken).safeTransferFrom(msg.sender, address(this), amount);
        
        IERC20(underlyingToken).forceApprove(address(exchange), amount);
        exchange.depositMargin(amount);
        marginDeposited += amount;

        return amount;
    }

    function withdraw(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        // Assume we have sufficient free margin, otherwise it would revert in exchange
        exchange.withdrawMargin(amount);
        marginDeposited -= amount;
        IERC20(underlyingToken).safeTransfer(msg.sender, amount);
        return amount;
    }

    function withdrawAll() external override onlyRole(VAULT_ROLE) returns (uint256) {
        uint256 amount = marginDeposited;
        exchange.withdrawMargin(amount);
        marginDeposited = 0;
        IERC20(underlyingToken).safeTransfer(msg.sender, amount);
        return amount;
    }

    function harvest() external override returns (int256 netPnL) {
        netPnL = cumulativeFunding;
        cumulativeFunding = 0;
        return netPnL;
    }

    // Guardian Actions
    function openHedge(address market, bool isLong, uint256 sizeUSD) external onlyRole(GUARDIAN_ROLE) returns (bytes32) {
        uint256 requiredMargin = sizeUSD / MAX_LEVERAGE;
        if (marginDeposited < requiredMargin) {
            revert Vault__InsufficientMargin(requiredMargin, marginDeposited);
        }

        bytes32 positionId = exchange.openPosition(market, isLong, sizeUSD);
        positions[positionId] = Position({
            market: market,
            isLong: isLong,
            sizeUSD: sizeUSD
        });
        activePositionIds.push(positionId);
        return positionId;
    }
    
    function adjustHedge(bytes32 positionId, int256 sizeDeltaUSD) external onlyRole(GUARDIAN_ROLE) {
        Position storage pos = positions[positionId];
        if (pos.market == address(0)) revert Strategy__InvalidParameters();

        if (sizeDeltaUSD > 0) {
            pos.sizeUSD += uint256(sizeDeltaUSD);
        } else {
            uint256 absDelta = uint256(-sizeDeltaUSD);
            if (absDelta >= pos.sizeUSD) {
                // Should use closeHedge instead
                revert Strategy__InvalidParameters();
            }
            pos.sizeUSD -= absDelta;
        }

        exchange.adjustPosition(positionId, sizeDeltaUSD);
    }

    function closeHedge(bytes32 positionId) external onlyRole(GUARDIAN_ROLE) {
        Position memory pos = positions[positionId];
        if (pos.market == address(0)) revert Strategy__InvalidParameters();

        int256 pnl = exchange.closePosition(positionId);
        
        if (pnl > 0) {
            marginDeposited += uint256(pnl);
        } else {
            uint256 absPnl = uint256(-pnl);
            if (absPnl > marginDeposited) {
                marginDeposited = 0;
            } else {
                marginDeposited -= absPnl;
            }
        }

        delete positions[positionId];
        _removePositionId(positionId);
    }

    function _removePositionId(bytes32 positionId) internal {
        uint256 length = activePositionIds.length;
        for (uint256 i = 0; i < length; i++) {
            if (activePositionIds[i] == positionId) {
                activePositionIds[i] = activePositionIds[length - 1];
                activePositionIds.pop();
                break;
            }
        }
    }

    function estimatedTotalAssets() external view override returns (uint256) {
        int256 totalMTM = 0;
        for (uint256 i = 0; i < activePositionIds.length; i++) {
            totalMTM += exchange.getPositionValue(activePositionIds[i]);
        }
        
        if (totalMTM < 0 && uint256(-totalMTM) > marginDeposited) {
            return 0;
        }
        
        return totalMTM >= 0 ? marginDeposited + uint256(totalMTM) : marginDeposited - uint256(-totalMTM);
    }

    function estimatedAPY() external view override returns (int256) {
        return 0; // Hedging isn't yield-focused
    }

    function riskScore() external pure override returns (uint8) {
        return 30;
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

        for (uint256 i = 0; i < activePositionIds.length; i++) {
            bytes32 pid = activePositionIds[i];
            Position memory pos = positions[pid];
            
            if (pos.isLong) {
                delta += int256(pos.sizeUSD);
            } else {
                delta -= int256(pos.sizeUSD);
            }

            int256 fundingRate = exchange.getFundingRate(pos.market);
            // Rough approximation of theta: daily funding rate * notional
            theta += (fundingRate * int256(pos.sizeUSD)) / 1e18; 
        }
        
        return (delta, 0, 0, theta);
    }

    function maxDrawdownHistorical() external pure override returns (uint256) {
        return 2000; // 20%
    }

    function sharpeRatio30d() external pure override returns (int256) {
        return 5e17; 
    }

    function setActive(bool _active) external onlyRole(DEFAULT_ADMIN_ROLE) {
        isActive = _active;
    }
}
