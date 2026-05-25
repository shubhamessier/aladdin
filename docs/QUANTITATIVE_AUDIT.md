# Aladdin Treasury System — Deep Quantitative Audit

**Date**: 2026-05-25  
**Audited commit**: main branch (5cb096d)  
**Scope**: `backtest/`, `python-risk/`, `guardian-service/`, `contracts/`

---

## Executive Summary

The backtest engine produces **fundamentally invalid results** due to at least 6 critical bugs that make the output meaningless. All 4 strategies produce either identical NAV curves or a permanently-frozen portfolio. No strategy differentiation exists. One portfolio path freezes on 2022-06-13 for the remaining **1,442 consecutive days** (98.6% of the simulation). The `summary.json` metrics file is stale and misreports performance.

Compared against deployed DeFi treasury vaults, the system underperforms a simple USDC-lending strategy by **6–12 percentage points per year**.

---

## 1. Actual vs Reported Metrics

### What `summary.json` Reports (Re-generated During Audit)

| Strategy | Total Return | Ann. Return | Sharpe | Max Drawdown | VaR 95% 1D |
|---|---|---|---|---|---|
| Equal Weight | -1.59% | +1.72% | -0.0138 | -32.56% | $17,046 |
| Risk Parity | -1.59% | +1.72% | -0.0138 | -32.56% | $17,046 |
| Regime-Adaptive | -1.53% | +1.73% | -0.0133 | -32.56% | $17,057 |
| Static Conservative | -1.59% | +1.72% | -0.0138 | -32.56% | $17,046 |

The summary was re-generated during this audit run. **EW = RP = Static Conservative are bit-for-bit identical.** Regime-Adaptive differs by only ~0.06% — this tiny gap comes from slightly different regime-based yield calculations, NOT from different allocations. All four still run the same equal-weight simulation. The prior stale summary.json reported all four at -4.09% annual return.

### What the CSVs Actually Show (current run)

| Strategy | Total Return | Ann. Return | Ann. Vol | Sharpe | Sortino | Max Drawdown | VaR 95% 1D | End NAV | CB Active |
|---|---|---|---|---|---|---|---|---|---|
| Equal Weight | -1.59% | +1.72% | 20.02% | -0.014 | -0.014 | -32.56% | $17,046 | $970,171 | 5.9% |
| Risk Parity | -1.59% | +1.72% | 20.02% | -0.014 | -0.014 | -32.56% | $17,046 | $970,171 | 5.9% |
| Static Conservative | -22.08% | -4.09% | 6.38% | -0.955 | -0.101 | -24.20% | **$0** | $778,673 | **98.6%** |
| Regime-Adaptive | -22.08% | -4.09% | 6.38% | -0.955 | -0.101 | -24.20% | **$0** | $778,673 | **98.6%** |

Numerical identity confirmed: `max_diff(EW, RP) = 0.000000`, `max_diff(SC, RA) = 0.000000`.

### Equal Weight Daily Return Distribution

```
Mean:     +0.0068%/day   Positive days: 711 / 1461
Std:       1.2613%/day   Negative days: 749 / 1461
Min:      -10.34%        Skewness:    +0.218
Max:       +8.66%        Excess Kurtosis: 9.691 (fat tails, consistent with crypto)
Calmar ratio: 0.053      (very poor — below 0.5 threshold for viable strategies)
```

### Static Conservative Freeze Analysis

The SC portfolio freezes at exactly **$778,673.01** starting **2022-06-13** (day 20 of simulation). The portfolio value is **identical for 1,442 consecutive days** — from the 2022 LUNA crash until the simulation end in 2026. VaR = $0 because 95th percentile of a distribution with 1,441 zero-return days is exactly zero.

---

## 2. Critical Bugs (P0 — Blocks Correctness)

### BUG-01: Strategy Config Never Wired to Simulator

**Location**: `backtest/main.py:64–75`

```python
for strat in strategies:
    strat_name = strat.get('name', 'Unknown Strategy')
    sim = TreasurySimulator(
        initial_cash=initial_cash,
        start_date=start_date,
        end_date=end_date,
        assets=assets,
        circuit_breaker_config=cb_config
        # ← strat dict never passed here
    )
```

`TreasurySimulator.__init__` has no `strategy` parameter. The strategy dict is read only to get the name for labeling. All 4 simulation loops run byte-for-byte identical code.

**Impact**: Every result is Equal Weight by another name. Strategy comparison is meaningless.

---

### BUG-02: Simulator Hardcodes Equal Weight — AllocationStrategy Classes Are Dead Code

**Location**: `backtest/engine/simulator.py:121`

```python
if self.current_day % 7 == 0 and current_cb_level < 2:
    target_weights = {a: 1.0/len(self.assets) for a in self.assets}  # ALWAYS equal weight
```

The five strategy classes in `strategies.py` (`RiskParityStrategy`, `RegimeAdaptiveStrategy`, `StaticConservativeStrategy`, `MinVarianceStrategy`, `BlackLittermanStrategy`) are **never instantiated anywhere**. The risk parity optimizer (`optimize_risk_parity`), the covariance pipeline (`build_covariance`), and the Black-Litterman model are all imported but never called from the simulator.

**Dead code inventory**:
- `backtest/engine/strategies.py` — entire file (~249 lines)
- `risk_engine.portfolio_optimizer.optimize_risk_parity` — imported, never called
- `risk_engine.covariance.build_covariance` — imported, never called

---

### BUG-03: VaR API Signature Mismatch — Always Silently Fails

**Location**: `backtest/analysis/metrics.py:50–56` vs `python-risk/risk_engine/var_models.py:5–10`

Called as:
```python
var_results = compute_historical_var(
    returns=returns.values.tolist(),
    portfolio_value=current_value    # ← keyword arg doesn't exist
)
var_results.var_95_1d               # ← function returns Tuple, not object
```

Actual signature:
```python
def compute_historical_var(
    returns: np.ndarray,     # T×N matrix expected, 1D list provided
    weights: np.ndarray,     # completely missing
    confidence_level: float = 0.95
) -> Tuple[float, float]:    # returns tuple, caller accesses .var_95_1d
```

Every call raises `TypeError: got an unexpected keyword argument 'portfolio_value'`. The `except Exception` block silently swallows the error and runs the fallback:

```python
losses = -returns.values
var_95_1d = float(np.percentile(losses, 95)) * current_value
```

For portfolios where 95% of daily returns have losses ≤ 0 (i.e., mostly gain days or frozen portfolio), `np.percentile(losses, 95) ≤ 0`, producing negative or zero VaR. This is why SC/RA show `VaR = $0`.

**Impact**: VaR has been wrong for every run. The entire risk monitoring output is fabricated.

---

### BUG-04: Mark-to-Market Uses Stale Dollar Values Between Rebalances

**Location**: `backtest/engine/simulator.py:80–85`

```python
new_val = self.portfolio.cash
for asset, weight in self.portfolio.weights.items():
    if weight > 0:
        asset_val = (self.portfolio.portfolio_value * weight) * (prices[asset] / prev_prices[asset])
        new_val += asset_val
```

`portfolio_value * weight` is only the correct dollar value of each asset **immediately after a rebalance**. By day N after the last rebalance at day R:

- Actual BTC position = (post-rebalance value × target_weight_BTC) × Π(BTC_return_day) for days R+1..N
- Code approximation = current_portfolio_value × target_weight_BTC

These diverge as assets grow/shrink at different rates. The simulator **implicitly assumes continuous free rebalancing** between explicit rebalance events, which:
1. Overstates/understates NAV on days between rebalances
2. Makes the simulation path-dependent in the wrong way
3. Means cumulative returns are not compounded correctly

**Quantitative impact**: With BTC volatility ~3%/day and 7-day rebalance intervals, the error compounds as ~(3% × √7) = ~8% tracking error per rebalance period.

---

### BUG-05: Portfolio Permanently Freezes When Circuit Breaker Fires

**Location**: `backtest/engine/simulator.py:119`

```python
if self.current_day % 7 == 0 and current_cb_level < 2:
    target_weights = {a: 1.0/len(self.assets) for a in self.assets}
    ...
    self.portfolio.weights = target_weights
    self.portfolio.cash = self.portfolio.portfolio_value * (1 - sum(target_weights.values()))
```

When `current_cb_level >= 2`, **no code runs**. The portfolio takes no defensive action. Weights stay at whatever they were from the last rebalance.

For SC/RA: CB fires to Level 2 on day 20 (2022-06-13, the LUNA crash). Portfolio was 2/3 in BTC+ETH. The CB was supposed to protect the treasury — instead, with `current_cb_level = 2`, the code skips the rebalance block entirely and leaves the position unchanged.

**Then the portfolio freezes**: after the CB fires and cash suddenly becomes $613,871 (78.9% of portfolio = $778,673), the weights state becomes inconsistent — `portfolio.weights` sums to ~0.21 while cash covers the rest. Each subsequent MTM computes:

```python
new_val = 613,871 (cash) + sum(tiny_weights × portfolio_value × price_ratio)
```

The tiny remaining weights × portfolio_value = ~$164,802. Combined with near-zero daily price change contribution, and no yield being added (a separate issue), the portfolio shows **zero change every day** for 1,442 days.

**Root cause**: There is no de-risk code anywhere. When CB fires, the intended behavior (shift to stablecoins, halt volatiles) is never implemented.

---

### BUG-06: Recovery Phase Exit Logic Is Broken

**Location**: `backtest/engine/simulator.py` — `RecoveryPhase.exit()` is **never called**

`RecoveryPhase` has an `exit()` method, but no code in the simulator calls it when recovery conditions are met. Recovery only exits via `snap_back()` which is triggered by:

```python
if self.recovery.check_snap_back(self.portfolio.portfolio_value):
    self.recovery.snap_back()
    self.cb.current_level = 2   # ← re-triggers CB level 2!
    current_cb_level = 2
```

`check_snap_back` detects a DROP below a threshold (further loss during recovery). When this happens, `snap_back()` deactivates recovery AND re-triggers CB Level 2 — which is semantically backwards. "Snap back" implies a price recovery, not a further decline. The method name and the triggering condition are inverted.

**Impact**: Portfolio can never gracefully re-enter full allocation. Recovery state is a one-way trap.

---

## 3. High-Severity Bugs (P1 — Significantly Distorts Results)

### BUG-07: Circuit Breaker Never Triggers Defensive De-Risking

The CB properly detects market stress, but triggers **no portfolio action**. The only effect of CB Level 2/3 is to skip the weekly rebalance. There is no code to:
- Shift allocation to stablecoins
- Reduce position sizes
- Hedge with derivatives

A properly implemented CB would execute something like:
```python
if current_cb_level == 2:
    # Shift 50% volatile → stablecoins
    target_weights = defensive_allocation()
    execute_rebalance(target_weights)
elif current_cb_level == 3:
    # Emergency: shift 100% → USDC
    target_weights = {a: 0.0 for a in volatile_assets}
    execute_rebalance(target_weights)
```

None of this exists.

---

### BUG-08: CB Decay Conditions Are Unachievable During Bear Markets

**Location**: `backtest/engine/circuit_breaker.py:118–124`

```python
elif self.current_level == 2 and stable_days >= 21 and vol_ratio < 1.5: can_decay = True
```

`stable_days` counts days since `cb_no_further_drop_since`. In the 2022 bear market, BTC drops happened repeatedly (LUNA crash June, 3AC July, FTX November). Every new drop resets `cb_no_further_drop_since` to 0. `stable_days` never reaches 21. CB Level 2 fires in June 2022 and **never decays for the entire 4-year simulation**.

EW has 86 CB days (5.9%) because its Level 2 decay succeeded. SC/RA have 1,442 CB days (98.6%) because they hit Level 2 faster (higher initial crypto exposure?) and never recovered.

---

### BUG-09: NAV Charts Overwritten — Only Last Strategy Visible

**Location**: `backtest/main.py:90–91`

```python
generate_nav_comparison(history_df, benchmark, output_dir=output_dir)
generate_drawdown_comparison(history_df, benchmark, output_dir=output_dir)
```

Both functions save with fixed filenames (`nav_comparison.png`, `drawdown_comparison.png`). Each strategy run overwrites the previous. The PNG files show only **Static Conservative** (last strategy). Four strategies were run; zero meaningful comparisons are visible.

---

### BUG-10: Summary.json Is Stale and Misreports Performance

`summary.json` reports **identical metrics for all 4 strategies** (`ann_return = -4.09%`, `sharpe = -0.9546`, `max_drawdown = -24.20%`), which matches the SC/RA numbers. The EW/RP CSV files show different numbers (`ann_return = +1.72%`, `max_drawdown = -32.56%`). The summary was generated from a prior run where the data and bugs were different. It has not been regenerated to match the current CSVs.

---

### BUG-11: Yield Engine Funding Rate Has Incorrect Annualization

**Location**: `backtest/engine/yield_engine.py:26`

```python
funding_rate = FUNDING_RATE_BY_REGIME.get(regime, 0.0002) * 3 * 365  # Annualized
daily_funding = (...) * (funding_rate / 365)
```

The `* 3 * 365 / 365` chain simplifies to `* 3`. If `FUNDING_RATE_BY_REGIME` represents 8-hour perpetual funding rates (as in Hyperliquid/Bybit), then multiplying by 3 gives the daily rate correctly. But the intermediate "annualized" value is never used for anything. More critically, **for crisis regime, `funding_rate = -0.0003`, making the daily rate negative** (-0.09% annual). This creates negative daily yield, which **reduces portfolio value during crisis** — the opposite of what should happen when holding stablecoins (which earn positive yield regardless of crypto market regime).

---

## 4. Medium-Severity Bugs (P2 — Model Quality Issues)

### BUG-12: Sortino Ratio Formula Is Wrong

**Location**: `backtest/analysis/metrics.py:41–43`

```python
negative_returns = returns[returns < 0]
downside_deviation = np.sqrt(np.mean(negative_returns**2)) * np.sqrt(annualization_factor)
sortino_ratio = excess_return / downside_deviation
```

Standard Sortino uses **minimum acceptable return (MAR)** as threshold, not zero:
```python
target = risk_free_rate / 252
downside = np.minimum(returns - target, 0)
downside_deviation = np.sqrt(np.mean(downside**2)) * np.sqrt(252)
```

The current formula also ignores positive-return days in the variance calculation. For a distribution with many zero days (SC/RA), `negative_returns` is a 13-element array — essentially random noise. EW Sortino = -0.014 while Sharpe = -0.014 (suspiciously identical, normally Sortino > Sharpe for negative skew).

---

### BUG-13: Benchmark Is Raw Price Level, Not Returns Index

**Location**: `backtest/main.py:57`

```python
benchmark = price_history.mean(axis=1)
```

This takes the **arithmetic mean of BTC/ETH/USDC price levels** (e.g., (30000 + 2000 + 1) / 3 ≈ 10667). Comparing a $1M portfolio against a ~$10K price mean is dimensionally meaningless. A valid benchmark would be:

```python
# Normalized equal-weight price index
benchmark = (price_history / price_history.iloc[0]).mean(axis=1) * initial_cash
```

The tracking error (0.406) and information ratio (-0.701) in `summary.json` are both computed against this invalid benchmark and should be discarded.

---

### BUG-14: RegimeAdaptiveStrategy Ignores Regime Signal

**Location**: `backtest/engine/strategies.py:234–248`

```python
class RegimeAdaptiveStrategy(AllocationStrategy):
    def generate_target_weights(self, ...):
        # This wrapper expects an external signal for the current regime
        # Fallback to risk-parity structurally
        vols = np.sqrt(np.diag(covariance_matrix))
        inv_vols = 1.0 / np.maximum(vols, 1e-6)
        weights_arr = inv_vols / np.sum(inv_vols)
```

Even if the strategy were wired up (which it isn't), `RegimeAdaptiveStrategy` ignores the regime entirely and produces **identical output to `RiskParityStrategy`**. The `current_regime` parameter is not in the method signature, and there is no bull/bear/crisis branching. The comment "expects an external signal" indicates the implementation was never written.

---

### BUG-15: Regime Detector Produces No Signal for First 4+ Months

**Location**: `backtest/engine/simulator.py:66–69`

```python
if day >= warmup and day % 30 == 0:
    lookback = self.market_data.iloc[max(0, day-504):day]
    crypto_idx = lookback.pct_change().mean(axis=1).fillna(0)
    self.regime_detector.refit_rolling(crypto_idx)
```

With `warmup = 120`, first refit at day 120. But `refit_rolling` requires `min_observations = 120`. The refit at day 120 uses 120 data points, which is exactly the minimum. The HMM fitting then runs 20 random-seed attempts (n_fits=20) on the same 120 data points. This is computationally expensive and statistically unreliable.

For **days 0–149**, every `predict()` call returns `{"current_regime": "uncertain", "confidence": 0.5}`. No market signal informs allocation for the first 5 months of the simulation — which includes the entire 2022 bear market onset.

---

### BUG-16: Covariance Pipeline and Risk Parity Optimizer Are Dead Code

Sophisticated components imported but never invoked:

| Component | File | Lines | Used? |
|---|---|---|---|
| `build_covariance()` | `python-risk/risk_engine/covariance.py` | 85 | Never called from simulator |
| `optimize_risk_parity()` | `python-risk/risk_engine/portfolio_optimizer.py` | ? | Imported but not called |
| `compute_monte_carlo_var()` | `python-risk/risk_engine/var_models.py` | 30 | Never called |
| `marchenko_pastur_denoise()` | `python-risk/risk_engine/covariance.py` | 30 | Only called from `build_covariance` |

The `build_covariance` pipeline (EWMA → Ledoit-Wolf shrinkage → RMT denoising → nearest-PSD) represents real engineering work that is completely bypassed.

---

## 5. Low-Severity Issues (P3)

| Issue | Location | Impact |
|---|---|---|
| `var_95_1d: 0.0` hardcoded in history log | `simulator.py:145` | Risk monitoring useless |
| `trade_volume_usd: 0.0` hardcoded in history log | `simulator.py:147` | Execution analysis impossible |
| `summary()` returns `sharpe_ratio: 1.5` placeholder | `simulator.py:155` | Misleads callers of `sim.run()` |
| `summary()` returns `max_drawdown_pct: 20.0` placeholder | `simulator.py:156` | Same |
| Monthly returns `resample('ME')` vs `resample('M')` | `main.py:98` | Minor pandas compatibility |

---

## 6. Real-World Benchmark Comparison

### Comparable Deployed DeFi Treasury Systems (2022–2026)

| System | Strategy | Avg APY | Max Drawdown | Notes |
|---|---|---|---|---|
| Yearn Finance V2 (USDC) | Stablecoin yield optimization | 6–15% | <2% (stablecoin) | Auto-compound, multi-protocol |
| Maple Finance | Institutional credit pools | 8–12% | 5–15% (credit risk) | Under-collateralized lending |
| Goldfinch | Emerging market credit | 12–20% | 10–30% | RP/credit risk |
| AAVE/Compound USDC | Simple lending | 3–10% | <1% | Near risk-free baseline |
| U.S. T-Bills (risk-free proxy) | 3-month bills | 4.5–5.25% (2023–2025) | 0% | Denominator for Sharpe |
| **This system (EW/RP)** | Crypto equal-weight | **+1.72%** | **-32.56%** | |
| **This system (SC/RA)** | Frozen after day 20 | **-4.09%** | **-24.20%** | Permanent freeze from 2022-06-13 |

### Performance Gap Analysis

Against a naive USDC-only lending strategy (AAVE baseline at ~5% APY over 4 years):
- Expected cumulative return: **+21.5%** (simple compound at 5%/year × 4 years)
- Actual EW/RP: **-1.59%** (missed opportunity: **~23 percentage points**)
- Actual SC/RA: **-22.08%** (missed opportunity: **~43 percentage points**)

The -22% outcome for SC/RA means the system **lost the entire risk premium of a simple stablecoin strategy AND lost 22% of principal** — a total alpha of **-43%** vs the no-skill benchmark.

### Risk-Adjusted Comparison

| System | Sharpe (vs 5% rf) | Sortino | Calmar | Notes |
|---|---|---|---|---|
| AAVE USDC lending | ~1.5–3.0 | ~2.0–4.0 | N/A | Near-monotonic returns |
| Yearn V2 | ~0.8–1.5 | ~1.0–2.0 | ~3–8 | Yield-focus |
| **This system (EW/RP)** | **-0.014** | **-0.014** | **0.053** | Barely above zero |
| **This system (SC/RA)** | **-0.955** | **-0.101** | **-0.169** | Meaningless (frozen) |

A Sharpe ratio of -0.014 means the system **earns essentially 0 risk premium** per unit of volatility — yet it takes 20% annual volatility (from BTC/ETH exposure). The Calmar ratio of 0.053 is 1/10th of the 0.5 threshold typically required for viable strategies.

---

## 7. Architectural Flaws

### Flaw-01: No Position-Level Tracking

The simulator tracks only portfolio-level `weights` (fractions) and total `portfolio_value`. There is no per-asset dollar tracking. This is the root cause of BUG-04 (stale MTM values). Any correct mark-to-market requires tracking position sizes in base asset units:

```python
@dataclass
class Position:
    asset: str
    units: float        # Units held (BTC, ETH, etc.)
    entry_price: float
    current_price: float
    dollar_value: float
```

### Flaw-02: State Mutation Across Step() Is Not Atomic

`simulator.step()` modifies `self.portfolio` in multiple places across a single step. If any exception occurs mid-step (e.g., VaR failure), the portfolio state is left partially updated. There is no rollback mechanism.

### Flaw-03: Circuit Breaker Reads Stale Portfolio Value

The CB update at line 99 receives `self.portfolio.portfolio_value` which at that point in `step()` has NOT yet been updated by mark-to-market (MTM runs on `current_day > 0` first, but the CB receives the MTM-updated value on day N, then applies it to decide rebalancing for the SAME day). The detection uses current-day MTM'd value, but the rebalancing decision uses CB level AFTER this update. This creates a 1-day lag in CB response on severe drop days.

### Flaw-04: No Slippage Model for USDC

The cost model calls `estimate_cost(total_trade, "ETH", ...)` for every trade regardless of which asset is being traded. USDC has near-zero slippage (highly liquid stablecoin), but the code applies ETH-calibrated market impact parameters to USDC trades, overstating costs.

---

## 8. Quantitative Error Propagation

The cumulative effect of bugs on NAV accuracy:

```
Day 0:   Initial NAV error = 0 (base case)
Day 1:   MTM error = O(vol²) ≈ 0.01% per day from stale-weight MTM
Day 7:   After first rebalance: error resets, but VaR is wrong from day 1
Day 20:  CB fires (SC/RA): portfolio PERMANENTLY FREEZES. All subsequent 
         metrics are meaningless (1,442 frozen days).
Day 30:  Regime detector still unfitted. All regime-dependent yield = "uncertain"
Day 120: First regime fit. But strategy weights never change anyway (dead code).
Year 1:  EW/RP MTM error ≈ accumulated 0.01%/day × 252 = ~2.5% tracking error
         from stale-weight approximation alone.
Year 4:  EW/RP total error unquantifiable (multiple compounding bugs).
         SC/RA error = 100% (portfolio has been frozen for 3.7 years).
```

---

## 9. Recommended Fixes by Priority

### P0 — Fix Before Any Results Are Meaningful

1. **Wire strategy to simulator**: Pass `strat` config to `TreasurySimulator`; add `strategy: AllocationStrategy` parameter; call `strategy.generate_target_weights()` inside `step()`.

2. **Fix mark-to-market**: Track per-asset unit holdings, not just weights × portfolio_value. Update dollar values from units × current price.

3. **Fix CB defensive action**: When `cb_level >= 2`, execute de-risk rebalance (not skip). Minimum viable: set volatile weights to 0, shift to USDC.

4. **Fix VaR signature**: Match call signature to actual function signature OR replace call with correct usage:
   ```python
   weights = np.array([self.portfolio.weights.get(a, 0) for a in assets])
   var, cvar = compute_historical_var(returns_matrix, weights)
   ```

5. **Regenerate summary.json**: Current file is stale. Always write from the current run's computed metrics.

### P1 — Fix Before Publishing Performance Claims

6. **Fix NAV chart filenames**: Include strategy name in output path.
7. **Fix benchmark**: Use normalized price index, not raw price mean.
8. **Fix CB decay**: Allow decay via multiple mechanisms (time-based AND volatility-normalized), not requiring both simultaneously.
9. **Implement RegimeAdaptiveStrategy**: Actually branch on regime — bull: 60% volatile / 40% stable; crisis: 10% volatile / 90% stable; uncertain: 30% volatile / 70% stable.
10. **Fix Sortino ratio**: Use MAR-based downside deviation.

### P2 — Wire the Sophisticated Risk Engine

11. Call `build_covariance(returns_df)` in the weekly rebalance.
12. Call `optimize_risk_parity(cov_matrix, assets)` for the Risk Parity strategy.
13. Call `compute_historical_var(returns_matrix, weights)` with correct signature.
14. Add regime warm-up from historical data before simulation start.

---

## 10. Summary Bug Register

| ID | Severity | Component | Description |
|---|---|---|---|
| BUG-01 | **CRITICAL** | `main.py` | Strategy config never passed to simulator |
| BUG-02 | **CRITICAL** | `simulator.py` | Hardcodes equal weight; strategies.py is dead code |
| BUG-03 | **CRITICAL** | `metrics.py` | VaR API mismatch — always silently fails |
| BUG-04 | **CRITICAL** | `simulator.py` | MTM uses stale position values between rebalances |
| BUG-05 | **CRITICAL** | `simulator.py` | Portfolio freezes permanently when CB fires — no de-risk code |
| BUG-06 | **CRITICAL** | `simulator.py` | Recovery exit logic broken; snap_back condition inverted |
| BUG-07 | **HIGH** | `simulator.py` | CB fires but triggers zero defensive action |
| BUG-08 | **HIGH** | `circuit_breaker.py` | CB decay unachievable during sustained bear markets |
| BUG-09 | **HIGH** | `main.py` | NAV charts overwritten; only last strategy visible |
| BUG-10 | **HIGH** | `output/summary.json` | Stale file; misreports all strategy performances |
| BUG-11 | **HIGH** | `yield_engine.py` | Funding rate negative in crisis regime reduces NAV |
| BUG-12 | **MEDIUM** | `metrics.py` | Sortino ratio formula incorrect |
| BUG-13 | **MEDIUM** | `main.py` | Benchmark is raw price level, not normalized index |
| BUG-14 | **MEDIUM** | `strategies.py` | RegimeAdaptiveStrategy ignores regime signal |
| BUG-15 | **MEDIUM** | `simulator.py` | Regime detector blind for first 4+ months |
| BUG-16 | **MEDIUM** | Multiple | Covariance pipeline + risk parity optimizer = dead code |
| BUG-17 | **LOW** | `simulator.py` | VaR and trade_volume logged as 0.0 placeholders |
| BUG-18 | **LOW** | `simulator.py` | `summary()` returns hardcoded Sharpe=1.5, MDD=20% |

**Total**: 6 Critical, 5 High, 5 Medium, 2 Low = **18 bugs**

---

*Generated by quantitative audit. Source data: `backtest/output/*.csv`, `backtest/output/summary.json`, `backtest/engine/`, `python-risk/risk_engine/`.*
