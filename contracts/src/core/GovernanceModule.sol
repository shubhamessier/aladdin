// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IGovernanceModule} from "../interfaces/IGovernanceModule.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

contract GovernanceModule is IGovernanceModule, AccessControl {
    bytes32 public constant EMERGENCY_ROLE = keccak256("EMERGENCY_ROLE");

    mapping(uint256 => Proposal) public proposals;
    uint256 public nextProposalId = 1;

    constructor() {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
    }

    function propose(
        ActionCategory category,
        address[] memory targets,
        uint256[] memory values,
        string[] memory signatures,
        bytes[] memory calldatas,
        string memory /*description*/
    ) external override returns (uint256 proposalId) {
        proposalId = nextProposalId++;
        Proposal storage p = proposals[proposalId];
        p.id = proposalId;
        p.proposer = msg.sender;
        p.category = category;
        p.targets = targets;
        p.values = values;
        p.signatures = signatures;
        p.calldatas = calldatas;
        p.creationBlock = block.number;
        p.executeAfter = block.timestamp + 1 days;
        p.executed = false;
        p.canceled = false;
        p.vetoed = false;
    }

    function execute(uint256 proposalId) external payable override {
        Proposal storage p = proposals[proposalId];
        require(!p.executed, "GovernanceModule: already executed");
        require(!p.canceled, "GovernanceModule: canceled");
        require(!p.vetoed, "GovernanceModule: vetoed");
        require(block.timestamp >= p.executeAfter, "GovernanceModule: timelock not expired");
        p.executed = true;
    }

    function cancel(uint256 proposalId) external override {
        Proposal storage p = proposals[proposalId];
        require(msg.sender == p.proposer, "GovernanceModule: only proposer");
        p.canceled = true;
    }

    function veto(uint256 proposalId) external override onlyRole(EMERGENCY_ROLE) {
        Proposal storage p = proposals[proposalId];
        p.vetoed = true;
    }

    function state(uint256 proposalId) external view override returns (ProposalState) {
        Proposal memory p = proposals[proposalId];
        if (p.vetoed) return ProposalState.VETOED;
        if (p.canceled) return ProposalState.CANCELED;
        if (p.executed) return ProposalState.EXECUTED;
        if (block.timestamp < p.executeAfter) return ProposalState.QUEUED;
        return ProposalState.ACTIVE;
    }

    function getProposal(uint256 proposalId) external view override returns (Proposal memory) {
        return proposals[proposalId];
    }
}
