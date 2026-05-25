import { PortfolioState } from '../portfolio/state.js';
import { Decimal } from 'decimal.js';
export class StrategyManager {
    async evaluateAndRebalance(inputs) {
        const actions = [];
        const { regime, riskMetrics } = inputs;
        if (regime === 'crisis') {
            actions.push({
                type: 'STRATEGY_WITHDRAWAL',
                amountUSD: new Decimal(0)
            });
        }
        if (riskMetrics.maxDrawdown.gt(0.15)) {
            // Scale back logic placeholder
        }
        return actions;
    }
}
//# sourceMappingURL=manager.js.map