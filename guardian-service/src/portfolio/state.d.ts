import { Decimal } from 'decimal.js';
import type { OnChainState } from '../blockchain/reader.js';
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
export declare class PortfolioState {
    totalValueUSD: Decimal;
    totalStableValueUSD: Decimal;
    drawdownFromHWM: Decimal;
    derivativePositions: DerivativePosition[];
    private positions;
    private bounds;
    static reconstruct(onChainState: OnChainState): PortfolioState;
    getAllocations(): Record<string, Decimal>;
    getAllocationBounds(): Record<string, AssetAllocationBounds>;
    getTierLimits(): Record<string, unknown>;
}
//# sourceMappingURL=state.d.ts.map