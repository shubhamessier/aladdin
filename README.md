# Autonomous Treasury Management System on Hyperliquid

Institutional-Grade Quantitative DeFi Infrastructure

## Architecture

This project consists of three main components:

1. **Smart Contracts (Foundry)**: Located in `contracts/`. Contains the `TreasuryVault` (Diamond/UUPS proxy), `AssetRegistry`, `SecurityHooks`, and strategy implementations.
2. **Guardian Service (TypeScript)**: Located in `guardian-service/`. An event-driven state machine that reconstructs portfolio state, monitors risk, and executes rebalancing.
3. **Risk Engine (Python)**: Located in `python-risk/`. A FastAPI service providing quantitative risk models (VaR, Black-Litterman, Markowitz, Regime Detection).

## Quickstart

### Smart Contracts
```bash
cd contracts
forge build
forge test
```

### Guardian Service
```bash
cd guardian-service
npm install
npx tsc
```

### Risk Engine
```bash
cd python-risk
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn risk_engine.api:app --reload
```

## Security Philosophy
- Defense in depth.
- Fail-safe, not fail-open.
- Measurable risk.
- Capital preservation first.
