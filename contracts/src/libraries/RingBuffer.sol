// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title RingBuffer
 * @notice Library for maintaining a circular buffer of uint256 values.
 */
library RingBuffer {
    struct Buffer {
        uint256[] data;
        uint256 head;
        uint256 count;
        uint256 capacity;
    }

    function init(Buffer storage buffer, uint256 capacity) internal {
        require(capacity > 0, "RingBuffer: capacity must be > 0");
        buffer.capacity = capacity;
        buffer.data = new uint256[](capacity);
        buffer.head = 0;
        buffer.count = 0;
    }

    function write(Buffer storage buffer, uint256 value) internal {
        require(buffer.capacity > 0, "RingBuffer: uninitialized");
        buffer.data[buffer.head] = value;
        buffer.head = (buffer.head + 1) % buffer.capacity;
        if (buffer.count < buffer.capacity) {
            buffer.count++;
        }
    }

    function average(Buffer storage buffer) internal view returns (uint256) {
        if (buffer.count == 0) return 0;
        
        uint256 sum = 0;
        bool overflow = false;
        
        for (uint256 i = 0; i < buffer.count; i++) {
            unchecked {
                uint256 nextSum = sum + buffer.data[i];
                if (nextSum < sum) {
                    overflow = true;
                    break;
                }
                sum = nextSum;
            }
        }
        
        if (!overflow) {
            return sum / buffer.count;
        } else {
            // Iterative mean to avoid overflow
            uint256 mean = 0;
            for (uint256 i = 0; i < buffer.count; i++) {
                mean += buffer.data[i] / buffer.count;
            }
            return mean;
        }
    }

    function length(Buffer storage buffer) internal view returns (uint256) {
        return buffer.count;
    }

    function read(Buffer storage buffer, uint256 offset) internal view returns (uint256) {
        require(offset < buffer.count, "RingBuffer: offset out of bounds");
        uint256 index = (buffer.head + buffer.capacity - 1 - offset) % buffer.capacity;
        return buffer.data[index];
    }
    
    function getAtAge(Buffer storage buffer, uint256 ageSeconds, uint256 intervalSeconds) internal view returns (uint256) {
        if (buffer.count == 0) return 0;
        uint256 offset = ageSeconds / intervalSeconds;
        if (offset >= buffer.count) {
            // Return the oldest available if the age is greater than what we have
            offset = buffer.count - 1;
        }
        return read(buffer, offset);
    }
}
