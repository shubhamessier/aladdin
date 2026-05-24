export class StateMachine {
    currentState = 'INITIALIZING';
    getCurrentState() {
        return this.currentState;
    }
    evaluate(input) {
        if (input.circuitBreakerLevel >= 3 || input.oracleHealth === 'STALE' || input.protocolHealth === 'DOWN' || input.stablecoinHealth === 'CRITICAL') {
            return 'EMERGENCY';
        }
        if (input.circuitBreakerLevel >= 1 || input.oracleHealth === 'SUSPECT' || input.currentDrawdown > 0.15 || input.stablecoinHealth === 'WARNING') {
            return 'RESTRICTED';
        }
        if (input.oracleHealth === 'DEGRADED' || input.protocolHealth === 'DEGRADED' || input.gasPrice > 100000000000n) { // Example high gas
            return 'DEGRADED';
        }
        return 'HEALTHY';
    }
    transition(newState) {
        // Implement transition logic, logging, and side effects if necessary
        this.currentState = newState;
    }
}
//# sourceMappingURL=state-machine.js.map