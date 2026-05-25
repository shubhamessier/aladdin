import { Decimal } from 'decimal.js';
import type { OnChainState, PriceData } from '../blockchain/reader.js';

export interface Position {
    asset: string;
    amount: bigint;
    valueUSD: Decimal;
}

export interface DerivativePosition {
    market: string;
    isLong: boolean;
    sizeUSD: Decimal;
    margin: Decimal;
    unrealizedPnL: Decimal;
}

export interface AssetAllocationBounds {
    maxBps: Decimal;
    minBps: Decimal;
}

export interface PortfolioLimits {
    maxHHI: Decimal;
    minStableReserveBps: Decimal;
}

export class PortfolioState {
    public totalValueUSD: Decimal = new Decimal(0);
    public totalStableValueUSD: Decimal = new Decimal(0);
    public drawdownFromHWM: Decimal = new Decimal(0);
    public derivativePositions: DerivativePosition[] = [];
    private positions: Map<string, Position> = new Map();
    private bounds: Map<string, AssetAllocationBounds> = new Map();

    public static reconstruct(onChainState: OnChainState): PortfolioState {
        throw new Error("reconstruct() is unsafe in HFT. Use EventSourcingLedger to rebuild state deterministically from WebSocket ACKs.");
    }

    public getAllocations(): Record<string, Decimal> {
        const allocations: Record<string, Decimal> = {};
        if (this.totalValueUSD.isZero()) return allocations;

        for (const [asset, position] of this.positions.entries()) {
            allocations[asset] = position.valueUSD.div(this.totalValueUSD);
        }
        return allocations;
    }

    public getAllocationBounds(): Record<string, AssetAllocationBounds> {
        return Object.fromEntries(this.bounds.entries());
    }

    public getTierLimits(): Record<string, unknown> {
        // Placeholder for tier limits logic
        return {};
    }
}
