export class AllocationOptimizer {
    pythonApiUrl;
    constructor(pythonApiUrl = 'http://localhost:8000') {
        this.pythonApiUrl = pythonApiUrl;
    }
    async optimize(inputs) {
        const payload = {
            method: inputs.method,
            expectedReturns: inputs.expectedReturns,
            covariance: inputs.covariance,
            constraints: inputs.constraints,
            views: inputs.views,
        };
        try {
            let endpoint = '/optimize/risk-parity';
            if (inputs.method === 'mean_variance')
                endpoint = '/optimize/mean-variance';
            if (inputs.method === 'black_litterman')
                endpoint = '/optimize/black-litterman';
            if (inputs.method === 'min_variance')
                endpoint = '/optimize/min-cvar';
            const response = await fetch(`${this.pythonApiUrl}${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) {
                throw new Error(`Optimization failed: ${response.statusText}`);
            }
            const data = await response.json();
            return data.allocations;
        }
        catch (error) {
            console.error('Optimizer API failed, returning empty allocations to avoid bad trades', error);
            return {};
        }
    }
}
//# sourceMappingURL=optimizer.js.map