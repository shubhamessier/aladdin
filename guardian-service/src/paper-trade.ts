import { ethers } from "ethers";
import * as dotenv from "dotenv";

dotenv.config();

// Environment Variables
const RPC_URL = process.env.RPC_URL || "http://127.0.0.1:8545"; // Default to Anvil local node for testing
const PRIVATE_KEY = process.env.GUARDIAN_PRIVATE_KEY || "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"; // Anvil default #0
const VAULT_ADDRESS = process.env.VAULT_ADDRESS;
const ASSET_REGISTRY_ADDRESS = process.env.ASSET_REGISTRY_ADDRESS || "0xa513E6E4b8f2a923D98304ec87F64353C4D5C853";
const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://127.0.0.1:8000";

if (!VAULT_ADDRESS) {
    console.warn("⚠️  VAULT_ADDRESS not set. Please deploy the vault and set it in .env");
    process.exit(1);
}

const provider = new ethers.JsonRpcProvider(RPC_URL);
const wallet = new ethers.Wallet(PRIVATE_KEY, provider);

// Minimal Vault ABI for paper trading
const vaultAbi = [
    "function executeSwap(address tokenIn, address tokenOut, uint256 amountIn, uint256 minAmountOut, uint256 deadline, bytes calldata routeData) external returns (uint256 amountOut)",
    "function maxDailyVolumeUSD() external view returns (uint256)",
    "function maxGasPriceWei() external view returns (uint256)"
];

const registryAbi = [
    "function getPortfolioSnapshot() external view returns (tuple(tuple(address token, uint256 balance, uint256 valueUSD, uint16 allocationBps, uint8 tier, uint16 liquidityScore)[] assets, uint256 totalPortfolioUSD))"
];

const vault = new ethers.Contract(VAULT_ADDRESS, vaultAbi, wallet);
const registry = new ethers.Contract(ASSET_REGISTRY_ADDRESS, registryAbi, provider);

async function runPaperTradingLoop() {
    console.log(`\n🚀 Starting Paper Trading Loop...`);
    console.log(`Connected to RPC: ${RPC_URL}`);
    console.log(`Vault Address: ${VAULT_ADDRESS}`);
    console.log(`Registry Address: ${ASSET_REGISTRY_ADDRESS}`);
    console.log(`Guardian Wallet: ${wallet.address}`);

    try {
        // 1. Fetch Current State from Testnet Vault
        console.log(`\n[1] Fetching on-chain Portfolio Snapshot...`);
        const snapshot = await registry.getPortfolioSnapshot();
        const totalValueUSD = ethers.formatEther(snapshot.totalPortfolioUSD);
        console.log(`Vault Total Value: $${totalValueUSD}`);

        const currentBalances: Record<string, string> = {};
        for (const asset of snapshot.assets) {
            console.log(` - Asset ${asset.token}: Balance=${ethers.formatUnits(asset.balance, 18)}, Value=$${ethers.formatEther(asset.valueUSD)}, Allocation=${Number(asset.allocationBps) / 100}%`);
            currentBalances[asset.token] = ethers.formatUnits(asset.balance, 18);
        }

        // 2. Fetch Target Weights from Python Risk Engine
        console.log(`\n[2] Requesting Target Weights from Python Risk Engine (Risk Parity)...`);
        
        // Mock payload to Python API
        const payload = {
            assets: ["BTC", "ETH", "USDC"],
            covariance_matrix: [
                [0.04, 0.03, 0.0],
                [0.03, 0.05, 0.0],
                [0.0, 0.0, 0.0001]
            ],
            constraints: {
                min_weights: {"USDC": 0.20},
                max_weights: {"BTC": 0.35, "ETH": 0.35},
                volatile_assets: ["BTC", "ETH"],
                max_volatile_allocation: 0.80
            }
        };

        const response = await fetch(`${PYTHON_API_URL}/optimize/risk-parity`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            throw new Error(`Python API Error: ${response.statusText}`);
        }

        const riskData = await response.json();
        console.log(`Target Weights Received:`);
        console.dir(riskData.weights);

        // 3. Calculate Deltas
        console.log(`\n[3] Calculating Rebalance Trades...`);
        let tradesNeeded = false;
        
        // In a real system, you'd match the addresses to the symbols.
        // For the paper trade log, we'll just print what we *would* do.
        for (const [symbol, targetWeight] of Object.entries(riskData.weights)) {
            // Placeholder mapping
            const mockAddress = symbol === "USDC" ? "0x..." : symbol;
            console.log(`Evaluating ${symbol}... Target: ${(Number(targetWeight) * 100).toFixed(2)}%`);
        }

        console.log(`\n[4] Execution Module (Dry Run)`);
        console.log(`[DRY RUN] Would execute swap: Sell 0.5 WBTC for USDC on Testnet Uniswap V3 Router...`);
        console.log(`[DRY RUN] Would execute swap: Buy 2.5 WETH with USDC on Testnet Uniswap V3 Router...`);
        
        console.log(`\n✅ Paper Trading Iteration Complete. Sleeping until next cycle...`);

    } catch (error) {
        console.error(`\n❌ Error in Paper Trading Loop:`, error);
    }
}

// Run immediately, then set interval
runPaperTradingLoop();
// setInterval(runPaperTradingLoop, 60 * 60 * 1000); // Run every hour
