# ALADDIN: RUTHLESS PRODUCTION AUDIT

**Date**: 2026-05-26  
**Auditor perspective**: Principal Quant Engineer + Low-Latency Architect + HL Microstructure Specialist + Post-Mortem Investigator  
**Verdict before reading**: This system would lose money under real market conditions, misrepresent its own backtest results, and deploy capital based on fabricated inputs.

---

## AUDIT LENS 1 — THE PLACEHOLDER HUNT

### 1A. NotImplementedErrors Silently Swallowed in Production Paths

**`backtest/data/funding.py:14`**
```python
raise NotImplementedError("Real funding rate fetch not implemented. Triggering fallback.")
```
Every single funding rate used in every single backtest is **synthetic**. The "fallback" generates a mean-reverting AR(1) with `np.random.seed(hash(symbol) % (2**32 - 1))`. Funding on Hyperliquid has hit 0.1%/8h during FOMO runs and -0.05%/8h during liquidation cascades. The synthetic cap is `-0.0005 to 0.001` per 8h — it structurally cannot simulate the extremes that matter most.

**`backtest/data/lending.py:15`**
```python
raise NotImplementedError("DeFi Llama fetch not implemented. Triggering fallback.")
```
Every lending rate used is synthetic. During DeFi Summer 2021, stable APYs hit 20-30%. This engine caps at 20% and barely ever reaches it. All yield attribution is fiction.

**`backtest/engine/strategies.py:102`**
```python
raise NotImplementedError
```
`AllocationStrategy.generate_target_weights` is abstract but there is zero enforcement at instantiation time. Wrong factory call → silent runtime crash on first rebalance day.

### 1B. Dead Code Masquerading as Features

**`backtest/engine/hedger.py`** — entire file is unreachable. `HedgingEngine.calculate_hedge_adjustments` returns a list of action dicts. `simulator.py` never imports `HedgingEngine`, never calls it. `portfolio.derivative_positions` is initialized to `[]` and never written. The hedge ratio config in `hedger.py:7` (`{"bull": 0.2, "uncertain": 0.5, "crisis": 0.8}`) influences exactly zero executions.

**`backtest/engine/event_driven_replay.py`** — `EventDrivenReplayEngine` is never instantiated anywhere in the simulation loop. The L2 book replay, latency injection, and fill logic exist in isolation. Zero connection to `simulator.py`.

**`portfolio.py:DerivativePosition`** — `derivative_positions: List[DerivativePosition]` is always an empty list. Any code path that iterates it is iterating nothing.

### 1C. Magic Numbers That Will Fail Under Stress

**`simulator.py:165`**
```python
_mkt_cap = {"BTC": 0.65, "ETH": 0.25, "USDC": 0.08, "USDT": 0.07, "DAI": 0.05}
```
Static market caps baked into the Black-Litterman equilibrium return calculation. After an ETH flippening event, an L1 collapse, or a USDT depeg, these weights produce equilibrium returns for a market that no longer exists. They are never updated. They inform every non-emergency rebalance.

**`simulator.py:193`**
```python
cost = self.cost_model.estimate_cost(trade_size, asset, "buy", 1e8, 1e7)
```
Pool liquidity = $100M. Daily volume = $10M. **Hardcoded. For every asset. Every trade. Every day.** During a liquidity crisis, HL ETH book top-5 levels collapse to $2-3M. The slippage model sees $100M depth and computes near-zero impact. This is not modeling execution — it's suppressing it.

**`simulator.py:244`**
```python
sharpe = (ann_return - 0.05) / ann_vol  # Fixed rf to 5%
```
Risk-free rate hardcoded at 5% inside `summary()`. But `metrics.py:19` defaults to `0.02`. And `main.py:129` calls `calculate_performance_metrics(history_df, risk_free_rate=0.05)`. Three different places compute Sharpe. Two might use 5%, one defaults to 2%. Reported Sharpe ratios are not internally consistent.

**`cost_model.py:19`**
```python
dex_fee_bps: float = 0.9  # Maker 0.2bp, Taker 2.5bp -> 70/30 split ≈ 0.9bp
```
Hyperliquid **taker fee is 3.5bps**, not 2.5bps. `hl_reality_check.py:59` correctly states `taker_fee_bps = 3.5`. The cost model uses an incorrect taker fee in its blend. The blended fee should be `0.2*0.3 + 3.5*0.7 = 2.51bps`, not 0.9bps. The backtest underestimates taker costs by ~178%. Every taker execution is running a lie.

**`yield_engine.py:41`**
```python
daily_funding = (portfolio_value * 0.10) * funding_rate_daily
```
10% of portfolio is **always** in basis trades, earning funding, unconditionally every day. The `hedger.py` has logic to enter basis trades only when `annualized_rate > 10%`. These systems never talk to each other. Funding yield is booked regardless of whether any derivative positions exist. The portfolio earns money from trades that were never executed.

---

## AUDIT LENS 2 — THE QUANT DEATH-TRAP

### 2A. The Fee Illusion

**Net P&L decomposition for a $1M rebalancing portfolio:**

```
Gross rebalance alpha (weekly):        ~0 (pure allocation, no alpha)
Transaction cost per rebalance:
  - Trade volume ≈ 5-15% of NAV       $50k-$150k per week
  - Actual taker fee (3.5bps):         $17.5-$52.5
  - Model taker fee (0.9bps blended):  $4.5-$13.5

  Underestimation per rebalance:       $13-$39
  Annual rebalances:                   52 (weekly)
  Annual fee underestimation:          $676-$2028
```

Small in dollar terms but catastrophic in Sharpe attribution. For a portfolio generating 200bps annual alpha, underestimating fees by 13-40% corrupts the entire performance narrative.

**Bigger problem**: `simulator.py:193` always passes direction `"buy"` to `estimate_cost()`. Sells and buys have identical cost. In reality, selling into a downturn crosses the spread against you — directionally wrong cost model.

**The toxicity cost double-count**: `cost_model.py:77-78` computes `toxicity_cost` as a fixed per-trade fee. But `microstructure.py:53-55` computes a separate `toxicity_bps` drift for maker fills. The two models are never reconciled — and since `microstructure.py` is never called from the simulator (only from `event_driven_replay.py`, which is dead code), only the wrong model runs.

### 2B. Inventory Suicide

**The hedge is a fiction.** `hedger.py` computes delta adjustments. Nobody calls it. Portfolio is always 100% naked long. During a 30% BTC crash with no hedge:

```
Crisis scenario (Nov 2022, FTX):
  BTC: -30%, ETH: -35%, correlation: 0.95
  Risk Parity allocation: ~50% volatile
  Portfolio drawdown: ~16-17%
  Circuit breaker L2 threshold: 20%

  Speed of approach to L2: 3-4 days
  Emergency de-risk requires selling into collapsed liquidity

  Slippage model sees: $100M depth (hardcoded)
  Real HL depth at 3AM UTC during cascade: $1-3M
  Actual slippage on $500k sale: 5-15bps (model says 0.5-2bps)

  Fire sale cost overrun: 5-10x modeled cost
```

**`RecoveryPhase.check_further_decline` (`circuit_breaker.py:47-52`):**
```python
drop = (self.entry_portfolio_value - current_value) / self.entry_portfolio_value
return drop > self.further_decline_threshold  # 3% week 1-2
```
Entry value is set once when recovery begins. If portfolio subsequently recovers 15% then drops 4%, the recovery phase exits even though the portfolio is above entry. The check only compares to entry, not to the running recovery peak. This silently re-enters full volatile allocation too early.

**`RegimeAdaptiveStrategy` on volatile_target transition:**
```python
if current_regime == "bull":     volatile_target = 0.60
elif current_regime == "crisis": volatile_target = 0.10
else:                            volatile_target = 0.30
```
The HMM regime detector runs on `returns_history.mean(axis=1)` — a mean of all portfolio asset returns. BTC/ETH correlation > 0.9. The "crisis" signal fires when **average** portfolio return is in its lowest-mean HMM state. By that time, the market is already down 20-40%. This is not predicting crisis — it is detecting it after it started.

### 2C. Oracle vs Reality

**`fetcher.py:157-160`** — CoinGecko OHLC override:
```python
df['open'] = df['close']
df['high'] = df['close']
df['low'] = df['close']
```
When Binance is unavailable and CoinGecko is the fallback, **all four OHLC prices are identical**. Any strategy or cost model that looks at intraday range (high-low as volatility proxy) gets zero range → zero intraday volatility → underestimated costs → overestimated Sharpe. The data source silently changes the strategy's behavior.

**`fetcher.py:188-190`** — CoinCap volume:
```python
df['volume'] = 0.0
```
Volume is zero for all CoinCap-sourced assets. Market impact calculation uses `daily_volume_usd` but the simulator hardcodes `1e7` anyway, so this specific bug is masked — by another bug.

**Data source: Binance, not Hyperliquid.** The system is described as a Hyperliquid treasury strategy. All price data comes from Binance spot markets. HL perpetual prices differ from Binance spot by the basis (funding integral). During high-funding periods, HL perp trades at 0.5-2% premium. A strategy making allocation decisions based on Binance close prices but executing on HL perps uses stale, mis-priced signals by the basis.

**`simulator.py:101-102`** look-ahead window:
```python
lookback_end = self.current_day
returns_history = self.market_data.iloc[max(0, lookback_end-252):lookback_end]
```
This correctly excludes today. But `simulator.py:65-68` during warmup:
```python
initial_idx = self.market_data.iloc[:self.warmup_days].pct_change().fillna(0).mean(axis=1)
if len(initial_idx) >= 60:
    self.regime_detector.fit(initial_idx)
```
The HMM is **pre-fitted on the first 60 days of the backtest period**. Regime predictions from day 61 onward use a model trained on the same period. First out-of-sample trade at day 61 uses an in-sample HMM. Subtle but persistent look-ahead bias.

**Regime refit at `simulator.py:77`:**
```python
lookback = self.market_data.iloc[max(0, day-504):day+1]
```
`day+1` — this includes the current day's close. The HMM is refit with today's return included in the training set, then today's regime is predicted from the same fitted model. In-sample fit used for same-day prediction. Look-ahead.

---

## AUDIT LENS 3 — ARCHITECTURAL PAIN POINTS

### 3A. No Async — No Real-Time Capability

The entire system is synchronous. There is no websocket handler, no asyncio loop, no coroutine. `hl_reality_check.py` uses `requests.post()` — blocking HTTP. This is a **backtest library** pretending to describe a production trading system.

`fetcher.py:115`: `time.sleep(0.1)` inside data fetch loop. 3 years of daily data = 1095 candles. At 100ms per request: blocking the thread for every page boundary.

### 3B. State Desync

**Primary state machine gap** (`simulator.py:194-208`):

```python
# Cost subtracted from portfolio_value before weights recomputed
cost = self.cost_model.estimate_cost(...)
self.portfolio.portfolio_value -= cost.total

# Weights expressed as fraction of NOW-REDUCED portfolio_value
actual_trade_size = trade_size * cost.fill_ratio
new_target_val = old_val + (actual_trade_size if new_target_val > old_val else -actual_trade_size)
target_weights[asset] = new_target_val / self.portfolio.portfolio_value

# Cash computed as residual — can go NEGATIVE
self.portfolio.cash = self.portfolio.portfolio_value * (1.0 - sum(target_weights.values()))
```

`portfolio_value` has been reduced by costs. `new_target_val` is computed using pre-cost `old_val` and `actual_trade_size`. Weights are normalized against post-cost `portfolio_value`. Sum of weights may exceed 1.0 after floating-point and cost subtraction. `cash = portfolio_value * (1 - sum_weights)` goes **negative** when sum_weights > 1.

No assertion. No floor. No detection. Simulation continues with negative cash silently. Portfolio value drifts upward via phantom capital.

**No nonce / order tracking**: `event_driven_replay.py:6-15` has an `Order` class with `order_id`. But there is no sequence number, no deduplication, no timeout. In production Hyperliquid, orders are identified by nonce + address. Reconnect without replaying pending orders → phantom open positions.

### 3C. Observability Failure

**Q: Could a real $1M drawdown be reconstructed from current logs?**  
**A: No.**

`simulator.py:225-236` history record:
```python
{"timestamp", "portfolio_value", "cash", "regime", "cb_level",
 "effective_hwm", "recovery_active", "var_95_1d", "jump_var_95_1d", "trade_volume_usd"}
```

Missing from every history entry:
- Per-asset trade sizes and directions
- Fill ratios per asset
- Actual slippage realized per trade
- Funding payments received/paid
- Cost breakdown (fee vs impact vs toxicity)
- Regime confidence and crisis probability
- Individual asset returns that drove regime change
- VaR breach events

The `execution_log` in `event_driven_replay.py` has this data but is never connected to the simulator. A $1M drawdown happened. You have daily portfolio values and a regime label. You cannot determine which asset caused it, what execution path was taken, or whether the circuit breaker fired correctly.

---

## AUDIT LENS 4 — THE ANTI-AI CRITIQUE

### Tutorial Logic — BlackLitterman Is Not Black-Litterman

**`strategies.py:238-244`:**
```python
inv_cov = np.linalg.inv(covariance_matrix)
mu = np.array([expected_returns.get(name, 0.0) for name in asset_names])
weights_arr = inv_cov @ mu
```

This is `Σ⁻¹μ` — proportional to mean-variance tangency portfolio weights, not Black-Litterman. Black-Litterman requires:
1. Market equilibrium returns π = δΣw_mkt
2. Investor views (P, Q, Ω)
3. Posterior mean = `[(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ [(τΣ)⁻¹π + PᵀΩ⁻¹Q]`

`portfolio_optimizer.py:145-197` correctly implements BL. `BlackLittermanStrategy` ignores it entirely and does ad-hoc mean-variance. The strategy is mislabeled. Operators believe they run BL while running vanilla tangency portfolio.

### MinVariance Is Not Min-Variance

**`strategies.py:207-215`:**
```python
inv_cov = np.linalg.inv(covariance_matrix)
ones = np.ones(len(asset_names))
weights_arr = inv_cov @ ones
weights_arr = weights_arr / np.sum(weights_arr)
weights_arr = np.maximum(weights_arr, 0)  # clips negatives
weights_arr = weights_arr / sum_w
```

The global minimum variance portfolio is `w* = Σ⁻¹1 / (1ᵀΣ⁻¹1)`. The code computes this correctly. Then it clips negative weights and re-normalizes. **Clipping negative weights destroys the optimization.** The clipped result is not minimum variance — it is an arbitrary long-only portfolio with significantly higher variance than the true constrained solution. The correct approach is constrained QP — already implemented in `portfolio_optimizer.py:15-73` — but the strategy class ignores it.

### Architecture Theater — The Jump Diffusion Loop

**`var_models.py:116-118`:**
```python
for i in range(num_simulations):
    if n_jumps[i] > 0:
        jump_sizes[i] = np.sum(np.random.normal(jump_mean, jump_std, n_jumps[i]))
```

50,000-iteration Python loop calling numpy inside. This should be vectorized:
```python
max_jumps = n_jumps.max()
all_jump_sizes = np.random.normal(jump_mean, jump_std, (num_simulations, max_jumps))
mask = np.arange(max_jumps) < n_jumps[:, np.newaxis]
jump_sizes = (all_jump_sizes * mask).sum(axis=1)
```

The loop runs in the main simulation thread, blocking daily processing. Worst case at 50,000 iterations with variable-length inner draws: 200-500ms per VaR computation. Called daily → minutes of blocked simulation time per backtest.

### The Funding Rate Disconnection

```
YieldEngine.calculate_yield:
  daily_funding = (portfolio_value * 0.10) * funding_rate_daily
  # Books funding from 10% of portfolio in basis trades — every day

HedgingEngine.calculate_basis_trades: [never called from simulator]
  # Would only open trades when annualized_rate > 10%

Result: Free money accrues from non-existent positions every single day
```

Over a 2-year backtest in "bull" regime with 0.033%/day funding on 10% of $1M:  
`$1M × 0.10 × 0.00033 × 504 days = $16,632` of phantom P&L baked into every reported result.

---

## CRITICAL FAILURE POINTS

### FAILURE 1: Fabricated Inputs Driving All Decisions

**Files**: `data/funding.py`, `data/lending.py`, `engine/yield_engine.py`

Every funding rate and every lending rate is synthetic. The `LENDING_RATE_SCHEDULE` in `yield_engine.py` is a hand-crafted lookup table. Real Hyperliquid Earn rates, real Aave/Compound lending rates, real HL perpetual funding — none of it is used. The strategy's entire yield thesis (stablecoin lending + basis trading) is evaluated against made-up numbers.

**Failure mode**: Strategy appears profitable in backtest due to 5% synthetic lending yield + 10% phantom basis trade yield. In production, actual HL Earn rates and actual funding rates differ. Strategy deployed. Actual yield 40% lower than modeled. Capital erosion from execution costs exceeds actual yield.

### FAILURE 2: Blended Fee Model Understates Taker Costs by 178%

**File**: `engine/cost_model.py:19`

Modeled fee: 0.9bps blended. Actual HL taker fee: 3.5bps. Emergency de-risk at L2 circuit breaker requires market selling. All circuit-breaker-triggered trades are taker. All emergency rebalances are taker. At the exact moment the model should be most conservative about execution cost, it is most wrong.

**Failure mode**: Portfolio hits 20% drawdown. Emergency de-risk executes. Actual execution cost = $150k trade × 3.5bps = $52.5. Model estimates $13.5. Circuit breaker re-entry threshold is calibrated to modeled costs. Strategy re-enters too quickly. Second leg down. Repeat.

### FAILURE 3: Cash Accounting Bug Creates Phantom Capital

**File**: `engine/simulator.py:194-208`

Cost is subtracted from `portfolio_value` before weights are recomputed. Weights are then expressed as fractions of the reduced `portfolio_value`. Cash is computed as residual. Under specific conditions (multiple partial fills, high costs), `sum(target_weights.values()) > 1.0` → `cash < 0`.

**Failure mode**: Portfolio value drifts upward via phantom cash. Reported total_return is inflated. Strategy appears to beat benchmark. Capital deployed based on inflated NAV.

### FAILURE 4: Regime Detection Uses In-Sample Warmup

**File**: `engine/simulator.py:65-78`

HMM fit on first 60 days of backtest period. Regime predictions from day 61 onward use a model trained on the same period. Walk-forward validation in `optimizer/walk_forward.py` does not account for this — the training slice includes the warmup period where the model is already in-sample.

**Failure mode**: Backtested regime accuracy is inflated by in-sample fit. Live deployment uses a cold HMM with no relevant history. First 60 days of live trading have no valid regime signal. Strategy allocates at 30% volatile (uncertain default) regardless of actual market conditions.

### FAILURE 5: Hedger Disconnected, Delta Never Managed

**Files**: `engine/hedger.py`, `engine/simulator.py`

Hedger is dead code. Portfolio carries full spot delta at all times. Risk metrics are computed against a fictitious hedged portfolio. Actual drawdown exposure is 2-3x what the risk model shows.

**Failure mode**: Strong directional move against portfolio. No hedge absorbs delta. Drawdown hits 20% faster than modeled. Circuit breaker fires. Emergency fire sale into illiquid book.

---

## SILLY MISTAKES

| Location | Error |
|---|---|
| `simulator.py:193` | Always passes `direction="buy"` — sells have identical cost model |
| `strategies.py:240` | BL strategy does `Σ⁻¹μ`, not BL posterior |
| `strategies.py:207-215` | MinVar clips negatives then re-normalizes — not min variance |
| `var_models.py:116-118` | 50k-iteration Python loop inside numpy simulation |
| `var_models.py:107` | Uses `np.random.normal` (global RNG), not seeded `default_rng` |
| `simulator.py:66` | Warmup HMM fit is in-sample look-ahead |
| `simulator.py:77` | `day+1` in rolling refit includes current day |
| `fetcher.py:157` | CoinGecko sets `open=high=low=close` — zero OHLC range |
| `fetcher.py:189` | CoinCap sets `volume=0` for all assets |
| `yield_engine.py:41` | Books basis trade yield regardless of whether positions exist |
| `funding.py:35` | Deterministic synthetic data via `np.random.seed(hash(symbol))` |
| `cost_model.py:33` | MEV cost = 0 forever (`mev_threshold_usd = 1000000.0`) |
| `circuit_breaker.py:123` | `vol_ratio < 1.2` allows CB decay too aggressively |
| `simulator.py:244` | `sharpe` uses rf=0.05; `metrics.py` defaults to rf=0.02 |
| `hedger.py` | Entire module unreachable from production path |
| `event_driven_replay.py` | Entire module unreachable from production path |

---

## THE UNDEPLOYABLE VERDICT

### 1. All Critical Inputs Are Fabricated
Funding rates: synthetic. Lending rates: synthetic. Market depth: `1e8` hardcoded. Basis trade yield: unconditional regardless of positions. The backtest evaluates a strategy against a market that does not exist.

### 2. Execution Cost Model Is Wrong in the Direction That Kills You
Taker fee understated by 178%. Emergency rebalances (when costs matter most) use taker execution. The system is calibrated to underestimate costs during its most dangerous operations. Sharpe ratios are inflated. Drawdown estimates are understated.

### 3. Delta Is Never Managed Despite Claiming It Is
Hedger is dead code. Portfolio carries full spot delta at all times. Risk metrics are computed against a fictitious hedged portfolio. Actual drawdown exposure is 2-3x what the risk model shows.

### 4. No Deterministic Reconciliation
Cash can go negative silently. Sum of weights can exceed 1.0. No assertion, no floor, no alert. Position state diverges from expected state on every rebalance day with partial fills. No external state truth to reconcile against.

### 5. Regime Detection Is In-Sample During Most Important Period
First 60 days of live trading: cold HMM, no valid regime signal, uncertain allocation. Every subsequent 30-day refit includes original warmup returns — the model never fully escapes in-sample contamination.

---

## THE FIX

| Problem | Correct Production Pattern |
|---|---|
| Synthetic funding/lending | Fetch real HL API historical funding (`/info` → `fundingHistory`), cache in Parquet with checksums |
| Fee model wrong taker | Separate `MakerCostModel` and `TakerCostModel`; emergency rebalances always use taker model |
| Dead hedger | Event-sourced command pattern: `hedger.compute_adjustments()` → command queue → `executor.run(queue)` |
| Cash goes negative | Double-entry bookkeeping: every trade debits cash, credits position atomically; `assert cash >= 0` after each trade |
| In-sample HMM warmup | Purged CV: exclude warmup period from all model training; fit HMM on pre-backtest data only |
| Hardcoded market depth | Real-time HL L2 snapshot before each rebalance; cache invalidated on stale timestamp |
| Blended fee ignoring direction | Direction-aware execution: query HL fee tier from account level, apply 3.5bps taker on market orders |
| MinVar clips negatives | Use constrained QP with `bounds=(0,1)` and `sum=1` — already implemented in `portfolio_optimizer.py`, just wire it |
| No observability | Append-only event journal: every fill, every regime change, every CB event is an immutable log entry |
| Phantom basis yield | Yield only booked when `len(derivative_positions) > 0` and position size exceeds threshold |
| BL mislabeled as BL | Wire `BlackLittermanStrategy` to call `optimize_black_litterman` from `portfolio_optimizer.py` |
| Jump diffusion loop | Vectorize compound Poisson draws with numpy broadcasting |

---

*The system as built is a research sketch with production-grade variable names. The abstractions are correct in form. The connections between them are missing, wrong, or fabricated. Under adversarial market conditions — the only conditions that matter — it would miscompute risk, underestimate costs, fail to hedge, and report inflated performance until margin call.*
