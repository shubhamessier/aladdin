import type { PortfolioState } from './state.js';
export interface PnLReport {
    realizedPnL: number;
    unrealizedPnL: number;
    totalYield: number;
    components: {
        lendingYield: number;
        lpFees: number;
        stakingYield: number;
        funding: number;
    };
}
export declare class AccountingEngine {
    computePnL(currentState: PortfolioState, previousState?: PortfolioState): PnLReport;
}
//# sourceMappingURL=accounting.d.ts.map