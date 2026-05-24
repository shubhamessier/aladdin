// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IStrategy} from "../interfaces/IStrategy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {FixedPointMath} from "../libraries/FixedPointMath.sol";
import {
    Strategy__NotActive,
    Strategy__InvalidParameters,
    Vault__ZeroAmount
} from "../errors/VaultErrors.sol";

interface INonfungiblePositionManager {
    struct MintParams {
        address token0;
        address token1;
        uint24 fee;
        int24 tickLower;
        int24 tickUpper;
        uint256 amount0Desired;
        uint256 amount1Desired;
        uint256 amount0Min;
        uint256 amount1Min;
        address recipient;
        uint256 deadline;
    }
    function mint(MintParams calldata params) external payable returns (
        uint256 tokenId,
        uint128 liquidity,
        uint256 amount0,
        uint256 amount1
    );

    struct DecreaseLiquidityParams {
        uint256 tokenId;
        uint128 liquidity;
        uint256 amount0Min;
        uint256 amount1Min;
        uint256 deadline;
    }
    function decreaseLiquidity(DecreaseLiquidityParams calldata params) external payable returns (uint256 amount0, uint256 amount1);

    struct CollectParams {
        uint256 tokenId;
        address recipient;
        uint128 amount0Max;
        uint128 amount1Max;
    }
    function collect(CollectParams calldata params) external payable returns (uint256 amount0, uint256 amount1);
    
    function positions(uint256 tokenId) external view returns (
        uint96 nonce,
        address operator,
        address token0,
        address token1,
        uint24 fee,
        int24 tickLower,
        int24 tickUpper,
        uint128 liquidity,
        uint256 feeGrowthInside0LastX128,
        uint256 feeGrowthInside1LastX128,
        uint128 tokensOwed0,
        uint128 tokensOwed1
    );
}

contract LiquidityProvisionStrategy is IStrategy, AccessControl {
    using SafeERC20 for IERC20;

    bytes32 public constant VAULT_ROLE = keccak256("VAULT_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    address public immutable override underlyingToken; // token0
    address public immutable token1;
    INonfungiblePositionManager public immutable positionManager;
    
    bool public override isActive = true;
    uint256 public override maxCapacity = 5_000_000e18;
    
    uint256 public currentTokenId;
    uint128 public currentLiquidity;
    int24 public currentTickLower;
    int24 public currentTickUpper;

    uint24 public poolFee = 3000; // 0.3%
    int24 public tickSpacing = 60;
    
    uint256 public accumulatedFees0;
    uint256 public accumulatedFees1;

    constructor(address _underlyingToken, address _token1, address _positionManager) {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        underlyingToken = _underlyingToken;
        token1 = _token1;
        positionManager = INonfungiblePositionManager(_positionManager);
    }

    function deposit(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (!isActive) revert Strategy__NotActive();
        if (amount == 0) revert Vault__ZeroAmount();
        
        // Strategy expects to be funded with both tokens or handles it via vault
        // For simplicity, we assume we receive `amount` of underlyingToken, and we already have some token1 balance 
        // to match it (or vault sent both). Here we just try to use all balance.
        
        uint256 bal0 = amount; // assume vault sent exactly amount of underlying
        IERC20(underlyingToken).safeTransferFrom(msg.sender, address(this), amount);
        uint256 bal1 = IERC20(token1).balanceOf(address(this));
        
        IERC20(underlyingToken).forceApprove(address(positionManager), bal0);
        IERC20(token1).forceApprove(address(positionManager), bal1);
        
        // Tick computation mock: normally queried from pool.slot0()
        int24 currentTick = 0; // Mock current tick
        int24 rangeFactor = tickSpacing * 10; // Mock range
        
        currentTickLower = ((currentTick - rangeFactor) / tickSpacing) * tickSpacing;
        currentTickUpper = ((currentTick + rangeFactor) / tickSpacing) * tickSpacing;

        INonfungiblePositionManager.MintParams memory params = INonfungiblePositionManager.MintParams({
            token0: underlyingToken,
            token1: token1,
            fee: poolFee,
            tickLower: currentTickLower,
            tickUpper: currentTickUpper,
            amount0Desired: bal0,
            amount1Desired: bal1,
            amount0Min: 0,
            amount1Min: 0,
            recipient: address(this),
            deadline: block.timestamp + 120
        });

        (uint256 tokenId, uint128 liquidity, , ) = positionManager.mint(params);
        
        currentTokenId = tokenId;
        currentLiquidity = liquidity;

        return amount; // return underlying amount deposited
    }

    function withdraw(uint256 amount) external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (currentTokenId == 0) return 0;
        
        // Approximate proportion of liquidity to remove based on requested amount
        // This is a naive approximation.
        uint128 liqToRemove = uint128(amount); 
        if (liqToRemove > currentLiquidity) {
            liqToRemove = currentLiquidity;
        }

        INonfungiblePositionManager.DecreaseLiquidityParams memory params = INonfungiblePositionManager.DecreaseLiquidityParams({
            tokenId: currentTokenId,
            liquidity: liqToRemove,
            amount0Min: 0,
            amount1Min: 0,
            deadline: block.timestamp + 120
        });

        (uint256 amount0, uint256 amount1) = positionManager.decreaseLiquidity(params);
        currentLiquidity -= liqToRemove;

        // Collect the tokens
        INonfungiblePositionManager.CollectParams memory collectParams = INonfungiblePositionManager.CollectParams({
            tokenId: currentTokenId,
            recipient: address(this),
            amount0Max: type(uint128).max,
            amount1Max: type(uint128).max
        });
        positionManager.collect(collectParams);

        IERC20(underlyingToken).safeTransfer(msg.sender, amount0);
        IERC20(token1).safeTransfer(msg.sender, amount1);

        return amount0; // Return underlying token amount withdrawn
    }

    function withdrawAll() external override onlyRole(VAULT_ROLE) returns (uint256) {
        if (currentTokenId == 0) return 0;

        INonfungiblePositionManager.DecreaseLiquidityParams memory params = INonfungiblePositionManager.DecreaseLiquidityParams({
            tokenId: currentTokenId,
            liquidity: currentLiquidity,
            amount0Min: 0,
            amount1Min: 0,
            deadline: block.timestamp + 120
        });

        (uint256 amount0, uint256 amount1) = positionManager.decreaseLiquidity(params);
        currentLiquidity = 0;

        INonfungiblePositionManager.CollectParams memory collectParams = INonfungiblePositionManager.CollectParams({
            tokenId: currentTokenId,
            recipient: address(this),
            amount0Max: type(uint128).max,
            amount1Max: type(uint128).max
        });
        positionManager.collect(collectParams);

        IERC20(underlyingToken).safeTransfer(msg.sender, IERC20(underlyingToken).balanceOf(address(this)));
        IERC20(token1).safeTransfer(msg.sender, IERC20(token1).balanceOf(address(this)));

        currentTokenId = 0;
        return amount0;
    }

    function harvest() external override returns (int256 netPnL) {
        if (currentTokenId == 0) return 0;
        
        INonfungiblePositionManager.CollectParams memory collectParams = INonfungiblePositionManager.CollectParams({
            tokenId: currentTokenId,
            recipient: address(this),
            amount0Max: type(uint128).max,
            amount1Max: type(uint128).max
        });
        
        uint256 bal0Before = IERC20(underlyingToken).balanceOf(address(this));
        uint256 bal1Before = IERC20(token1).balanceOf(address(this));
        
        positionManager.collect(collectParams);
        
        uint256 fees0 = IERC20(underlyingToken).balanceOf(address(this)) - bal0Before;
        uint256 fees1 = IERC20(token1).balanceOf(address(this)) - bal1Before;
        
        accumulatedFees0 += fees0;
        accumulatedFees1 += fees1;

        // Mock: just returning fees0 as net PnL for simplicity
        return int256(fees0);
    }

    function rerange(int24 newTickLower, int24 newTickUpper) external onlyRole(GUARDIAN_ROLE) {
        if (currentTokenId == 0) revert Strategy__NotActive();
        
        // 1. Withdraw all liquidity
        INonfungiblePositionManager.DecreaseLiquidityParams memory decParams = INonfungiblePositionManager.DecreaseLiquidityParams({
            tokenId: currentTokenId,
            liquidity: currentLiquidity,
            amount0Min: 0,
            amount1Min: 0,
            deadline: block.timestamp + 120
        });
        positionManager.decreaseLiquidity(decParams);
        
        INonfungiblePositionManager.CollectParams memory collectParams = INonfungiblePositionManager.CollectParams({
            tokenId: currentTokenId,
            recipient: address(this),
            amount0Max: type(uint128).max,
            amount1Max: type(uint128).max
        });
        positionManager.collect(collectParams);
        
        // 2. Mint new position
        uint256 bal0 = IERC20(underlyingToken).balanceOf(address(this));
        uint256 bal1 = IERC20(token1).balanceOf(address(this));
        
        IERC20(underlyingToken).forceApprove(address(positionManager), bal0);
        IERC20(token1).forceApprove(address(positionManager), bal1);
        
        currentTickLower = newTickLower;
        currentTickUpper = newTickUpper;

        INonfungiblePositionManager.MintParams memory params = INonfungiblePositionManager.MintParams({
            token0: underlyingToken,
            token1: token1,
            fee: poolFee,
            tickLower: currentTickLower,
            tickUpper: currentTickUpper,
            amount0Desired: bal0,
            amount1Desired: bal1,
            amount0Min: 0,
            amount1Min: 0,
            recipient: address(this),
            deadline: block.timestamp + 120
        });

        (uint256 tokenId, uint128 liquidity, , ) = positionManager.mint(params);
        
        currentTokenId = tokenId;
        currentLiquidity = liquidity;
    }

    function estimatedTotalAssets() external view override returns (uint256) {
        if (currentTokenId == 0) return 0;
        
        // Reading position info
        (,,,,,,,uint128 liquidity,,,,) = positionManager.positions(currentTokenId);
        
        // Simplified estimate (assumes 1:1 price for mock purposes)
        // A real impl needs to compute token0 and token1 amounts based on current tick and liquidity
        return uint256(liquidity); 
    }

    function estimatedAPY() external view override returns (int256) {
        return 12e18; // Mock 12% APY
    }

    function riskScore() external pure override returns (uint8) {
        return 40; // Medium risk (IL)
    }

    function liquidationValue() external view override returns (uint256) {
        return this.estimatedTotalAssets();
    }

    function getGreeks() external pure override returns (
        int256 delta,
        int256 gamma,
        int256 vega,
        int256 theta
    ) {
        // Positive gamma for LP? No, LP is short gamma
        return (5e17, -1e16, 0, 1e17); 
    }

    function maxDrawdownHistorical() external pure override returns (uint256) {
        return 1500; // 15%
    }

    function sharpeRatio30d() external pure override returns (int256) {
        return 1e18; // Mock
    }

    function setActive(bool _active) external onlyRole(DEFAULT_ADMIN_ROLE) {
        isActive = _active;
    }
}
