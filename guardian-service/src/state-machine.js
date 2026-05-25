import { Decimal } from 'decimal.js';
export class StateMachine {
    currentState = 'INITIALIZING';
    getCurrentState() {
        return this.currentState;
    }
    evaluate(input) {
        if (input.circuitBreakerLevel >= 3 || input.oracleHealth === 'STALE' || input.protocolHealth === 'DOWN' || input.stablecoinHealth === 'CRITICAL') {
            return 'EMERGENCY';
        }
        if (input.circuitBreakerLevel >= 1 || input.oracleHealth === 'SUSPECT' || input.currentDrawdown.gt(0.15) || input.stablecoinHealth === 'WARNING') {
            return 'RESTRICTED';
        }
        if (input.oracleHealth === 'DEGRADED' || input.protocolHealth === 'DEGRADED' || input.gasPrice > 100000000000n) { // Example high gas
            return 'DEGRADED';
        }
        return 'HEALTHY';
    }
    computeEffectiveHWM(state, currentTimeSeconds) {
        if (state.hwmAbsolute.isZero())
            return new Decimal(0);
        const elapsed = currentTimeSeconds - state.hwmLastUpdatedTimestamp;
        if (elapsed <= 0)
            return state.hwmAbsolute;
        const halflives = Math.floor(elapsed / state.hwmDecayHalflifeSeconds);
        if (halflives >= 64)
            return new Decimal(0);
        let decayedValue = state.hwmAbsolute.div(new Decimal(2).pow(halflives));
        const remainder = elapsed % state.hwmDecayHalflifeSeconds;
        if (remainder > 0) {
            const decayFactor = new Decimal(remainder).div(new Decimal(2).mul(state.hwmDecayHalflifeSeconds));
            decayedValue = decayedValue.sub(decayedValue.mul(decayFactor));
        }
        return decayedValue.floor();
    }
    checkCBDecayConditions(cbState, currentTimeSeconds) {
        if (cbState.currentCBLevel === 0)
            return false;
        const oneDay = 86400;
        const timeSinceSet = currentTimeSeconds - cbState.cbLevelSetTimestamp;
        if (timeSinceSet >= oneDay && cbState.cbConsecutiveStableDays >= 1) {
            return true;
        }
        return false;
    }
    processState(input, cbState, currentTimeSeconds) {
        const newState = this.evaluate(input);
        const actions = [];
        if (this.checkCBDecayConditions(cbState, currentTimeSeconds)) {
            actions.push({ type: 'DECAY_CB_LEVEL' });
        }
        if (newState === 'RESTRICTED' || newState === 'EMERGENCY') {
            if (this.currentState !== 'RESTRICTED' && this.currentState !== 'EMERGENCY') {
                actions.push({ type: 'SET_RECOVERY_PHASE', active: true, maxVolatileBps: new Decimal(2000) }); // e.g. cap at 20%
            }
        }
        else if (newState === 'HEALTHY' || newState === 'DEGRADED') {
            if (this.currentState === 'RESTRICTED' || this.currentState === 'EMERGENCY') {
                actions.push({ type: 'SET_RECOVERY_PHASE', active: false, maxVolatileBps: new Decimal(0) });
            }
        }
        return { newState, actions };
    }
    transition(newState) {
        this.currentState = newState;
    }
}
//# sourceMappingURL=state-machine.js.map