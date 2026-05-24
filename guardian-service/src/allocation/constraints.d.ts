import type { ProposedAction } from '../blockchain/writer.js';
export declare const constraintChecker: {
    validate: (action: ProposedAction, portfolio: unknown) => {
        allowed: boolean;
        reason: string;
    };
};
//# sourceMappingURL=constraints.d.ts.map