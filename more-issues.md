---
Aladdin / Hyperliquid Vault — Deep Audit

TL;DR — the hard truth

The README presents a coherent three-tier system. The code is three disconnected silos with a polished theoretical interior and a non-functional exterior. The on-chain vault is unusable in its current state
(division-by-zero on every trade), the off-chain guardian doesn't actually talk to the vault, the backtest engine quietly underestimates crisis costs by ~one order of magnitude, and several "fixes" in comments
(BUG-5-04, FIX 11, etc.) introduce new bugs in the opposite direction. The math layer (covariance, VaR, MC) is the best-built piece, but it sits on top of unrealistic execution assumptions that make every
published backtest result inflated.

You don't have a working vault. You have a research prototype with vault-shaped scaffolding.
---

A. Catastrophic — system cannot operate as-is

A1. AssetRegistry.getPortfolioSnapshot() returns empty / zero

contracts/src/core/AssetRegistry.sol:96-102:
AssetSnapshot[] memory assets = new AssetSnapshot[](0);
return SnapshotData({ assets: assets, totalPortfolioUSD: 0 });
Every consumer of this is broken. TreasuryVault.\_updatePortfolioSnapshot always writes totalValueUSD = 0. SecurityHooks.validate then divides by totalPortfolioUSD in six different places (Rule 7, 8, 11, 12 and
recovery). Every trade reverts on division by zero.

Fix: the registry must enumerate \_activeTokens, pull each asset's freeBalance from the vault and price from the oracle, classify by tier, and aggregate. There is no shortcut — the function is a stub, not "to
be wired later".

A2. \_updatePortfolioSnapshot is onlyRole(KEEPER_ROLE) but is called from a GUARDIAN_ROLE function

TreasuryVault.sol:282 decorates \_updatePortfolioSnapshot with onlyRole(KEEPER_ROLE), and executeBatchActions calls it directly at line 279 while running as guardian. Every batch action reverts at the snapshot
step. And executeSwap never calls it at all, so single swaps never refresh snapshots.

Fix: make the function internal (no modifier) and expose an external onlyRole(KEEPER_ROLE) wrapper for keepers. Call the internal version unconditionally at the tail of executeSwap and executeBatchActions.

A3. Router whitelist commented out — guardian compromise = full drain

TreasuryVault.sol:170:
// require($.whitelistedRouters[router], "Vault\_\_NotWhitelisted"); // ignoring for mock
Then line 195 grants safeIncreaseAllowance to that arbitrary router. A guardian-key compromise lets the attacker encode their own contract as router, get an approval, and transferFrom the entire balance.
executeBatchActions has the same bug at a.target. The "MAX_TRADE_USD $500k" cap doesn't help once the approval is granted — the attacker pulls amountIn and returns garbage.

Worse: no allowance reset after the call. Approvals stack on each invocation.

Fix: uncomment the whitelist; revert if !$.whitelistedRouters[router]. After the external call, set allowance back to 0 (safeApprove(router, 0) after safeApprove(router, amountIn)). Better: use Permit2 and
never grant standing allowances.

A4. OracleAdapter.getPrice (view) is the function SecurityHooks calls — and it always returns PriceStatus.GOOD

OracleAdapter.sol:229-243:
return PriceData({ ..., status: PriceStatus.GOOD, ... });
The real oracle resolution lives in resolvePrice (state-mutating), which SecurityHooks.validate (view) can't call. So the entire "Rule 1: oracle status gate" in SecurityHooks is dead code. STALE / SUSPECT /
DEGRADED never fire. A token whose feeds have never been resolved returns lastGoodPrice = 0 with status GOOD — divisions by zero return non-determinism (in 0.8.x divisions of 0 by 0 revert with Panic(0x12)).

Fix: SecurityHooks must read price freshness via a view that reflects the latest resolvePrice execution (i.e., a getPriceUnsafe that exposes the actual status from \_states[token]), OR the vault must call
resolvePrice first and pass the result into validate. The current architecture (validate is view, resolve is non-view) is fundamentally incompatible.

A5. OracleAdapter.setFeeds has no access control

OracleAdapter.sol:63:
function setFeeds(address token, address chainlinkFeed, address pythContract, bytes32 pythFeedId) external {
Anyone can replace any token's Chainlink and Pyth feeds with their own malicious contracts. Total loss-of-funds vulnerability — the whole price layer is overwritable by any EOA.

Fix: onlyRole(GOVERNOR_ROLE). This is a one-line catastrophe.

A6. withdraw bypasses every control

TreasuryVault.sol:138-149. No CB check, no SecurityHooks call, no timelock, no role gate, no large-withdrawal threshold check despite largeWithdrawalThreshold and withdrawalTimelockSeconds being declared as
storage. The withdrawalQueue mapping is dead state. Anyone with a balance can drain immediately even while the vault is circuit-broken.

Fix: route withdrawals through SecurityHooks (ActionType.WITHDRAWAL — the path exists in recordAction line 215 and Rule 12). For above-threshold withdrawals, write to the queue and return a non-zero requestId;
require a separate claimWithdrawal(requestId) after withdrawalTimelockSeconds.

A7. UUPS upgrade has no timelock, no multisig

TreasuryVault.sol:121:
function \_authorizeUpgrade(address newImplementation) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}
Single key, zero delay. Any DEFAULT_ADMIN compromise = drop in a malicious implementation that empties the vault next block. For a treasury contract this is the single biggest unforced security error.

Fix: require \_authorizeUpgrade to consult a timelock contract (e.g., OZ TimelockController with 48h minimum delay) and gate DEFAULT_ADMIN_ROLE behind a 2-of-N multisig.

---

B. Major — silent correctness failures

B1. Solidity HWM decay and Python HWM decay compute different functions

This is the single most important quant-engineering bug because the spec claims the on-chain CB mirrors the off-chain CB, and they don't.

Python (backtest/engine/circuit_breaker.py:79-92):
HWM_eff = current + (HWM_abs - current) \* 0.5^(days/halflife), only after 30d grace
Decays the gap toward current_value. Asymptote: current_value. Bounded above by HWM, below by current.

Solidity (TreasuryVault.sol:343-358):
decayedValue = hwmAbsolute >> halflives ; minus linear-interp remainder
Decays the HWM itself toward 0. Asymptote: 0. No 30-day grace.

After ~5 halflives (~450 days with default 90d), on-chain hwmEffective == 0, and \_checkCircuitBreaker has if (currentValue < $.hwmEffective && $.hwmEffective > 0) — the second clause short-circuits. HWM-drop
branch of the CB silently stops triggering. Window-drop branch still works, so this is hard to notice unless you stare at it.

Fix: in Solidity, compute decayedGap = gap >> halflives (with linear remainder), then effective = current + decayedGap. Use fixed-point math; clamp gap to 0 below 30 days elapsed.

B2. Solidity CB level decay only checks "1 day passed"

TreasuryVault.sol:360-367:
function decayCBLevel() external onlyRole(GUARDIAN_ROLE) {
if (block.timestamp < $.cbLevelSetTimestamp + 1 days) revert ...
$.currentCBLevel--;
}  
 That's it. The README and Python implementation say decay requires (vol normalized) OR (N stable days at level) OR (60d forced). On-chain there is no vol check, no stable-day check (the cbConsecutiveStableDays
accumulator is written but never read), no level-specific gating. A guardian can just spam decayCBLevel() every day to peel a level off regardless of recovery.

Worse, the recovery phase on-chain (SecurityHooks.setRecoveryPhase) accepts any recoveryMaxVolatileBps the guardian passes — the recovery curve (10%→20%→35%) isn't enforced. A guardian sets it to 10000
immediately on L2→L1 transition and the entire graduated re-entry doctrine is bypassed.

Fix: port the actual Python decay logic to Solidity (vol ratio passed in via IOracleAdapter or a separate volatility feed; track levelEnteredAt, cbNoFurtherDropSince properly with consecutive-day counter;
level-specific thresholds: L1=7d, L2=14d, L3=7d-after-vol-normalizes). Make recovery a state machine, not a guardian-set value.

B3. "Crisis funding earns yield" sign error

backtest/engine/yield_engine.py:14-18:
FUNDING_RATES = {
"bull": 0.0003,
"uncertain": 0.0001,
"crisis": 0.0004, # comment: "Earn funding in crisis (BUG-5-04 fix)"
}  
 Empirically: during crashes (Mar 2020, May 2021, Nov 2022) perp funding goes deeply negative (longs underwater paying shorts to maintain). The "fix" assumes positive funding, then multiplies by -direction: for
a long hedge this yields negative payment (cost). Comment intent ("earn funding when short during crisis") is right; sign is wrong.

Fix: FUNDING_RATES["crisis"] = -0.0010 (-10 bps/8h, ~-110% annualized, consistent with cascade liquidation periods). And — gate this on the actual hedge direction.

B4. Hedger "fix" creates free leverage and unbounded loss

backtest/engine/simulator.py:215-249. The MTM block (line 126-131) marks the unrealized PnL of derivative positions and adds it to portfolio_value directly. But:

- Opening a hedge (line 239-249) doesn't deduct margin_usd from cash.
- Funding payments aren't booked to cash.
- Liquidation isn't modeled. A short BTC hedge during a 100% rally yields unrealized_pnl = -notional. Portfolio_value goes negative but no margin call ever triggers.

Combined effect: the system can open arbitrary short notional at zero capital cost, and lose unbounded amounts that ride only through portfolio_value — never affecting cash. Then cost_model.estimate_cost is
fed is_emergency=False for hedge actions, so crisis cost is underapplied to the most-likely-to-be-toxic flow.

Fix: on open, cash -= margin_usd. On close/adjust, settle PnL into cash and release margin. On every step, check cash_collateral + unrealized_pnl > maintenance_margin (e.g., 50% of margin_usd); if not,
force-liquidate at adverse fill ratio. Stop counting unrealized_pnl as freely-spendable portfolio value.

B5. Backtest crisis cost is silently default-vol

simulator.py:331-336:
book*depth = self.depth_by_asset.get(asset, 5_000_000)
vol_haircut = max(0.1, 1.0 - 5.0 * rolling*vol)
effective_depth = book_depth * vol_haircut
cost = self.cost_model.estimate_cost(trade_size, asset, direction, effective_depth, 1e7, is_emergency=is_emergency)
estimate_cost accepts asset_volatility (default 0.03). The simulator never passes the current vol. So vol_multiplier = max(1.0, 0.03/0.03) \* direction_multiplier = 1.0 or 1.3. The "emergency adverse impact"
multiplier collapses to a factor of 1.3 regardless of actual realized vol. Crisis slippage is underestimated 5-20x depending on regime. This single bug makes every backtest result misleading.

Fix: pass asset_volatility=rolling_vol_for_asset (compute per-asset from returns_history).

B6. Look-ahead via close-of-day rebalancing

simulator.py:107-108: prices = self.market_data.iloc[self.current_day]. Then MTM uses today's close, then rebalances against today's close prices. Real systems can't execute at close — they execute at
next-open (or VWAP, or current bid/ask intra-day). Sharpe and drawdown are systematically optimistic.

Fix: offset execution by one bar — MTM at iloc[d], rebalance against iloc[d+1] (with the d→d+1 gap return as a tax). Or model the open price separately if you have OHLC.

B7. HMM regime detector: documented parameters don't match code

README says: n_fits=20, n_iter=500. Code (regime_detector.py:30-39): n_fits: int = 2, n_iter=30 in the GaussianHMM(...) call. With 2 random seeds and 30 iterations on noisy crypto returns and a 3-state
full-covariance HMM, you will frequently end up in a degenerate local optimum. The robustness story is theater.

Fix: restore n_fits=20 and n_iter=500 (or accept the speed/quality tradeoff and update README to reality). Add convergence-monitor logging.

B8. HMM rank-transform leaks period statistics into regime classification

regime_detector.py:10-24 — inverse_normal_transform ranks values within the input window only. At predict time you pass crypto_idx.tail(30). The "worst-in-30-days" always gets mapped to Φ⁻¹(0.001) ≈ -3.09,
regardless of whether the actual return was -0.5% (calm market) or -10% (crash). A benign-but-relatively-bad day looks like a crisis. Regime probabilities are window-relative, not absolute. This single design
choice quietly defeats the purpose of regime detection on small windows.

Fix: fit the rank map on a long historical window (e.g., 504d), then apply that fixed CDF to incoming predictions — don't re-rank within each call. Concretely, store empirical CDF (or percentile-spline) at fit
time; at predict use transform_with_fixed_cdf.

B9. BL omega uses tau\*Σ on confidence path (Idzorek is Σ)

portfolio*optimizer.py:177:
omega_diag[i] = ((1.0 / max(view.confidence, 0.01)) - 1.0) * float(P[i] @ (tau \_ covariance) @ P[i].T)
Idzorek (2005): Ω_ii = (1/c − 1) · P_i Σ P_i', not P_i (τΣ) P_i'. Using τΣ (τ≈0.05) makes Ω 20× smaller for given confidence → the optimizer trusts the views ~20× more than the user specified.

Fix: drop the tau \* from the confidence branch. Keep the no-confidence branch (P (τΣ) P') — that one is Meucci-standard.

B10. Student-t MC VaR is variance-inflated by sqrt(df/(df-2))

var_models.py:42-43 and monte_carlo.py:46-48: scaling raw t_5 innovations by Cholesky of correlation doesn't normalize for t's intrinsic variance df/(df−2) = 5/3 ≈ 1.67. Resulting portfolio shocks have ~29%
inflated standard deviation. VaR is high by ~29%, CVaR more. The MC says you'll lose more than you actually will under a t-5 model.

Fix: multiply z by sqrt((df-2)/df) to re-unitize. Or use scipy.stats.multivariate_t directly.

B11. monte_carlo.simulate_portfolio re-scales already-daily vol to "more-daily"

monte_carlo.py:33: daily_sigma = volatilities / np.sqrt(252.0). The docstring says input is "daily vols from GARCH". If the caller passes daily vols, this divides them again. Either the docstring is wrong
(input is annualized) or the math is wrong. Given how the simulator wires this — it doesn't, this function isn't called from the live path — this lurks until someone uses it.

Fix: rename parameter to annualized_volatilities and update callers, or drop the rescale and trust the docstring.

B12. Recovery week counter inflates on hourly runs

simulator.py:178-182:
days_in = (pd.Timestamp(date) - self.recovery.entry_date).days # floors to int days
elif days_in > 0 and days_in % 7 == 0:
self.recovery.advance_week()
For hourly steps, days_in is constant across 24 consecutive calls, and the modulo condition holds for all 24 each "week boundary day". weeks_in_recovery jumps by 24 per actual week → recovery exits in ~2
actual days. The max_volatile_pct curve compresses; the entire recovery doctrine collapses.

Fix: track via timestamp diffs: weeks_in_recovery = days_in // 7, no incrementer. Or guard with last_advance_date != date.

B13. Optimizers swallow non-convergence; strategies use the result anyway

portfolio_optimizer.py:61-67 returns the initial w0 with converged=False, method='...\_failed'. Strategy classes (strategies.py:127-129) read res.weights without checking res.converged. Silent fallback to
uniform weights, no log, no metric, no alert.

Fix: strategy code must assert res.converged and either retry with relaxed bounds, log a warning and increment a failure counter, or escalate. Treat non-convergence as a real event.

B14. Risk-parity crisis stable-floor is infeasible → silently violated

strategies.py:116-127: min_stable = 0.60 in crisis, bounds = (0.0, 0.35) per asset. With one stable asset, max stable = 0.35 < required 0.60 → infeasible → SLSQP fails → falls back to inverse-vol, which
ignores min_total. Crisis floor never enforced.

Fix: lift the per-asset bound to max(0.35, min_stable) when stable is the only stable, or expand stables. Better: validate feasibility before SLSQP and abort with a clear message.

B15. cb.update uses last_peak_time but updates it only when current_value > hwm_absolute

circuit_breaker.py:104-110: last_peak_time only advances when a new peak prints. Fine. But days_since_hwm = (current_time - last_peak_time).days underflows to negative integers if last_peak_time is None
(handled via ternary). What's missing: if you re-enter a higher regime briefly, last_peak_time updates, the grace period restarts, HWM decay restarts from the new peak even if the new peak was a spike. A short
pop above the prior HWM resets the entire decay clock. Combine with B12 and the recovery system becomes path-dependent in non-obvious ways.

Fix: Use a windowed-max with min-dwell-time before HWM updates (e.g., "new HWM only if value > prior HWM for >24h"), or smooth with EWMA.

B16. compute_effective_hwm returns current_value when current_value >= hwm_absolute — but this masks decay

Line 85-86: if current_value >= hwm_absolute: return current_value. Combined with the caller updating hwm_absolute to current, the effective HWM jumps up at every new high, then immediately decays. Symmetry is
broken — peaks count instantly, drops are debounced by 30 days. CB triggers on a tiny dip after a peak rally even though portfolio value just made an all-time-high last week.

Fix: debounce the peak update with a 1-day cooldown to match the asymmetry of the decay direction, or use HWM as the running max over the last K days rather than all-time-max.

---

C. Architectural truths (the things AI auto-praise tools miss)

C1. The guardian doesn't talk to the vault

guardian-service/src/index.ts and guardian-service/src/blockchain/writer.ts are in the same repo and reference no shared types. BlockchainWriter.execute() is exported and never imported anywhere else in the
codebase. The "main orchestrator" guardianTick logs [EXECUTED] and sleeps — it never builds calldata, never signs, never broadcasts. The "EventSourcingLedger" starts with hardcoded cashUSD = new
Decimal(1000000) and is never reconciled against on-chain state. There is no WebSocket subscription wired up.

This isn't "almost-finished". The integration tier doesn't exist. The system is three demos.

Fix: decide first whether the guardian is the executor (in which case implement: WS subscription → state diff → action plan → calldata builder → BlockchainWriter → on-chain confirmation → reconcile) or just an
observer that proposes actions for human/multisig execution. Don't pretend it's both.

C2. The TS guardian has a fatal nonce race

writer.ts:97-101, 140: sendTransaction increments this.nonceTracker after the await. The orchestrator submits actions via Promise.all(actions.map(...)) (index.ts:350-382). Concurrent sends read the same nonce
→ at most one tx confirms; others fail with nonce too low.

Fix: serialize signing in a queue per-address; or use the noncemanager pattern (preassign nonces from a mutex-protected counter atomically).

C3. "Sequence gap recovery" is broken-by-design

index.ts:162-172:
this.expectedSeqId = Date.now();
Setting expected seq to a timestamp guarantees the next real seqId (a counter) won't match. The ledger gets stuck in invalidated state permanently after one gap.

Fix: on recovery, fetch the authoritative seqId from a REST snapshot endpoint and resume from there.

C4. Daily-volume cap and gas cap split-brain

On-chain maxDailyVolumeUSD initializes to $2M (TreasuryVault.sol:118). But SecurityHooks.MAX_DAILY_VOLUME_USD is a separate constant at $2M (line 20) used in validate. The vault's storage value isn't read by
the hooks. Change one, the other doesn't change. Same for maxSlippageBps, maxTradeUSD. Two sources of truth for the same constants.

Also $.maxGasPriceWei = 0 after initialize() (never set). The gas guard at executeSwap:164 (if($.maxGasPriceWei > 0 && ...)) is permanently disabled. Only the TS guardian's 100 gwei guard fires — and that's
bypassable with any RPC config.

Fix: delete the duplicated constants in SecurityHooks; read them via getter calls to the vault. Set maxGasPriceWei in initialize.

C5. whitelistedStrategies and whitelistedRouters are write-only state

Mappings are declared in TreasuryVault storage; setters exist via implicit governor controls; no reader ever checks them. SecurityHooks doesn't, executeSwap doesn't, executeBatchActions doesn't. The "strategy
whitelisting" claim is unfounded.

C6. StrategyManager is a ledger that doesn't move tokens

StrategyManager.deposit/withdraw only update accounting (capitalDeployed, capitalReturned). No IERC20 transfers. The vault's executeBatchActions STRATEGY_DEPOSIT branch is literally // For mock completeness,
other actions just pass. Strategies don't receive funds. The fancy PerpHedgingStrategy is unreachable from the vault flow.

C7. pause boolean has no setter

paused is checked in deposit/withdraw/executeSwap/executeBatchActions but no pause() or unpause() admin function exists in TreasuryVault.sol. EMERGENCY_ROLE is defined and never used. The pause kill-switch
doesn't exist.

Fix: add function pause() external onlyRole(EMERGENCY_ROLE) { \_getTreasuryVaultStorage().paused = true; } and an unpause behind GOVERNOR + timelock.

C8. Snapshot ring buffer can be starved

\_checkCircuitBreaker walks backwards through snapshots looking for one with timestamp <= now - cbWindowSeconds. If snapshots only update on batch actions (and they don't update on single swaps — see A2), then
a vault with sparse activity has snapshots all clustered together. The loop falls off the end and refValue stays equal to currentValue → windowDropBps = 0 → window-based CB never triggers. Only HWM-based
triggers (subject to B1) catch drops. An attacker can intentionally starve snapshots to manipulate CB sensitivity.

Fix: require a KEEPER to call updateSnapshot on a heartbeat (independent of trades). On-chain force snapshot at the head of every action that bypasses snapshot-on-write.

C9. Funding data is synthetic in the default path despite "real" wiring

backtest/data/funding.py is the only place real HL funding is fetched. simulator.py:56: self.yield_engine = yield_engine or YieldEngine(). YieldEngine() defaults to empty funding_series → falls back to the
regime dict at yield_engine.py:14-18 (which has the sign error from B3). Default backtest/main.py invocation never constructs a YieldEngine with real funding data. The "real funding" claim is not delivered by
the default code path.

Also funding.py's synthetic fallback returns the result without flagging it. The DataFrame has no synthetic=True column.

C10. Binance spot ≠ HL perp — basis is unmodeled

README says it. Then hl_reality_check.py exists as a separate standalone — its only role appears to be "we ran some live numbers once". Nothing in the simulator path actually uses Binance/HL basis. Backtests
on Binance spot are not telling you what an HL perp basis trade does. All Sharpe/Calmar numbers from the optimizer are upper bounds, not estimates.

C11. The "robust covariance pipeline" is two stages bolted together with a kludge

covariance.py:67-78. EWMA covariance is computed; Ledoit-Wolf is computed on raw (unweighted) returns; LW's diagonal is rescaled to match EWMA stds while keeping LW's correlations. The off-diagonal LW
correlations are unweighted-historical, the diagonals are EWMA-recent. The two pieces are inconsistent — recent vol spikes aren't reflected in correlation tightening. Real crises show correlations going to 1,
but this pipeline keeps the LW historical correlation.

Also marchenko_pastur_denoise returns corr unchanged when t <= n (silent passthrough). For small-window refits this kicks in undetectably.

Fix: use an EWMA correlation matrix (e.g., RiskMetrics IGARCH) instead of LW correlations, OR compute LW shrinkage with EWMA-weighted observations (LedoitWolf(...).fit(weighted_returns)).

C12. inverse_normal_transform is applied at predict-time using window-local ranks

See B8. This is the single biggest reason regime calls feel "twitchy" — predictions are not about absolute return magnitude, they're about rank within the window you happened to pass. Pass tail(30) and the
worst-of-30 is always a tail event. Pass tail(300) and the same return is mid-distribution.

C13. The walk-forward optimizer is purging without separating fit and signal scaling

backtest/optimizer/walk_forward.py (looking at sizes 2.4KB) is small enough that I'd bet it doesn't purge embargo between train/test for HMM refit windows. The HMM's rolling 504d window in test fold can
include observations from the train fold (since each fold's 60d HMM warmup reaches backwards). Real walk-forward needs an embargo zone.

C14. The composite score lets bad strategies pass

Per README, 20% Sharpe, 15% Sortino, 10% Calmar, 25% DD penalty, 15% vol penalty, 10% CB days, 5% cost drag. Hard constraints: DD < 40%, vol < 30%, total return > -30%, cost drag < 5%/yr. A strategy with 39%
DD, 29% vol, -29% return passes all hard constraints. The composite weights then dominate — a high-Sharpe, high-drag strategy can score well even at near-ruin metrics. Composite scoring without absolute
drawdown convexity rewards Sharpe-pumping with tail risk.

Fix: convex penalty on DD (quadratic past 20%), and zero score below a "investability floor" rather than soft penalty.

---

D. Hard truths AI tools usually miss when reviewing code like this

1. The README's risk story is the system's marketing, not its code. Every check it advertises (router whitelist, oracle staleness, strategy concentration, withdrawal timelock, gas cap, recovery curve
   enforcement) is either disabled, dead, or unreached in the actual call graph. The discipline gap between docs and implementation is the loudest signal here.
2. You don't have a CB. You have an off-chain CB and a different on-chain CB. They share a name and a README diagram. They use different decay laws (B1), different threshold mechanics (B2), and never
   reconcile. Whichever one fires first wins by accident.
3. The "robustness" stack is sequential but not coherent. EWMA + LW + RMT + PSD is four steps glued together, each a defensible technique, but the rescale at step 2.5 (vol-only EWMA, correlation-only LW) means
   the math doesn't add up to "robust". It adds up to "calmer than LW but less reactive than EWMA". For a crypto regime detector that's a real cost.
4. Hardcoded $500k starting cash in EventSourcingLedger is not a placeholder. It's a sign no one has wired the guardian to a real account. Same with cb.config = CircuitBreakerConfig() — default-only
   construction throughout. Look at every **init** and ask: where do these values come from in production? Mostly: nowhere.
5. The backtest is optimistic by construction, not by chance. Close-of-day execution (B6), default-vol cost model (B5), free leverage hedger (B4), synthetic funding (C9), spot-not-perp prices (C10), look-ahead
   in BL equilibrium computation (it uses MARKET_CAP_PRIORS which are time-invariant top-down truths the original investor couldn't have known historically). Stack four optimisms and your Sharpe of 1.8 is
   probably 0.6 in reality.
6. The on-chain math is not gas-aware. \_checkCircuitBreaker loops up to 720 snapshots. validate (called every action) sums a 200-element outflow buffer. These are cheap individually but compound across
   actions. On HyperEVM that's tolerable; on mainnet it would be untenable. The system was designed without a strict gas budget per action, which makes adding more checks (e.g., HHI concentration mentioned but
   skipped on line 193) increasingly hard.
7. There is no replay-attack and no MEV defense beyond the gas cap. The vault on HyperEVM may inherit the sequencer's anti-MEV, but the design as written assumes it. Move it to any rollup or L1 without a
   private mempool and the rebalance flows are sandwich-bait.
8. The whole upgrade path is unforced. UUPS without a timelock with single-signer admin makes A1–C12 fixable in one txn — but also makes any compromise unrecoverable. Treasury contracts of this size require
   boring multisig+timelock plumbing. The lack of it is the most damning architectural choice.
9. You have no integration test for the live system. Tests under backtest/tests/ exist for data integrity. There is no Foundry test for SecurityHooks calling against a real OracleAdapter calling against a real
   AssetRegistry. The bugs A1–A4 above would be caught by literally one happy-path forge test.

---

E. Minimum-viable absolute fix order

If you only do six things in order, do these:

1. Implement AssetRegistry.getPortfolioSnapshot properly — actual asset enumeration. Without this nothing else works.
2. Add onlyRole to OracleAdapter.setFeeds — one line, ends the live "anyone can repoint feeds" hole.
3. Move \_updatePortfolioSnapshot access control (internal-callable from any vault action) and call it from executeSwap too.
4. Uncomment the router whitelist + reset allowance to 0 after external call. Defeats guardian-key drain.
5. Either gate withdraw through SecurityHooks + timelock, or delete the queue/timelock storage and own the "instant withdraw" stance honestly. Don't have both.
6. Port the real Python CB decay logic to Solidity (gap-decay HWM, vol-aware level decay, recovery curve enforced on-chain). Make the two CBs identical, not "spiritually similar".

Below this line the backtest fidelity work begins (B3–B16). That's a separate, longer engagement and doesn't affect whether the vault can be deployed.

---

If you want, I can implement any of A1–A7 as concrete patches — those are the only ones where the diff is short and the reasoning is settled. The B-tier fixes need decisions about the strategy model (e.g., how
aggressively to enforce the stable floor, whether to model liquidation) before code lands.
