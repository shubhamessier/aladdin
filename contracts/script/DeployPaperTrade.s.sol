// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/core/TreasuryVault.sol";
import "../src/core/AssetRegistry.sol";
import "../src/core/SecurityHooks.sol";
import "../src/core/OracleAdapter.sol";
import "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockERC20 is ERC20 {
    uint8 private _decimals;
    constructor(string memory name, string memory symbol, uint8 dec) ERC20(name, symbol) {
        _decimals = dec;
        _mint(msg.sender, 1000000000 * 10**dec);
    }
    function decimals() public view override returns (uint8) {
        return _decimals;
    }
}

contract MockPyth {
    struct Price {
        int64 price;
        uint64 conf;
        int32 expo;
        uint256 publishTime;
    }
    mapping(bytes32 => Price) public prices;
    function setPrice(bytes32 id, int64 price, int32 expo) external {
        prices[id] = Price(price, 0, expo, block.timestamp);
    }
    function getPriceUnsafe(bytes32 id) external view returns (Price memory price) {
        return prices[id];
    }
}

contract DeployPaperTrade is Script {
    function run() external {
        vm.startBroadcast();

        // 1. Deploy Mocks
        MockERC20 usdc = new MockERC20("USD Coin", "USDC", 6);
        MockERC20 btc = new MockERC20("Wrapped BTC", "WBTC", 8);
        MockERC20 eth = new MockERC20("Wrapped ETH", "WETH", 18);
        MockPyth pyth = new MockPyth();

        // Feed IDs (dummy)
        bytes32 btcFeed = keccak256("BTC");
        bytes32 ethFeed = keccak256("ETH");
        bytes32 usdcFeed = keccak256("USDC");
        
        pyth.setPrice(btcFeed, 6500000000000, -8); // $65k
        pyth.setPrice(ethFeed, 350000000000, -8);  // $3.5k
        pyth.setPrice(usdcFeed, 100000000, -8);    // $1.00

        // 2. Deploy Core
        AssetRegistry registry = new AssetRegistry();
        OracleAdapter oracle = new OracleAdapter(address(registry));
        TreasuryVault vaultImpl = new TreasuryVault();
        
        // Pre-compute proxy address
        address proxyAddress = vm.computeCreateAddress(msg.sender, vm.getNonce(msg.sender) + 1);
        
        SecurityHooks hooks = new SecurityHooks(address(oracle), address(registry), proxyAddress);
        
        bytes memory initData = abi.encodeWithSelector(
            TreasuryVault.initialize.selector,
            address(registry),
            address(hooks),
            address(oracle)
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(vaultImpl), initData);
        TreasuryVault vault = TreasuryVault(address(proxy));

        // 3. Setup Dependencies
        registry.grantRole(registry.GOVERNOR_ROLE(), msg.sender);
        registry.setDependencies(address(vault), address(oracle));
        
        oracle.setFeeds(address(btc), address(0), address(pyth), btcFeed);
        oracle.setFeeds(address(eth), address(0), address(pyth), ethFeed);
        oracle.setFeeds(address(usdc), address(0), address(pyth), usdcFeed);

        // Grant roles on vault
        vault.grantRole(registry.GOVERNOR_ROLE(), msg.sender);

        vault.whitelistToken(address(usdc), true);
        vault.whitelistToken(address(btc), true);
        vault.whitelistToken(address(eth), true);

        // Grant Keeper Role for paper trading bot
        vault.grantRole(vault.KEEPER_ROLE(), msg.sender);

        vm.stopBroadcast();

        console.log("--- Deployment Complete ---");
        console.log("USDC:   ", address(usdc));
        console.log("WBTC:   ", address(btc));
        console.log("WETH:   ", address(eth));
        console.log("Vault:  ", address(vault));
        console.log("Oracle: ", address(oracle));
    }
}
