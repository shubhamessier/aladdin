// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IOracleAdapter} from "../interfaces/IOracleAdapter.sol";
import {IAssetRegistry} from "../interfaces/IAssetRegistry.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {Oracle__NoSourcesAvailable, Oracle__AllSourcesStale, Oracle__PriceSuspect, Oracle__PendingConfirmation, Oracle__InvalidFeed, Oracle__ZeroPrice} from "../errors/OracleErrors.sol";

interface IPyth {
    struct Price {
        int64 price;
        uint64 conf;
        int32 expo;
        uint256 publishTime;
    }
    function getPriceNoOlderThan(bytes32 id, uint256 age) external view returns (Price memory price);
}

interface AggregatorV3Interface {
    function latestRoundData() external view returns (
        uint80 roundId,
        int256 answer,
        uint256 startedAt,
        uint256 updatedAt,
        uint80 answeredInRound
    );
}

contract OracleAdapter is IOracleAdapter {
    IAssetRegistry public immutable assetRegistry;

    uint256 public constant MAX_STALENESS_SECONDS = 3600;
    uint256 public constant MAX_DEVIATION_BPS = 500; // 5%
    uint256 public constant EMERGENCY_DEVIATION_BPS = 1000; // 10%
    uint256 public constant TWAP_UPDATE_INTERVAL = 60; // 60 seconds
    
    // Feeds configuration
    mapping(address => address) public chainlinkFeeds;
    mapping(address => bytes32) public pythFeeds;
    mapping(address => address) public pythAddress;
    
    // Flash loan defense
    struct PendingPrice {
        uint256 price;
        uint256 blockNumber;
    }
    mapping(address => PendingPrice) public pendingPrices;

    struct TokenState {
        uint256[20] twapBuffer;
        uint8 twapIndex;
        uint8 twapCount;
        uint256 lastTwapUpdate;
        uint256 lastGoodPrice;
        uint256 cooldownUntil;
    }

    mapping(address => TokenState) private _states;

    constructor(address _assetRegistry) {
        assetRegistry = IAssetRegistry(_assetRegistry);
    }
    
    function setFeeds(address token, address chainlinkFeed, address pythContract, bytes32 pythFeedId) external {
        bytes32 GOVERNOR_ROLE = keccak256("GOVERNOR_ROLE");
        require(IAccessControl(address(assetRegistry)).hasRole(GOVERNOR_ROLE, msg.sender), "OracleAdapter: unauthorized");
        chainlinkFeeds[token] = chainlinkFeed;
        pythAddress[token] = pythContract;
        pythFeeds[token] = pythFeedId;
    }

    function _getChainlinkPrice(address token) internal view returns (uint256 price, uint256 timestamp) {
        address feed = chainlinkFeeds[token];
        if (feed == address(0)) return (0, 0);
        try AggregatorV3Interface(feed).latestRoundData() returns (
            uint80 roundId,
            int256 answer,
            uint256 /*startedAt*/,
            uint256 updatedAt,
            uint80 /*answeredInRound*/
        ) {
            if (roundId > 0 && answer > 0 && updatedAt > 0 && (block.timestamp - updatedAt <= MAX_STALENESS_SECONDS)) {
                // assume 8 decimals for chainlink, convert to 18
                return (uint256(answer) * 1e10, updatedAt);
            }
        } catch {}
        return (0, 0);
    }
    
    function _getPythPrice(address token) internal view returns (uint256 price, uint256 timestamp) {
        address pythContract = pythAddress[token];
        bytes32 feedId = pythFeeds[token];
        if (pythContract == address(0) || feedId == bytes32(0)) return (0, 0);
        
        try IPyth(pythContract).getPriceNoOlderThan(feedId, MAX_STALENESS_SECONDS) returns (IPyth.Price memory pythPrice) {
            if (pythPrice.price > 0) {
                // Adjust Pyth expo to 18 decimals
                if (pythPrice.expo < 0) {
                    uint256 adjust = 10 ** uint256(int256(-pythPrice.expo));
                    price = (uint256(int256(pythPrice.price)) * 1e18) / adjust;
                } else {
                    uint256 adjust = 10 ** uint256(int256(pythPrice.expo));
                    price = uint256(int256(pythPrice.price)) * 1e18 * adjust;
                }
                return (price, pythPrice.publishTime);
            }
        } catch {}
        return (0, 0);
    }
    
    function _getGuardianPrice(address token) internal view returns (uint256 price, uint256 timestamp) {
        // Fallback or Guardian injected, simulated as lastGoodPrice
        TokenState storage state = _states[token];
        if (state.lastGoodPrice > 0 && (block.timestamp - state.lastTwapUpdate <= MAX_STALENESS_SECONDS)) {
            return (state.lastGoodPrice, state.lastTwapUpdate);
        }
        return (0, 0);
    }

    function _updateTWAPBuffer(address token, uint256 newPrice) internal {
        if (newPrice == 0) return;
        TokenState storage state = _states[token];
        if (block.timestamp - state.lastTwapUpdate >= TWAP_UPDATE_INTERVAL) {
            state.twapBuffer[state.twapIndex] = newPrice;
            state.twapIndex = (state.twapIndex + 1) % 20;
            if (state.twapCount < 20) state.twapCount++;
            state.lastTwapUpdate = block.timestamp;
            state.lastGoodPrice = newPrice;
        }
    }

    // A deviation logic
    function _deviation(uint256 p1, uint256 p2) internal pure returns (uint256) {
        if (p1 == 0 || p2 == 0) return type(uint256).max;
        uint256 minPrice = p1 < p2 ? p1 : p2;
        uint256 diff = p1 > p2 ? p1 - p2 : p2 - p1;
        return (diff * 10000) / minPrice;
    }

    // Resolves and validates price
    function resolvePrice(address token) public returns (PriceData memory) {
        (uint256 cPrice, ) = _getChainlinkPrice(token);
        (uint256 pPrice, ) = _getPythPrice(token);
        (uint256 gPrice, ) = _getGuardianPrice(token);
        
        uint256[] memory prices = new uint256[](3);
        uint256 count = 0;
        if (cPrice > 0) prices[count++] = cPrice;
        if (pPrice > 0) prices[count++] = pPrice;
        if (gPrice > 0 && count < 2) prices[count++] = gPrice; // Use guardian only if needed
        
        TokenState storage state = _states[token];
        uint256 twap = _calculateTWAP(state.twapBuffer, state.twapCount);
        uint256 finalPrice;
        PriceStatus status;
        
        if (count == 3) {
            _sort(prices, count);
            finalPrice = prices[1]; // Median
            uint256 dev1 = _deviation(prices[0], prices[1]);
            uint256 dev2 = _deviation(prices[1], prices[2]);
            if (dev1 > MAX_DEVIATION_BPS || dev2 > MAX_DEVIATION_BPS) {
                status = PriceStatus.DEGRADED;
            } else {
                status = PriceStatus.GOOD;
            }
        } else if (count == 2) {
            uint256 dev = _deviation(prices[0], prices[1]);
            if (dev <= MAX_DEVIATION_BPS) {
                finalPrice = (prices[0] + prices[1]) / 2;
                status = PriceStatus.GOOD;
            } else {
                // Check against TWAP
                uint256 dev0 = _deviation(prices[0], twap);
                uint256 dev1 = _deviation(prices[1], twap);
                if (dev0 < dev1) {
                    finalPrice = prices[0];
                } else {
                    finalPrice = prices[1];
                }
                status = PriceStatus.SUSPECT;
            }
        } else if (count == 1) {
            uint256 dev = _deviation(prices[0], twap);
            if (dev <= EMERGENCY_DEVIATION_BPS) {
                finalPrice = prices[0];
                status = PriceStatus.DEGRADED;
            } else {
                finalPrice = twap > 0 ? twap : prices[0];
                status = PriceStatus.SUSPECT;
            }
        } else {
            status = PriceStatus.STALE;
            finalPrice = state.lastGoodPrice;
            if (finalPrice == 0) revert Oracle__NoSourcesAvailable(token);
        }

        // Flash-loan defense logic
        if (status != PriceStatus.STALE && _deviation(finalPrice, twap) > MAX_DEVIATION_BPS && twap > 0) {
            PendingPrice memory pending = pendingPrices[token];
            if (pending.blockNumber > 0 && pending.blockNumber < block.number) {
                // Confirmed in a subsequent block
                delete pendingPrices[token];
            } else if (pending.blockNumber == 0) {
                // New large deviation, pend it
                pendingPrices[token] = PendingPrice({price: finalPrice, blockNumber: block.number});
                revert Oracle__PendingConfirmation(token, finalPrice, block.number);
            } else {
                // Same block, revert
                revert Oracle__PendingConfirmation(token, pending.price, pending.blockNumber);
            }
        } else {
             delete pendingPrices[token];
        }

        if (status != PriceStatus.STALE) {
             _updateTWAPBuffer(token, finalPrice);
        }

        return PriceData({
            price: finalPrice, 
            tokenDecimals: 18,
            status: status,
            timestamp: block.timestamp,
            numActiveSources: count,
            maxDeviation: 0, 
            twap: twap,
            confidence: 10000
        });
    }

    function getPrice(address token) public view override returns (PriceData memory) {
        // Real logic is in resolvePrice. getPrice just falls back to view-only TWAP/lastGood
        TokenState storage state = _states[token];
        uint256 twap = _calculateTWAP(state.twapBuffer, state.twapCount);
        
        PriceStatus status = PriceStatus.GOOD;
        if (state.lastGoodPrice == 0 || block.timestamp - state.lastTwapUpdate > MAX_STALENESS_SECONDS) {
            status = PriceStatus.STALE;
        }
        
        return PriceData({
            price: state.lastGoodPrice, 
            tokenDecimals: 18,
            status: status,
            timestamp: state.lastTwapUpdate,
            numActiveSources: 1,
            maxDeviation: 0, 
            twap: twap,
            confidence: status == PriceStatus.GOOD ? 10000 : 0
        });
    }

    function getBatchPrices(address[] calldata tokens) external view override returns (PriceData[] memory) {
        PriceData[] memory data = new PriceData[](tokens.length);
        for (uint256 i = 0; i < tokens.length; i++) {
            data[i] = this.getPrice(tokens[i]);
        }
        return data;
    }

    function getTWAP(address token) external view override returns (uint256 twap, uint256 numObservations) {
        TokenState storage state = _states[token];
        return (_calculateTWAP(state.twapBuffer, state.twapCount), state.twapCount);
    }

    function getPriceStatus(address token) external view override returns (PriceStatus) {
        return this.getPrice(token).status;
    }

    function isTradeAllowed(address token) external view override returns (bool allowed, string memory reason) {
        TokenState storage state = _states[token];
        if (block.timestamp < state.cooldownUntil) {
            return (false, "OracleAdapter: cooling down");
        }
        if (state.lastGoodPrice == 0 || block.timestamp - state.lastTwapUpdate > MAX_STALENESS_SECONDS) {
            return (false, "OracleAdapter: price stale");
        }
        return (true, "");
    }
    
    function _calculateTWAP(uint256[20] memory buffer, uint8 count) internal pure returns (uint256) {
        if (count == 0) return 0;
        uint256 sum = 0;
        for (uint8 i = 0; i < count; i++) {
            sum += buffer[i];
        }
        return sum / count;
    }

    function _sort(uint256[] memory arr, uint256 length) internal pure {
        if (length < 2) return;
        for (uint256 i = 0; i < length - 1; i++) {
            for (uint256 j = 0; j < length - i - 1; j++) {
                if (arr[j] > arr[j + 1]) {
                    uint256 temp = arr[j];
                    arr[j] = arr[j + 1];
                    arr[j + 1] = temp;
                }
            }
        }
    }
}
