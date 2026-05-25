import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
export interface VaRMetrics {
    var95_1d: Decimal;
    var99_1d: Decimal;
    cvar95_1d: Decimal;
    cvar99_1d: Decimal;
    lvar: Decimal;
}
export declare class VaRCalculator {
    private readonly pythonApiUrl;
    constructor(pythonApiUrl?: string);
    computeVaR(portfolio: PortfolioState, prices: PriceData[]): Promise<VaRMetrics>;
}
//# sourceMappingURL=var.d.ts.map