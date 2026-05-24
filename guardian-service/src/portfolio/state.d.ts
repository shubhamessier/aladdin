import type { OnChainState } from '../blockchain/reader.js';
export interface Position {
    asset: string;
    amount: bigint;
    valueUSD: number;
}
export interface DerivativePosition {
    market: string;
    isLong: boolean;
    sizeUSD: number;
    margin: number;
    unrealizedPnL: number;
}
export interface AssetAllocationBounds {
    maxBps: number;
    minBps: number;
}
export interface PortfolioLimits {
    maxHHI: number;
    minStableReserveBps: number;
}
export declare class PortfolioState {
    totalValueUSD: number;
    totalStableValueUSD: number;
    drawdownFromHWM: number;
    derivativePositions: DerivativePosition[];
    private positions;
    private bounds;
    static reconstruct(onChainState: OnChainState): PortfolioState;
    getAllocations(): Record<string, number>;
    getAllocationBounds(): Record<string, AssetAllocationBounds>;
    getTierLimits(): Record<string, unknown>;
}
//# sourceMappingURL=state.d.ts.map