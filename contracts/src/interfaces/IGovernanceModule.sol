// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title IGovernanceModule
 * @notice Interface for the Governance Module, handling timelocked proposals.
 */
interface IGovernanceModule {
    /**
     * @notice Enum representing the state of a proposal.
     */
    enum ProposalState {
        PENDING,
        ACTIVE,
        CANCELED,
        DEFEATED,
        SUCCEEDED,
        QUEUED,
        EXPIRED,
        EXECUTED,
        VETOED
    }

    /**
     * @notice Enum representing the category of an action for timelock calculation.
     */
    enum ActionCategory {
        PARAMETER_UPDATE,
        CRITICAL_PARAMETER_UPDATE,
        ASSET_UPDATE,
        STRATEGY_UPDATE,
        ROUTER_UPDATE,
        ROLE_UPDATE,
        CONTRACT_UPGRADE,
        EMERGENCY
    }

    /**
     * @notice Struct representing a governance proposal.
     */
    struct Proposal {
        uint256 id;
        address proposer;
        ActionCategory category;
        address[] targets;
        uint256[] values;
        string[] signatures;
        bytes[] calldatas;
        uint256 creationBlock;
        uint256 executeAfter;    // Timestamp after which it can be executed
        bool executed;
        bool canceled;
        bool vetoed;
    }

    /**
     * @notice Proposes a new action to be executed after a timelock.
     * @param category The category of the action.
     * @param targets Array of target addresses.
     * @param values Array of ETH values to send.
     * @param signatures Array of function signatures.
     * @param calldatas Array of encoded calldata.
     * @param description A human-readable description of the proposal.
     * @return proposalId The ID of the newly created proposal.
     */
    function propose(
        ActionCategory category,
        address[] memory targets,
        uint256[] memory values,
        string[] memory signatures,
        bytes[] memory calldatas,
        string memory description
    ) external returns (uint256 proposalId);

    /**
     * @notice Executes a successful proposal after its timelock has expired.
     * @param proposalId The ID of the proposal to execute.
     */
    function execute(uint256 proposalId) external payable;

    /**
     * @notice Cancels a pending or queued proposal. Can be called by the proposer.
     * @param proposalId The ID of the proposal to cancel.
     */
    function cancel(uint256 proposalId) external;

    /**
     * @notice Vetoes a proposal. Restricted to the Security Council (EMERGENCY_ROLE).
     * @param proposalId The ID of the proposal to veto.
     */
    function veto(uint256 proposalId) external;

    /**
     * @notice Retrieves the current state of a proposal.
     * @param proposalId The ID of the proposal.
     * @return The ProposalState.
     */
    function state(uint256 proposalId) external view returns (ProposalState);

    /**
     * @notice Retrieves the details of a proposal.
     * @param proposalId The ID of the proposal.
     * @return The Proposal struct.
     */
    function getProposal(uint256 proposalId) external view returns (Proposal memory);
}
