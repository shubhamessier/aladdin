export type GuardianState = 'INITIALIZING' | 'HEALTHY' | 'DEGRADED' | 'RESTRICTED' | 'EMERGENCY' | 'SHUTDOWN';
export interface EvaluationInput {
    oracleHealth: 'GOOD' | 'DEGRADED' | 'SUSPECT' | 'STALE';
    gasPrice: bigint;
    protocolHealth: 'HEALTHY' | 'DEGRADED' | 'DOWN';
    stablecoinHealth: 'HEALTHY' | 'WATCH' | 'WARNING' | 'CRITICAL';
    circuitBreakerLevel: 0 | 1 | 2 | 3;
    currentDrawdown: number;
}
export declare class StateMachine {
    private currentState;
    getCurrentState(): GuardianState;
    evaluate(input: EvaluationInput): GuardianState;
    transition(newState: GuardianState): void;
}
//# sourceMappingURL=state-machine.d.ts.map