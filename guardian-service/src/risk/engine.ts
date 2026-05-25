import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
import { VaRCalculator } from './var.js';

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

export class RiskEngine {
    private varCalculator: VaRCalculator;

    constructor() {
        this.varCalculator = new VaRCalculator();
    }

    public async computeAll(inputs: RiskEngineInputs): Promise<RiskMetrics> {
        const { portfolio, prices, regime, covariance } = inputs;

        const varMetrics = await this.varCalculator.computeVaR(portfolio, prices);

        const allocations = portfolio.getAllocations();
        let hhi = new Decimal(0);
        for (const w of Object.values(allocations)) {
            const wBps = w.mul(10000);
            hhi = hhi.add(wBps.mul(wBps));
        }
        hhi = hhi.div(10000); 

        let netDelta = new Decimal(0);
        for (const pos of portfolio.derivativePositions) {
            netDelta = netDelta.add(pos.isLong ? pos.sizeUSD : pos.sizeUSD.neg());
        }

        return {
            var95_1d: varMetrics.var95_1d,
            cvar99_1d: varMetrics.cvar99_1d,
            lvar: varMetrics.lvar,
            maxDrawdown: portfolio.drawdownFromHWM,
            hhi,
            netDelta,
            regime,
            expectedReturns: [], 
            covariance,
        };
    }
}
