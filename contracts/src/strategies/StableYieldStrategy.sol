// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IStrategy} from "../interfaces/IStrategy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IAavePool} from "../interfaces/IAavePool.sol";
import {
    Strategy__NotActive,
    Strategy__UtilizationTooHigh
} from "../errors/VaultErrors.sol";

contract StableYieldStrategy is IStrategy, AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant VAULT_ROLE = keccak256("VAULT_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    address public immutable override underlyingToken;
    IAavePool public immutable aavePool;
    IERC20 public immutable aToken;

    bool public override isActive = true;
    uint256 public override maxCapacity = 10_000_000e18;

    uint256 public lastKnownAssets;

    event Shortfall(uint256 expected, uint256 actual);

    constructor(address _underlyingToken, address _aavePool, address _aToken) {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        underlyingToken = _underlyingToken;
        aavePool = IAavePool(_aavePool);
        aToken = IERC20(_aToken);
    }

    function deposit(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (!isActive) revert Strategy__NotActive();

        // Check utilization rate
        IAavePool.ReserveData memory reserveData = aavePool.getReserveData(underlyingToken);
        
        // In a real Aave deployment, you would calculate total debt from variable and stable debt tokens.
        // For simplicity in this mock, we assume we can read balances.
        // totalDebt = IERC20(reserveData.variableDebtTokenAddress).totalSupply() + IERC20(reserveData.stableDebtTokenAddress).totalSupply();
        // availableLiquidity = IERC20(underlyingToken).balanceOf(reserveData.aTokenAddress);
        
        uint256 totalDebt = IERC20(reserveData.variableDebtTokenAddress).totalSupply() + IERC20(reserveData.stableDebtTokenAddress).totalSupply();
        uint256 availableLiquidity = IERC20(underlyingToken).balanceOf(reserveData.aTokenAddress);
        
        if (totalDebt + availableLiquidity > 0) {
            uint256 utilizationBps = (totalDebt * 10000) / (totalDebt + availableLiquidity);
            if (utilizationBps > 8500) {
                revert Strategy__UtilizationTooHigh(utilizationBps, 8500);
            }
        }

        IERC20(underlyingToken).safeTransferFrom(msg.sender, address(this), amount);
        
        IERC20(underlyingToken).forceApprove(address(aavePool), amount);
        aavePool.supply(underlyingToken, amount, address(this), 0);
        
        lastKnownAssets = aToken.balanceOf(address(this));

        return amount;
    }

    function withdraw(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        uint256 currentAssets = aToken.balanceOf(address(this));
        uint256 withdrawAmount = amount > currentAssets ? currentAssets : amount;
        
        uint256 actualWithdrawn;
        try aavePool.withdraw(underlyingToken, withdrawAmount, msg.sender) returns (uint256 withdrawn) {
            actualWithdrawn = withdrawn;
        } catch {
            // If the full amount fails, try withdrawing available liquidity
            IAavePool.ReserveData memory reserveData = aavePool.getReserveData(underlyingToken);
            uint256 available = IERC20(underlyingToken).balanceOf(reserveData.aTokenAddress);
            if (available > 0) {
                uint256 fallbackAmount = available < withdrawAmount ? available : withdrawAmount;
                actualWithdrawn = aavePool.withdraw(underlyingToken, fallbackAmount, msg.sender);
            }
        }

        if (actualWithdrawn < amount) {
            emit Shortfall(amount, actualWithdrawn);
        }

        lastKnownAssets = aToken.balanceOf(address(this));
        return actualWithdrawn;
    }

    function withdrawAll() external override onlyRole(VAULT_ROLE) returns (uint256) {
        uint256 bal = aToken.balanceOf(address(this));
        uint256 actualWithdrawn;
        try aavePool.withdraw(underlyingToken, type(uint256).max, msg.sender) returns (uint256 withdrawn) {
            actualWithdrawn = withdrawn;
        } catch {
             IAavePool.ReserveData memory reserveData = aavePool.getReserveData(underlyingToken);
             uint256 available = IERC20(underlyingToken).balanceOf(reserveData.aTokenAddress);
             if (available > 0) {
                uint256 fallbackAmount = available < bal ? available : bal;
                actualWithdrawn = aavePool.withdraw(underlyingToken, fallbackAmount, msg.sender);
             }
        }

        lastKnownAssets = aToken.balanceOf(address(this));
        return actualWithdrawn;
    }

    function harvest() external override returns (int256 netPnL) {
        uint256 currentAssets = aToken.balanceOf(address(this));
        
        if (currentAssets > lastKnownAssets) {
            netPnL = int256(currentAssets - lastKnownAssets);
        } else {
            netPnL = -int256(lastKnownAssets - currentAssets);
        }

        lastKnownAssets = currentAssets;
        return netPnL;
    }

    function estimatedTotalAssets() external view override returns (uint256) {
        return aToken.balanceOf(address(this));
    }

    function estimatedAPY() external view override returns (int256) {
        return 5e18; // 5% APY mock
    }

    function riskScore() external pure override returns (uint8) {
        return 10; // Low risk
    }

    function liquidationValue() external view override returns (uint256) {
        return aToken.balanceOf(address(this));
    }

    function getGreeks() external pure override returns (
        int256 delta,
        int256 gamma,
        int256 vega,
        int256 theta
    ) {
        return (0, 0, 0, 1e18); // positive theta (accruing interest)
    }

    function maxDrawdownHistorical() external pure override returns (uint256) {
        return 100; // 1%
    }

    function sharpeRatio30d() external pure override returns (int256) {
        return 3e18;
    }

    // Admin
    function setActive(bool _active) external onlyRole(DEFAULT_ADMIN_ROLE) {
        isActive = _active;
    }
}
