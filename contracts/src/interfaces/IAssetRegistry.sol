// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title IAssetRegistry
 * @notice Interface for the canonical registry of all assets the treasury is authorized to hold.
 */
interface IAssetRegistry {
    /**
     * @notice Enum representing the risk tier of an asset.
     */
    enum RiskTier {
        STABLE,        // Tier 1: Stablecoins (USDC, USDT, DAI)
        CORE,          // Tier 2: Blue-chip crypto (WETH, WBTC)
        VOLATILE,      // Tier 3: Mid/small-cap tokens
        DERIVATIVE,    // Tier 4: Synthetic/derivative positions
        YIELD_BEARING  // Tier 5: Yield tokens (stETH, aUSDC, LP tokens)
    }

    /**
     * @notice Struct containing configuration and risk parameters for a specific asset.
     */
    struct AssetConfig {
        address token;                 // Token address
        string symbol;                 // Token symbol
        uint8 decimals;                // Token decimals
        RiskTier tier;                 // Risk classification
        uint256 maxAllocationBps;      // Max % of portfolio (10000 = 100%)
        uint256 minAllocationBps;      // Min % (used for mandatory stablecoin reserves)
        address primaryOracle;         // Primary price feed
        address secondaryOracle;       // Secondary price feed (different provider)
        address tertiaryOracle;        // Tertiary (optional, address(0) if none)
        uint256 maxOracleDeviationBps; // Max allowed deviation between feeds
        uint256 maxStalenessSeconds;   // Max age of price data before flagging stale
        uint8 liquidityScore;          // 1-100, updated by guardian
        uint256 maxPositionUSD;        // Absolute max USD value in this asset
        uint256 minTradeUSD;           // Minimum trade size
        bool isActive;                 // Can be frozen without removal
        bool isBorrowable;             // Can this asset be lent out
        bool isShortable;              // Can we short via perps
        uint256 haircut;               // Collateral haircut in bps (e.g., 2000 = 80% counts as collateral)
    }

    /**
     * @notice Struct containing constraints for a specific risk tier.
     */
    struct TierConfig {
        uint256 maxTotalAllocationBps;  // Max combined allocation for this tier
        uint256 minTotalAllocationBps;  // Min combined allocation for this tier
        uint256 maxSingleAssetBps;      // Max any single asset in this tier
        uint256 defaultHaircutBps;      // Default collateral haircut for this tier
    }

    /**
     * @notice Struct representing a snapshot of an asset's state.
     */
    struct AssetSnapshot {
        address token;
        uint256 balance;
        uint256 valueUSD;
        uint256 allocationBps;
        RiskTier tier;
        uint8 liquidityScore;
    }

    /**
     * @notice Struct containing snapshots for all active assets.
     */
    struct SnapshotData {
        AssetSnapshot[] assets;
        uint256 totalPortfolioUSD;
    }

    // === State Modifications ===

    /**
     * @notice Adds a new asset to the registry.
     * @param config The AssetConfig struct for the new asset.
     */
    function addAsset(AssetConfig calldata config) external;

    /**
     * @notice Updates the configuration of an existing asset.
     * @param token The address of the token to update.
     * @param config The new AssetConfig struct.
     */
    function updateAsset(address token, AssetConfig calldata config) external;

    /**
     * @notice Freezes an asset, preventing new positions from being opened.
     * @param token The address of the token to freeze.
     */
    function freezeAsset(address token) external;

    /**
     * @notice Unfreezes a previously frozen asset.
     * @param token The address of the token to unfreeze.
     */
    function unfreezeAsset(address token) external;

    // === Views ===

    /**
     * @notice Retrieves the configuration for a specific asset.
     * @param token The address of the token.
     * @return The AssetConfig struct.
     */
    function getAssetConfig(address token) external view returns (AssetConfig memory);

    /**
     * @notice Retrieves the configuration for a specific tier.
     * @param tier The RiskTier.
     * @return The TierConfig struct.
     */
    function getTierConfig(RiskTier tier) external view returns (TierConfig memory);

    /**
     * @notice Retrieves a snapshot of all active assets in the portfolio.
     * @return The SnapshotData struct containing the current state.
     */
    function getPortfolioSnapshot() external view returns (SnapshotData memory);

    /**
     * @notice Retrieves a list of active assets belonging to a specific tier.
     * @param tier The RiskTier to query.
     * @return An array of token addresses.
     */
    function getAssetsByTier(RiskTier tier) external view returns (address[] memory);

    /**
     * @notice Validates if a proposed allocation change violates any constraints.
     * @param token The address of the token.
     * @param newValueUSD The proposed new value in USD.
     * @param totalPortfolioUSD The projected total portfolio value in USD.
     * @return isValid True if the allocation is valid.
     * @return reason A string describing the reason if invalid.
     */
    function validateAllocation(
        address token,
        uint256 newValueUSD,
        uint256 totalPortfolioUSD
    ) external view returns (bool isValid, string memory reason);
}
