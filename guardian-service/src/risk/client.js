export class RiskEngineError extends Error {
}
const logger = {
    warn: (msg) => console.warn(JSON.stringify(msg)),
};
class CircuitBreaker {
    options;
    failures = 0;
    lastFailureTime = 0;
    state = 'CLOSED';
    constructor(options) {
        this.options = options;
    }
    async execute(action) {
        if (this.state === 'OPEN') {
            if (Date.now() - this.lastFailureTime > this.options.recoveryTimeout) {
                this.state = 'HALF_OPEN';
            }
            else {
                throw new RiskEngineError('Circuit breaker is OPEN');
            }
        }
        try {
            const result = await action();
            if (this.state === 'HALF_OPEN') {
                this.state = 'CLOSED';
                this.failures = 0;
            }
            return result;
        }
        catch (err) {
            this.failures++;
            this.lastFailureTime = Date.now();
            if (this.failures >= this.options.failureThreshold) {
                this.state = 'OPEN';
            }
            throw err;
        }
    }
}
export class RiskEngineClient {
    baseUrl = 'http://localhost:8000';
    timeout = 30000;
    circuitBreaker = new CircuitBreaker({
        failureThreshold: 3,
        recoveryTimeout: 60000,
    });
    cache = new Map();
    async computeVaR(params) {
        return this.circuitBreaker.execute(async () => {
            const controller = new AbortController();
            const id = setTimeout(() => controller.abort(), this.timeout);
            const response = await fetch(`${this.baseUrl}/risk/var`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params),
                signal: controller.signal,
            });
            clearTimeout(id);
            if (!response.ok) {
                const error = await response.text();
                throw new RiskEngineError(`VaR computation failed: ${response.status} ${error}`);
            }
            return response.json();
        });
    }
    async computeVaRWithFallback(params) {
        const MAX_RISK_CACHE_AGE_MS = 30 * 60 * 1000; // 30 minutes
        try {
            const result = await this.computeVaR(params);
            this.cache.set('var', { value: result, timestamp: Date.now() });
            return result;
        }
        catch (err) {
            logger.warn({ error: err.message, msg: 'Risk engine unavailable, using cached VaR' });
            const cached = this.cache.get('var');
            if (cached && Date.now() - cached.timestamp < MAX_RISK_CACHE_AGE_MS) {
                return cached.value;
            }
            // No cached value or too old — return a conservative fallback
            return {
                var_95_1d: params.portfolioValueUSD * 0.10,
                var_99_1d: params.portfolioValueUSD * 0.15,
                cvar_95_1d: params.portfolioValueUSD * 0.12,
                cvar_99_1d: params.portfolioValueUSD * 0.20,
                isEstimate: true,
                source: 'fallback',
            };
        }
    }
    async healthCheck() {
        // mock health check
        return true;
    }
    async initializeModels(history) {
        // mock initialization
        return true;
    }
}
//# sourceMappingURL=client.js.map