import { Decimal } from 'decimal.js';
export type GuardianState = 'INITIALIZING' | 'HEALTHY' | 'DEGRADED' | 'RESTRICTED' | 'EMERGENCY' | 'SHUTDOWN';
export interface EvaluationInput {
    oracleHealth: 'GOOD' | 'DEGRADED' | 'SUSPECT' | 'STALE';
    gasPrice: bigint;
    protocolHealth: 'HEALTHY' | 'DEGRADED' | 'DOWN';
    stablecoinHealth: 'HEALTHY' | 'WATCH' | 'WARNING' | 'CRITICAL';
    circuitBreakerLevel: 0 | 1 | 2 | 3;
    currentDrawdown: Decimal;
}
export interface CBState {
    cbLevelSetTimestamp: number;
    cbConsecutiveStableDays: number;
    currentCBLevel: number;
}
export interface HWMState {
    hwmAbsolute: Decimal;
    hwmLastUpdatedTimestamp: number;
    hwmDecayHalflifeSeconds: number;
}
export type GuardianAction = {
    type: 'DECAY_CB_LEVEL';
} | {
    type: 'SET_RECOVERY_PHASE';
    active: boolean;
    maxVolatileBps: Decimal;
} | {
    type: 'NONE';
};
export declare class StateMachine {
    private currentState;
    getCurrentState(): GuardianState;
    evaluate(input: EvaluationInput): GuardianState;
    computeEffectiveHWM(state: HWMState, currentTimeSeconds: number): Decimal;
    checkCBDecayConditions(cbState: CBState, currentTimeSeconds: number): boolean;
    processState(input: EvaluationInput, cbState: CBState, currentTimeSeconds: number): {
        newState: GuardianState;
        actions: GuardianAction[];
    };
    transition(newState: GuardianState): void;
}
//# sourceMappingURL=state-machine.d.ts.map