// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

library FixedPointMath {
    uint256 internal constant WAD = 1e18;

    function mulDivDown(uint256 x, uint256 y, uint256 d) internal pure returns (uint256) {
        return Math.mulDiv(x, y, d);
    }

    function mulDivUp(uint256 x, uint256 y, uint256 d) internal pure returns (uint256) {
        return Math.mulDiv(x, y, d, Math.Rounding.Ceil);
    }

    function sqrt(uint256 x) internal pure returns (uint256) {
        return Math.sqrt(x);
    }

    function mulWad(uint256 x, uint256 y) internal pure returns (uint256) {
        return mulDivDown(x, y, WAD);
    }

    function divWad(uint256 x, uint256 y) internal pure returns (uint256) {
        return mulDivDown(x, WAD, y);
    }

    function exp(int256 x) internal pure returns (int256 r) {
        // Simplified exp for demonstration/testing
        int256 WAD_INT = 1e18;
        if (x == 0) return WAD_INT;
        if (x < -42e18) return 0;
        
        unchecked {
            int256 p = x;
            if (p >= 0) {
                r = WAD_INT + p + (p * p / WAD_INT) / 2 + (p * p / WAD_INT * p / WAD_INT) / 6;
            } else {
                p = -p;
                int256 denom = WAD_INT + p + (p * p / WAD_INT) / 2 + (p * p / WAD_INT * p / WAD_INT) / 6;
                r = (WAD_INT * WAD_INT) / denom;
            }
        }
    }
    
    function ln(int256 x) internal pure returns (int256 r) {
        require(x > 0, "UNDEFINED");
        int256 WAD_INT = 1e18;
        unchecked {
            int256 z = (x - WAD_INT) * WAD_INT / (x + WAD_INT);
            r = 2 * (z + (z * z / WAD_INT * z / WAD_INT) / 3);
        }
    }
}
