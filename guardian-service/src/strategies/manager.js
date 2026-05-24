import { PortfolioState } from '../portfolio/state.js';
export class StrategyManager {
    async evaluateAndRebalance(inputs) {
        const actions = [];
        const { regime, riskMetrics } = inputs;
        if (regime === 'crisis') {
            actions.push({
                type: 'STRATEGY_WITHDRAWAL',
                amountUSD: 0
            });
        }
        if (riskMetrics.maxDrawdown > 0.15) {
            // Scale back logic placeholder
        }
        return actions;
    }
}
//# sourceMappingURL=manager.js.map