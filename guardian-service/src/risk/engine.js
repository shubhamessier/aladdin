import { PortfolioState } from '../portfolio/state.js';
import { VaRCalculator } from './var.js';
export class RiskEngine {
    varCalculator;
    constructor() {
        this.varCalculator = new VaRCalculator();
    }
    async computeAll(inputs) {
        const { portfolio, prices, regime, covariance } = inputs;
        const varMetrics = await this.varCalculator.computeVaR(portfolio, prices);
        const allocations = portfolio.getAllocations();
        let hhi = 0;
        for (const w of Object.values(allocations)) {
            hhi += (w * 10000) * (w * 10000);
        }
        hhi = hhi / 10000;
        let netDelta = 0;
        for (const pos of portfolio.derivativePositions) {
            netDelta += pos.isLong ? pos.sizeUSD : -pos.sizeUSD;
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
//# sourceMappingURL=engine.js.map