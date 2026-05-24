# Aladdin: Autonomous Treasury Management System

Institutional-grade quantitative DeFi infrastructure for Hyperliquid L1.

## Architecture
- **Contracts (`contracts/`)**: Solidity execution layer with UUPS proxies, Security Hooks, and Decaying HWM Circuit Breakers.
- **Guardian (`guardian-service/`)**: TypeScript orchestrator that manages state transitions and transaction execution.
- **Risk Engine (`python-risk/`)**: Python FastAPI service providing Robust HMM Regime Detection, Risk Parity optimization, and VaR models.
- **Backtest (`backtest/`)**: 4-year historical simulation engine with Transaction Cost Modeling (Almgren-Chriss slippage, MEV leakage).

## Key Features (Stage 4 Fixed)
- **Decaying High-Water Mark**: Prevents circuit breaker death spirals by allowing the system to establish new recovery baselines after a crash.
- **Robust HMM Regime Detector**: Utilizes Rank-based Inverse Normal Transformation and Sticky Bayesian Priors to ensure convergence on fat-tailed crypto returns.
- **Graduated Re-Entry**: A multi-week recovery phase that slowly ramps volatile exposure to mitigate execution risk and psychological panic.

## Quickstart
1. **Contracts**: `cd contracts && forge build`
2. **Backtest**: 
   ```bash
   cd backtest
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python -m backtest.main
   ```

## Results (4-Year Stress Test)
- **Total Return**: +31.32%
- **Max Drawdown**: -32.56% (survived LUNA/FTX)
- **Circuit Breaker Coverage**: 173 days (11% of time) - successfully unlocked after crises.
