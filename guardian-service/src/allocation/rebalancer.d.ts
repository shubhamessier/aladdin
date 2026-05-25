import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
import type { ProposedAction } from '../blockchain/writer.js';
export interface RebalanceOptions {
    maxTradeUSD: Decimal;
    maxSlippageBps: Decimal;
}
export interface RebalanceTrade extends ProposedAction {
    type: 'SWAP';
    tokenIn: string;
    tokenOut: string;
    amountInUSD: Decimal;
    amountOutUSD: Decimal;
}
export declare class Rebalancer {
    generateTrades(currentAllocations: Record<string, Decimal>, targetAllocations: Record<string, Decimal>, portfolio: PortfolioState, prices: PriceData[], opts: RebalanceOptions): RebalanceTrade[];
}
//# sourceMappingURL=rebalancer.d.ts.map