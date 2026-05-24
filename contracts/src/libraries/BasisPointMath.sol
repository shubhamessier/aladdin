// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {FixedPointMath} from "./FixedPointMath.sol";

/**
 * @title BasisPointMath
 * @notice Library for basis point calculations.
 * @dev 1 basis point (bps) = 0.01% = 0.0001. 10000 bps = 100%.
 */
library BasisPointMath {
    uint256 internal constant MAX_BPS = 10000;

    function bpsToWad(uint256 bps) internal pure returns (uint256) {
        return bps * 1e14;
    }

    function wadToBps(uint256 wad) internal pure returns (uint256) {
        return wad / 1e14;
    }

    function applyBps(uint256 value, uint256 bps) internal pure returns (uint256) {
        return FixedPointMath.mulDivDown(value, bps, MAX_BPS);
    }

    function percentDiff(uint256 a, uint256 b) internal pure returns (uint256) {
        if (a == 0 && b == 0) return 0;
        uint256 minVal = a < b ? a : b;
        if (minVal == 0) return type(uint256).max;
        uint256 diff = a > b ? a - b : b - a;
        return FixedPointMath.mulDivDown(diff, MAX_BPS, minVal);
    }
}
