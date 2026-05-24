import type { ProposedAction } from '../blockchain/writer.js';
export const executionSimulator = {
    simulate: async (action: ProposedAction, state: unknown) => ({ success: true, slippageBps: 0 }),
};
