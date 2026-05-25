import { Decimal } from 'decimal.js';

export interface OptimizerConstraints {
    bounds: Record<string, any>;
    tierLimits: Record<string, any>;
    maxHHI: Decimal;
    stableMinimum: Decimal;
}

export interface OptimizerInputs {
    method: string;
    expectedReturns: Decimal[];
    covariance: Decimal[][];
    constraints: OptimizerConstraints;
    views: any[];
}

export class AllocationOptimizer {
    private readonly pythonApiUrl: string;

    constructor(pythonApiUrl: string = 'http://localhost:8000') {
        this.pythonApiUrl = pythonApiUrl;
    }

    public async optimize(inputs: OptimizerInputs): Promise<Record<string, Decimal>> {
        const payload = {
            method: inputs.method,
            expectedReturns: inputs.expectedReturns.map(r => r.toNumber()),
            covariance: inputs.covariance.map(row => row.map(c => c.toNumber())),
            constraints: {
                ...inputs.constraints,
                maxHHI: inputs.constraints.maxHHI.toNumber(),
                stableMinimum: inputs.constraints.stableMinimum.toNumber(),
            },
            views: inputs.views,
        };

        try {
            let endpoint = '/optimize/risk-parity';
            if (inputs.method === 'mean_variance') endpoint = '/optimize/mean-variance';
            if (inputs.method === 'black_litterman') endpoint = '/optimize/black-litterman';
            if (inputs.method === 'min_variance') endpoint = '/optimize/min-cvar';

            const response = await fetch(`${this.pythonApiUrl}${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                throw new Error(`Optimization failed: ${response.statusText}`);
            }

            const data = await response.json() as { allocations: Record<string, number> };
            const result: Record<string, Decimal> = {};
            for (const [asset, weight] of Object.entries(data.allocations)) {
                result[asset] = new Decimal(weight);
            }
            return result;
        } catch (error) {
            console.error('Optimizer API failed, returning empty allocations to avoid bad trades', error);
            return {};
        }
    }
}
