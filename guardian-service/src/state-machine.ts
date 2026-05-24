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

export interface CBState {
    cbLevelSetTimestamp: number;
    cbConsecutiveStableDays: number;
    currentCBLevel: number;
}

export interface HWMState {
    hwmAbsolute: number;
    hwmLastUpdatedTimestamp: number;
    hwmDecayHalflifeSeconds: number;
}

export type GuardianAction = 
    | { type: 'DECAY_CB_LEVEL' }
    | { type: 'SET_RECOVERY_PHASE'; active: boolean; maxVolatileBps: number }
    | { type: 'NONE' };

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

    public computeEffectiveHWM(state: HWMState, currentTimeSeconds: number): number {
        if (state.hwmAbsolute === 0) return 0;
        const elapsed = currentTimeSeconds - state.hwmLastUpdatedTimestamp;
        if (elapsed <= 0) return state.hwmAbsolute;
        
        const halflives = Math.floor(elapsed / state.hwmDecayHalflifeSeconds);
        if (halflives >= 64) return 0;
        
        let decayedValue = state.hwmAbsolute / Math.pow(2, halflives);
        
        const remainder = elapsed % state.hwmDecayHalflifeSeconds;
        if (remainder > 0) {
            decayedValue = decayedValue - (decayedValue * remainder) / (2 * state.hwmDecayHalflifeSeconds);
        }
        
        return Math.floor(decayedValue);
    }

    public checkCBDecayConditions(cbState: CBState, currentTimeSeconds: number): boolean {
        if (cbState.currentCBLevel === 0) return false;
        
        const oneDay = 86400;
        const timeSinceSet = currentTimeSeconds - cbState.cbLevelSetTimestamp;
        
        if (timeSinceSet >= oneDay && cbState.cbConsecutiveStableDays >= 1) {
            return true;
        }
        return false;
    }

    public processState(input: EvaluationInput, cbState: CBState, currentTimeSeconds: number): { newState: GuardianState, actions: GuardianAction[] } {
        const newState = this.evaluate(input);
        const actions: GuardianAction[] = [];

        if (this.checkCBDecayConditions(cbState, currentTimeSeconds)) {
            actions.push({ type: 'DECAY_CB_LEVEL' });
        }

        if (newState === 'RESTRICTED' || newState === 'EMERGENCY') {
            if (this.currentState !== 'RESTRICTED' && this.currentState !== 'EMERGENCY') {
                actions.push({ type: 'SET_RECOVERY_PHASE', active: true, maxVolatileBps: 2000 }); // e.g. cap at 20%
            }
        } else if (newState === 'HEALTHY' || newState === 'DEGRADED') {
            if (this.currentState === 'RESTRICTED' || this.currentState === 'EMERGENCY') {
                actions.push({ type: 'SET_RECOVERY_PHASE', active: false, maxVolatileBps: 0 });
            }
        }

        return { newState, actions };
    }

    public transition(newState: GuardianState): void {
        this.currentState = newState;
    }
}
