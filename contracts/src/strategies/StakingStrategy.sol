// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IStrategy} from "../interfaces/IStrategy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {
    Strategy__NotActive,
    Vault__ZeroAmount
} from "../errors/VaultErrors.sol";

// Mock Lido Interface
interface ILido {
    function submit(address referral) external payable returns (uint256);
}

// Mock WETH Interface
interface IWETH {
    function withdraw(uint wad) external;
    function deposit() external payable;
}

contract StakingStrategy is IStrategy, AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant VAULT_ROLE = keccak256("VAULT_ROLE");

    address public immutable override underlyingToken; // WETH
    IERC20 public immutable stETH;
    ILido public immutable lido;
    
    bool public override isActive = true;
    uint256 public override maxCapacity = 2_000_000e18;
    
    constructor(address _weth, address _stETH, address _lido) {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        underlyingToken = _weth;
        stETH = IERC20(_stETH);
        lido = ILido(_lido);
    }

    receive() external payable {}

    function deposit(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (!isActive) revert Strategy__NotActive();
        if (amount == 0) revert Vault__ZeroAmount();

        IERC20(underlyingToken).safeTransferFrom(msg.sender, address(this), amount);
        
        IWETH(underlyingToken).withdraw(amount); // Unwrap WETH to ETH
        lido.submit{value: amount}(address(0)); // Submit ETH to Lido
        
        return amount; // 1:1 roughly
    }

    function withdraw(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        // Withdrawals from Lido are delayed and via NFT.
        // For a fast exit we would swap stETH for WETH on Curve/Uniswap.
        // For this mock, we assume 1:1 conversion.
        stETH.safeTransfer(msg.sender, amount); // Mock returning stETH back to Vault
        return amount;
    }

    function withdrawAll() external override onlyRole(VAULT_ROLE) returns (uint256) {
        uint256 amount = stETH.balanceOf(address(this));
        stETH.safeTransfer(msg.sender, amount);
        return amount;
    }

    function harvest() external override returns (int256 netPnL) {
        return 0; 
    }

    function estimatedTotalAssets() external view override returns (uint256) {
        return stETH.balanceOf(address(this));
    }

    function estimatedAPY() external view override returns (int256) {
        return 4e18; // 4%
    }

    function riskScore() external pure override returns (uint8) {
        return 20;
    }

    function liquidationValue() external view override returns (uint256) {
        return stETH.balanceOf(address(this)); 
    }

    function getGreeks() external pure override returns (
        int256 delta,
        int256 gamma,
        int256 vega,
        int256 theta
    ) {
        return (1e18, 0, 0, 1e17); // 1 Delta
    }

    function maxDrawdownHistorical() external pure override returns (uint256) {
        return 500; // 5% depeg historical max
    }

    function sharpeRatio30d() external pure override returns (int256) {
        return 2e18; 
    }

    function setActive(bool _active) external onlyRole(DEFAULT_ADMIN_ROLE) {
        isActive = _active;
    }
}
