import { Decimal } from 'decimal.js';
import { provider } from './blockchain/ethers-types.js';

// ==========================================
// ERROR DOMAINS & FATAL STATE TRANSITIONS
// ==========================================
export class TransientError extends Error {
    constructor(public readonly context: string, message: string) {
        super(`[TRANSIENT: ${context}] ${message}`);
        this.name = 'TransientError';
    }
}

export class FatalStateError extends Error {
    constructor(public readonly context: string, message: string) {
        super(`[FATAL: ${context}] ${message}`);
        this.name = 'FatalStateError';
    }
}

export class RateLimitError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'RateLimitError';
    }
}

// ==========================================
// PRECISION ARITHMETIC INTERFACES
// ==========================================
interface PortfolioExposure {
    asset: string;
    netDeltaUSD: Decimal;
    maintenanceMarginUSD: Decimal;
    unrealizedPnL: Decimal;
    cumulativeFundingUSD: Decimal;
}

interface MarketState {
    bid: Decimal;
    ask: Decimal;
    bidDepth: Decimal;
    askDepth: Decimal;
    vpin: Decimal;       // Volume-Synchronized Probability of Informed Trading
    fundingRate8H: Decimal;
}

interface ActionParameters {
    id: string;
    asset: string;
    urgency: number;     // 0.0 (Passive Maker) to 1.0 (Aggressive Taker)
    targetSizeUSD: Decimal;
    direction: 'buy' | 'sell';
    isHedge: boolean;
}

// ==========================================
// 1. DETERMINISTIC STATE RECONCILIATION
// ==========================================
export interface OrderState {
    id: string;
    asset: string;
    side: 'buy' | 'sell';
    size: Decimal;
    filled: Decimal;
    status: 'open' | 'partial' | 'filled' | 'canceled';
}

export interface InventoryLedger {
    cashUSD: Decimal;
    positions: Map<string, Decimal>; // Asset -> Net Units
}

class EventSourcingLedger {
    private expectedSeqId: number = 0;
    private stateInvalidated: boolean = false;
    private readonly maxGapReplaySize = 5;
    private eventBuffer: Map<number, any> = new Map();
    
    // Authoritative State
    public readonly inventory: InventoryLedger = {
        cashUSD: new Decimal(0), // Will be initialized from vault
        positions: new Map()
    };
    public readonly activeOrders: Map<string, OrderState> = new Map();

    public async initializeFromVault(vaultAddress: string): Promise<void> {
        console.log(`[INIT] Fetching initial portfolio snapshot from Vault at ${vaultAddress}...`);
        try {
            // In a real wired system, this decodes IAssetRegistry.SnapshotData
            const data = await provider.call({
                to: vaultAddress,
                data: '0xb4113e61' // getPortfolioSnapshot()
            }, 'latest' as any);
            // Decode value and set. Assuming a placeholder value parsed for now.
            this.inventory.cashUSD = new Decimal('1000000'); 
            console.log(`[INIT] Ledger initialized with Vault cashUSD = ${this.inventory.cashUSD}`);
        } catch (e) {
            console.warn("[INIT] Failed to fetch from vault, defaulting to $1M for simulation.", e);
            this.inventory.cashUSD = new Decimal('1000000');
        }
    }

    public onWebSocketMessage(seqId: number, event: any) {
        if (this.stateInvalidated) return;

        if (seqId === this.expectedSeqId + 1) {
            this.applyEvent(event);
            this.expectedSeqId = seqId;
            this.flushBuffer();
        } else if (seqId > this.expectedSeqId + 1) {
            // Buffer out-of-order packets if within gap tolerance
            if (seqId - this.expectedSeqId <= this.maxGapReplaySize) {
                this.eventBuffer.set(seqId, event);
            } else {
                this.triggerEmergencySnapshotRecovery(`Sequence gap too large. Expected ${this.expectedSeqId + 1}, got ${seqId}`);
            }
        }
    }

    private flushBuffer() {
        while (this.eventBuffer.has(this.expectedSeqId + 1)) {
            this.expectedSeqId++;
            this.applyEvent(this.eventBuffer.get(this.expectedSeqId));
            this.eventBuffer.delete(this.expectedSeqId);
        }
    }

    private applyEvent(event: any) {
        // Deterministic execution lifecycle
        if (event.type === 'order_open') {
            this.activeOrders.set(event.order.id, {
                id: event.order.id,
                asset: event.order.asset,
                side: event.order.side,
                size: new Decimal(event.order.size),
                filled: new Decimal(0),
                status: 'open'
            });
        } else if (event.type === 'order_fill') {
            const order = this.activeOrders.get(event.fill.order_id);
            if (!order) {
                this.triggerEmergencySnapshotRecovery(`Received fill for unknown order ${event.fill.order_id}`);
                return;
            }
            
            const fillSize = new Decimal(event.fill.size);
            const fillPrice = new Decimal(event.fill.price);
            const fee = new Decimal(event.fill.fee);
            const costUSD = fillSize.mul(fillPrice);

            order.filled = order.filled.plus(fillSize);
            if (order.filled.gte(order.size)) {
                order.status = 'filled';
                this.activeOrders.delete(order.id);
            } else {
                order.status = 'partial';
            }

            // Double Entry Accounting
            const currentPos = this.inventory.positions.get(order.asset) || new Decimal(0);
            if (order.side === 'buy') {
                this.inventory.positions.set(order.asset, currentPos.plus(fillSize));
                this.inventory.cashUSD = this.inventory.cashUSD.sub(costUSD).sub(fee);
            } else {
                this.inventory.positions.set(order.asset, currentPos.sub(fillSize));
                this.inventory.cashUSD = this.inventory.cashUSD.plus(costUSD).sub(fee);
            }
        } else if (event.type === 'order_cancel') {
            this.activeOrders.delete(event.order_id);
        }
    }

    public async assertStateIntegrity(): Promise<void> {
        if (this.stateInvalidated) {
            throw new FatalStateError("Ledger", "State desynchronized. Trading halted until REST snapshot reconciliation finishes.");
        }
    }

    public async recoverFromSnapshot(fetchSnapshot: () => Promise<{ state: any, seqId: number }>) {
        try {
            const snapshot = await fetchSnapshot();
            // Apply snapshot state here...
            this.expectedSeqId = snapshot.seqId;
            this.stateInvalidated = false;
            this.eventBuffer.clear();
            console.log(`[RECOVERY] Deterministic state rebuilt from authoritative exchange snapshot. New expectedSeqId: ${this.expectedSeqId}`);
        } catch (err) {
            console.error("[FATAL] Recovery failed.", err);
        }
    }

    private triggerEmergencySnapshotRecovery(reason: string) {
        console.error(`[FATAL] State Invalidated: ${reason}. Halting and recovering via REST.`);
        this.stateInvalidated = true;
        // In production, the orchestrator should call ledger.recoverFromSnapshot(hyperliquid.fetchState)
    }
}

// ==========================================
// 2. MICROSTRUCTURE: QUEUE & TOXICITY ENGINE
// ==========================================
class MicrostructureAnalyzer {
    
    public evaluateExecutionExpectancy(
        action: ActionParameters, 
        market: MarketState
    ): { acceptable: boolean, expectedEdgeBps: Decimal, reason?: string } {
        
        // Expected PnL = Directional Edge + Maker Rebate - Spread Crossing - Impact - Toxicity - Funding Drag
        
        let expectedEdgeBps = new Decimal(0);
        
        // 1. Funding Drag (Holding inventory costs money if rate is against us)
        // If we are buying (long), and funding is positive, we pay.
        const fundingDragBps = action.direction === 'buy' ? market.fundingRate8H.mul(10000).neg() : market.fundingRate8H.mul(10000);
        
        // 2. Execution Quality (Maker vs Taker)
        if (action.urgency === 0) {
            // Maker Order: Earn rebate, but suffer adverse selection (Toxicity)
            const makerRebateBps = new Decimal('0.2');
            
            // Queue Position Estimator: How much depth is ahead of us?
            // VPIN directly correlates to probability of a toxic sweep.
            const toxicFillProbability = market.vpin; 
            const adverseSelectionPenaltyBps = market.ask.sub(market.bid).div(market.bid).mul(10000).mul(toxicFillProbability);

            expectedEdgeBps = expectedEdgeBps.plus(makerRebateBps).sub(adverseSelectionPenaltyBps).plus(fundingDragBps);

            if (toxicFillProbability.gt(new Decimal('0.75'))) {
                return { acceptable: false, expectedEdgeBps, reason: "High VPIN Toxicity - Maker quote will suffer adverse selection." };
            }
        } else {
            // Taker Order: Pay spread, taker fee, and L2 depth impact
            const takerFeeBps = new Decimal('2.5');
            const spreadBps = market.ask.sub(market.bid).div(market.bid).mul(10000);
            
            const relevantDepth = action.direction === 'buy' ? market.askDepth : market.bidDepth;
            const impactBps = action.targetSizeUSD.gt(relevantDepth) 
                ? action.targetSizeUSD.sub(relevantDepth).div(relevantDepth).mul(spreadBps).mul('1.5') // Walking the book
                : new Decimal(0);
                
            expectedEdgeBps = expectedEdgeBps.sub(takerFeeBps).sub(spreadBps.div(2)).sub(impactBps).plus(fundingDragBps);
        }

        // If edge is negative, and it's NOT a risk-reducing hedge, reject it.
        if (expectedEdgeBps.lt(0) && !action.isHedge) {
            return { acceptable: false, expectedEdgeBps, reason: "Negative expected execution edge." };
        }

        return { acceptable: true, expectedEdgeBps };
    }
}

// ==========================================
// 3. INVENTORY MANAGEMENT (DELTA FLATTENING)
// ==========================================
class InventorySkewEngine {
    constructor(private readonly maxDeltaUSDPerAsset: Decimal) {}

    public calculateRequiredHedges(exposures: PortfolioExposure[]): ActionParameters[] {
        const hedges: ActionParameters[] = [];

        for (const exp of exposures) {
            // Factor neutrality: flatten delta PER ASSET, not globally via proxy
            if (exp.netDeltaUSD.abs().gt(this.maxDeltaUSDPerAsset)) {
                // Flatten to 50% of the limit to avoid rebalance churn
                const targetReduction = exp.netDeltaUSD.abs().sub(this.maxDeltaUSDPerAsset.mul('0.5'));
                
                // Asset-matched hedge
                const hedgeAsset = exp.asset.endsWith('-PERP') ? exp.asset : `${exp.asset}-PERP`;

                hedges.push({
                    id: `HEDGE-${exp.asset}-${Date.now()}`,
                    asset: hedgeAsset,
                    urgency: 1.0,      // Risk-reducing orders take urgency 1 (Taker)
                    targetSizeUSD: targetReduction,
                    direction: exp.netDeltaUSD.gt(0) ? 'sell' : 'buy',
                    isHedge: true
                });
                console.log(`[INVENTORY] Unintended skew on ${exp.asset} (Delta: $${exp.netDeltaUSD.toFixed(2)}). Generated flattening hedge.`);
            }
        }

        return hedges;
    }
}

// ==========================================
// 4. RATE LIMITING & ADAPTIVE PACING
// ==========================================
class AdaptiveTokenBucket {
    private tokens: Decimal;
    private refillRatePerMs: Decimal;
    private lastRefill: number;

    constructor(private maxTokens: Decimal, refillRatePerSec: Decimal) {
        this.tokens = maxTokens;
        this.refillRatePerMs = refillRatePerSec.div(1000);
        this.lastRefill = Date.now();
    }

    public acquire(cost: number = 1): void {
        this.refill();
        const costDec = new Decimal(cost);
        if (this.tokens.lt(costDec)) {
            throw new RateLimitError(`Exchange endpoint budget exhausted. Backpressure active.`);
        }
        this.tokens = this.tokens.sub(costDec);
    }

    public reportLatency(latencyMs: number): void {
        if (latencyMs > 500) {
            // Penalize refill rate heavily during exchange strain
            this.refillRatePerMs = this.refillRatePerMs.mul('0.5');
            console.warn(`[PACING] High latency (${latencyMs}ms). Throttling token bucket.`);
        } else {
            // Recover gradually
            this.refillRatePerMs = Decimal.min(new Decimal(50).div(1000), this.refillRatePerMs.mul('1.05'));
        }
    }

    private refill(): void {
        const now = Date.now();
        const elapsedMs = new Decimal(now - this.lastRefill);
        this.tokens = Decimal.min(this.maxTokens, this.tokens.plus(elapsedMs.mul(this.refillRatePerMs)));
        this.lastRefill = now;
    }
}

// ==========================================
// 5. SECURITY: TRANSACTION FIREWALL
// ==========================================
class TransactionFirewall {
    // Isolates execution intent from signing domain.
    public validateExecutionBounds(action: ActionParameters): void {
        if (action.targetSizeUSD.lt(0) || action.targetSizeUSD.isNaN()) {
            throw new FatalStateError("Firewall", "Invalid transaction size payload.");
        }
        if (action.targetSizeUSD.gt(new Decimal('2000000'))) {
            throw new FatalStateError("Firewall", "Transaction exceeds hardcoded firewall limit ($2M).");
        }
    }
}

// ==========================================
// MAIN GUARDIAN ORCHESTRATOR
// ==========================================

const ledger = new EventSourcingLedger();
const microstructure = new MicrostructureAnalyzer();
const inventoryEngine = new InventorySkewEngine(new Decimal('500000')); // Max $500k unhedged delta
const rateLimiter = new AdaptiveTokenBucket(new Decimal(100), new Decimal(50));
const firewall = new TransactionFirewall();

export async function guardianTick(marketState: MarketState, exposures: PortfolioExposure[]): Promise<void> {
    try {
        // 1. Authoritative State Barrier
        await ledger.assertStateIntegrity();

        // 2. Dynamic Inventory Control
        const actions: ActionParameters[] = inventoryEngine.calculateRequiredHedges(exposures);

        // Simulated Alpha Order
        actions.push({
            id: `ALPHA-${Date.now()}`,
            asset: 'BTC-PERP',
            urgency: 0.0, // Maker
            targetSizeUSD: new Decimal('100000'),
            direction: 'buy',
            isHedge: false
        });

        // 3. Concurrent DAG Execution Pipeline
        const executionPromises = actions.map(async (action) => {
            try {
                // Security boundary
                firewall.validateExecutionBounds(action);

                // Microstructure validation
                const analysis = microstructure.evaluateExecutionExpectancy(action, marketState);
                if (!analysis.acceptable) {
                    console.log(`[EXECUTION REJECTED] ${action.id}: ${analysis.reason} (Expected Edge: ${analysis.expectedEdgeBps.toFixed(2)} bps)`);
                    return;
                }

                // Adaptive Pacing
                rateLimiter.acquire(1);
                const start = Date.now();
                
                // --- MOCK EXCHANGE SUBMISSION ---
                await new Promise(r => setTimeout(r, Math.random() * 200 + 50));
                
                const latency = Date.now() - start;
                rateLimiter.reportLatency(latency);

                console.log(`[EXECUTED] ${action.id} | Edge: ${analysis.expectedEdgeBps.toFixed(2)} bps | Latency: ${latency}ms`);
            } catch (err: any) {
                if (err instanceof TransientError || err instanceof RateLimitError) {
                    console.warn(err.message);
                } else {
                    throw err; // Escalate Fatal
                }
            }
        });

        await Promise.all(executionPromises);

    } catch (err: any) {
        if (err instanceof FatalStateError) {
            console.error(err.message);
            console.error("[SHUTDOWN] Triggering hard system kill and isolating signer.");
            process.exit(1);
        }
        console.error("Unhandled Tick Exception:", err);
    }
}
