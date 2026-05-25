import { Decimal } from 'decimal.js';

export interface L2OrderbookState {
    bids: { price: Decimal; size: Decimal }[];
    asks: { price: Decimal; size: Decimal }[];
    vpin: Decimal;
    lastUpdateTimestamp: number;
}

export class QueueAwareExecutionEngine {
    private readonly MAX_TOXICITY_VPIN = new Decimal('0.65');
    private readonly SPREAD_WIDENING_MULTIPLIER = new Decimal('1.5');

    public evaluateMakerOrder(targetPrice: Decimal, targetSize: Decimal, bookState: L2OrderbookState): { execute: boolean, reason?: string } {
        // 1. Toxicity Check
        if (bookState.vpin.gt(this.MAX_TOXICITY_VPIN)) {
            return { execute: false, reason: "High VPIN Toxicity detected. Withdrawing maker orders." };
        }

        // 2. Queue Evaporation Check
        const bestBid = bookState.bids[0];
        const bestAsk = bookState.asks[0];
        const spread = bestAsk.price.sub(bestBid.price);

        // If the spread suddenly widens compared to rolling average (simulated here)
        // Assume toxic informed flow is sweeping
        // (In a full system, you track the rolling 5-minute spread)
        if (spread.gt(bestBid.price.mul(new Decimal('0.001')))) {
            return { execute: false, reason: "Abnormal spread widening detected." };
        }

        return { execute: true };
    }

    public determineUrgency(alphaDecayBps: Decimal, takerFeeBps: Decimal): 'MAKER' | 'TAKER' {
        // If the signal is decaying faster than the taker fee cost, we cross the spread
        if (alphaDecayBps.gt(takerFeeBps)) {
            return 'TAKER';
        }
        return 'MAKER';
    }
}
