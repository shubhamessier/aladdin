import type { ProposedAction } from '../blockchain/writer.js';
export const constraintChecker = {
    validate: (action: ProposedAction, portfolio: unknown) => ({ allowed: true, reason: '' }),
};
