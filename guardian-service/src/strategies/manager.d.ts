import { PortfolioState } from '../portfolio/state.js';
import type { RiskMetrics } from '../risk/engine.js';
import type { ProposedAction } from '../blockchain/writer.js';
export interface StrategyEvaluationInputs {
    portfolio: PortfolioState;
    riskMetrics: RiskMetrics;
    regime: 'bull' | 'uncertain' | 'crisis';
    yieldData: Record<string, any>;
}
export declare class StrategyManager {
    evaluateAndRebalance(inputs: StrategyEvaluationInputs): Promise<ProposedAction[]>;
}
//# sourceMappingURL=manager.d.ts.map