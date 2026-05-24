// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title IOracleAdapter
 * @notice Interface for the Oracle Adapter, which provides sanitized price data.
 */
interface IOracleAdapter {
    /**
     * @notice Enum representing the health status of the price data.
     */
    enum PriceStatus { GOOD, DEGRADED, SUSPECT, STALE }

    /**
     * @notice Struct containing comprehensive price information and metadata.
     */
    struct PriceData {
        uint256 price;           // USD price, 18 decimals
        uint8 tokenDecimals;     // Token's native decimals
        PriceStatus status;      // Health status of the price
        uint256 timestamp;       // When this price was resolved
        uint256 numActiveSources;// Number of active oracle sources used
        uint256 maxDeviation;    // Max deviation between sources, in basis points (bps)
        uint256 twap;            // 20-period TWAP for reference
        uint256 confidence;      // 0-10000 bps, derived from status + deviation + staleness
    }

    /**
     * @notice Retrieves the current price data for a given token.
     * @param token The address of the token.
     * @return The PriceData struct containing the price and metadata.
     */
    function getPrice(address token) external view returns (PriceData memory);

    /**
     * @notice Retrieves price data for multiple tokens in a single call.
     * @param tokens An array of token addresses.
     * @return An array of PriceData structs corresponding to the tokens.
     */
    function getBatchPrices(address[] calldata tokens) external view returns (PriceData[] memory);

    /**
     * @notice Retrieves the Time-Weighted Average Price (TWAP) for a token.
     * @param token The address of the token.
     * @return twap The current TWAP value.
     * @return numObservations The number of observations used to calculate the TWAP.
     */
    function getTWAP(address token) external view returns (uint256 twap, uint256 numObservations);

    /**
     * @notice Retrieves just the health status of a token's price feed.
     * @param token The address of the token.
     * @return The current PriceStatus.
     */
    function getPriceStatus(address token) external view returns (PriceStatus);

    /**
     * @notice Checks if trading is allowed for a token based on its price feed health.
     * @param token The address of the token.
     * @return allowed True if trading is allowed.
     * @return reason A string describing the reason if trading is not allowed.
     */
    function isTradeAllowed(address token) external view returns (bool allowed, string memory reason);
}
