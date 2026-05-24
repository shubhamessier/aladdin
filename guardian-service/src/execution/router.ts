import type { ProposedAction } from '../blockchain/writer.js';

export class ExecutionRouter {
    public routeTrade(action: ProposedAction): ProposedAction[] {
        // Implement multi-pool split logic if needed
        // For now, return the action directly as a single route
        return [action];
    }
}
