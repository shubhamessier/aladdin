// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title ITreasuryVault
 * @notice Interface for the master custodian of all treasury assets.
 */
interface ITreasuryVault {
    /**
     * @notice Struct for tracking balances and P&L for a specific asset.
     */
    struct AssetLedger {
        uint256 freeBalance;            // Liquid, available in vault
        uint256 allocatedToStrategies;  // Deployed to yield strategies
        uint256 pendingWithdrawals;     // Queued for withdrawal
        uint256 reservedForHedges;      // Margin for derivative positions
        uint256 cumulativeDeposits;     // Lifetime deposits (for analytics)
        uint256 cumulativeWithdrawals;  // Lifetime withdrawals
        int256  realizedPnL;            // Cumulative realized profit/loss
        uint256 lastUpdatedBlock;       // Block number of the last update
    }

    /**
     * @notice Struct representing a point-in-time snapshot of the portfolio's value and risk.
     */
    struct PortfolioSnapshot {
        uint256 totalValueUSD;              // Total portfolio value in USD
        uint256 totalStableValueUSD;        // Total value of stablecoins in USD
        uint256 totalVolatileValueUSD;      // Total value of volatile assets in USD
        uint256 totalStrategyValueUSD;      // Total value deployed to strategies in USD
        uint256 totalDerivativeExposureUSD; // Gross notional of derivatives
        int256  netDelta;                   // Net directional exposure in USD
        uint256 timestamp;                  // Timestamp of the snapshot
        uint256 blockNumber;                // Block number of the snapshot
    }

    /**
     * @notice Struct representing an open derivative position.
     */
    struct DerivativePosition {
        address market;          // Perp market contract address
        bool isLong;             // Direction: true if long, false if short
        uint256 sizeUSD;         // Notional size in USD
        uint256 entryPrice;      // Average entry price (WAP)
        uint256 margin;          // Collateral posted
        int256 unrealizedPnL;    // Current unrealized P&L
        int256 cumulativeFunding;// Net funding received/paid
        uint256 openTimestamp;   // When the position was opened
        uint256 lastUpdateBlock; // Block number of the last update
    }

    /**
     * @notice Struct representing a queued withdrawal request.
     */
    struct WithdrawalRequest {
        address depositor;       // Address of the user requesting withdrawal
        address token;           // Token requested
        uint256 amount;          // Amount requested
        uint256 unlockTimestamp; // When the withdrawal can be executed
        bool isExecuted;         // Whether it has been processed
        bool isCancelled;        // Whether it was cancelled by governance/guardian
    }

    /**
     * @notice Struct representing a single action in a batch.
     */
    struct Action {
        uint8 actionType;        // matches ISecurityHooks.ActionType
        address target;          // target contract (router, strategy, market)
        address tokenIn;         // input token
        address tokenOut;        // output token
        uint256 amountIn;        // input amount
        uint256 minAmountOut;    // min output amount
        bool isLong;             // derivative direction
        uint256 derivativeSize;  // derivative size
        bytes data;              // encoded call data
    }

    // === Core Functions ===

    /**
     * @notice Executes a batch of actions atomically.
     * @param actions Array of Action structs.
     */
    function executeBatchActions(Action[] calldata actions) external;

    /**
     * @notice Opens a new derivative position on a supported market.
     * @param market The market address.
     * @param isLong True if long, false if short.
     * @param sizeUSD The notional size in USD.
     * @param leverage The leverage to use.
     */
    function openDerivativePosition(address market, bool isLong, uint256 sizeUSD, uint256 leverage) external;

    /**
     * @notice Closes an existing derivative position.
     * @param positionId The ID/key of the position to close.
     */
    function closeDerivativePosition(bytes32 positionId) external;

    /**
     * @notice Deposits a whitelisted asset into the treasury.
     * @param token The address of the token to deposit.
     * @param amount The amount to deposit.
     */
    function deposit(address token, uint256 amount) external;

    /**
     * @notice Requests or executes a withdrawal of an asset.
     * @param token The address of the token to withdraw.
     * @param amount The amount to withdraw.
     * @return requestId The ID of the withdrawal request (if queued), or 0 if executed immediately.
     */
    function withdraw(address token, uint256 amount) external returns (uint256 requestId);

    /**
     * @notice Claims a queued withdrawal after the timelock has expired.
     * @param requestId The ID of the withdrawal request.
     */
    function claimWithdrawal(uint256 requestId) external;

    /**
     * @notice Executes a swap through a whitelisted DEX router.
     * @param tokenIn The address of the input token.
     * @param tokenOut The address of the output token.
     * @param amountIn The amount of tokenIn to swap.
     * @param minAmountOut The minimum amount of tokenOut to receive.
     * @param deadline The timestamp after which the swap is invalid.
     * @param routeData Encoded swap path data for the router.
     * @return amountOut The actual amount of tokenOut received.
     */
    function executeSwap(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut,
        uint256 deadline,
        bytes calldata routeData
    ) external returns (uint256 amountOut);

    // === Views ===

    /**
     * @notice Retrieves the asset ledger for a specific token.
     * @param token The address of the token.
     * @return The AssetLedger struct.
     */
    function getAssetLedger(address token) external view returns (AssetLedger memory);

    /**
     * @notice Retrieves the latest portfolio snapshot.
     * @return The PortfolioSnapshot struct.
     */
    function getLatestSnapshot() external view returns (PortfolioSnapshot memory);

    /**
     * @notice Retrieves a specific derivative position.
     * @param positionKey The keccak256 hash of (market, isLong).
     * @return The DerivativePosition struct.
     */
    function getDerivativePosition(bytes32 positionKey) external view returns (DerivativePosition memory);

    /**
     * @notice Retrieves the current circuit breaker level.
     * @return The CB level (0 = HEALTHY).
     */
    function currentCBLevel() external view returns (uint8);

    function maxDailyVolumeUSD() external view returns (uint256);
    function maxTradeUSD() external view returns (uint256);
    function maxGasPriceWei() external view returns (uint256);
    function maxSlippageBps() external view returns (uint256);

    /**
     * @notice Retrieves the portfolio high water mark in USD.
     * @return The high water mark.
     */
    function portfolioHighWaterMark() external view returns (uint256);
}
