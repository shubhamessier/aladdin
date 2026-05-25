# Aladdin

Quantitative treasury management system for Hyperliquid L1. Three components: a Solidity vault with on-chain circuit breakers, a TypeScript guardian that bridges off-chain risk signals to on-chain execution, and a Python backtest/risk stack that runs allocation strategy research.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Guardian Service (TS)                      │
│  State machine orchestrator. Polls risk engine, triggers        │
│  rebalances, manages CB level transitions, signs transactions.  │
└──────────────┬──────────────────────────┬───────────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────┐    ┌─────────────────────────────────────┐
│   TreasuryVault.sol  │    │     Risk Engine (Python FastAPI)    │
│   StrategyManager    │    │  HMM regime detection               │
│   SecurityHooks      │    │  Risk parity / BL / MinVar          │
│   OracleAdapter      │    │  VaR: historical, parametric, MC    │
│   UUPS proxy pattern │    │  Covariance: EWMA+LW+RMT+PSD       │
└──────────────────────┘    └─────────────────────────────────────┘
                                          │
                             ┌────────────▼────────────┐
                             │   Backtest Engine (Py)  │
                             │   4-strategy simulation │
                             │   Walk-forward optimizer│
                             │   TCM + microstructure  │
                             └─────────────────────────┘
```

### `contracts/`

Solidity execution layer. UUPS upgradeable proxy pattern with role-separated access (`GUARDIAN_ROLE`, `GOVERNOR_ROLE`, `EMERGENCY_ROLE`, `KEEPER_ROLE`).

**`TreasuryVault.sol`** — Core state. Holds asset ledgers, derivative positions, a 720-slot snapshot ring buffer, and all circuit breaker state (HWM, effective HWM, CB level timestamps). On-chain circuit breaker mirrors the Python CB logic: 3 levels at configurable drop thresholds from effective HWM with a decaying HWM to prevent CB death spirals.

**`SecurityHooks.sol`** — Pre-trade firewall. Every execution call validates:
- Gas price within bounds
- Per-asset trade cooldown (≥30s)
- Daily volume cap ($2M/day)
- Max single trade ($500k)
- Max slippage (100bps)
- Gross notional ≤ 50% NAV
- Net delta ≤ 30% NAV
- Strategy concentration ≤ 25% per strategy
- Total strategy allocation ≤ 60% NAV
- Max drawdown from snapshot ≤ 20%

**`OracleAdapter.sol`** — Price feed abstraction. Supports multiple oracle sources with staleness detection.

**Strategy contracts**: `StableYieldStrategy`, `PerpHedgingStrategy`, `BasisTradeStrategy`, `LiquidityProvisionStrategy`, `StakingStrategy`. All implement `IStrategy` and are whitelisted through `StrategyManager`.

**Libraries**: `FixedPointMath` (18-decimal precision), `BasisPointMath`, `RingBuffer` (snapshot history).

---

### `python-risk/`

FastAPI service. Consumed by the guardian for live risk signals and by the backtest engine for strategy logic.

**`covariance.py` — Robust covariance pipeline**

Four-stage pipeline run on every rebalance:

1. **EWMA** (`halflife=63d`) — captures recent volatility clustering without static window boundary effects
2. **Ledoit-Wolf shrinkage** — pulls sample covariance toward structured estimator; shrinkage intensity computed analytically (no cross-validation)
3. **RMT de-noising** (Marchenko-Pastur) — filters eigenvalues below the noise floor `λ+ = (1 + √(N/T))²`; replaces noise eigenvalues with their mean. Keeps signal eigenvalues intact.
4. **Nearest PSD projection** — clamps negative eigenvalues to 1e-8 via eigendecomposition; guarantees Cholesky factorizability for downstream optimization

EWMA vols are used to rescale the LW shrunk covariance before RMT — this combines LW's correlation stability with EWMA's recency weighting on the diagonal.

**`regime_detector.py` — HMM with rank transform and sticky priors**

Input: portfolio mean returns series. Preprocessing: rank-based inverse normal transform (Blom's formula) maps arbitrary fat-tailed crypto return distribution to N(0,1) while preserving temporal order. This makes GaussianHMM assumptions hold better on crypto data.

HMM config: 3 states (bull / uncertain / crisis), full covariance type, 500 iterations per fit, `n_fits=20` restarts to escape local optima. Sticky Bayesian priors: diagonal of transition prior set to 10 (vs 1 off-diagonal) — penalizes frequent regime switching.

State labeling: states ranked by `mean(returns) - 0.5 * std(returns)`. Highest score = bull, lowest = crisis. 3-step crisis probability computed from matrix multiplication of transition matrix: `P(crisis in 3 steps) = 1 - ∏(1 - P_k[current→crisis])` for k=1,2,3.

Rolling refit: every 30 days on 504-day window.

**`portfolio_optimizer.py` — Three optimization methods**

All use SLSQP with `maxiter=1000`, `ftol=1e-12`, long-only bounds.

- **Risk Parity**: minimizes `Σ (RC_i - σ_p/N)²` where `RC_i = w_i(Σw)_i / σ_p`. Falls back to inverse-vol if SLSQP fails.
- **Mean-Variance**: maximizes `w'μ - 0.5δ w'Σw`, `δ=2.5`. Supports tier constraints (asset group min/max bounds).
- **Black-Litterman**: constructs equilibrium returns `π = δΣw_mkt`, then blends with investor views via:
  ```
  μ_BL = [(τΣ)⁻¹ + P'Ω⁻¹P]⁻¹ [(τΣ)⁻¹π + P'Ω⁻¹Q]
  Σ_BL = Σ + [(τΣ)⁻¹ + P'Ω⁻¹P]⁻¹
  ```
  Passes posterior to MV optimizer. `Ω` diagonal computed from view confidence: `Ω_ii = (1/c - 1) * P_i (τΣ) P_i'`.

**`var_models.py` — Three VaR estimators**

- **Historical**: `VaR = -percentile(w'R, 1-α)`. CVaR = conditional mean below VaR threshold.
- **Parametric**: normal distribution, `VaR = -μ + z_α σ`, CVaR from closed-form normal PDF.
- **Monte Carlo Jump-Diffusion** (Merton): GBM diffusion + compound Poisson jumps. Parameters: `λ=20 jumps/year`, `μ_j=-2%`, `σ_j=5%`. 50,000 paths per call.

**`monte_carlo.py` — Portfolio path simulation**

Correlated Student-t innovations (df=5). Cholesky decomposition of correlation matrix. GBM log-return step per day. Outputs: VaR 95/99, CVaR 95/99, expected max drawdown across paths, P(ruin | 30% loss), mean/median return distribution. 50,000 simulations × horizon days.

---

### `backtest/`

Historical simulation engine. Daily timestep. Not real-time — pure research/calibration.

**Strategies** (all implement `AllocationStrategy.generate_target_weights`):

| Strategy | Logic |
|---|---|
| Equal Weight | `1/N` to all assets |
| Static Conservative | 80% stablecoins / 20% volatile, equally split within each group |
| Risk Parity | Calls `optimize_risk_parity` from risk engine; fallback to inv-vol if SLSQP diverges |
| Min Variance | Constrained QP via `scipy.optimize.minimize(SLSQP)` |
| Black-Litterman | Equilibrium returns from `2.5 * Σ @ w_mkt`; posterior via BL formula |
| Regime-Adaptive | Volatile target: 60% (bull) / 30% (uncertain) / 10% (crisis); volatile sub-portfolio uses inv-vol weights |
| Buy & Hold | Returns current weights unchanged |

All strategies support `max_volatile_override` — a scalar cap on total volatile allocation passed in by recovery phase.

**Circuit breaker** (`engine/circuit_breaker.py`):

Three-level system. Drop measured from *effective* HWM, not absolute HWM. Effective HWM decays toward current portfolio value after 30 days without a new peak: `HWM_eff = value + (HWM_abs - value) * 0.5^(elapsed_days / halflife)`. This prevents a permanent breaker lock after a regime shift where the old HWM is no longer relevant.

Level escalation is immediate (any qualifying drop triggers). Level decay requires:
- Vol ratio < 1.2× lifetime average, OR
- N stable days at current level (L1: 7d, L2: 14d, L3: 7d after vol normalizes), OR
- Forced decay after 60 days at same level

Recovery phase on L2→L1 transition: graduated volatile exposure over 7 weeks (10% → 20% → 35% → 50%). Re-escalates to L2 if portfolio drops more than the weekly decline threshold from recovery entry value.

**Transaction cost model** (`engine/cost_model.py`):

```
total_cost = dex_fee + market_impact + gas + latency_cost + toxicity_cost

dex_fee       = size × 0.9bps blended (70% maker @ 0.2bps, 30% taker @ 2.5bps)
market_impact = size × base_slippage[asset] × (size/100k)^0.7 × vol_multiplier
gas           = $0.50 flat (HyperEVM)
latency_cost  = size × (150ms / 100ms) × 1.5bps/100ms
toxicity_cost = size × min(0.9, 0.15 × vol_multiplier) × 5bps
fill_ratio    = max(0.2, 1 - vol×2) if size > $500k and vol > 5%
```

Asset-specific base slippage (per $100k): BTC 1.5bps, ETH 2.0bps, SOL 4.0bps, USDT 0.1bps, DAI 0.5bps, USDC 0bps.

**Optimizer** (`optimizer/`):

- Grid search: exhaustive over discrete param space
- Random search: uniform sampling over continuous ranges
- Walk-forward: 18-month train / 6-month test / 6-month step. Each fold runs random search on train slice, evaluates best params on held-out test slice.
- Composite score (0-100): weighted sum of Sharpe (20%), Sortino (15%), Calmar (10%), max drawdown penalty (25%), vol penalty (15%), CB days penalty (10%), cost drag penalty (5%)
- Hard constraints: DD < 40%, vol < 30%, total return > -30%, cost drag < 5%/yr

**Tunable parameter space** (20 parameters across 6 categories):

| Category | Parameters |
|---|---|
| Circuit Breaker | L1/L2/L3 thresholds, HWM decay halflife, stable-day decay counts per level, vol ratio threshold |
| Recovery Phase | Max volatile % at weeks 1-2, 3-4, 5-6; caution days post-recovery |
| Allocation | Min stable reserve, max volatile cap |
| Rebalancing | Drift threshold, cooldown days |
| Hedging | Hedge ratio in uncertain/crisis regimes |
| Covariance / Regime | Covariance lookback, HMM fitting window |

---

## Data Sources

| Feed | Source | Status |
|---|---|---|
| OHLCV prices | Binance spot (`/api/v3/klines`) | Live — Parquet cache |
| Funding rates | Synthetic AR(1) fallback | Placeholder — real HL API not wired |
| Lending rates | Synthetic random walk | Placeholder — DeFi Llama not wired |

All data cached as Parquet under `backtest/cache/`. Cache key: `{source}_{symbol}_{start_ts}_{end_ts}.parquet`.

---

## Quickstart

**Prerequisites**: Python 3.12+, Foundry, Node 18+

**Backtest**
```bash
cd backtest
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run all strategies on default config (2022-2026, BTC/ETH/USDC, $1M)
python -m backtest.main --config backtest/config/default.yaml

# With Monte Carlo projections (90-day, 10k paths)
python -m backtest.main --monte-carlo

# Parameter optimization (walk-forward, random search)
python -m backtest.main --optimize --optimize-strategy risk_parity

# Output to custom dir
python -m backtest.main --output-dir backtest/results/run_001
```

**Contracts**
```bash
cd contracts
forge build
forge test
```

**Risk engine**
```bash
cd python-risk
pip install -r requirements.txt
uvicorn risk_engine.api:app --port 8000
```

---

## Known Limitations

- **Funding/lending data is synthetic.** Both `fetch_funding_rates` and `fetch_lending_rates` raise `NotImplementedError` and fall back to generated AR(1) processes. All yield attribution numbers reflect modeled, not real, carry.
- **Binance spot ≠ HL perp.** Price data from Binance spot. HL perpetuals trade at a basis to spot. High-funding periods see 0.5-2% premium. Signal quality degrades proportionally.
- **Hedger is disconnected.** `HedgingEngine` computes adjustments but `TreasurySimulator` does not call it. Portfolio is always fully unhedged in backtest.
- **Daily granularity only.** Simulation steps once per day. Intraday drawdowns, liquidation cascades, and funding payment timing are not modeled.
- **Market depth hardcoded.** Cost model receives `pool_liquidity=1e8, daily_volume=1e7` regardless of asset or market conditions. Slippage estimates are optimistic during low-liquidity periods.
- **BlackLitterman strategy** uses equilibrium returns as `μ` in `Σ⁻¹μ` rather than calling the full BL posterior. The correct implementation is in `portfolio_optimizer.py:optimize_black_litterman` and is not wired to the strategy class.

---

## Directory Structure

```
aladdin/
├── contracts/
│   ├── src/
│   │   ├── core/           # TreasuryVault, SecurityHooks, OracleAdapter, AssetRegistry, StrategyManager
│   │   ├── strategies/     # StableYield, PerpHedging, BasisTrade, LiquidityProvision, Staking
│   │   ├── interfaces/
│   │   ├── libraries/      # FixedPointMath, BasisPointMath, RingBuffer
│   │   └── errors/
│   └── lib/forge-std/
├── guardian-service/
│   └── src/               # TypeScript orchestrator
├── python-risk/
│   └── risk_engine/
│       ├── api.py          # FastAPI routes
│       ├── covariance.py   # EWMA + LW + RMT + PSD pipeline
│       ├── regime_detector.py   # HMM with rank transform
│       ├── portfolio_optimizer.py  # MV, Risk Parity, Black-Litterman
│       ├── var_models.py   # Historical, parametric, jump-diffusion VaR
│       ├── monte_carlo.py  # Correlated Student-t path simulation
│       ├── tsmom_risk_parity.py
│       └── schemas.py
└── backtest/
    ├── main.py
    ├── config/
    │   └── default.yaml
    ├── engine/
    │   ├── simulator.py        # TreasurySimulator — main loop
    │   ├── strategies.py       # Allocation strategy implementations
    │   ├── circuit_breaker.py  # CB + RecoveryPhase + decaying HWM
    │   ├── cost_model.py       # Transaction cost model
    │   ├── microstructure.py   # L2 CLOB simulation (standalone)
    │   ├── event_driven_replay.py  # Tick-level replay (standalone)
    │   ├── hedger.py           # Hedge adjustment logic (not wired to sim)
    │   ├── yield_engine.py     # Lending + funding yield estimation
    │   └── portfolio.py        # PortfolioState dataclass
    ├── data/
    │   ├── fetcher.py          # Binance / CoinGecko / CoinCap OHLCV
    │   ├── assembler.py        # Multi-source data assembly
    │   ├── funding.py          # Funding rate fetch (synthetic fallback)
    │   └── lending.py          # Lending rate fetch (synthetic fallback)
    ├── optimizer/
    │   ├── grid_search.py
    │   ├── random_search.py
    │   ├── walk_forward.py     # 18/6/6 purged walk-forward
    │   ├── scorer.py           # Composite score + hard constraints
    │   └── param_space.py      # 20-param tuning space
    ├── analysis/
    │   ├── metrics.py          # Sharpe, Sortino, max DD, VaR
    │   ├── attribution.py      # Return decomposition
    │   └── hl_reality_check.py # Live HL microstructure simulation
    └── reporting/
        ├── terminal.py
        └── charts.py           # NAV comparison, drawdown, allocation charts
```
