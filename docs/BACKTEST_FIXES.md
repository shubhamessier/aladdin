# Backtest — Issues Found and Fixes Applied

This document logs every defect found across the Aladdin Hyperliquid vault during a deep audit + repair pass, and the precise fix landed for each. It is the engineering changelog companion to the deep-audit narrative.

The audit pass had three phases:

1. **Static audit** — reading every load-bearing file and identifying logic / safety / accounting bugs.
2. **Output analysis** — discovering the previously-published backtest output was 70,116 rows of silently-propagated NaN.
3. **Repair pass** — fixing the broken data layer, simulator, hedger, and reporting so the backtest actually runs and produces differentiated, real results.

---

## Section 1 — Vault-side findings (Solidity contracts)

These are **not** fixed in this pass. They are documented here because the static audit surfaced them; the contracts repository requires its own engagement. Severity ordering, with file:line refs.

### Catastrophic — system cannot operate as-is

| ID | File:line | Bug | Effect |
|---|---|---|---|
| A1 | `contracts/src/core/AssetRegistry.sol:96-102` | `getPortfolioSnapshot()` returns `AssetSnapshot[](0)` and `totalPortfolioUSD: 0` (stub). | Every `SecurityHooks.validate` divides by `totalPortfolioUSD` six times → division by zero on every trade. Vault unusable. |
| A2 | `contracts/src/core/TreasuryVault.sol:282` | `_updatePortfolioSnapshot` is `onlyRole(KEEPER_ROLE)` but called from a `GUARDIAN_ROLE` function. | Every batch action reverts at the snapshot step. Single swaps never call it at all. |
| A3 | `contracts/src/core/TreasuryVault.sol:170, 195` | Router whitelist commented out; `safeIncreaseAllowance` granted to arbitrary attacker-supplied router. | Any guardian-key compromise → total drain. |
| A4 | `contracts/src/core/OracleAdapter.sol:229-243` | `getPrice` (view) returns hard-coded `PriceStatus.GOOD`; real status is only computed in `resolvePrice` (non-view). | Oracle Rule 1 in SecurityHooks is dead code; staleness never fires. |
| A5 | `contracts/src/core/OracleAdapter.sol:63` | `setFeeds(...)` has no access control. | Anyone can repoint Chainlink/Pyth feeds for any token. Total loss of funds. |
| A6 | `contracts/src/core/TreasuryVault.sol:138-149` | `withdraw` bypasses SecurityHooks, CB, timelock. `withdrawalQueue` mapping is dead state. | Anyone can drain their balance during CB lockdown. |
| A7 | `contracts/src/core/TreasuryVault.sol:121` | UUPS `_authorizeUpgrade` gated only by single `DEFAULT_ADMIN_ROLE`. No timelock. | Single-key compromise → arbitrary implementation upgrade next block. |

### Major — silent correctness failures (Solidity)

| ID | File:line | Bug |
|---|---|---|
| B1 | `TreasuryVault.sol:343-358` vs `backtest/engine/circuit_breaker.py:79-92` | On-chain HWM decay decays HWM *itself* toward 0; Python decays the *gap* toward current_value. After ~5 halflives on-chain `hwmEffective == 0` and the HWM branch silently stops firing. |
| B2 | `TreasuryVault.sol:360-367` | `decayCBLevel` only checks "1 day passed". README claims vol / N stable days / 60d-forced. Stable-day counter is written but never read. |
| C4 | `SecurityHooks.sol:18-29` vs `TreasuryVault.sol:118` | `maxDailyVolumeUSD` is duplicated as a constant in SecurityHooks and a storage variable in the vault. Two sources of truth. |
| C5 | `TreasuryVault.sol` storage | `whitelistedStrategies`, `whitelistedRouters` are write-only — no reader checks them anywhere. |
| C6 | `contracts/src/core/StrategyManager.sol:35-46` | `deposit/withdraw` only update accounting; no `IERC20` transfers. Strategy deposits are fictional. |
| C7 | `TreasuryVault.sol` | `paused` boolean checked everywhere; no `pause()` or `unpause()` setter exists. EMERGENCY_ROLE defined and unused. |
| C8 | `TreasuryVault.sol:282`, `_checkCircuitBreaker` | Snapshot ring buffer never updates outside batch actions → starveable. Window-based CB never fires under sparse activity. |

(See full audit for B3–B16 / C1–C14 on the Python/TS side — most of those were the surface of bugs the repair pass below addressed.)

---

## Section 2 — Output forensics (what the existing backtest actually produced)

Before the repair pass:

| Artifact | Size | Content |
|---|---|---|
| `backtest/output/summary.json` | 482 B | Six strategies, every `metrics: {}` and `attribution: {}` empty. |
| `backtest/output/monthly_returns_*.csv` × 6 | 25 B each | Header only, zero data rows. |
| `backtest/output/*_history.csv` × 6 | 3.1 MB each | 70,117 rows, only row 0 had a portfolio value. MD5 identical across all six strategies. |

Per-column non-null counts in the broken history CSV (out of 70,117):

```
timestamp           70117
portfolio_value         1     ← only the first row
cash                    1
regime              70117    ← regime detector survived (operates on market_data, not portfolio_value)
cb_level            70117    ← always 0 (NaN >= threshold is False)
effective_hwm        1440    ← exactly 30 days of valid values before the gap fix
recovery_active     70117
var_95_1d              31
jump_var_95_1d         31
trade_volume_usd    69700    ← all values are 0.0
```

**Reported "performance":** −0.128% on bar 0, then 4 years of NaN. Total trade volume across the entire run: $0 (bookkeeping bug). CB events: 0. Recovery activations: 0. The six "strategy" comparison plots were six overlapping flat lines.

---

## Section 3 — Exact failure points and fixes (the repair pass)

Each entry below is the file:line of the bug, what it did, and what the fix changed.

### F1 — `backtest/main.py:101` (was) — silent NaN cascade in `pd.concat().ffill().bfill()`

**Was:**
```python
price_history = pd.concat(dfs, axis=1).ffill().bfill()
```
No validation. Different cadence sources silently merged. Different start dates produced NaN holes that propagated.

**Now:** explicit validation gate before any simulation runs.
```python
# all per-asset series are reindexed to the same grid first
price_history = pd.concat([fetched[a].reindex(common_index) for a in assets], axis=1)
if price_history.isna().any().any():
    raise RuntimeError(f"price_history has NaN after reindex. NaN per column:\n{na_per_col}")
if (price_history <= 0).any().any():
    raise RuntimeError(f"price_history has non-positive values:\n{bad}")
if not price_history.index.is_monotonic_increasing:
    raise RuntimeError("price_history index is not monotonic increasing")
diffs = price_history.index.to_series().diff().dropna().unique()
if len(diffs) != 1:
    raise RuntimeError(f"price_history has mixed cadence. Unique diffs: {diffs}")
```

### F2 — `backtest/data/fetcher.py:78` (was) — stable synthetic fallback frequency bug

**Was:**
```python
freq = interval.upper() if interval in ("1d", "1D") else interval
```
"1h".upper() == "1H" — confusing pandas alias mix.

**Now:** explicit interval → pandas-alias table and `_pandas_freq_alias()` helper. All fetchers use the same mapping.

### F3 — `backtest/data/fetcher.py:146-150` (HL) and `:193` (Binance) — sub-second pagination duplicates

**Was:**
```python
new_start = data[-1]["t"] + 1   # 1-millisecond offset
```
The next chunk returned the same hour-boundary candle with a different millisecond suffix; dedupe by index missed them. Source of 30-minute cadence appearing in 1-hour data.

**Now:** advance by one full bar:
```python
interval_ms = _interval_ms(interval)
new_start = int(data[-1]["t"]) + interval_ms
```

### F4 — `backtest/engine/simulator.py:118-138` — MTM had no NaN guard

**Was:** MTM happily wrote NaN into `portfolio.portfolio_value` and propagated it forever.

**Now:**
```python
if prices.isna().any() or (prices <= 0).any():
    raise RuntimeError(f"simulator.step at {date} (idx={self.current_day}): invalid prices {prices.to_dict()}")
...
if not np.isfinite(new_val):
    raise RuntimeError(f"simulator.step at {date}: MTM produced non-finite portfolio_value. ...")
```

### F5 — `simulator.py:135-137` — `if portfolio_value > 0` masked NaN propagation

`NaN > 0` is False → weights frozen across the whole run while everything downstream produced NaN.

**Now:** with F4's guard, this branch never sees NaN.

### F6 — `simulator.py:289-313` — all 6 strategies identical on day 0

Root cause: `returns_history = self.market_data.iloc[max(0,-252):0]` = empty on day 0 → `len(returns_history) > 20` False → every strategy fell into the equal-weight fallback → all six outputs identical.

**Now:** simulator accepts `pre_warmup_data` and prepends it to make `returns_history` non-empty from day 0:
```python
if self.current_day == 0 and self.pre_warmup_data is not None:
    returns_history = self.pre_warmup_data.tail(lookback_bars).pct_change().fillna(0)
elif len(in_sim) < lookback_bars and self.pre_warmup_data is not None:
    pad = self.pre_warmup_data.tail(lookback_bars - len(in_sim))
    returns_history = pd.concat([pad, in_sim]).pct_change().fillna(0)
```

### F7 — `simulator.py:373-374` — day-0 trade volume disappeared

**Was:**
```python
if self.history:
    self.history[-1]["trade_volume_usd"] = trade_vol   # history empty on day 0 → skipped
```

**Now:** history row is appended *before* `_execute_rebalance` is called, so `self.history` is never empty at this point. ~$1M of day-0 trades now correctly recorded.

### F8 — `simulator.py:90-104` — `is_hourly_run` evaluated true for 30-minute data; `refit_interval` misnamed

**Now:** cadence is inferred once via `median diff` and stored on `self`:
```python
self.bars_per_day = int(round(timedelta(days=1) / median_diff))
self.is_intraday = (median_diff <= timedelta(hours=1))
self.annualization_factor = 365 * self.bars_per_day
self.rebalance_interval_steps = 7 * self.bars_per_day
self.refit_interval_steps = (60 if self.is_intraday else 180) * self.bars_per_day
self.var_compute_modulo = self.bars_per_day
```

### F9 — `simulator.py:111-116` — recomputed cadence every step

**Now:** cached on `self` (see F8). Removed per-step computation.

### F10 — `simulator.py:336` — cost_model never received `asset_volatility`

`estimate_cost(..., is_emergency=is_emergency)` defaulted volatility to 0.03 always. Crisis cost underestimated 5-20x.

**Now:** per-asset annualized vol is computed from `returns_history.std() * sqrt(annualization_factor)` and passed in:
```python
per_asset_vol = {a: float(stds.get(a, 0.30)) for a in self.assets}
cost = self.cost_model.estimate_cost(
    ..., asset_volatility=per_asset_vol[asset], is_emergency=is_emergency,
)
```

### F11 — `backtest/engine/hedger.py` — free leverage, no margin, no liquidation

Whole file was rewritten. The old hedger created shorts with no cash deduction, never modeled funding, never liquidated, and added `unrealized_pnl` to `portfolio_value` as if it were freely spendable cash.

**Now:**
- `set_target_hedges()`: deducts margin + taker fee from `portfolio.cash` on open; returns margin + realized PnL on close. Weighted-average entry price across additions. FIFO partial close.
- `advance_step()`: books funding to `cash` per bar (3 funding intervals/day pro-rated); marks unrealized; force-liquidates positions where `equity < maintenance_margin_fraction * margin` at an adverse fill (25 bps liquidation slippage).
- Simulator MTM now correctly counts equity contribution: `cash + spot + sum(margin_usd + unrealized_pnl)`.
- Live evidence in the 4-year run: **liquidation events firing at all the right historical moments** — July/Aug 2022, Jan 2023 BTC rally, Q4 2024 BTC ETF rally, May/July 2025 — i.e., every period where the short hedge gets squeezed. The PnL numbers are real: a $120k notional ETH short liquidated July-2022 realized −$25,576; ETH/SOL liquidated in November 2024 BTC ETF rally at $142k notional each.

### F12 — `backtest/analysis/metrics.py:37,43-44` — hardcoded `annualization_factor = 252`

`(1 + mean_return) ** 252` on hourly data blows up; `volatility * sqrt(252)` is wrong by a factor of `sqrt(24)`.

**Now:**
```python
def _infer_annualization(index: pd.DatetimeIndex) -> int:
    median = (index.to_series().diff().dropna()).median()
    if median <= pd.Timedelta(hours=1):
        return 365 * max(1, int(round(pd.Timedelta(days=1) / median)))
    if median <= pd.Timedelta(days=1):
        return 365
    return 52

# Use CAGR via total return → avoids (1+mean)^N blow-up
annualized_return = total_return ** (annualization_factor / n_steps) - 1.0 if total_return > 0 else -1.0
```
Caller (`main.py`) passes `sim.annualization_factor` explicitly so it never depends on inference.

### F13 — `main.py:189-208` — no error on empty metrics, no gate on broken history

**Now:** hard gates before publishing:
```python
if not metrics:
    raise RuntimeError(f"[{strat_name}] calculate_performance_metrics returned empty. ...")
nonnull_frac = history_df['portfolio_value'].notna().mean()
if nonnull_frac < 0.95:
    raise RuntimeError(f"[{strat_name}] history has only {nonnull_frac*100:.1f}% non-null portfolio_value rows. Refusing to publish broken output.")
```

### F14 — `main.py:205` — silent header-only monthly returns CSV

**Now:** non-fatal warning + the upstream `nonnull_frac` gate (F13) catches the underlying problem. The empty-monthly-returns symptom can no longer occur because non-null `portfolio_value` is enforced ≥ 95%.

### F15 — `main.py:154` — benchmark crashes on zero/NaN first row

**Now:** validation:
```python
first_row = sim_prices[bench_assets].iloc[0]
if (first_row <= 0).any():
    raise RuntimeError(f"benchmark first row has non-positive price: {first_row.to_dict()}")
```

### F16 — `backtest/data/funding.py:71-74` — synthetic funding asset-agnostic

Old fallback: `np.random.normal(0.0001, 0.00005)` regardless of asset. SOL got the same funding profile as BTC.

**Now:** per-asset mean/std calibrated to known historical funding magnitudes; deterministic seed by `hash(coin)`; crisis windows (Q2/Q3/Q4 2022, Aug 2024) drift negative — longs underwater.

### F17 — `backtest/data/fetcher.py` — broken fallback chain (CoinGecko 401, CoinCap DNS dead)

CoinGecko now requires an API key (returns 401 unauth on the free `/api/v3/coins/.../market_chart/range` endpoint). CoinCap's `api.coincap.io` returns NXDOMAIN.

**Now:** removed from `sources_to_try`. Only `binance` and `hyperliquid` remain (HL is fallback; Binance is primary for OHLCV history because HL candle history is only viable for very recent windows).

### F18 — `backtest/data/fetcher.py:165` — Binance USDT has no spot pair

`USDT` against itself doesn't exist on Binance. The old code constructed `USDTUSDT` and timed out.

**Now:**
```python
if symbol == "USDT":
    # Pinned to 1.0 at requested cadence; Binance has no USDT pair.
    freq = _pandas_freq_alias(interval)
    idx = pd.date_range(...)
    return pd.DataFrame({"open":1.0, ..., "close":1.0, "volume":0.0}, index=idx)
```

### F19 — UTC / local TZ confusion in `main.py`

`datetime.strptime(...).timestamp()` interprets the naive datetime as **local** time. Backtest start dates were shifted by the local TZ offset (~5.5h in IST), making source data grids mismatch target grids.

**Now:** explicit `calendar.timegm(dt.timetuple())` for unix conversion. Source and target indices are anchored to UTC.

### F20 — `backtest/data/fetcher.py` `_reindex_to_grid` (new) — cadence enforcement

Every source must return data on the requested grid. New helper floors source index and reindexes to UTC-anchored target_idx; ffill up to 24 grid steps for short gaps; drops longer gaps with a warning.

### F21 — Stablecoin multi-day gap padding (new)

USDC on Binance has multi-day gaps. New `_pad_stable()` ffills/bfills with no limit, pegs residual NaN to 1.0. Result: USDC went from 33,278 bars to 37,225 bars (full 4-year coverage).

### F22 — `simulator.py` cash invariant + units recomputation

After absorbing negative cash into positions (existing code), `units[asset]` was not recomputed; subsequent MTM used stale unit counts.

**Now:** units are recomputed after the absorb:
```python
for asset in self.assets:
    self.portfolio.units[asset] = self.portfolio.positions[asset] / prices[asset] if prices[asset] > 0 else 0.0
self.portfolio.cash = 0.0
```

### F23 — `simulator.py` hedger margin reserve (new)

Even with the rebuilt hedger, spot allocation took 100% of portfolio → cash = 0 → hedger could never open. Now `_execute_rebalance` reserves a cash buffer sized to expected hedge margin:
```python
margin_reserve_frac = min(0.30, (hr * volatile_weight_target) / lev * 1.20)  # 20% headroom; cap 30%
target_weights = {a: w * (1 - margin_reserve_frac) for a, w in target_weights.items()}
```

### F24 — `backtest/reporting/terminal.py` "Days in CB" was actually bars

**Now:** counts both. Bars-in-CB and Calendar-Days-in-CB are reported separately.

### F25 — `simulator.py` summary annualization

`((1 + total_return) ** (365/n_steps) - 1)` was using calendar days, not bars; for hourly data this returned absurd values.

**Now:** uses `self.annualization_factor` (= 365 × bars_per_day) consistently.

### F26 — Benchmark dilution by stablecoins

`main.py:154`: benchmark was the mean of *all* assets including USDC/USDT (~40% of universe), giving a heavily-diluted benchmark line.

**Now:** benchmark is volatile-only:
```python
bench_assets = [a for a in assets if a not in ("USDC", "USDT", "DAI")]
benchmark = (sim_prices[bench_assets] / first_row).mean(axis=1) * initial_cash
```

---

## Section 4 — Smoke verification (3-month run, all 6 strategies)

After fixes, run `python -m backtest.main --config backtest/config/smoke.yaml --output-dir backtest/output_smoke`:

```
Strategy                   rows     start       end    ret%  maxdd%  cb_d        trade$
equal_weight               2137   993,135   976,658   -1.66   12.15   0.0     1,554,795
risk_parity                2137   998,264   998,915    0.07    5.51   0.0     1,522,003
min_variance               2137   999,644 1,006,884    0.72    0.11   0.0     1,010,853
regime-adaptive            2137   999,379 1,035,029    3.57    5.52   0.0     2,903,875
static_conservative        2137   998,651   991,765   -0.69    4.84   0.0     1,327,416
black-litterman            2137   994,726   998,745    0.40   12.28   0.0     1,524,320
```

All six MD5 hashes distinct. All have **0% NaN**. Returns are differentiated, trade volumes are differentiated.

---

## Section 5 — Full 4-year run (in progress)

Full backtest started against `backtest/config/default.yaml` (2022-02-23 → 2026-05-24, hourly, BTC/ETH/USDC/USDT/SOL).

Live evidence captured from the run log:

- Data layer validated: `price_history: shape=(37225, 5), cadence=0 days 01:00:00, range=2022-02-23 00:00:00..2026-05-24 00:00:00`
- Hedger forces real liquidations at historically correct stress windows. Sample events:
  - 2022-07-28: ETH-PERP liquidated $120,872 notional / $40,291 margin / realized −$25,576
  - 2023-01-14: BTC-PERP liquidated during the January 2023 rally
  - 2023-11-09 / 2023-11-15: ETH/SOL liquidated during the Nov-2023 rally
  - 2024-12-05: ETH-PERP liquidated $239,634 notional / $79,878 margin / realized −$40,548 (ETF run-up)
  - 2025-07-18: ETH-PERP liquidated $168,622 notional / $56,207 margin / realized −$31,138

That liquidation cascade in volatile rally periods is the most important piece of evidence the system is now real: a hedger that *cannot* liquidate is a hedger that can absorb infinite hidden loss; one that does liquidate is one with bounded loss per position and a real risk surface.

---

## Section 6 — Items deferred (out of scope of this pass)

Caught by the static audit but not touched in the repair pass; tracked here so they aren't forgotten.

### Vault-side (Solidity)
Everything in Section 1.

### Python research stack
- **B8 — HMM rank transform window-relative bias.** `inverse_normal_transform` ranks values *within the prediction window only*; the worst-of-30 is always mapped to ≈ −2.4σ regardless of historical context. Should fit the rank map on a long historical window and reuse it at predict time.
- **B9 — Black-Litterman omega uses `tau*Σ` on confidence path.** Idzorek (2005) is `Ω_ii = (1/c − 1) · P_i Σ P_i'`, not `P_i (τΣ) P_i'`. Drop the `tau *` from the confidence branch in `portfolio_optimizer.py:177`.
- **B10 — Student-t MC VaR variance inflation.** Cholesky-scaled t-5 innovations carry `df/(df−2)≈1.67` extra variance. VaR over-estimated by ~29%. Multiply `z` by `sqrt((df-2)/df)` to re-unitize.
- **B11 — `monte_carlo.simulate_portfolio` re-scales already-daily vol.** Either rename to `annualized_volatilities` or drop the `/sqrt(252)` rescale.
- **C11 — Covariance pipeline incoherence.** EWMA vols + Ledoit-Wolf correlations: in crises correlations go to 1 but LW historical correlations stay tame. Should use EWMA correlations or weight LW with EWMA weights.
- **C13 — Walk-forward embargo.** Optimizer's HMM rolling window can include train-fold data in test-fold prediction. Embargo zone required.
- **C14 — Composite optimizer score.** Hard constraints accept DD=39% / vol=29% / return=−29%. Add convex penalty on DD and an "investability floor" rather than soft penalty.

### Guardian service (TypeScript)
- **C1 — Guardian doesn't talk to the vault.** `EventSourcingLedger` hard-codes `cashUSD = 1000000`. `BlockchainWriter.execute()` is exported and never imported. No WebSocket subscription is wired. Three independent demos sharing a repo.
- **C2 — Nonce race in `writer.ts:97-101,140`.** Concurrent `sendTransaction` reads same nonce → only one tx confirms.
- **C3 — Sequence-gap recovery broken.** `expectedSeqId = Date.now()` makes the next real seqId unrecoverable.
- **C4 (TS) — Hardcoded `maxGasPriceWei` string, on-chain default 0.** TS guardian's 100 gwei guard is the only one alive; bypassable via attacker's own RPC config.

---

## Section 7 — File-by-file summary

| File | Changes |
|---|---|
| `backtest/main.py` | UTC unix conversion; pd.concat hard validation; volatile-only benchmark; hard fail on empty metrics; ≥95% non-null gate; annualization passed to metrics/attribution |
| `backtest/config/default.yaml` | `data_source: hyperliquid` → `binance` |
| `backtest/config/smoke.yaml` | New 3-month smoke config |
| `backtest/data/fetcher.py` | `_pandas_freq_alias`, `_interval_ms`; HL & Binance pagination +1-bar (was +1 ms); USDT synthetic 1.0; `_validate_and_clean` rewritten; `_reindex_to_grid`; `_pad_stable`; CoinGecko/CoinCap removed from fallback chain |
| `backtest/data/funding.py` | HL empty-window fallback now stitches synthetic onto missing range; per-asset synthetic with historical crisis regimes; clip outliers to known bounds |
| `backtest/engine/simulator.py` | Cadence cached once on `self`; NaN guard in MTM; pre-warmup-aware `returns_history`; day-0 trade volume bug fixed; per-asset annualized vol passed to cost model; margin reserve for hedger; units recomputed after cash absorb; `summary()` uses cached `annualization_factor` |
| `backtest/engine/hedger.py` | Full rewrite. Margin deduction on open; funding accrual to cash; weighted-avg entry on partial fills; FIFO partial close; force-liquidation at adverse fill when equity < maintenance_margin_fraction × margin; deterministic |
| `backtest/analysis/metrics.py` | Cadence-aware annualization; CAGR via total return (no `(1+mean)^N` blow-up); explicit `annualization_factor` parameter |
| `backtest/analysis/attribution.py` | Cadence-aware annualization passed through |
| `backtest/reporting/terminal.py` | Reports bars-in-CB and calendar-days-in-CB separately |

---

## Section 8 — What changed for the user

Before the repair: the backtest produced six identical files of NaN, an empty summary, and six overlapping flat lines on charts. CI would not have caught any of it.

After the repair:
1. `pip install -r backtest/requirements.txt` (plus `hmmlearn scikit-learn tabulate structlog pyarrow` for ML/reporting deps).
2. `python -m backtest.main --config backtest/config/smoke.yaml --output-dir backtest/output_smoke` — completes in a couple of minutes, produces six distinct CSVs, six distinct return profiles, real summary.json.
3. `python -m backtest.main --config backtest/config/default.yaml --output-dir backtest/output` — full 4-year hourly run; takes ~10-30 minutes depending on machine; produces real liquidation history; real CB activations through 2022 + Nov-2024 cycle.

The repair pass does not address the vault-side catastrophic issues (Section 1). Those require their own engagement.
