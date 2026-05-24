// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

error Oracle__NoSourcesAvailable(address token);
error Oracle__AllSourcesStale(address token, uint256 lastUpdate, uint256 maxStaleness);
error Oracle__PriceSuspect(address token, uint256 price, uint256 twap, uint256 deviationBps);
error Oracle__PendingConfirmation(address token, uint256 pendingPrice, uint256 pendingSinceBlock);
error Oracle__InvalidFeed(address token, address feed);
error Oracle__ZeroPrice(address token, address source);
