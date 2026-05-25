# Aladdin Design Patterns & Microstructure Architecture

**Date:** 2026-05-25
**Scope:** Architectural Post-Mortem & HFT Realism Guidelines

The previous iteration of the Aladdin system utilized institutional portfolio optimization vocabulary but lacked the actual market microstructure mechanics required to survive on Hyperliquid's CLOB (Central Limit Order Book). The system treated execution as an abstraction, resulting in fake alpha and latency vulnerability. 

This document defines the strictly enforced design patterns that the Aladdin system must follow going forward to ensure survivability in high-frequency trading (HFT) and automated market-making environments.

---

## 1. Event-Sourced State Reconciliation (No Shadow State)

### The Mistake:
The system reconstructed a "shadow portfolio" internally (`PortfolioState.reconstruct(onChainState)`) using HTTP polling and unverified assumptions about order fills. When websockets stalled or dropped packets, the internal state desynchronized from the actual exchange state, leaving the bot blind while holding massive directional risk.

### The Design Pattern:
**Deterministic Event Sourcing & Exchange Truth Layer**
- The exchange is the absolute source of truth. Internal state is only a projection of strictly ordered execution ACKs.
- **Implementation:**
  - Connect to Hyperliquid via WebSocket for real-time L2 orderbook updates and user-data streams.
  - Implement a `Sequence ID Validator`: If an event ID skips (e.g., `seq_id = 45` then `seq_id = 47`), the system must immediately halt trading, drop existing state, and trigger a full REST API snapshot recovery before processing any new signals.
  - No order is marked as "filled" until an explicit `order_fill` WS event is processed and cross-referenced with the exchange's ledger.

---

## 2. Microstructure-Aware Execution (Toxicity & Queue Estimation)

### The Mistake:
The execution engine used static slippage caps (`maxSlippageBps = 100`) and naive routing (`'immediate'`). It posted passive liquidity without recognizing adverse selection. The bot acted as toxic exit liquidity, filling only when informed flow (smart money) dumped into its bids right before a price collapse.

### The Design Pattern:
**Queue Aging & Fill Toxicity Estimation**
- Maker orders are mathematically losing unless adverse selection is accurately modeled.
- **Implementation:**
  - **Volume-Synchronized Probability of Informed Trading (VPIN):** Estimate order book imbalance before quoting. If the sell-side imbalance is extreme, withdraw bids.
  - **Queue Position Simulator:** Track resting liquidity ahead of our orders. If the queue ahead vanishes rapidly without prints (orders cancelled), assume an incoming toxic wave and cancel passive quotes.
  - **Post-Fill Drift Modeling:** Measure expected edge not just at the fill price, but at $T+500ms$ and $T+5s$. If expected drift consumes the Maker rebate, the trade is rejected entirely.

---

## 3. Fixed-Point Arithmetic Precision

### The Mistake:
The TypeScript layer heavily relied on native JS floating-point numbers (`number` / `amountUSD`). In leveraged finance, IEEE 754 float rounding errors compound rapidly during PnL accounting, risk parity sizing, and margin fraction calculations, leading to accidental liquidations.

### The Design Pattern:
**Strict Decimal/BigNumber Boundaries**
- Float math is banned for all value, price, and margin computations.
- **Implementation:**
  - Utilize `decimal.js` or `bignumber.js` for every monetary variable inside the `guardian-service`.
  - All comparisons (`>`, `<`, `==`) must use the Decimal API methods (`.gt()`, `.lt()`, `.eq()`).
  - E.g., `new Decimal(price).mul(size).sub(fees)` instead of `price * size - fees`.

---

## 4. Concurrent Risk-Prioritized Execution

### The Mistake:
Execution was serialized (`for (const action of actions) await execute(action)`). In fast-moving markets, sequential looping causes latency compounding. If the first trade stalls, the subsequent hedges are delayed, exposing the portfolio to unhedged directional liquidation cascades.

### The Design Pattern:
**Execution DAG (Directed Acyclic Graph) & Promise Concurrency**
- Trading operations must be batched, parallelized, and mapped to a risk-priority scheduler.
- **Implementation:**
  - Risk-reducing orders (closing positions, adding margin, hedging delta) are executed with `Priority 0` (immediately and concurrently via `Promise.all()`).
  - Risk-seeking orders (opening new exposure) are executed at `Priority 1` and only *after* all `Priority 0` ACKs are received.

---

## 5. Fail-Fast Error Domains (No Pokemon Catches)

### The Mistake:
Broad `catch (err) { logger.error(...) }` blocks were used throughout the transaction loop. If a cancel failed, the loop logged the error and continued as if it succeeded, creating phantom inventory and double-exposure.

### The Design Pattern:
**Strict Error Classification & Fatal State Transitions**
- A trading engine cannot "ignore" a failure. Ambiguity in state is fatal.
- **Implementation:**
  - Separate errors into `Transient` (e.g., rate limit hit, wait 50ms and retry) and `Fatal` (e.g., partial fill unconfirmed, websocket timeout).
  - If a `Fatal` error occurs, the Guardian enters `SHUTDOWN` state, issues an emergency `CANCEL_ALL` via a redundant REST endpoint, and pages the human operator. It does not attempt to continue trading while blind.

---

## 6. Distributed Nonce & Replacement Management

### The Mistake:
Local nonce tracking (`Map<string, number>`) was used. Under network partitions, process restarts, or mempool re-orgs, the local cache desyncs from the chain. One stuck nonce blocks the entire transaction pipeline, freezing the portfolio.

### The Design Pattern:
**Mempool-Aware Reconciliation**
- Never rely on local counters for critical transaction sequencing without chain verification.
- **Implementation:**
  - Implement a dedicated replacement transaction tracker.
  - If an urgent hedge transaction is pending for > $N$ blocks/seconds, the TransactionManager automatically issues a replacement transaction with a 20% higher gas fee reusing the exact same nonce, guaranteeing eventual inclusion.

---

## 7. Dynamic Liquidation Cascade & Latency Modeling (Backtest)

### The Mistake:
The backtest assumed a stationary liquidity model and 100% fill rates at mid-price. It ignored the reality that during volatility, Hyperliquid spreads widen significantly, and execution takes time.

### The Design Pattern:
**HFT-Realistic Impact Simulation**
- The backtester must aggressively penalize the portfolio to simulate real execution environments.
- **Implementation:**
  - **Latency Decay:** Expected edge drops by $X$ bps per millisecond of delay.
  - **Stochastic Partial Fills:** Large orders only fill a calculated `fill_ratio` based on local volatility; the rest of the order experiences severe adverse selection (the market runs away from the passive limit).
  - **Correlation Spikes:** During simulated crises (e.g., portfolio drop > 10% in 1 day), asset correlations are artificially forced to 0.95+ to simulate cross-margin liquidation cascades.

---

## 8. Event-Level Market Replay (No Mid-Price Execution Fantasy)

### The Mistake:
The backtest assumed `Executed Price = Observed Mid-Price`. This is the single biggest source of fake alpha in HFT benchmarking. It ignored spread crossing, taker fees, slippage, and adverse selection, generating positive PnL for trades that mathematically carry a negative expectancy in production.

### The Design Pattern:
**Tick-Level Orderbook Replay**
- A realistic benchmark must simulate the execution process against the actual L2 orderbook.
- **Implementation:**
  - Ingest tick-level data (or high-resolution snapshots) including top-of-book depth and real funding intervals.
  - Implement a `Match Engine Simulator` that forces strategy orders to cross the spread and deducts the correct Maker/Taker fees.

---

## 9. Inventory Carry Cost & Funding Dynamics

### The Mistake:
The benchmark completely ignored funding rates and treated perpetual futures like spot assets. Holding a directional perp position during adverse funding bleeds capital, turning a directionally correct trade into a net loser.

### The Design Pattern:
**Expected PnL with Full Economics**
- Every position must be priced with its holding cost.
- **Implementation:**
  - `Expected PnL = Directional Edge + Carry - Funding - Fees - Impact - Inventory Cost`.
  - Simulate funding payments strictly on 8-hour intervals matching exchange mechanics.
  - Introduce an `Inventory Cost Penalty` that decays signal alpha based on holding time.

---

## 10. Exchange Failure & Latency Modeling

### The Mistake:
Benchmarks assumed `Decision Time = Execution Time`. In reality, network latency, websocket lag, and matching engine delay consume the signal edge. Additionally, the benchmark assumed 100% exchange uptime.

### The Design Pattern:
**Adversarial Exchange Simulation**
- The backtester must inject real-world faults.
- **Implementation:**
  - **Jitter Simulation:** Add a randomized latency delay (e.g., $100ms - 400ms$) between signal generation and simulated order arrival. Delay fills accordingly.
  - **Outage Replay:** Inject synthetic API disconnects and websocket timeouts. The strategy logic must prove it fails gracefully (e.g., entering `SHUTDOWN` and attempting `CANCEL_ALL`) rather than continuing blindly.
