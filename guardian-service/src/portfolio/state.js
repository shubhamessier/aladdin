import { Decimal } from 'decimal.js';
export class PortfolioState {
    totalValueUSD = new Decimal(0);
    totalStableValueUSD = new Decimal(0);
    drawdownFromHWM = new Decimal(0);
    derivativePositions = [];
    positions = new Map();
    bounds = new Map();
    static reconstruct(onChainState) {
        throw new Error("reconstruct() is unsafe in HFT. Use EventSourcingLedger to rebuild state deterministically from WebSocket ACKs.");
    }
    getAllocations() {
        const allocations = {};
        if (this.totalValueUSD.isZero())
            return allocations;
        for (const [asset, position] of this.positions.entries()) {
            allocations[asset] = position.valueUSD.div(this.totalValueUSD);
        }
        return allocations;
    }
    getAllocationBounds() {
        return Object.fromEntries(this.bounds.entries());
    }
    getTierLimits() {
        // Placeholder for tier limits logic
        return {};
    }
}
//# sourceMappingURL=state.js.map