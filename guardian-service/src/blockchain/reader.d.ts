import type { DerivativePosition } from '../portfolio/state.js';
export interface PriceData {
    token: string;
    price: bigint;
    status: 'GOOD' | 'DEGRADED' | 'SUSPECT' | 'STALE';
    timestamp: number;
}
export interface OnChainState {
    balances: Record<string, string>;
    prices: PriceData[];
    cbLevel: 0 | 1 | 2 | 3;
    totalValueUSD: number;
    drawdown: number;
    derivativePositions: DerivativePosition[];
    paused: boolean;
}
export declare class BlockchainReader {
    getFullState(): Promise<OnChainState>;
}
//# sourceMappingURL=reader.d.ts.map