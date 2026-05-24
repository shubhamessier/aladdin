import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
import type { ProposedAction } from '../blockchain/writer.js';
export interface RebalanceOptions {
    maxTradeUSD: number;
    maxSlippageBps: number;
}
export interface RebalanceTrade extends ProposedAction {
    type: 'SWAP';
    tokenIn: string;
    tokenOut: string;
    amountInUSD: number;
    amountOutUSD: number;
}
export declare class Rebalancer {
    generateTrades(currentAllocations: Record<string, number>, targetAllocations: Record<string, number>, portfolio: PortfolioState, prices: PriceData[], opts: RebalanceOptions): RebalanceTrade[];
}
//# sourceMappingURL=rebalancer.d.ts.map