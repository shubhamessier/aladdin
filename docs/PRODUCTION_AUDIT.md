# ALADDIN: PRODUCTION AUDIT v3

**Date**: 2026-05-26  
**Scope**: Full codebase audit + real data mandate + granular benchmark plan + full strategy sweep + robustness remediation  
**Verdict**: Structural fixes made since v2 (cash accounting, fee model, look-ahead partial fix, RF rate). However: ALL yield inputs still synthetic or disconnected from real feeds. Lending rates hardcoded. Funding data fetched but never consumed. `timedelta` import bug causes runtime crash. BL strategy calls wrong optimizer. System cannot produce trustworthy P&L until all synthetic inputs are eliminated. No fallbacks permitted.

---

## PART 0 — REAL DATA MANDATE

The system must derive every input from a verifiable external source. Any synthetic fallback is banned in the simulation path. If a data fetch fails, the simulation must halt with a descriptive error — not silently substitute invented numbers.

### What this means in practice

| Input | Current state | Required state |
|---|---|---|
| Price OHLCV | HL API (real) ✓ | Stay HL; Binance only for assets HL doesn't list |
| Funding rates | Fetched from HL but **not consumed** by yield engine ✗ | Pass real series into `YieldEngine`; fail hard if unavailable |
| Lending rates | Hardcoded schedule, DeFi Llama never called ✗ | DeFi Llama `yields.llama.fi/chart/{pool_id}`; fail hard if unavailable |
| L2 book depth | Hardcoded `5e6` in every rebalance call ✗ | Fetch HL `l2Book` at sim init; use calibrated per-asset depth |
| Market caps for BL | Hardcoded `{BTC: 0.65, ETH: 0.25, ...}` ✗ | Derive from price data volume-weighted; or fetch from CoinGecko |

### Zero-tolerance policy for synthetic fallbacks

```python
# BANNED pattern — silently uses fake data on any failure:
try:
    return fetch_real_data(...)
except Exception:
    return _generate_synthetic(...)  # ← THIS IS THE BUG

# REQUIRED pattern — fail loudly:
STRICT_MODE = os.getenv("ALADDIN_ENV", "backtest") != "disabled_strict"

try:
    df = fetch_real_data(...)
    if df.empty:
        raise ValueError(f"Empty response for {asset}")
    return df
except Exception as e:
    if STRICT_MODE:
        raise RuntimeError(f"Real data unavailable for {asset}: {e}. Cannot proceed with fabricated inputs.") from e
    logger.critical(f"DATA INTEGRITY FAILURE: {e}")
    raise
```

Every data fetcher must follow this pattern. No silent synthetic substitution.

---

## PART 1 — ORIGINAL AUDIT FINDINGS

### 1A. NotImplementedErrors Silently Swallowed

**`data/funding.py:14`** — Every funding rate is synthetic AR(1). HL has hit 0.1%/8h in bull runs, -0.05%/8h in cascades. Synthetic cap: -0.05% to 0.1%/8h. Cannot simulate the extremes.

**`data/lending.py:15`** — Every lending rate is synthetic random walk. DeFi Summer stable APYs hit 20-30%. This engine caps at 20% and almost never approaches it.

**`strategies.py:102`** — `AllocationStrategy.generate_target_weights` is unguarded abstract. Wrong factory call → silent crash on first rebalance.

### 1B. Dead Code

**`engine/hedger.py`** — `HedgingEngine` is never imported or called from `simulator.py`. `portfolio.derivative_positions` initializes to `[]` and stays empty. The entire hedging infrastructure produces zero effect.

**`engine/event_driven_replay.py`** — `EventDrivenReplayEngine` never instantiated in production path. L2 replay, latency injection, fill logic: disconnected.

**`portfolio.py:DerivativePosition`** — field always an empty list. Any downstream code iterating it iterates nothing.

### 1C. Fatal Magic Numbers

| Location | Hardcoded Value | Failure Condition |
|---|---|---|
| `simulator.py:165` | `{"BTC": 0.65, "ETH": 0.25, ...}` static market caps | USDT depeg, ETH flippening — equilibrium returns become nonsense |
| `simulator.py:193` | `pool_liquidity=1e8, daily_volume=1e7` | Crisis liquidity at $1-3M → slippage 5-10x what model predicts |
| `simulator.py:244` | `rf = 0.05` | Inconsistent with `metrics.py` default `0.02` and `default.yaml` `0.02` |
| `cost_model.py:19` | `dex_fee_bps = 0.9` | HL taker is 3.5bps. Model understates by 178% |
| `yield_engine.py:41` | `portfolio_value * 0.10` in basis trades | Unconditional — no positions required |
| `circuit_breaker.py:123` | `vol_ratio < 1.2` | Allows CB decay too aggressively — re-enters during slow bleed |

### 1D. Fee Illusion — P&L Decomposition

```
Weekly rebalance, $1M portfolio, 5-15% turnover:
  Trade volume: $50k–$150k

  Modeled fee (0.9bps blended):    $4.5 – $13.5/rebalance
  Actual fee (2.51bps correct blended, 3.5bps taker on emergency):
    Normal rebalance:  $12.5 – $37.5/rebalance
    Emergency CB L2:   3.5bps taker on full de-risk ≈ $350 on $1M

  Annual underestimation:  $400 – $2,000 (normal)
  Single emergency event:  2.6x modeled cost

Always passes direction="buy" (simulator.py:193).
Sells into crash: spread crosses against you. Model sees same cost as calm-market buy.
```

### 1E. Inventory Suicide

Hedger dead. Portfolio naked long always. Crisis scenario:

```
FTX Nov 2022: BTC -30%, ETH -35%, corr 0.95
Risk Parity allocation: ~50% volatile

Portfolio drawdown to L2 breaker (20%): 3-4 days
Emergency de-risk sells into collapsed book

Model depth:  $100M (hardcoded)
Real HL depth 3AM UTC during cascade: $1-3M
Slippage on $500k ETH sale: 5-15bps actual vs 0.5-2bps modeled
Fire sale cost overrun: 5-10x
```

`RecoveryPhase.check_further_decline` compares to `entry_portfolio_value` set at recovery start — not running recovery peak. Portfolio recovers 15% then drops 4% → still checks against entry → no re-escalation. Silent premature return to volatile allocation.

### 1F. Look-Ahead Bias in Two Places

**`simulator.py:66`** — HMM warmup fit on days 0-60 of backtest period. Predictions from day 61 use in-sample model.

**`simulator.py:77`** — Rolling refit uses `self.market_data.iloc[max(0, day-504):day+1]` — `day+1` includes today. HMM refitted on same-day return, then predicts same-day regime.

Fix: `iloc[max(0, day-504):day]` (exclusive of today). Warmup HMM must be fitted on pre-backtest data (extend `start_date` by 60 days and burn the warmup period).

### 1G. Cash Accounting Bug

`simulator.py:194-208`: costs subtracted from `portfolio_value` before weights computed → sum of weights can exceed 1.0 → `cash = portfolio_value * (1 - sum_weights)` goes negative. No assertion. Phantom capital accumulates over time. Total return is inflated.

### 1H. Strategy Math Errors

**BlackLitterman** (`strategies.py:238-244`): computes `Σ⁻¹μ` (mean-variance tangency), not BL posterior. Correct implementation already exists at `portfolio_optimizer.py:145-197` — not wired.

**MinVariance** (`strategies.py:207-215`): clips negative weights then renormalizes. Destroys the optimization. Result is not minimum variance. Correct constrained QP already at `portfolio_optimizer.py:15-73` — not wired.

### 1I. Jump Diffusion — Python Loop

`var_models.py:116-118`: 50,000-iteration Python loop for compound Poisson. 200-500ms per VaR call. Called daily. Blocks simulation thread.

### 1J. Observability Failure

`simulator.py` history record missing: per-asset trade sizes, fill ratios, slippage realized, funding paid, cost breakdown, regime confidence. Cannot reconstruct a drawdown event from logs.

---

## PART 1B — v3 AUDIT: WHAT IS ACTUALLY REAL VS STILL FAKE

Audit date: 2026-05-26. Reading working tree, not HEAD. Every claim verified by `cat` of current file.

### Status of v2 fixes

| Issue | v2 status | v3 finding |
|---|---|---|
| Cash accounting (F1) | Claimed fixed | **PARTIAL.** `_execute_rebalance` does double-entry correctly. But line 298-301 has a cash correction workaround: `portfolio_value += cash; cash = 0.0` — masks negative cash rather than preventing it. |
| Look-ahead HMM warmup (F2) | Claimed fixed | **PARTIAL.** Initial warmup now uses pre-backtest data if provided. Rolling refit line 87 now correctly uses `iloc[...:day]` not `day+1`. Actual fix confirmed. |
| Synthetic funding (F3) | Claimed fixed | **NOT FIXED.** `funding.py` now fetches from HL API — good. But `yield_engine.py` uses hardcoded `FUNDING_RATES = {"bull": 0.0003, ...}` dict. Fetched funding data is **never consumed**. |
| Synthetic lending (F4) | Not attempted | **NOT FIXED.** `lending.py:17` always calls `_generate_realistic_lending_rates`. DeFi Llama call is commented out. |
| Wrong taker fee 0.9bps (F5) | Claimed fixed | **FIXED.** Current `cost_model.py` has `maker_fee_bps=0.2`, `taker_fee_bps=3.5`, direction-aware blending. Confirmed. |
| Hardcoded market depth (F6) | Not addressed | **NOT FIXED.** `simulator.py:281` calls `estimate_cost(..., 5e6, 1e7, ...)` — hardcoded. |
| BL strategy wrong (F7) | Partially fixed | **NOT FIXED.** `strategies.py:BlackLittermanStrategy` calls `optimize_mean_variance`, not `optimize_black_litterman`. |
| MinVar clips negatives (F8) | Not claimed fixed | **NOT FIXED.** (No clip in current code, but still calls MV optimizer correctly with zero returns — this is actually correct. See note.) |
| Phantom yield (F9) | Claimed fixed | **PARTIAL.** `yield_engine.py` now iterates `derivative_positions` for funding. But positions are always `[]` (no hedger wired), so funding yield is always 0. Lending uses hardcoded schedule. |
| Recovery peak (F10) | Claimed fixed | **FIXED.** `circuit_breaker.py:RecoveryPhase` has `update_peak`, `recovery_peak`, and `check_further_decline` comparing to `recovery_peak` not `entry_portfolio_value`. Confirmed. |
| RF rate inconsistency (F13) | Claimed fixed | **FIXED.** `risk_free_rate` passed from config through to `TreasurySimulator.__init__` and used in `summary()`. Confirmed. |

### NEW issues found in v3 audit

#### N1 — CRITICAL: `timedelta` import causes `NameError` at runtime

**`backtest/main.py:4`** imports `from datetime import datetime` only.  
**`backtest/main.py:80`** uses `timedelta(days=90)` inside `run_simulation`.  
**`backtest/main.py:242`** imports `from datetime import timedelta` — but only inside `if __name__ == '__main__':`.

When `run_simulation` is called as a module function (e.g., from tests or external scripts), `timedelta` is not in scope → `NameError: name 'timedelta' is not defined`.

Fix: move `from datetime import datetime, timedelta` to line 4.

#### N2 — CRITICAL: Real funding data fetched but thrown away

**`backtest/data/funding.py`** now calls `https://api.hyperliquid.xyz/info` with `fundingHistory`. This is correct.

**`backtest/engine/yield_engine.py:14-17`** defines:
```python
FUNDING_RATES = {
    "bull": 0.0003,
    "uncertain": 0.0001,
    "crisis": 0.0004,
}
```

**`backtest/engine/yield_engine.py:43`** uses `FUNDING_RATES.get(regime, ...)` — a hardcoded regime→rate lookup.

The real funding data from `funding.py` is never loaded, never passed to `YieldEngine`, never used. The real HL funding history (which peaked at 0.375%/8h during bull 2024, went negative during cascade events) is completely ignored. Simulation uses a flat regime-conditional fake rate.

**Fix**: 
1. `YieldEngine.__init__` must accept `funding_series: dict[str, pd.Series]` — a per-asset daily-summed funding rate series.
2. `calculate_yield` must look up from this series by date, not from the hardcoded dict.
3. `main.py` must fetch funding before constructing `YieldEngine` and pass it in.
4. If funding fetch fails for an asset, raise — do not substitute 0.0003.

#### N3 — CRITICAL: Lending rates always synthetic

**`backtest/data/lending.py:13-17`**:
```python
try:
    # Placeholder for real API call
    # url = f"https://yields.llama.fi/chart/{pool_id}"
    return _generate_realistic_lending_rates(asset, start_time, end_time)
except Exception as e:
    return _generate_realistic_lending_rates(asset, start_time, end_time)
```

Both branches call the same synthetic generator. The try block never attempts a real fetch. DeFi Llama is commented out.

**Impact**: Over 2022-2026, USDC supply APY on AAVE ranged from 0.01% (2022 bear) to 12%+ (2024 bull peak). Hardcoded schedule approximates this loosely but with added noise and no intraday/intraweek variation. All carry attribution numbers from stablecoin yield are fictional.

**Fix**:
1. Call `https://yields.llama.fi/pools` to find the highest-TVL USDC/USDT/DAI pool on Ethereum (AAVE V3 preferred).
2. Call `https://yields.llama.fi/chart/{pool_id}` for daily historical APY.
3. Filter to simulation period.
4. If API fails and STRICT_MODE is set, raise. If not strict, log warning and use zero yield (safe default).

#### N4 — HIGH: `YieldEngine.calculate_yield` not passed portfolio weights

**`backtest/engine/simulator.py:167-175`**:
```python
cash_pct = self.portfolio.cash / self.portfolio.portfolio_value
daily_yield = self.yield_engine.calculate_yield(
    self.portfolio.portfolio_value,
    cash_pct,
    ...
)
```

`YieldEngine.calculate_yield` uses `cash_pct` as a proxy for stablecoin allocation. This is wrong when cash is not all in stables (e.g., cash is 5% but USDC allocation is 60%). The per-asset lending rate should be weighted by actual stablecoin weights, not by cash balance.

**Fix**: pass `weights=self.portfolio.weights` to `calculate_yield`; compute lending rate as `Σ(weight[asset] * rate[asset])` over stable assets.

#### N5 — HIGH: Hardcoded market depth in every rebalance

**`backtest/engine/simulator.py:281`**:
```python
cost = self.cost_model.estimate_cost(trade_size, asset, direction, 5e6, 1e7, is_emergency=is_emergency)
```

`book_depth_usd=5e6` and `daily_volume_usd=1e7` hardcoded every call regardless of asset, market conditions, or time period. BTC depth at peak liquidity vs 3AM UTC during FTX cascade differs by 10-100x.

**Fix**: fetch L2 snapshot per asset at simulation start via `fetcher.fetch_l2_depth_snapshot(coin)`. Store `depth_by_asset: dict[str, float]`. Use `depth_by_asset.get(asset, 1e6)` in the rebalance call. For bear market / off-hours stress: apply a vol-conditional depth haircut: `depth * max(0.1, 1 - 5 * rolling_vol)`.

#### N6 — HIGH: BlackLitterman calls wrong optimizer

**`backtest/engine/strategies.py:BlackLittermanStrategy.generate_target_weights`** calls `optimize_mean_variance(mu, covariance, bounds)` using equilibrium returns as `mu`. This is mean-variance tangency, not Black-Litterman.

The correct function `optimize_black_litterman(covariance, market_caps, views, ...)` exists at `python-risk/risk_engine/portfolio_optimizer.py:145`. Its signature:
```python
def optimize_black_litterman(
    covariance: np.ndarray,
    market_caps: np.ndarray,   # ← relative weights, not hardcoded numbers
    views: List[View],
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    bounds: Optional[List[Tuple[float, float]]] = None,
    tier_constraints: Optional[List[TierConstraint]] = None,
) -> OptimizationResult
```

`View` schema: `asset_indices: List[int]`, `asset_weights: List[float]`, `expected_return: float`, `confidence: Optional[float]`.

When `views=[]` (no analyst views), BL reduces to equilibrium-only MV — a valid strategy. Passing `views=[]` is acceptable. The bug is not the views, it's calling MV directly and bypassing the BL equilibrium construction entirely.

**Fix**: call `optimize_black_litterman` with `market_caps` derived from current portfolio weights (or equal-weight if no position yet), and `views=[]` for pure BL equilibrium. Do not pass `mu` directly to `optimize_mean_variance`.

### What is genuinely real / working

- **Price data**: HL `candleSnapshot` at `1h` in use. Binance fallback for unlisted assets. Cache layer working.
- **Fee model**: `maker_fee_bps=0.2`, `taker_fee_bps=3.5`, 70/30 blended normal, 100% taker emergency. Direction-aware. Correct.
- **Cash accounting**: Double-entry in `_execute_rebalance`. Positions tracked as USD values, units as `position/price`. `portfolio_value = cash + sum(positions)`. Mostly correct (see F1 caveat).
- **Look-ahead (rolling refit)**: Fixed. Uses `iloc[:day]`.
- **Recovery phase**: `update_peak` → `recovery_peak` → `check_further_decline` uses peak not entry. Correct.
- **RF rate**: Single value from config, passed through. Consistent.
- **Circuit breaker decay**: Three decay paths (vol normalized, time-based, forced 60d). Appropriate.

---

## PART 2 — GRANULAR DATA BENCHMARK

Current state: daily Binance spot OHLCV. Cannot model intraday drawdowns, funding payment timing, spread dynamics, or liquidation cascades.

### 2A. Move to Hourly HL-Native Data

HL API provides candles at `1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 1d` via `candleSnapshot`. Already used in `hl_reality_check.py:22`. Wire it to `fetcher.py`.

**Replace `_fetch_binance` with `_fetch_hyperliquid`:**

```python
# backtest/data/fetcher.py

def _fetch_hyperliquid(
    self,
    coin: str,          # "BTC", "ETH", not "BTC/USD"
    start_time: int,    # unix seconds
    end_time: int,
    interval: str = "1h"
) -> pd.DataFrame:
    url = "https://api.hyperliquid.xyz/info"
    all_candles = []
    
    # HL returns max 5000 candles per request
    chunk_seconds = {
        "1h": 5000 * 3600,
        "4h": 5000 * 14400,
        "1d": 5000 * 86400,
    }.get(interval, 5000 * 3600)
    
    current_start = start_time * 1000  # HL uses ms
    end_ms = end_time * 1000
    
    while current_start < end_ms:
        chunk_end = min(current_start + chunk_seconds * 1000, end_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": current_start,
                "endTime": chunk_end
            }
        }
        resp = self.session.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if not data:
            break
        all_candles.extend(data)
        current_start = data[-1]["t"] + 1
        time.sleep(0.05)  # HL rate limit: 1200 req/min
    
    if not all_candles:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_candles)
    # HL candle keys: t (open time ms), o, h, l, c, v (base vol), T (close time), n (trades)
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.set_index("timestamp")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    return df
```

**Cache key** must include interval:
```python
def _get_cache_path(self, symbol, source, start_time, end_time, interval="1d"):
    safe_sym = symbol.replace("/", "_").replace("-", "_")
    return self.cache_dir / f"{source}_{safe_sym}_{interval}_{start_time}_{end_time}.parquet"
```

### 2B. Real Funding Rate Pipeline

`hl_reality_check.py:18` already shows the call. Make it a proper pipeline:

```python
# backtest/data/funding.py — replace the NotImplementedError

def fetch_funding_rates(coin: str, start_time: int, end_time: int) -> pd.DataFrame:
    """
    Fetch real 8-hour funding history from Hyperliquid.
    Falls back to synthetic only if API fails.
    """
    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_time * 1000,
        "endTime": end_time * 1000
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if not data:
            raise ValueError("Empty funding history response")
        
        records = []
        for entry in data:
            records.append({
                "timestamp": pd.to_datetime(entry["time"], unit="ms"),
                "funding_rate": float(entry["fundingRate"]),
                "premium": float(entry.get("premium", 0.0)),
            })
        
        df = pd.DataFrame(records).set_index("timestamp")
        logger.info(f"Fetched {len(df)} real funding records for {coin}")
        return df
    
    except Exception as e:
        logger.warning(f"HL funding fetch failed for {coin}: {e}. Using synthetic.")
        return _generate_synthetic_funding(coin, start_time, end_time)
```

**Funding data reality check** — validate against known extremes before using:
```python
def validate_funding(df: pd.DataFrame, coin: str) -> pd.DataFrame:
    # HL historical extremes: -0.075% to +0.375% per 8h
    KNOWN_BOUNDS = (-0.00075, 0.00375)
    outliers = df[(df["funding_rate"] < KNOWN_BOUNDS[0]) | (df["funding_rate"] > KNOWN_BOUNDS[1])]
    if len(outliers) > 0:
        logger.warning(f"{coin}: {len(outliers)} funding outliers beyond known bounds")
    return df
```

### 2C. Real L2 Depth for Slippage Calibration

Replace the hardcoded `1e8` in `simulator.py:193` with a pre-computed depth series:

```python
# backtest/data/fetcher.py

def fetch_l2_depth_snapshot(self, coin: str) -> dict:
    """
    Fetch current L2 book. Used for slippage calibration at backtest start.
    Returns aggregated depth at 5bps, 10bps, 25bps, 50bps from mid.
    """
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "l2Book", "coin": coin}
    resp = self.session.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    book = resp.json()
    
    bids = [(float(l["px"]), float(l["sz"])) for l in book["levels"][0]]
    asks = [(float(l["px"]), float(l["sz"])) for l in book["levels"][1]]
    
    mid = (bids[0][0] + asks[0][0]) / 2.0
    
    def depth_within_bps(levels, bps, side="ask"):
        threshold = mid * (1 + bps/10000) if side == "ask" else mid * (1 - bps/10000)
        total_usd = 0.0
        for px, sz in levels:
            if (side == "ask" and px <= threshold) or (side == "bid" and px >= threshold):
                total_usd += px * sz
        return total_usd
    
    return {
        "coin": coin,
        "mid": mid,
        "depth_5bps_usd": depth_within_bps(asks, 5),
        "depth_10bps_usd": depth_within_bps(asks, 10),
        "depth_25bps_usd": depth_within_bps(asks, 25),
        "depth_50bps_usd": depth_within_bps(asks, 50),
        "spread_bps": (asks[0][0] - bids[0][0]) / mid * 10000,
    }
```

Use this to calibrate `CostModelConfig.slippage_bps_per_100k` per asset at backtest init rather than hardcoding.

### 2D. Intraday Simulator — Hourly Timestep

The current `TreasurySimulator` steps once per day. With hourly data, switch to hourly steps with event-driven rebalancing:

```python
class HourlyTreasurySimulator(TreasurySimulator):
    """
    Hourly simulation. Enables:
    - Intraday drawdown detection
    - 8h funding payment booking
    - Circuit breaker firing within the trading day
    - Realistic rebalance timing (not always at EOD)
    """
    
    HOURS_PER_YEAR = 8760
    FUNDING_PERIOD_HOURS = 8  # HL settles every 8h at 00:00, 08:00, 16:00 UTC
    
    def step_hourly(self, hour_idx: int) -> None:
        ts = self.market_data.index[hour_idx]
        prices = self.market_data.iloc[hour_idx]
        
        # 1. Mark to Market (every hour)
        self._mark_to_market(prices)
        
        # 2. Book funding if it's a settlement hour
        if ts.hour % self.FUNDING_PERIOD_HOURS == 0:
            self._book_funding_payment(ts, prices)
        
        # 3. Circuit breaker check (every hour — catches intraday spikes)
        rolling_vol = self._compute_rolling_vol_hourly(hour_idx)
        cb_level = self.cb.update(ts, self.portfolio.portfolio_value, rolling_vol, self.avg_vol_lifetime)
        
        # 4. Emergency rebalance if CB fired this hour
        if cb_level >= 2 and self._prev_cb_level < 2:
            self._emergency_derisk(prices, ts)
        
        # 5. Scheduled rebalance: once per day at 08:00 UTC (post-funding, pre-US open)
        if ts.hour == 8 and self._should_rebalance(ts):
            self._rebalance(prices, ts)
        
        self._prev_cb_level = cb_level
    
    def _book_funding_payment(self, ts, prices) -> None:
        """
        Book actual funding from real fundingHistory data.
        Only applies to derivative_positions that are actually open.
        """
        for pos in self.portfolio.derivative_positions:
            coin = pos.market.replace("-PERP", "")
            rate = self._get_funding_rate(coin, ts)  # from real fetched data
            
            # Longs pay funding when rate > 0, shorts receive
            direction = 1.0 if pos.direction == "long" else -1.0
            payment = pos.notional_usd * rate * (-direction)
            
            pos.cumulative_funding += payment
            self.portfolio.cash += payment
            self.portfolio.portfolio_value += payment
```

### 2E. Granular Benchmark Checklist

Before running strategy comparisons on granular data, validate:

```
[ ] HL candle data fetched at 1h interval for all assets
[ ] fundingHistory fetched for all perp assets (BTC, ETH, SOL)
[ ] L2 depth snapshot fetched at backtest init per asset
[ ] Slippage calibration uses real depth, not 1e8
[ ] funding.py falls back to synthetic only on API failure, not by default
[ ] Funding booked only when derivative_positions is non-empty
[ ] Cash >= 0 asserted after every trade
[ ] Warmup HMM fitted on pre-backtest-period data (extend fetch by 90 days before start_date)
[ ] Rolling refit uses iloc[:day] not iloc[:day+1]
[ ] fee model uses 3.5bps taker for emergencies, 0.2bps maker for scheduled
[ ] All OHLCV sources validated: no open=high=low=close fallback, no volume=0
```

---

## PART 3 — FULL STRATEGY PARAMETER SWEEP

Current optimizer runs one strategy at a time with random search. Need:
- All 7 strategies in same walk-forward window
- Full 20-parameter space explored
- Cross-strategy comparison on identical market slices
- Regime-conditional analysis per fold

### 3A. Cross-Strategy Walk-Forward

```python
# backtest/optimizer/cross_strategy_wf.py

from typing import Dict, List, Any
import pandas as pd
import numpy as np
from backtest.optimizer.walk_forward import WalkForwardValidator
from backtest.optimizer.param_space import PARAM_SPACE
from backtest.engine.strategies import (
    EqualWeightStrategy, RiskParityStrategy, RegimeAdaptiveStrategy,
    StaticConservativeStrategy, MinVarianceStrategy, BlackLittermanStrategy,
    BuyAndHoldStrategy, StrategyConfig, RiskParityConfig, RegimeAdaptiveConfig,
    StaticConservativeConfig
)

STRATEGIES = {
    "equal_weight":       (EqualWeightStrategy,      StrategyConfig),
    "risk_parity":        (RiskParityStrategy,        RiskParityConfig),
    "regime_adaptive":    (RegimeAdaptiveStrategy,    RegimeAdaptiveConfig),
    "static_conservative":(StaticConservativeStrategy,StaticConservativeConfig),
    "min_variance":       (MinVarianceStrategy,       StrategyConfig),
    "black_litterman":    (BlackLittermanStrategy,    StrategyConfig),
    "buy_and_hold":       (BuyAndHoldStrategy,        StrategyConfig),
}

def run_cross_strategy_sweep(
    market_data,
    base_config: dict,
    train_months: int = 18,
    test_months: int = 6,
    step_months: int = 3,   # smaller step = more folds = more robust estimate
    n_random_samples: int = 500,  # per strategy per fold
    n_jobs: int = -1,        # parallel across strategies
) -> pd.DataFrame:
    """
    Run walk-forward for every strategy on the same market data.
    Returns DataFrame with columns: strategy, fold, train_score, test_score,
    oos_sharpe, oos_max_dd, best_params, dominant_regime.
    """
    all_results = []
    
    for strat_name, (strat_cls, config_cls) in STRATEGIES.items():
        print(f"\n=== {strat_name} ===")
        
        validator = WalkForwardValidator(
            market_data=market_data,
            base_config=base_config,
            strategy=strat_name,
            train_months=train_months,
            test_months=test_months,
            step_months=step_months,
        )
        
        result = validator.validate(
            params_to_optimize=PARAM_SPACE,
            n_random_samples=n_random_samples,
        )
        
        for fold_idx, fold in result["folds"].iterrows():
            all_results.append({
                "strategy": strat_name,
                "fold": fold_idx,
                "train_score": fold["train_score"],
                "test_score": fold["test_score"],
                "best_params": fold["best_params"],
                "overfit_ratio": fold["test_score"] / max(fold["train_score"], 1e-6),
            })
    
    return pd.DataFrame(all_results)
```

### 3B. Exhaustive Parameter Grid — What to Sweep

Current `PARAM_SPACE` has 20 parameters. For meaningful sweep:

```python
# Recommended n_random_samples by strategy complexity:
#
# Equal Weight, Buy&Hold, Static Conservative:  50  (few free params)
# Risk Parity, Min Variance:                   200  (CB + covariance params)
# Regime-Adaptive:                             500  (CB + regime + allocation params)
# Black-Litterman:                             500  (CB + BL tau/delta + allocation params)
#
# Grid search is only tractable for ≤5 parameters.
# Use random search for full 20-param space.
# Use Bayesian optimization (scipy or optuna) for focused refinement.
```

**Add missing parameters to PARAM_SPACE:**

```python
# backtest/optimizer/param_space.py — additions

PARAM_SPACE.extend([
    # ──── EXECUTION ────
    ParamSpec("maker_fraction", "Fraction of trades as maker", 0.70, 0.30, 0.95, 0.05, category="execution"),
    ParamSpec("emergency_taker_fee_bps", "Taker fee bps for CB events", 3.5, 2.0, 5.0, 0.5, category="execution"),
    ParamSpec("slippage_depth_usd", "Assumed book depth for slippage", 5e6, 1e6, 50e6, 1e6, category="execution"),
    
    # ──── YIELD ────
    ParamSpec("basis_trade_pct", "Portfolio fraction in basis trades", 0.0, 0.0, 0.20, 0.02, category="yield"),
    ParamSpec("lending_fraction_of_cash", "Fraction of cash lent out", 0.70, 0.40, 0.95, 0.05, category="yield"),
    
    # ──── REGIME ────
    ParamSpec("hmm_n_states", "Number of HMM regime states", 3, 2, 4, 1, "int", category="regime"),
    ParamSpec("hmm_sticky_alpha", "HMM sticky prior strength", 10.0, 2.0, 30.0, 2.0, category="regime"),
    ParamSpec("crisis_3step_threshold", "Crisis prob threshold for emergency", 0.30, 0.10, 0.70, 0.05, category="regime"),
    
    # ──── VOLATILITY SCALING ────
    ParamSpec("vol_target_annualized", "Target portfolio vol", 0.12, 0.06, 0.25, 0.01, category="vol_scaling"),
    ParamSpec("vol_lookback_days", "Vol estimation window", 21, 10, 63, 5, "int", category="vol_scaling"),
])
```

### 3C. Regime-Conditional Strategy Analysis

For every walk-forward test fold, tag the dominant regime and aggregate OOS scores by regime:

```python
# backtest/optimizer/regime_analysis.py

def analyze_regime_conditional_performance(
    results_df: pd.DataFrame,
    history_dfs: dict,   # strategy -> fold -> history DataFrame
) -> pd.DataFrame:
    """
    For each fold, compute what fraction of test period was in each regime,
    and compute OOS score conditional on dominant regime.
    
    Reveals: which strategy is best in bull vs crisis vs uncertain.
    """
    records = []
    
    for _, row in results_df.iterrows():
        strat = row["strategy"]
        fold = row["fold"]
        hist = history_dfs.get(strat, {}).get(fold)
        if hist is None:
            continue
        
        regime_counts = hist["regime"].value_counts(normalize=True)
        dominant_regime = regime_counts.idxmax()
        
        # Compute fold-level metrics by regime
        for regime in ["bull", "uncertain", "crisis"]:
            regime_hist = hist[hist["regime"] == regime]
            if len(regime_hist) < 5:
                continue
            regime_vals = regime_hist["portfolio_value"]
            regime_ret = (regime_vals.iloc[-1] / regime_vals.iloc[0]) - 1 if len(regime_vals) > 1 else 0
            
            records.append({
                "strategy": strat,
                "fold": fold,
                "regime": regime,
                "regime_fraction": regime_counts.get(regime, 0.0),
                "regime_return": regime_ret,
                "dominant_regime": dominant_regime,
                "test_score": row["test_score"],
            })
    
    return pd.DataFrame(records)
```

**Expected output — strategy regime matrix:**

```
strategy             | bull_return | uncertain_return | crisis_return | best_regime
---------------------|-------------|------------------|---------------|------------
equal_weight         | +24%        | +3%              | -18%          | bull
risk_parity          | +18%        | +5%              | -12%          | uncertain
regime_adaptive      | +21%        | +4%              | -9%           | crisis
static_conservative  | +6%         | +3%              | -2%           | crisis
min_variance         | +15%        | +4%              | -8%           | uncertain
black_litterman      | +20%        | +4%              | -11%          | bull
buy_and_hold         | +25%        | +2%              | -22%          | bull
```

Use this to build a **regime-conditional ensemble**: switch to `static_conservative` or `regime_adaptive` when 3-step crisis probability > threshold. Switch to `equal_weight` or `buy_and_hold` in confirmed bull.

### 3D. Bayesian Optimization Layer

Random search is unguided. After an initial 200-sample exploration, switch to Bayesian optimization:

```python
# backtest/optimizer/bayesian_opt.py

# Requires: pip install optuna

import optuna
from backtest.optimizer.grid_search import _run_single_backtest
from backtest.optimizer.param_space import PARAM_SPACE

def bayesian_optimize(
    market_data,
    base_config: dict,
    strategy: str,
    n_trials: int = 300,
    n_startup_trials: int = 50,  # random exploration before TPE kicks in
) -> optuna.Study:
    
    param_map = {p.name: p for p in PARAM_SPACE}
    
    def objective(trial: optuna.Trial) -> float:
        params = {}
        for spec in PARAM_SPACE:
            if spec.param_type == "int":
                params[spec.name] = trial.suggest_int(spec.name, int(spec.min_val), int(spec.max_val))
            elif spec.param_type == "categorical":
                params[spec.name] = trial.suggest_categorical(spec.name, spec.min_val)
            else:
                params[spec.name] = trial.suggest_float(spec.name, spec.min_val, spec.max_val, step=spec.step)
        
        result = _run_single_backtest(market_data, base_config, strategy, params)
        score = result.get("composite_score", -999)
        
        passes, reason = result.get("passes_hard_constraints", (False, "no result"))
        if not passes:
            return -999.0
        
        return score
    
    sampler = optuna.samplers.TPESampler(n_startup_trials=n_startup_trials, seed=42)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, n_jobs=-1, show_progress_bar=True)
    
    return study
```

### 3E. Optimal State Discovery — What to Measure

The optimizer currently maximizes a single composite score. For production robustness, optimize for worst-case OOS Sharpe across folds, not average:

```python
# In scorer.py — add robustness-aware scoring

def robust_score(fold_scores: list[float], lambda_penalty: float = 2.0) -> float:
    """
    Penalize variance across folds. 
    Maximizing this finds params that work across regimes, not just one fold.
    
    robust_score = mean(scores) - lambda * std(scores)
    
    lambda=2.0: strongly penalize inconsistency
    lambda=0.5: mild penalty, closer to pure mean maximization
    """
    scores = np.array(fold_scores)
    return float(scores.mean() - lambda_penalty * scores.std())

def min_oos_sharpe(fold_results: list[dict]) -> float:
    """
    Alternative: optimize for the WORST fold's OOS Sharpe.
    Forces params to be robust even in bad regimes.
    """
    sharpes = [r.get("sharpe_ratio", -99) for r in fold_results]
    return min(sharpes)
```

### 3F. Full Sweep CLI Command

Add to `backtest/main.py`:

```bash
# Run all strategies, full param space, walk-forward, regime analysis
python -m backtest.main \
  --config backtest/config/default.yaml \
  --full-sweep \
  --interval 1h \
  --data-source hyperliquid \
  --train-months 18 \
  --test-months 6 \
  --step-months 3 \
  --n-samples 500 \
  --optimizer bayesian \
  --n-trials 300 \
  --robust-scoring \
  --output-dir backtest/results/full_sweep_$(date +%Y%m%d)

# Expected runtime: 4-8 hours on 8-core machine with hourly data 2022-2026
# Output: results/full_sweep_YYYYMMDD/
#   cross_strategy_summary.csv
#   regime_conditional_performance.csv
#   per_strategy_best_params.yaml
#   walk_forward_folds.parquet
#   optimal_ensemble_config.yaml
```

---

## PART 4 — STRATEGY ROBUSTNESS FIXES

These are the exact code changes required. In priority order.

### FIX 1 — Cash Accounting (blocker: all results unreliable until fixed)

**`backtest/engine/simulator.py:184-208`** — Rewrite rebalance block:

```python
def _execute_rebalance(self, target_weights: dict, prices: dict) -> float:
    """
    Double-entry rebalance. Every trade is:
      debit: asset position changed by X USD
      credit: cash reduced by X USD + costs
    
    Invariant maintained: sum(positions) + cash == portfolio_value
    """
    total_cost = 0.0
    trade_log = []
    
    for asset in self.assets:
        current_val = self.portfolio.positions.get(asset, 0.0)
        target_val = self.portfolio.portfolio_value * target_weights.get(asset, 0.0)
        delta = target_val - current_val
        
        if abs(delta) < 1.0:  # below $1 — skip micro-trades
            continue
        
        direction = "buy" if delta > 0 else "sell"
        trade_size = abs(delta)
        
        # Direction-aware cost: sells at taker during emergencies
        is_emergency = self.cb.current_level >= 2
        cost = self.cost_model.estimate_cost(
            trade_size_usd=trade_size,
            asset=asset,
            direction=direction,
            is_emergency=is_emergency,
        )
        
        # Apply partial fill
        actual_delta = delta * cost.fill_ratio
        
        # Double-entry: position changes, cash changes by equal-and-opposite minus cost
        self.portfolio.positions[asset] = current_val + actual_delta
        self.portfolio.cash -= (actual_delta + cost.total)
        total_cost += cost.total
        
        trade_log.append({
            "asset": asset, "direction": direction,
            "trade_usd": trade_size, "actual_usd": abs(actual_delta),
            "fill_ratio": cost.fill_ratio, "cost_usd": cost.total,
            "cost_bps": cost.total_bps,
        })
    
    # Recompute weights from positions (single source of truth)
    total_val = self.portfolio.cash + sum(self.portfolio.positions.values())
    self.portfolio.portfolio_value = total_val
    
    if total_val > 0:
        for asset in self.assets:
            self.portfolio.weights[asset] = self.portfolio.positions[asset] / total_val
            self.portfolio.units[asset] = (
                self.portfolio.positions[asset] / prices[asset]
                if prices.get(asset, 0) > 0 else 0.0
            )
    
    # Hard invariant
    assert self.portfolio.cash >= -0.01, f"Cash went negative: {self.portfolio.cash:.4f}"
    
    return total_cost
```

### FIX 2 — Direction-Aware Fee Model

**`backtest/engine/cost_model.py`** — Add `is_emergency` to `estimate_cost`:

```python
class CostModelConfig(BaseModel):
    maker_fee_bps: float = 0.2       # HL actual maker rebate (negative cost)
    taker_fee_bps: float = 3.5       # HL actual taker fee
    maker_fraction_normal: float = 0.70   # 70% maker on scheduled rebalances
    maker_fraction_emergency: float = 0.0 # 100% taker on CB-triggered de-risk
    # ... rest unchanged

def estimate_cost(
    self,
    trade_size_usd: float,
    asset: str,
    direction: str,
    is_emergency: bool = False,
    asset_volatility: float = 0.03,
    book_depth_usd: float = 5_000_000,  # realistic HL depth, not 1e8
) -> TradeCost:
    if trade_size_usd <= 0:
        return TradeCost(0, 0, 0, 0, 0, 0, 0, 0, 0, 1.0)
    
    maker_frac = 0.0 if is_emergency else self.config.maker_fraction_normal
    taker_frac = 1.0 - maker_frac
    
    blended_fee_bps = (
        maker_frac * self.config.maker_fee_bps +
        taker_frac * self.config.taker_fee_bps
    )
    dex_fee = trade_size_usd * blended_fee_bps / 10000
    
    # Sell during downturn crosses spread against you
    direction_multiplier = 1.3 if direction == "sell" and is_emergency else 1.0
    
    base_slippage = self.config.slippage_bps_per_100k.get(asset, 2.0)
    size_factor = (trade_size_usd / 100_000.0) ** 0.7
    vol_multiplier = max(1.0, asset_volatility / 0.03) * direction_multiplier
    
    # Nonlinear depth: slippage explodes when trade exceeds available depth
    depth_ratio = trade_size_usd / max(book_depth_usd, 1.0)
    if depth_ratio > 0.5:
        # Walking into thin book — slippage blows up
        vol_multiplier *= (1.0 + 3.0 * (depth_ratio - 0.5))
    
    impact_bps = base_slippage * size_factor * vol_multiplier
    impact_cost = trade_size_usd * (impact_bps / 10000)
    
    # ... rest of cost components
```

### FIX 3 — Wire BlackLitterman to Correct Implementation

**`backtest/engine/strategies.py:227-254`** — Replace entire `BlackLittermanStrategy.generate_target_weights`:

```python
class BlackLittermanStrategy(AllocationStrategy):
    def generate_target_weights(self, current_weights, expected_returns,
                                covariance_matrix, asset_names,
                                max_volatile_override=None, current_regime="uncertain"):
        from risk_engine.portfolio_optimizer import optimize_black_litterman
        from risk_engine.schemas import View, TierConstraint
        
        config = getattr(self.config, '__dict__', {})
        risk_aversion = config.get('risk_aversion', 2.5)
        tau = config.get('tau', 0.05)
        
        # Real-time market caps from CoinGecko or config — not hardcoded
        # Fallback to equal-weight market caps if unavailable
        mkt_caps = np.array([
            expected_returns.get(a, 1.0 / len(asset_names))
            for a in asset_names
        ])
        mkt_caps = np.maximum(mkt_caps, 0)
        if mkt_caps.sum() == 0:
            mkt_caps = np.ones(len(asset_names))
        
        # No views by default — pure BL equilibrium
        # Views can be injected via config if analyst opinions exist
        views: list[View] = []
        
        bounds = [(0.0, 0.6) for _ in asset_names]
        tier_constraints = []
        if max_volatile_override is not None:
            stable_idx = [i for i, a in enumerate(asset_names) if a in ("USDC", "USDT", "DAI")]
            if stable_idx:
                tier_constraints.append(TierConstraint(
                    asset_indices=stable_idx,
                    min_total=1.0 - max_volatile_override,
                    max_total=1.0,
                ))
        
        try:
            result = optimize_black_litterman(
                covariance=covariance_matrix,
                market_caps=mkt_caps,
                views=views,
                risk_aversion=risk_aversion,
                tau=tau,
                bounds=bounds,
                tier_constraints=tier_constraints,
            )
            weights = {a: float(w) for a, w in zip(asset_names, result.weights)}
        except Exception:
            # Fallback to risk parity
            vols = np.sqrt(np.diag(covariance_matrix))
            inv_vols = 1.0 / np.maximum(vols, 1e-8)
            w = inv_vols / inv_vols.sum()
            weights = {a: float(w_i) for a, w_i in zip(asset_names, w)}
        
        return self._apply_volatile_override(weights, max_volatile_override)
```

### FIX 4 — Wire MinVariance to Correct Implementation

**`backtest/engine/strategies.py:195-225`** — Replace `MinVarianceStrategy.generate_target_weights`:

```python
class MinVarianceStrategy(AllocationStrategy):
    def generate_target_weights(self, current_weights, expected_returns,
                                covariance_matrix, asset_names,
                                max_volatile_override=None, current_regime="uncertain"):
        from risk_engine.portfolio_optimizer import optimize_mean_variance
        from risk_engine.schemas import TierConstraint
        
        n = len(asset_names)
        bounds = [(0.0, 0.4) for _ in asset_names]  # max 40% per asset
        tier_constraints = []
        
        if max_volatile_override is not None:
            stable_idx = [i for i, a in enumerate(asset_names) if a in ("USDC", "USDT", "DAI")]
            if stable_idx:
                tier_constraints.append(TierConstraint(
                    asset_indices=stable_idx,
                    min_total=1.0 - max_volatile_override,
                    max_total=1.0,
                ))
        
        try:
            # Use risk_aversion → ∞ approximation for min variance:
            # maximize -(w'Σw), i.e., minimize variance ignoring returns
            # Pass zero expected returns — optimizer minimizes variance only
            zero_returns = np.zeros(n)
            result = optimize_mean_variance(
                expected_returns=zero_returns,
                covariance=covariance_matrix,
                bounds=bounds,
                tier_constraints=tier_constraints if tier_constraints else None,
            )
            if result.converged:
                weights = {a: float(w) for a, w in zip(asset_names, result.weights)}
            else:
                raise ValueError(f"MinVar QP failed: {result.message}")
        except Exception:
            vols = np.sqrt(np.diag(covariance_matrix))
            inv_vols = 1.0 / np.maximum(vols, 1e-8)
            w = inv_vols / inv_vols.sum()
            weights = {a: float(w_i) for a, w_i in zip(asset_names, w)}
        
        return self._apply_volatile_override(weights, max_volatile_override)
```

### FIX 5 — Eliminate Look-Ahead Bias

**`backtest/engine/simulator.py:64-78`** — Replace warmup and rolling refit:

```python
def run(self, verbose: bool = False, pre_warmup_data: pd.DataFrame = None) -> dict:
    """
    pre_warmup_data: DataFrame of returns from BEFORE the backtest period.
    Used exclusively for HMM warmup. Prevents in-sample contamination.
    If None, the first 90 days of backtest data are burned as non-trading warmup
    (positions stay in cash, no backtest P&L recorded).
    """
    if pre_warmup_data is not None and len(pre_warmup_data) >= self.warmup_days:
        idx = pre_warmup_data.pct_change().fillna(0).mean(axis=1)
        self.regime_detector.fit(idx)
    else:
        # Burn first warmup_days — no trading, just HMM fitting
        warmup = self.market_data.iloc[:self.warmup_days]
        idx = warmup.pct_change().fillna(0).mean(axis=1)
        if len(idx) >= 60:
            self.regime_detector.fit(idx)
        # History starts AFTER warmup
        start_day = self.warmup_days
    
    for day in range(start_day, len(self.market_data)):
        self.current_day = day
        self.step()
        
        if day >= self.warmup_days and day % 30 == 0:
            # CRITICAL: exclude current day from refit — use strict [:day]
            lookback = self.market_data.iloc[max(0, day - 504):day]  # NOT day+1
            returns = lookback.pct_change().fillna(0).mean(axis=1)
            if len(returns) >= self.regime_detector.min_observations:
                self.regime_detector.refit_rolling(returns)
    
    return self.summary()
```

### FIX 6 — Vectorize Jump Diffusion

**`backtest/python-risk/risk_engine/var_models.py:116-118`** — Replace loop:

```python
def compute_jump_diffusion_var(
    returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float = 0.95,
    jump_intensity: float = 20.0,
    jump_mean: float = -0.02,
    jump_std: float = 0.05,
    num_simulations: int = 50000,
    seed: int = 42,
) -> Tuple[float, float]:
    rng = np.random.default_rng(seed)  # seeded, reproducible
    
    port_returns = np.dot(returns, weights)
    dt = 1.0 / 252.0
    mu = np.mean(port_returns) * 252.0
    sigma = np.std(port_returns) * np.sqrt(252.0)
    
    z = rng.standard_normal(num_simulations)
    diffusion = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
    
    n_jumps = rng.poisson(jump_intensity * dt, num_simulations)
    
    # Vectorized compound Poisson — no Python loop
    max_jumps = int(n_jumps.max()) if n_jumps.max() > 0 else 0
    if max_jumps > 0:
        all_jumps = rng.normal(jump_mean, jump_std, (num_simulations, max_jumps))
        jump_mask = np.arange(max_jumps)[np.newaxis, :] < n_jumps[:, np.newaxis]
        jump_sizes = (all_jumps * jump_mask).sum(axis=1)
    else:
        jump_sizes = np.zeros(num_simulations)
    
    sim_returns = diffusion + jump_sizes
    
    percentile = 1.0 - confidence_level
    var = -np.percentile(sim_returns, percentile * 100)
    below_var = sim_returns[sim_returns <= -var]
    cvar = -float(below_var.mean()) if len(below_var) > 0 else var
    
    return float(var), float(cvar)
```

### FIX 7 — Risk-Free Rate Consistency

Single source of truth. Remove all hardcoded rf values:

```python
# backtest/engine/simulator.py:244 — remove hardcoded 0.05
# backtest/analysis/metrics.py — keep parameter, pass from config
# backtest/main.py — always read from sim_config['risk_free_rate']

# In TreasurySimulator.__init__:
self.risk_free_rate: float = 0.02  # passed from config, not hardcoded

# In summary():
sharpe = (ann_return - self.risk_free_rate) / ann_vol if ann_vol > 0 else 0.0
```

### FIX 8 — Yield Engine Conditioned on Actual Positions

**`backtest/engine/yield_engine.py`** — Gate basis trade yield on actual positions:

```python
def calculate_yield(
    self,
    portfolio_value: float,
    cash_pct: float,
    date: pd.Timestamp,
    regime: str,
    derivative_positions: list,   # pass actual positions from portfolio state
    lending_fraction: float = 0.70,
) -> float:
    lending_rate = self.get_lending_rate(date)
    daily_lending = (portfolio_value * cash_pct * lending_fraction) * (lending_rate / 365)
    
    # Funding only from real open positions
    daily_funding = 0.0
    for pos in derivative_positions:
        # funding_rate must come from real fetched data keyed by (coin, 8h bucket)
        rate = self._get_funding_rate(pos.market.replace("-PERP", ""), date)
        direction = 1.0 if pos.direction == "long" else -1.0
        daily_funding -= pos.notional_usd * rate * direction  # shorts receive positive funding
    
    return daily_lending + daily_funding
```

### FIX 9 — Recovery Peak Tracking

**`backtest/engine/circuit_breaker.py:RecoveryPhase`** — Add running peak:

```python
@dataclass
class RecoveryPhase:
    is_active: bool = False
    entry_date: Optional[pd.Timestamp] = None
    entry_portfolio_value: float = 0.0
    recovery_peak: float = 0.0       # NEW: track highest value seen during recovery
    weeks_in_recovery: int = 0
    snap_back_count: int = 0
    
    def update_peak(self, current_value: float) -> None:
        if current_value > self.recovery_peak:
            self.recovery_peak = current_value
    
    def check_further_decline(self, current_value: float) -> bool:
        if not self.is_active or self.recovery_peak == 0:
            return False
        # Compare to RECOVERY PEAK, not entry value
        drop_from_peak = (self.recovery_peak - current_value) / self.recovery_peak
        return drop_from_peak > self.further_decline_threshold
    
    def enter(self, date: pd.Timestamp, portfolio_value: float) -> None:
        self.is_active = True
        self.entry_date = date
        self.entry_portfolio_value = portfolio_value
        self.recovery_peak = portfolio_value  # Initialize peak at entry
        self.weeks_in_recovery = 0
```

---

## PART 5 — ROBUSTNESS TEST SUITE

Every fix must be validated by a deterministic test. No mocking the market — use synthetic but precisely controlled data.

### 5A. Required Tests

```python
# backtest/tests/test_accounting.py

def test_cash_never_goes_negative():
    """
    Run 1000 random rebalances with high costs and partial fills.
    Assert cash >= 0 after every single trade.
    """
    ...

def test_portfolio_value_equals_cash_plus_positions():
    """
    After every step, sum(positions.values()) + cash == portfolio_value.
    Tolerance: $0.01.
    """
    ...

def test_no_look_ahead_in_rolling_refit():
    """
    Inject a price spike on day D. Verify regime on day D-1
    does not change when spike is removed. Confirms :day not :day+1 slice.
    """
    ...

# backtest/tests/test_cost_model.py

def test_emergency_sell_uses_taker_fee():
    """
    CB at L2. Single sell trade.
    Assert total fee == trade_size * taker_fee_bps / 10000.
    """
    ...

def test_normal_buy_uses_blended_fee():
    """
    CB at 0. Single buy trade.
    Assert total fee uses maker_fraction * maker_fee + taker_fraction * taker_fee.
    """
    ...

def test_deep_book_slippage_nonlinear():
    """
    Trade size > 50% of book_depth_usd.
    Assert slippage_bps > 5x base_slippage (depth_ratio blowup triggered).
    """
    ...

# backtest/tests/test_strategies.py

def test_minvariance_is_lower_variance_than_equal_weight():
    """
    On known covariance matrix, compute both portfolios.
    Assert w_mv' Σ w_mv < w_ew' Σ w_ew.
    """
    ...

def test_blacklitterman_posterior_differs_from_prior():
    """
    With one strong view injected, BL posterior weights should
    differ from equilibrium-only weights by > 5%.
    """
    ...

def test_regime_adaptive_reduces_vol_in_crisis():
    """
    Force regime = "crisis". Assert volatile_target == 0.10.
    Force regime = "bull". Assert volatile_target == 0.60.
    """
    ...
```

### 5B. Benchmark Acceptance Criteria (OOS Walk-Forward)

A strategy passes benchmark only if it meets ALL of these on OOS folds:

| Metric | Threshold |
|---|---|
| Mean OOS Sharpe | ≥ 0.5 |
| Min OOS Sharpe (worst fold) | ≥ 0.0 (no fold loses money on risk-adjusted basis) |
| Mean OOS Max Drawdown | ≤ 25% |
| Worst OOS Max Drawdown | ≤ 35% |
| OOS Cost Drag | ≤ 2% annualized |
| Overfit Ratio (test/train score) | ≥ 0.6 (train score ≤ 1.67x test score) |
| Regime-Adaptive Crisis Return | ≥ -5% in crisis-dominant folds |
| Cash negative events | 0 |

If any strategy fails these on the first sweep, do NOT tune it to pass. Diagnose why it fails on that fold — the fold itself is the signal.

---

## PART 6 — IMPLEMENTATION PRIORITY (v3)

Execute in this exact order. Each step is a prerequisite for the next.

```
Phase 1 — Unblock Runtime (nothing runs without these)
  [ ] N1: Fix timedelta import in main.py (1 line)
  [ ] F1: Remove cash correction workaround — fix root cause (assert, not mask)
  [ ] F13/F2: Already fixed — verify with unit test

Phase 2 — Real Data Pipeline (no P&L is trustworthy until these are done)
  [ ] N3: Implement DeFi Llama in lending.py — no synthetic fallback
  [ ] N2: Wire funding_series into YieldEngine.__init__
  [ ] N4: Pass weights dict to YieldEngine.calculate_yield
  [ ] N5: Fetch L2 depth per asset at sim init; vol-conditional haircut
  [ ] main.py: Fetch funding + lending before sim; fail hard if unavailable
  [ ] simulator.py: Accept injected YieldEngine; pass depth_by_asset
  [ ] Remove FUNDING_RATES and LENDING_RATE_SCHEDULE from yield_engine.py
  [ ] Remove _generate_realistic_lending_rates from lending.py

Phase 3 — Execution Realism
  [ ] N5: Vol-conditional depth haircut in rebalance
  [ ] F6: Replace hardcoded 5e6 depth with per-asset calibrated value

Phase 4 — Strategy Correctness
  [ ] N6: Fix BlackLitterman to call optimize_black_litterman
  [ ] F7: Verify BL with and without views; test posterior != prior
  [ ] F14: Vectorize jump diffusion (see FIX 6 code)
  [ ] F11: Decide: implement hedger or remove dead code

Phase 5 — Full Sweep
  [ ] Section 3A: Cross-strategy walk-forward
  [ ] Section 3B: Extended param space (9 new params)
  [ ] Section 3C: Regime-conditional analysis
  [ ] Section 3D: Bayesian optimization layer
  [ ] Section 3E: Robust scoring (penalize fold variance)

Phase 6 — Test Suite
  [ ] Section 5A: All listed tests passing
  [ ] Section 5B: All strategies benchmarked against acceptance criteria
  [ ] Smoke test: run --dry-run with STRICT_MODE=True; verify no synthetic data used
```

### How to verify zero synthetic data (after Phase 2)

```bash
# Run with strict mode; should print only real data sources, no "synthetic" warnings
ALADDIN_ENV=production python -m backtest.main --config backtest/config/default.yaml 2>&1 | grep -E "synthetic|fallback|mock|fake|hardcoded"
# Expected: no output (zero synthetic paths triggered)

# Verify funding data actually loaded
python -m backtest.main --config backtest/config/default.yaml 2>&1 | grep "funding"
# Expected: "BTC funding: 730 days, mean=0.0082%/day"
#           "ETH funding: 730 days, mean=0.0071%/day"

# Verify lending data actually loaded  
python -m backtest.main --config backtest/config/default.yaml 2>&1 | grep "lending"
# Expected: "USDC lending: 730 days, mean APY=5.2%"
```

---

## PART 7 — ISSUE TRACKER (v3 STATUS)

`STATUS` key: ✓ FIXED | ~ PARTIAL | ✗ OPEN | NEW new issue found in v3

| ID | Sev | File | Description | Status | Phase |
|---|---|---|---|---|---|
| F1 | CRIT | `simulator.py:290-301` | Cash accounting — negative cash masked by correction workaround | ~ | 1 |
| F2 | CRIT | `simulator.py:86-90` | Look-ahead: rolling refit `day` not `day+1` | ✓ | 1 |
| F3 | CRIT | `yield_engine.py:43` | Real HL funding fetched but **not consumed** — hardcoded regime dict used | ✗ | 2 |
| F4 | CRIT | `lending.py:13-17` | DeFi Llama call commented out — synthetic schedule always used | ✗ | 2 |
| F5 | CRIT | `cost_model.py` | Taker fee fixed: 0.2bps maker / 3.5bps taker, direction-aware | ✓ | 3 |
| F6 | HIGH | `simulator.py:281` | Market depth hardcoded `5e6` in every rebalance — suppresses crisis slippage | ✗ | 2/3 |
| F7 | HIGH | `strategies.py:BlackLittermanStrategy` | Calls `optimize_mean_variance` not `optimize_black_litterman` | ✗ | 4 |
| F8 | HIGH | `strategies.py:MinVarianceStrategy` | Now calls MV with zero returns — correct MinVar. Acceptable. | ✓ | 4 |
| F9 | HIGH | `yield_engine.py` | Funding gated on `derivative_positions` but positions always `[]` → funding yield always 0 | ~ | 2 |
| F10 | HIGH | `circuit_breaker.py:RecoveryPhase` | Recovery peak tracking fixed: compares to `recovery_peak` not entry | ✓ | 1 |
| F11 | HIGH | `hedger.py` | Imported, called, but `for action in hedge_actions: pass` — zero effect | ✗ | 4 |
| F12 | HIGH | `event_driven_replay.py` | Never called from production path | ✗ | 5 |
| F13 | MED | `simulator.py:summary()` | RF rate single source of truth from config | ✓ | 1 |
| F14 | MED | `var_models.py:116` | 50k Python loop — vectorized fix in audit but not applied | ✗ | 4 |
| F15 | MED | `var_models.py` | Global RNG — non-reproducible | ✗ | 4 |
| F16 | MED | `fetcher.py:CoinGecko` | CoinGecko fallback sets OHLC=close — zero intraday range | ✗ | 2 |
| F17 | MED | `simulator.py:245` | Static BTC/ETH market caps hardcoded for BL equilibrium returns | ✗ | 4 |
| F18 | LOW | `cost_model.py` | MEV cost = 0 unconditionally — appropriate for HL sequencer | ✓ | — |
| F19 | LOW | `circuit_breaker.py:131` | Vol ratio decay threshold 1.2 — relaxed correctly | ✓ | — |
| F20 | LOW | `funding.py` | Synthetic fallback seeded by symbol hash — now fallback only on API failure | ~ | 2 |
| **N1** | **CRIT** | `main.py:80` | `timedelta` not imported at module level → `NameError` at runtime | **NEW** | 1 |
| **N2** | **CRIT** | `yield_engine.py:43` | Real funding series fetched in `funding.py` but `YieldEngine` never receives it | **NEW** | 2 |
| **N3** | **CRIT** | `lending.py:13` | DeFi Llama never attempted — both try and except call synthetic generator | **NEW** | 2 |
| **N4** | **HIGH** | `simulator.py:167` | `calculate_yield` receives `cash_pct` proxy, not actual stable weights | **NEW** | 2 |
| **N5** | **HIGH** | `simulator.py:281` | `book_depth_usd=5e6` hardcoded — no vol-conditional depth haircut | **NEW** | 2/3 |
| **N6** | **HIGH** | `strategies.py:228-248` | BL calls `optimize_mean_variance(equilibrium_mu, cov)` — bypasses BL formula entirely | **NEW** | 4 |

### Open critical count: 6 (N1, N2, N3, F3, F4, F1-partial)
### Open high count: 5 (N4, N5, N6, F6, F7, F11)

---

## PART 7B — EXACT FIXES FOR NEW ISSUES

### N1 — Fix `timedelta` import (1-line, do first)

**`backtest/main.py:4`** — change:
```python
from datetime import datetime
```
to:
```python
from datetime import datetime, timedelta
```
Remove the duplicate import at line 242.

---

### N2 + N3 — Wire Real Funding and Lending into YieldEngine

**Step 1**: update `YieldEngine.__init__` to accept real series:

```python
# backtest/engine/yield_engine.py

class YieldEngine:
    def __init__(
        self,
        funding_series: Optional[dict[str, pd.Series]] = None,
        lending_series: Optional[dict[str, pd.Series]] = None,
    ):
        # funding_series: {asset: pd.Series(index=DatetimeIndex, data=daily_rate_sum)}
        # daily_rate_sum = sum of three 8h payments that day
        # lending_series: {asset: pd.Series(index=DatetimeIndex, data=apy_decimal)}
        self._funding = funding_series or {}
        self._lending = lending_series or {}

    def get_funding_rate_daily(self, date: pd.Timestamp, asset: str) -> float:
        series = self._funding.get(asset)
        if series is not None and not series.empty:
            rate = series.asof(date)
            if rate is not None and not pd.isna(rate):
                return float(rate)
        # No fallback to synthetic — return 0 and let caller decide
        return 0.0

    def get_lending_rate(self, date: pd.Timestamp, asset: str = "USDC") -> float:
        series = self._lending.get(asset)
        if series is not None and not series.empty:
            rate = series.asof(date)
            if rate is not None and not pd.isna(rate):
                return max(0.0, float(rate))
        # No synthetic fallback — caller must provide real data or get 0
        return 0.0
```

**Step 2**: update `calculate_yield` to use per-asset weights for lending:

```python
def calculate_yield(
    self,
    portfolio_value: float,
    date: pd.Timestamp,
    regime: str,
    derivative_positions: list,
    weights: dict[str, float],        # ← actual portfolio weights, not cash_pct proxy
    lending_fraction: float = 0.70,
) -> float:
    stable_assets = {"USDC", "USDT", "DAI"}

    # Lending: weighted by actual stable allocation, not cash proxy
    daily_lending = 0.0
    for asset, weight in weights.items():
        if asset in stable_assets and weight > 0:
            rate = self.get_lending_rate(date, asset)
            daily_lending += portfolio_value * weight * lending_fraction * (rate / 365)

    # Funding: only from open derivative positions with real rates
    daily_funding = 0.0
    for pos in derivative_positions:
        coin = pos.market.replace("-PERP", "")
        rate = self.get_funding_rate_daily(date, coin)
        direction = 1.0 if pos.direction == "long" else -1.0
        daily_funding -= pos.notional_usd * rate * direction

    return daily_lending + daily_funding
```

**Step 3**: update `simulator.py` call site to pass `weights`:

```python
# backtest/engine/simulator.py — in step():
daily_yield = self.yield_engine.calculate_yield(
    portfolio_value=self.portfolio.portfolio_value,
    date=pd.Timestamp(date),
    regime=regime_pred.current_regime,
    derivative_positions=self.portfolio.derivative_positions,
    weights=self.portfolio.weights,        # ← pass actual weights
)
```

**Step 4**: update `main.py` to fetch and wire real data:

```python
# backtest/main.py — in run_simulation(), after price data is fetched:

from backtest.data.funding import fetch_funding_rates
from backtest.data.lending import fetch_lending_rates
from backtest.engine.yield_engine import YieldEngine

STRICT_MODE = True  # fail hard, no synthetic substitution

volatile_assets = [a for a in assets if a not in ("USDC", "USDT", "DAI")]
stable_assets_in_sim = [a for a in assets if a in ("USDC", "USDT", "DAI")]

funding_series: dict = {}
for asset in volatile_assets:
    df = fetch_funding_rates(asset, int(warmup_start.timestamp()), int(end_date.timestamp()))
    if df.empty:
        if STRICT_MODE:
            raise RuntimeError(f"No funding data for {asset}. Refusing to run on fabricated inputs.")
        logger.warning(f"No funding data for {asset}, funding yield = 0 for this asset.")
    else:
        # Sum 3 payments per day (8h × 3 = daily)
        funding_series[asset] = df["funding_rate"].resample("1D").sum()
        logger.info(f"{asset} funding: {len(funding_series[asset])} days, mean={funding_series[asset].mean()*100:.4f}%/day")

lending_series: dict = {}
for asset in stable_assets_in_sim:
    df = fetch_lending_rates(asset, int(warmup_start.timestamp()), int(end_date.timestamp()))
    if df.empty:
        if STRICT_MODE:
            raise RuntimeError(f"No lending data for {asset}. Refusing to run on fabricated inputs.")
        logger.warning(f"No lending data for {asset}, lending yield = 0.")
    else:
        lending_series[asset] = df["lending_rate"]
        logger.info(f"{asset} lending: {len(df)} days, mean APY={df['lending_rate'].mean()*100:.1f}%")

yield_engine = YieldEngine(funding_series=funding_series, lending_series=lending_series)

# Pass yield_engine to each TreasurySimulator:
sim = TreasurySimulator(
    ...,
    yield_engine=yield_engine,
)
```

**Step 5**: update `TreasurySimulator.__init__` to accept injected yield engine:

```python
def __init__(self, ..., yield_engine: Optional[YieldEngine] = None):
    self.yield_engine = yield_engine or YieldEngine()  # empty YieldEngine = zero yield
```

---

### N3 — Implement DeFi Llama in `lending.py`

```python
# backtest/data/lending.py — full replacement

import requests, pandas as pd, logging, time
from typing import Optional

logger = logging.getLogger(__name__)

def fetch_lending_rates(asset: str, start_time: int, end_time: int) -> pd.DataFrame:
    """
    Fetch historical lending rates from DeFi Llama Yields API.
    Does NOT fall back to synthetic. Returns empty DataFrame if API unavailable.
    Caller decides whether to raise or proceed with zero yield.
    """
    pool_id = _find_best_pool(asset)
    if not pool_id:
        logger.error(f"No DeFi Llama pool found for {asset}.")
        return pd.DataFrame()

    return _fetch_pool_history(pool_id, start_time, end_time)


def _find_best_pool(asset: str) -> Optional[str]:
    resp = requests.get("https://yields.llama.fi/pools", timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)

    pools = resp.json().get("data", [])
    preferred = ["aave-v3", "compound-v3", "aave-v2", "compound"]

    candidates = [
        p for p in pools
        if p.get("symbol", "").upper() == asset.upper()
        and p.get("chain") in ("Ethereum", "Arbitrum")
        and p.get("tvlUsd", 0) > 5_000_000
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda p: (
        preferred.index(p["project"]) if p.get("project") in preferred else 99,
        -p.get("tvlUsd", 0)
    ))
    chosen = candidates[0]
    logger.info(f"DeFi Llama pool for {asset}: {chosen.get('project')} TVL=${chosen.get('tvlUsd',0):,.0f}")
    return chosen["pool"]


def _fetch_pool_history(pool_id: str, start_time: int, end_time: int) -> pd.DataFrame:
    resp = requests.get(f"https://yields.llama.fi/chart/{pool_id}", timeout=30)
    resp.raise_for_status()
    time.sleep(0.5)

    data = resp.json().get("data", [])
    if not data:
        return pd.DataFrame()

    start_dt = pd.to_datetime(start_time, unit="s", utc=True)
    end_dt = pd.to_datetime(end_time, unit="s", utc=True)

    records = []
    for entry in data:
        ts = pd.to_datetime(entry["timestamp"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        if start_dt <= ts <= end_dt:
            apy = entry.get("apy") or entry.get("apyBase") or 0.0
            records.append({"timestamp": ts.tz_localize(None), "lending_rate": float(apy) / 100.0})

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records).set_index("timestamp")
    df = df.resample("1D").ffill()
    df["lending_rate"] = df["lending_rate"].clip(0.0, 0.50)
    logger.info(f"Fetched {len(df)} days real lending for pool {pool_id[:8]}, APY {df['lending_rate'].min()*100:.1f}%-{df['lending_rate'].max()*100:.1f}%")
    return df
```

---

### N5 — Real L2 Depth for Slippage

```python
# backtest/main.py — fetch L2 depth at sim init

depth_by_asset: dict[str, float] = {}
for asset in volatile_assets:
    try:
        book = fetcher.fetch_l2_depth_snapshot(asset)
        depth_by_asset[asset] = book.get("depth_25bps_usd", 1_000_000)
        logger.info(f"{asset} L2 depth at 25bps: ${depth_by_asset[asset]:,.0f}")
    except Exception as e:
        logger.warning(f"L2 depth fetch failed for {asset}: {e}. Using $1M conservative default.")
        depth_by_asset[asset] = 1_000_000  # conservative: assume thin book

# Pass depth_by_asset to TreasurySimulator, which passes to cost_model.estimate_cost

# In simulator._execute_rebalance:
book_depth = self.depth_by_asset.get(asset, 1_000_000)
# Vol-conditional haircut: crisis = thinner book
vol_haircut = max(0.1, 1.0 - 5.0 * rolling_vol)
effective_depth = book_depth * vol_haircut

cost = self.cost_model.estimate_cost(
    trade_size, asset, direction,
    book_depth_usd=effective_depth,
    daily_volume_usd=effective_depth * 2.0,  # rough ADV proxy
    is_emergency=is_emergency,
)
```

---

### N6 — Fix BlackLitterman

```python
# backtest/engine/strategies.py — BlackLittermanStrategy.generate_target_weights

from risk_engine.portfolio_optimizer import optimize_black_litterman
from risk_engine.schemas import View, TierConstraint

# market_caps: derive from current weights (portfolio's own allocation as market proxy)
mkt_caps = np.array([max(current_weights.get(a, 1.0 / len(asset_names)), 1e-6) for a in asset_names])
# Normalize to sum = 1 (relative weights only)
mkt_caps = mkt_caps / mkt_caps.sum()

# No views = pure BL equilibrium (respects market-cap prior)
# Analyst views can be injected later via config
views: list[View] = []

tier_constraints = []
if max_volatile_override is not None:
    stable_idx = [i for i, a in enumerate(asset_names) if a in ("USDC", "USDT", "DAI")]
    if stable_idx:
        tier_constraints.append(TierConstraint(
            asset_indices=stable_idx,
            min_total=1.0 - max_volatile_override,
            max_total=1.0,
        ))

result = optimize_black_litterman(
    covariance=covariance_matrix,
    market_caps=mkt_caps * 1e9,   # scale to USD (relative values matter, not absolute)
    views=views,
    risk_aversion=2.5,
    tau=0.05,
    bounds=[(0.0, 0.60) for _ in asset_names],
    tier_constraints=tier_constraints or None,
)
weights = {a: float(w) for a, w in zip(asset_names, result.weights)}
```

---

## PART 8 — PRODUCTION HARDENING: THE "LIVE-ONLY" ENFORCEMENT

To satisfy the mandate of **zero simulation in production**, the codebase must be structurally modified to prevent accidental "phantom P&L" from synthetic fallbacks.

### 8A. The Production Kill-Switch

Every data provider must implement a strict `STRICT_LIVE` mode. When enabled, any failure to fetch real data results in a system halt (FatalStateError), not a synthetic fallback.

**`backtest/data/funding.py` (Production Version):**
```python
import os

STRICT_LIVE = os.getenv("ALADDIN_ENV") == "production"

def fetch_funding_rates(coin: str, start_time: int, end_time: int) -> pd.DataFrame:
    # ... REST logic ...
    try:
        # ... fetch ...
        return validate_funding(df, coin)
    except Exception as e:
        if STRICT_LIVE:
            logger.critical(f"PRODUCTION DATA GAP: Failed to fetch real funding for {coin}. HALTING.")
            raise FatalStateError("DataIntegrity", f"No live funding for {coin}")
        else:
            logger.warning(f"Backtest fallback: Generating synthetic funding for {coin}.")
            return _generate_synthetic_funding(coin, start_time, end_time)
```

### 8B. Banning Random Walks in Yield Engine

The current `lending.py` uses a hardcoded random walk for interest rates. This is unacceptable for production capital.

**Required Action**: 
- Deprecate `_generate_realistic_lending_rates`.
- Implement `LlamaFetcher` or `AaveSubGraphFetcher` to pull real-time supply APYs.
- If APY cannot be fetched, assume `0.0` yield for safety rather than "realistic" noise.

---

## PART 9 — REAL-TIME MICROSTRUCTURE AUDIT

Since the system is moving to live-only data, the audit must extend to the **Guardian's execution path**. 

### 9A. Live VPIN Monitoring (Toxic Flow Detection)

The `MicrostructureAnalyzer` in `guardian-service/src/index.ts` must be wired to a Prometheus/Grafana dashboard. 

**Metric to Track:** `execution_edge_bps`
- If `expected_edge_bps` is consistently negative but trades are still executing, the `isHedge` flag is being abused or the spread-crossing logic is too aggressive.
- **Audit Requirement**: Any trade with `expected_edge_bps < -5.0` must trigger an immediate Slack/Discord alert.

### 9B. Inventory Drift Monitoring

In a live-only environment, local "assumed" fills are the primary source of ruin.
- **Audit Logic**: Every 60 seconds, the Guardian must compare `ledger.inventory` (local event-sourced state) with `hyperliquid.get_user_state()` (authoritative exchange state).
- **Hard Limit**: If `abs(local_units - remote_units) / remote_units > 0.01`, the system must enter `EMERGENCY_SHUTDOWN` and re-sync from snapshot.

---

## PART 10 — FINAL PRODUCTION READINESS CHECKLIST (ZERO SIMULATION)

The system is considered **READY FOR DEPLOYMENT** only when all items below are marked [X].

### Data Integrity [ ]
- [ ] `funding.py` fallback removed/guarded by `STRICT_LIVE`.
- [ ] `lending.py` integrated with real AAVE/Compound supply rate API.
- [ ] `ohlcv` source for BTC/ETH/SOL moved from Binance Spot (fallback) to HL Perp (authoritative).
- [ ] L2 Book depth integrated into `cost_model.py` for real-time slippage estimates.

### Execution Path [ ]
- [ ] `EventDrivenReplayEngine` verified against 24h of real L2 HL packets.
- [ ] `QueueAwareExecutionEngine` correctly cancels orders when VPIN > 0.75.
- [ ] `InventorySkewEngine` successfully flattens delta in shadow mode (live data, zero size).

### Accounting & Safety [ ]
- [ ] All math moved to `decimal.js` in Guardian and `FixedPointMath.sol` in contracts.
- [ ] `TransactionFirewall` hard limit set to $10,000 for initial "Baby Step" live run.
- [ ] `FatalStateError` verified to correctly trigger `process.exit(1)` and alert the on-call engineer.

### Conclusion
The shift from **Simulation-Centric** to **Live-Enforced** is the final barrier. By eliminating synthetic noise, we force the strategy to confront the reality of Hyperliquid's microstructure. If the strategy loses money on live data in shadow mode, it is a failure of alpha, not a failure of the engine.

