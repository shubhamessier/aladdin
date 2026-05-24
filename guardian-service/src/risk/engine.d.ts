import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
export interface RiskMetrics {
    var95_1d: number;
    cvar99_1d: number;
    lvar: number;
    maxDrawdown: number;
    hhi: number;
    netDelta: number;
    regime: 'bull' | 'uncertain' | 'crisis';
    expectedReturns: number[];
    covariance: number[][];
}
export interface RiskEngineInputs {
    portfolio: PortfolioState;
    prices: PriceData[];
    regime: 'bull' | 'uncertain' | 'crisis';
    covariance: number[][];
}
export declare class RiskEngine {
    private varCalculator;
    constructor();
    computeAll(inputs: RiskEngineInputs): Promise<RiskMetrics>;
}
//# sourceMappingURL=engine.d.ts.map