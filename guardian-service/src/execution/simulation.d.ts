import type { ProposedAction } from '../blockchain/writer.js';
export declare const executionSimulator: {
    simulate: (action: ProposedAction, state: unknown) => Promise<{
        success: boolean;
        slippageBps: number;
    }>;
};
//# sourceMappingURL=simulation.d.ts.map