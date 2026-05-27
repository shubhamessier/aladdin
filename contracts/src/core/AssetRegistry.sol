// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IAssetRegistry} from "../interfaces/IAssetRegistry.sol";
import {ITreasuryVault} from "../interfaces/ITreasuryVault.sol";
import {IOracleAdapter} from "../interfaces/IOracleAdapter.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

contract AssetRegistry is IAssetRegistry, AccessControl {
    bytes32 public constant GOVERNOR_ROLE = keccak256("GOVERNOR_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    ITreasuryVault public vault;
    IOracleAdapter public oracle;

    mapping(address => AssetConfig) private _assets;
    mapping(RiskTier => TierConfig) private _tierConfigs;
    
    address[] private _activeTokens;

    constructor() {
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        
        // Setup default tier configs based on design.md
        _tierConfigs[RiskTier.STABLE] = TierConfig({
            maxTotalAllocationBps: 7000,
            minTotalAllocationBps: 2000,
            maxSingleAssetBps: 3500,
            defaultHaircutBps: 0
        });
        _tierConfigs[RiskTier.CORE] = TierConfig({
            maxTotalAllocationBps: 5000,
            minTotalAllocationBps: 0,
            maxSingleAssetBps: 3000,
            defaultHaircutBps: 1000
        });
        _tierConfigs[RiskTier.VOLATILE] = TierConfig({
            maxTotalAllocationBps: 2500,
            minTotalAllocationBps: 0,
            maxSingleAssetBps: 1000,
            defaultHaircutBps: 3000
        });
        _tierConfigs[RiskTier.DERIVATIVE] = TierConfig({
            maxTotalAllocationBps: 2000,
            minTotalAllocationBps: 0,
            maxSingleAssetBps: 1000,
            defaultHaircutBps: 4000
        });
        _tierConfigs[RiskTier.YIELD_BEARING] = TierConfig({
            maxTotalAllocationBps: 4000,
            minTotalAllocationBps: 0,
            maxSingleAssetBps: 2000,
            defaultHaircutBps: 1500
        });
    }

    function addAsset(AssetConfig calldata config) external override onlyRole(GOVERNOR_ROLE) {
        require(config.primaryOracle != address(0), "AssetRegistry: no primary oracle");
        require(config.maxAllocationBps >= config.minAllocationBps, "AssetRegistry: max < min alloc");
        require(config.maxAllocationBps <= 10000, "AssetRegistry: max alloc > 10000");
        
        _assets[config.token] = config;
        
        bool exists = false;
        for (uint256 i = 0; i < _activeTokens.length; i++) {
            if (_activeTokens[i] == config.token) {
                exists = true;
                break;
            }
        }
        if (!exists) {
            _activeTokens.push(config.token);
        }
    }

    function updateAsset(address token, AssetConfig calldata config) external override onlyRole(GOVERNOR_ROLE) {
        require(_assets[token].token != address(0), "AssetRegistry: asset not found");
        require(config.primaryOracle != address(0), "AssetRegistry: no primary oracle");
        require(config.maxAllocationBps >= config.minAllocationBps, "AssetRegistry: max < min alloc");
        require(config.maxAllocationBps <= 10000, "AssetRegistry: max alloc > 10000");
        _assets[token] = config;
    }

    function freezeAsset(address token) external override {
        require(hasRole(GUARDIAN_ROLE, msg.sender) || hasRole(GOVERNOR_ROLE, msg.sender), "AssetRegistry: unauthorized");
        _assets[token].isActive = false;
    }

    function unfreezeAsset(address token) external override onlyRole(GOVERNOR_ROLE) {
        _assets[token].isActive = true;
    }

    function getAssetConfig(address token) external view override returns (AssetConfig memory) {
        return _assets[token];
    }

    function getTierConfig(RiskTier tier) external view override returns (TierConfig memory) {
        return _tierConfigs[tier];
    }

    function setDependencies(address _vault, address _oracle) external onlyRole(GOVERNOR_ROLE) {
        vault = ITreasuryVault(_vault);
        oracle = IOracleAdapter(_oracle);
    }

    function getPortfolioSnapshot() external view override returns (SnapshotData memory) {
        if (address(vault) == address(0) || address(oracle) == address(0)) {
            return SnapshotData({
                assets: new AssetSnapshot[](0),
                totalPortfolioUSD: 0
            });
        }

        uint256 activeCount = 0;
        for (uint256 i = 0; i < _activeTokens.length; i++) {
            if (_assets[_activeTokens[i]].isActive) {
                activeCount++;
            }
        }

        AssetSnapshot[] memory assets = new AssetSnapshot[](activeCount);
        uint256 totalUSD = 0;
        uint256 idx = 0;

        for (uint256 i = 0; i < _activeTokens.length; i++) {
            address token = _activeTokens[i];
            AssetConfig memory config = _assets[token];
            
            if (!config.isActive) continue;

            ITreasuryVault.AssetLedger memory ledger = vault.getAssetLedger(token);
            IOracleAdapter.PriceData memory priceData = oracle.getPrice(token);
            
            uint256 valUSD = 0;
            if (config.decimals > 0 && priceData.price > 0) {
                // valUSD in 18 decimals. price is 18 decimals. balance is in config.decimals.
                valUSD = (ledger.freeBalance * priceData.price) / (10 ** config.decimals);
            }
            
            assets[idx] = AssetSnapshot({
                token: token,
                balance: ledger.freeBalance,
                valueUSD: valUSD,
                allocationBps: 0,
                tier: config.tier,
                liquidityScore: config.liquidityScore
            });
            totalUSD += valUSD;
            idx++;
        }

        if (totalUSD > 0) {
            for (uint256 i = 0; i < activeCount; i++) {
                assets[i].allocationBps = (assets[i].valueUSD * 10000) / totalUSD;
            }
        }

        return SnapshotData({
            assets: assets,
            totalPortfolioUSD: totalUSD
        });
    }

    function getAssetsByTier(RiskTier tier) external view override returns (address[] memory) {
        uint256 count = 0;
        for (uint256 i = 0; i < _activeTokens.length; i++) {
            if (_assets[_activeTokens[i]].tier == tier && _assets[_activeTokens[i]].isActive) {
                count++;
            }
        }
        address[] memory result = new address[](count);
        uint256 idx = 0;
        for (uint256 i = 0; i < _activeTokens.length; i++) {
            if (_assets[_activeTokens[i]].tier == tier && _assets[_activeTokens[i]].isActive) {
                result[idx] = _activeTokens[i];
                idx++;
            }
        }
        return result;
    }

    function validateAllocation(
        address token,
        uint256 newValueUSD,
        uint256 totalPortfolioUSD
    ) external view override returns (bool isValid, string memory reason) {
        AssetConfig memory config = _assets[token];
        if (config.token == address(0)) {
            return (false, "AssetRegistry: asset not found");
        }
        
        if (totalPortfolioUSD == 0) {
            return (true, "");
        }
        
        uint256 newAllocationBps = (newValueUSD * 10000) / totalPortfolioUSD;
        if (newAllocationBps > config.maxAllocationBps) {
            return (false, "AssetRegistry: exceeds max alloc");
        }
        
        TierConfig memory tierConfig = _tierConfigs[config.tier];
        if (newAllocationBps > tierConfig.maxSingleAssetBps) {
            return (false, "AssetRegistry: exceeds max single tier alloc");
        }
        
        return (true, "");
    }
}
