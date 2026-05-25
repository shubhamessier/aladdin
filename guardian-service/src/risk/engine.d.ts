import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
export interface RiskMetrics {
    var95_1d: Decimal;
    cvar99_1d: Decimal;
    lvar: Decimal;
    maxDrawdown: Decimal;
    hhi: Decimal;
    netDelta: Decimal;
    regime: 'bull' | 'uncertain' | 'crisis';
    expectedReturns: Decimal[];
    covariance: Decimal[][];
}
export interface RiskEngineInputs {
    portfolio: PortfolioState;
    prices: PriceData[];
    regime: 'bull' | 'uncertain' | 'crisis';
    covariance: Decimal[][];
}
export declare class RiskEngine {
    private varCalculator;
    constructor();
    computeAll(inputs: RiskEngineInputs): Promise<RiskMetrics>;
}
//# sourceMappingURL=engine.d.ts.map