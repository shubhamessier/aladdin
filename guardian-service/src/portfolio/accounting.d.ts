import { Decimal } from 'decimal.js';
import type { PortfolioState } from './state.js';
export interface PnLReport {
    realizedPnL: Decimal;
    unrealizedPnL: Decimal;
    totalYield: Decimal;
    components: {
        lendingYield: Decimal;
        lpFees: Decimal;
        stakingYield: Decimal;
        funding: Decimal;
    };
}
export declare class AccountingEngine {
    computePnL(currentState: PortfolioState, previousState?: PortfolioState): PnLReport;
}
//# sourceMappingURL=accounting.d.ts.map