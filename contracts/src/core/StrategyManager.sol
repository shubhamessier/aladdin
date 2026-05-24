// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IStrategy} from "../interfaces/IStrategy.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

contract StrategyManager is AccessControl {
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    struct StrategyData {
        uint256 capitalDeployed;
        uint256 capitalReturned;
        int256 realizedPnL;
        int256 unrealizedPnL;
        uint256 deploymentTimestamp;
        bool isRegistered;
    }

    mapping(address => StrategyData) public strategies;

    event StrategyRegistered(address indexed strategy);
    event DepositedToStrategy(address indexed strategy, uint256 amount);
    event WithdrawnFromStrategy(address indexed strategy, uint256 amount);

    constructor() {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
    }

    function registerStrategy(address strategy) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(!strategies[strategy].isRegistered, "StrategyManager: already registered");
        strategies[strategy].isRegistered = true;
        emit StrategyRegistered(strategy);
    }

    function deposit(address strategy, uint256 amount) external onlyRole(GUARDIAN_ROLE) {
        require(strategies[strategy].isRegistered, "StrategyManager: not registered");
        strategies[strategy].capitalDeployed += amount;
        strategies[strategy].deploymentTimestamp = block.timestamp;
        emit DepositedToStrategy(strategy, amount);
    }

    function withdraw(address strategy, uint256 amount) external onlyRole(GUARDIAN_ROLE) {
        require(strategies[strategy].isRegistered, "StrategyManager: not registered");
        strategies[strategy].capitalReturned += amount;
        emit WithdrawnFromStrategy(strategy, amount);
    }

    function getStrategyData(address strategy) external view returns (StrategyData memory) {
        return strategies[strategy];
    }
}
