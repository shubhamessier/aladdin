# Real Data Audit — Aladdin Backtest Engine

**Date**: 2026-05-26  
**Method**: Direct file reads of working tree. Every claim has a file:line citation.  
**Question**: Is the backtest running on real Hyperliquid data or fabricated inputs?

---

## Verdict

Price data is real (HL API). Everything else — funding yield, lending yield, book depth — is either hardcoded or fabricated. The system fetches real HL funding history but immediately discards it and uses a hardcoded lookup table instead. Lending rates are always synthetically generated; DeFi Llama is commented out. Strategy performance numbers are therefore not trustworthy.

---

## What Is Actually Real

| Component | File | Evidence |
|---|---|---|
| Price OHLCV | `data/fetcher.py:100-148` | Calls `https://api.hyperliquid.xyz/info` with `candleSnapshot`. Config: `data_source: hyperliquid, interval: 1h`. Working. |
| Fee structure | `engine/cost_model.py:20-22` | `maker_fee_bps=0.2`, `taker_fee_bps=3.5`. Direction-aware blend. Correct HL fees. |
| Circuit breaker | `engine/circuit_breaker.py` | HWM/vol computed from real price series. No hardcoded thresholds. |
| Covariance | `risk_engine/covariance.py` | EWMA+LW+RMT on actual returns. Real. |

---

## What Is Fake — Exact Lines

### 1. Funding rates fetched from HL but never used

**`data/funding.py:13-38`** — correctly calls HL `fundingHistory` API and parses real 8h rates. This code works.

**`engine/yield_engine.py:14-18`** — what actually gets used instead:
```python
FUNDING_RATES = {
    "bull": 0.0003,      # 0.03%/8h — hardcoded guess
    "uncertain": 0.0001,
    "crisis": 0.0004,
}
```

**`engine/yield_engine.py:43`** — used at call time:
```python
funding_rate_8h = FUNDING_RATES.get(regime, FUNDING_RATES["uncertain"])
```

**`main.py`** — `fetch_funding_rates` is never called anywhere in the simulation path. `YieldEngine()` at `simulator.py:53` takes no arguments.

**Impact**: HL BTC funding peaked at 0.375%/8h (= 4.1% annualized cost) during 2024 bull run, went negative during FTX cascade. Hardcoded dict uses 0.03%/8h flat for bull — misses actual funding dynamics entirely.

---

### 2. Lending rates always synthetic — DeFi Llama commented out

**`data/lending.py:13-20`**:
```python
def fetch_lending_rates(asset, start_time, end_time):
    try:
        # Placeholder for real API call
        # url = f"https://yields.llama.fi/chart/{pool_id}"   ← COMMENTED OUT
        return _generate_realistic_lending_rates(...)        ← ALWAYS RUNS
    except Exception as e:
        return _generate_realistic_lending_rates(...)        ← SAME THING
```

Both branches generate synthetic data. The try block never attempts a real request.

**`engine/yield_engine.py:4-11`** — what simulator actually uses:
```python
LENDING_RATE_SCHEDULE = {
    (2022, 2): 0.015, (2022, 3): 0.02, (2022, 4): 0.025,
    (2023, 1): 0.035, (2023, 2): 0.04, ...
    (2024, 1): 0.08,  (2024, 2): 0.10, ...
}
```

`main.py` never calls `fetch_lending_rates`. The hardcoded schedule is what drives every yield calculation.

**Impact**: USDC AAVE V3 supply rate ranged 0.01%–12%+ over 2022–2026. The hardcoded schedule approximates this with no intraweek variation and no market-driven dynamics.

---

### 3. Lending yield uses wrong allocation proxy

**`simulator.py:168-175`**:
```python
cash_pct = self.portfolio.cash / self.portfolio.portfolio_value
daily_yield = self.yield_engine.calculate_yield(
    self.portfolio.portfolio_value,
    cash_pct,       # ← WRONG: residual cash, not stablecoin weight
    ...
)
```

**`yield_engine.py:37`**:
```python
daily_lending = (portfolio_value * cash_pct * lending_fraction) * (lending_rate / 365)
```

When portfolio is 60% BTC / 30% ETH / 10% USDC, `cash_pct ≈ 0.0` (all deployed in positions). Lending yield = ~0. But USDC allocation = 10% and should earn lending APY on that 10%. The simulator misses this yield entirely when all capital is invested.

---

### 4. Funding yield always zero

**`yield_engine.py:44-49`** — iterates `derivative_positions` for funding:
```python
for pos in derivative_positions:
    direction = 1.0 if pos.direction == "long" else -1.0
    payment = pos.notional_usd * (funding_rate_8h * 3) * (-direction)
    daily_funding += payment
```

**`engine/portfolio.py`** — `derivative_positions` initialized to `[]` and never populated. Hedger is dead code (`simulator.py:198-200`: `for action in hedge_actions: pass`).

Result: `daily_funding = 0.0` every single step. Funding yield — the core carry component of any HL treasury strategy — contributes exactly zero P&L to every strategy across the entire backtest.

---

### 5. Book depth hardcoded in every rebalance

**`simulator.py:281`**:
```python
cost = self.cost_model.estimate_cost(
    trade_size, asset, direction,
    5e6,   # ← book_depth_usd hardcoded
    1e7,   # ← daily_volume_usd hardcoded
    is_emergency=is_emergency
)
```

`fetcher.py:193` has `fetch_l2_depth_snapshot` that calls HL `l2Book` endpoint. Never called during simulation.

**Impact**: During FTX cascade (Nov 2022), HL ETH depth at 10bps was ~$500k–$2M. Hardcoded `$5M` overstates liquidity by 2.5–10x. Slippage on emergency de-risk is proportionally understated.

---

### 6. BlackLitterman strategy is mean-variance in disguise

**`strategies.py:237-242`**:
```python
from risk_engine.portfolio_optimizer import optimize_mean_variance
mu = np.array([expected_returns.get(name, 0.0) for name in asset_names])
bounds = [(0.0, 1.0) for _ in range(N)]
res = optimize_mean_variance(mu, covariance_matrix, bounds)
```

`optimize_black_litterman` exists at `portfolio_optimizer.py:145` with full BL posterior implementation (market caps, views, `[(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹ [(τΣ)⁻¹π + PᵀΩ⁻¹Q]`). Never called.

---

### 7. Market cap weights for equilibrium returns hardcoded

**`simulator.py:245`**:
```python
mkt_weights = np.array([0.65 if a == "BTC" else 0.25 if a == "ETH" else 0.1 for a in self.assets])
```

All non-BTC/ETH assets (SOL, USDC, USDT) get identical weight 0.1 regardless of relative market cap. Never updates across the 4-year simulation window.

---

### 8. `timedelta` import scoping bug

**`main.py:4`**: `from datetime import datetime` — `timedelta` not imported.  
**`main.py:80`**: uses `timedelta(days=90)` in `run_simulation`.  
**`main.py:242`**: `from datetime import timedelta` inside `if __name__ == '__main__':`.

Works when run as `python -m backtest.main` (the `__main__` block executes before `main()` is called, adding `timedelta` to module globals). Fails with `NameError` when `run_simulation` is called from any other module (tests, notebooks, external scripts).

---

## Complete Real vs Fake Matrix

| Input | Claimed | Reality | Impact |
|---|---|---|---|
| Price OHLCV | HL real 1h | ✓ Real | — |
| Fee model | HL maker/taker | ✓ Real | — |
| Funding rates | "real HL data" | ✗ Fetched but discarded. Hardcoded regime dict. | Carry P&L wrong direction and magnitude |
| Lending rates | "realistic schedule" | ✗ Fully synthetic. DeFi Llama commented out. | Carry P&L based on invented numbers |
| Lending allocation | Per stable weight | ✗ Uses `cash_pct`. Zero yield when fully invested. | Underestimates stable yield by 100% when deployed |
| Funding yield | Conditional on positions | ✗ Always 0. No positions ever opened. | Entire funding component missing |
| Book depth | HL L2 | ✗ Hardcoded `$5M` / `$10M` | Crisis slippage understated 2–10x |
| BL posterior | BL formula | ✗ `optimize_mean_variance` with equilibrium mu | BL strategy produces MV-tangency portfolio |
| Market cap weights | Dynamic | ✗ Hardcoded 65/25/10 for BTC/ETH/rest | Equilibrium returns wrong for non-BTC/ETH assets |

---

## Minimum Fixes to Get Real Data

Three changes unblock the yield pipeline. Everything else is accuracy improvement.

### Fix 1 — Wire YieldEngine to real series (15 min)

`yield_engine.py`: add `__init__(self, funding_series=None, lending_series=None)`. Replace hardcoded dict lookups with `series.asof(date)`.

`simulator.py:53`: change `YieldEngine()` to `YieldEngine(funding_series=..., lending_series=...)`.

`simulator.py:168`: change `cash_pct` arg to pass `weights=self.portfolio.weights`.

### Fix 2 — Implement DeFi Llama in lending.py (30 min)

```python
def fetch_lending_rates(asset, start_time, end_time):
    # 1. GET https://yields.llama.fi/pools — find highest-TVL AAVE V3 pool for asset
    # 2. GET https://yields.llama.fi/chart/{pool_id} — daily APY history
    # 3. Return DataFrame. If fetch fails, return empty (caller raises, not synthetic)
```

### Fix 3 — Call fetch_funding_rates in main.py (20 min)

```python
# After price data fetch:
volatile_assets = [a for a in assets if a not in ("USDC", "USDT", "DAI")]
funding_series = {}
for asset in volatile_assets:
    df = fetch_funding_rates(asset, int(warmup_start.timestamp()), int(end_date.timestamp()))
    if df.empty:
        raise RuntimeError(f"No HL funding data for {asset}. Cannot run on fabricated inputs.")
    funding_series[asset] = df["funding_rate"].resample("1D").sum()

stable_assets = [a for a in assets if a in ("USDC", "USDT", "DAI")]
lending_series = {}
for asset in stable_assets:
    df = fetch_lending_rates(asset, int(warmup_start.timestamp()), int(end_date.timestamp()))
    if df.empty:
        raise RuntimeError(f"No lending data for {asset}.")
    lending_series[asset] = df["lending_rate"]

yield_engine = YieldEngine(funding_series=funding_series, lending_series=lending_series)
```

### Fix 4 — Fetch L2 depth per asset (10 min)

```python
depth_by_asset = {}
for asset in volatile_assets:
    try:
        book = fetcher.fetch_l2_depth_snapshot(asset)
        depth_by_asset[asset] = book["depth_25bps_usd"]
    except Exception:
        depth_by_asset[asset] = 1_000_000  # conservative default, not 5M

# In _execute_rebalance, replace hardcoded 5e6:
book_depth = depth_by_asset.get(asset, 1_000_000)
vol_haircut = max(0.1, 1.0 - 5.0 * rolling_vol)  # crisis = thinner book
```

### Fix 5 — BL strategy (5 min)

Change `strategies.py:BlackLittermanStrategy.generate_target_weights` to call `optimize_black_litterman(covariance, market_caps, views=[], ...)` instead of `optimize_mean_variance`.

### Fix 6 — `timedelta` import (1 line)

`main.py:4`: `from datetime import datetime, timedelta` and remove line 242.

---

## Expected Impact on Results After Fixes

These are directional estimates based on known HL historical rates:

| Component | Current | After Fix | Effect on Returns |
|---|---|---|---|
| BTC funding (2024 bull) | +flat 0.03%/8h | +0.05–0.375%/8h real | +1–4% annual carry |
| BTC funding (2022 bear) | +flat 0.03%/8h | −0.075–0.00%/8h real | −0.3–0% (negative funding = receive) |
| USDC lending (2024 H1) | ~10% APY (hardcoded) | 8–12% APY (AAVE real) | ~neutral, ±2% |
| USDC lending (2022 bear) | ~2% APY (hardcoded) | 0.01–1% APY (real AAVE crashed) | −1.5% annual |
| Crisis slippage | 2–5bps modeled | 10–50bps actual | Larger drawdowns on CB events |
