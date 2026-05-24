export type GuardianState =
    | 'INITIALIZING'
    | 'HEALTHY'
    | 'DEGRADED'
    | 'RESTRICTED'
    | 'EMERGENCY'
    | 'SHUTDOWN';

export interface EvaluationInput {
    oracleHealth: 'GOOD' | 'DEGRADED' | 'SUSPECT' | 'STALE';
    gasPrice: bigint;
    protocolHealth: 'HEALTHY' | 'DEGRADED' | 'DOWN';
    stablecoinHealth: 'HEALTHY' | 'WATCH' | 'WARNING' | 'CRITICAL';
    circuitBreakerLevel: 0 | 1 | 2 | 3;
    currentDrawdown: number;
}

export class StateMachine {
    private currentState: GuardianState = 'INITIALIZING';

    public getCurrentState(): GuardianState {
        return this.currentState;
    }

    public evaluate(input: EvaluationInput): GuardianState {
        if (input.circuitBreakerLevel >= 3 || input.oracleHealth === 'STALE' || input.protocolHealth === 'DOWN' || input.stablecoinHealth === 'CRITICAL') {
            return 'EMERGENCY';
        }

        if (input.circuitBreakerLevel >= 1 || input.oracleHealth === 'SUSPECT' || input.currentDrawdown > 0.15 || input.stablecoinHealth === 'WARNING') {
            return 'RESTRICTED';
        }

        if (input.oracleHealth === 'DEGRADED' || input.protocolHealth === 'DEGRADED' || input.gasPrice > 100_000_000_000n) { // Example high gas
            return 'DEGRADED';
        }

        return 'HEALTHY';
    }

    public transition(newState: GuardianState): void {
        // Implement transition logic, logging, and side effects if necessary
        this.currentState = newState;
    }
}
