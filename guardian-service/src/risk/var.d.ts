import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
export interface VaRMetrics {
    var95_1d: number;
    var99_1d: number;
    cvar95_1d: number;
    cvar99_1d: number;
    lvar: number;
}
export declare class VaRCalculator {
    private readonly pythonApiUrl;
    constructor(pythonApiUrl?: string);
    computeVaR(portfolio: PortfolioState, prices: PriceData[]): Promise<VaRMetrics>;
}
//# sourceMappingURL=var.d.ts.map