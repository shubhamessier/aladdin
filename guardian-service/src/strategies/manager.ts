import { PortfolioState } from '../portfolio/state.js';
import type { RiskMetrics } from '../risk/engine.js';
import type { ProposedAction } from '../blockchain/writer.js';
import { Decimal } from 'decimal.js';

export interface StrategyEvaluationInputs {
    portfolio: PortfolioState;
    riskMetrics: RiskMetrics;
    regime: 'bull' | 'uncertain' | 'crisis';
    yieldData: Record<string, any>;
}

export class StrategyManager {
    public async evaluateAndRebalance(inputs: StrategyEvaluationInputs): Promise<ProposedAction[]> {
        const actions: ProposedAction[] = [];
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
