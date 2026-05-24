// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

error Gov__TimelockNotExpired(uint256 proposalId, uint256 executeAfter);
error Gov__ProposalAlreadyExecuted(uint256 proposalId);
error Gov__ProposalVetoed(uint256 proposalId);
error Gov__VelocityLimit(address proposer, uint256 count, uint256 maxPerDay);
error Gov__ValueCapExceeded(uint256 proposedValueUSD, uint256 maxValueUSD);
error Gov__InvalidPayload(uint256 proposalId, bytes4 expectedSelector, bytes4 actualSelector);
error Gov__InsufficientBond(uint256 provided, uint256 required);
