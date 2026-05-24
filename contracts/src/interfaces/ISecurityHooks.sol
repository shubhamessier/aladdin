// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title ISecurityHooks
 * @notice Interface for the Security Hooks, which acts as an on-chain firewall
 *         validating all treasury actions before execution.
 */
interface ISecurityHooks {
    /**
     * @notice Enum representing the different types of actions the treasury can perform.
     */
    enum ActionType {
        SWAP,
        STRATEGY_DEPOSIT,
        STRATEGY_WITHDRAWAL,
        DERIVATIVE_OPEN,
        DERIVATIVE_CLOSE,
        DERIVATIVE_ADJUST,
        BRIDGE_TRANSFER,
        WITHDRAWAL,
        PARAMETER_UPDATE,
        EMERGENCY
    }

    /**
     * @notice Struct containing the parameters of an action to be validated.
     */
    struct ActionParams {
        ActionType actionType;    // The type of action
        address caller;           // The address initiating the action
        address tokenIn;          // The input token address (if applicable)
        address tokenOut;         // The output token address (if applicable)
        uint256 amountIn;         // The input amount
        uint256 amountOut;        // The expected output amount (for slippage check)
        uint256 minAmountOut;     // The minimum acceptable output amount
        address strategy;         // The strategy address (for strategy actions)
        address market;           // The market address (for derivative actions)
        bool isLong;              // True if long, false if short (for derivative actions)
        uint256 derivativeSize;   // Notional size for derivatives
        bytes additionalData;     // Extensible payload for custom action data
    }

    /**
     * @notice Struct representing the result of a validation check.
     */
    struct ValidationResult {
        bool allowed;             // True if the action is permitted
        string reason;            // Human-readable rejection reason (if allowed is false)
        uint8 riskLevel;          // 0 = safe, 1 = caution, 2 = high risk
    }

    /**
     * @notice Validates a proposed action against all security rules and constraints.
     * @param params The ActionParams struct containing the details of the action.
     * @return The ValidationResult struct indicating whether the action is allowed.
     */
    function validate(ActionParams calldata params) external view returns (ValidationResult memory);

    /**
     * @notice Records a successful action to update internal tracking state (e.g. velocity, volume limits).
     * @param params The ActionParams struct that was just executed.
     */
    function recordAction(ActionParams calldata params) external;
}
