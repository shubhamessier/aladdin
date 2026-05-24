// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title IStrategy
 * @notice Interface for all yield and hedging strategies in the Treasury Vault.
 */
interface IStrategy {
    // === Lifecycle ===

    /**
     * @notice Deposits the underlying asset into the strategy.
     * @param amount The amount of the underlying asset to deposit.
     * @return sharesReceived The number of strategy shares received.
     */
    function deposit(uint256 amount) external returns (uint256 sharesReceived);

    /**
     * @notice Withdraws a specific amount of the underlying asset from the strategy.
     * @param amount The amount of the underlying asset to withdraw.
     * @return actualWithdrawn The actual amount withdrawn (may be less due to slippage/fees).
     */
    function withdraw(uint256 amount) external returns (uint256 actualWithdrawn);

    /**
     * @notice Withdraws all deposited assets from the strategy.
     * @return totalWithdrawn The total amount withdrawn.
     */
    function withdrawAll() external returns (uint256 totalWithdrawn);

    /**
     * @notice Harvests yields or performs maintenance on the strategy.
     * @return netPnL The net profit or loss realized during harvest (can be negative).
     */
    function harvest() external returns (int256 netPnL);

    // === Views ===

    /**
     * @notice Gets the estimated total assets held by the strategy in terms of the underlying token.
     * @return The total assets.
     */
    function estimatedTotalAssets() external view returns (uint256);

    /**
     * @notice Gets the estimated Annual Percentage Yield (APY) of the strategy.
     * @return The estimated APY (1e18 scale, can be negative).
     */
    function estimatedAPY() external view returns (int256);

    /**
     * @notice Checks if the strategy is currently active and accepting deposits.
     * @return True if active, false otherwise.
     */
    function isActive() external view returns (bool);

    /**
     * @notice Gets the risk score of the strategy.
     * @return The risk score from 1 to 100 (100 = highest risk).
     */
    function riskScore() external view returns (uint8);

    /**
     * @notice Gets the address of the underlying token used by the strategy.
     * @return The address of the underlying token.
     */
    function underlyingToken() external view returns (address);

    /**
     * @notice Gets the maximum capacity this strategy can absorb.
     * @return The maximum capacity in terms of the underlying token.
     */
    function maxCapacity() external view returns (uint256);

    /**
     * @notice Gets the estimated liquidation value if exited immediately.
     * @return The estimated liquidation value (accounting for slippage/IL/fees).
     */
    function liquidationValue() external view returns (uint256);

    // === Risk ===

    /**
     * @notice Gets the Greeks (risk metrics) for the strategy's positions.
     * @return delta The directional exposure.
     * @return gamma The delta sensitivity to price changes.
     * @return vega The volatility sensitivity.
     * @return theta The time decay.
     */
    function getGreeks() external view returns (
        int256 delta,
        int256 gamma,
        int256 vega,
        int256 theta
    );

    /**
     * @notice Gets the worst historically observed drawdown for this strategy.
     * @return The maximum historical drawdown (in basis points, 1e4 scale).
     */
    function maxDrawdownHistorical() external view returns (uint256);

    /**
     * @notice Gets the 30-day rolling Sharpe ratio.
     * @return The 30-day Sharpe ratio (1e18 scale).
     */
    function sharpeRatio30d() external view returns (int256);
}
