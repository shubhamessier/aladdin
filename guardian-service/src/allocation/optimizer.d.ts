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
export declare class AllocationOptimizer {
    private readonly pythonApiUrl;
    constructor(pythonApiUrl?: string);
    optimize(inputs: OptimizerInputs): Promise<Record<string, Decimal>>;
}
//# sourceMappingURL=optimizer.d.ts.map