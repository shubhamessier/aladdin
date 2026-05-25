# Deployment Runbook (HyperEVM Testnet)

This runbook outlines the step-by-step procedure for deploying the Autonomous Treasury Management System to the HyperEVM testnet.

## Prerequisites

1. **Tools**:
   - `forge` (Foundry) installed and updated.
   - `node` (v18+) and `npm` installed.
   - `python` (v3.10+) and `pip` installed.
2. **Keys & Addresses**:
   - `DEPLOYER_PRIVATE_KEY`: Private key for the account deploying the contracts. Needs testnet gas tokens.
   - `GUARDIAN_PRIVATE_KEY`: Private key for the Guardian service hot wallet. Needs testnet gas tokens.
3. **Test Tokens**:
   - Testnet USDC, WETH, WBTC available on HyperEVM testnet to fund the vault.

## 1. Configuration File Template

Create a `.env` file in the root directory (used by both contracts and Guardian):

```env
# Network
HYPEREVM_TESTNET_RPC_URL=https://rpc.testnet.hyperliquid.xyz/evm
DEPLOYER_PRIVATE_KEY=your_deployer_key
GUARDIAN_PRIVATE_KEY=your_guardian_key

# Contracts (populated post-deployment)
TREASURY_VAULT_ADDRESS=
SECURITY_HOOKS_ADDRESS=
ORACLE_ADAPTER_ADDRESS=
ASSET_REGISTRY_ADDRESS=

# Guardian Service
CYCLE_INTERVAL_SECONDS=300
MAX_GAS_PRICE_GWEI=50
TX_TIMEOUT_SECONDS=120
RISK_ENGINE_URL=http://localhost:8000
```

## 2. Smart Contract Deployment

Navigate to the `contracts/` directory:

```bash
cd contracts

# 1. Compile contracts
forge build

# 2. Run the deployment script targeting HyperEVM testnet
forge script script/DeployTreasury.s.sol:DeployTreasury \
    --rpc-url $HYPEREVM_TESTNET_RPC_URL \
    --private-key $DEPLOYER_PRIVATE_KEY \
    --broadcast \
    --verify
```

*Note: The deployment script must output the addresses of all deployed contracts. Update the `.env` file with these addresses.*

## 3. Post-Deployment Verification

Verify the on-chain configuration using `cast`:

```bash
# Verify Guardian role is set correctly on the vault
cast call $TREASURY_VAULT_ADDRESS "hasRole(bytes32,address)(bool)" \
    $(cast keccak "GUARDIAN_ROLE") $GUARDIAN_ADDRESS \
    --rpc-url $HYPEREVM_TESTNET_RPC_URL

# Verify Oracles are configured (e.g., WETH)
cast call $ORACLE_ADAPTER_ADDRESS "getPrice(address)(uint256,uint256,uint8)" \
    $WETH_ADDRESS \
    --rpc-url $HYPEREVM_TESTNET_RPC_URL
```

## 4. Fund the Vault

Transfer test tokens to the `TREASURY_VAULT_ADDRESS`:

```bash
# Transfer 100,000 test USDC
cast send $USDC_ADDRESS "transfer(address,uint256)" \
    $TREASURY_VAULT_ADDRESS 100000000000 \
    --rpc-url $HYPEREVM_TESTNET_RPC_URL \
    --private-key $DEPLOYER_PRIVATE_KEY
```

## 5. Risk Engine Startup

Navigate to the `python-risk/` directory:

```bash
cd python-risk

# 1. Create and activate virtual environment
python -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the FastAPI server
uvicorn risk_engine.api:app --host 0.0.0.0 --port 8000
```

Verify the Risk Engine is healthy:
```bash
curl http://localhost:8000/health
```

## 6. Guardian Service Startup

Navigate to the `guardian-service/` directory:

```bash
cd guardian-service

# 1. Install dependencies
npm install

# 2. Compile TypeScript
npx tsc

# 3. Start the Guardian
npm run start
```

## 7. System Verification

Watch the Guardian service logs for the following sequence to confirm the system is operational:

1. `Guardian bootstrapping — reconstructing state from chain`
2. `Bootstrap complete` (State should be `HEALTHY`).
3. `Computing optimal weights via Risk Engine`.
4. `Executing TWAP rebalance...` (If current allocation deviates from target).
5. `Transaction confirmed` (Vault executes the trades).

Check the vault balances again using `cast` to verify trades occurred and the portfolio is balanced.
