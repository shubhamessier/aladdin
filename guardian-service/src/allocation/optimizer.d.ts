export interface OptimizerConstraints {
    bounds: Record<string, any>;
    tierLimits: Record<string, any>;
    maxHHI: number;
    stableMinimum: number;
}
export interface OptimizerInputs {
    method: string;
    expectedReturns: number[];
    covariance: number[][];
    constraints: OptimizerConstraints;
    views: any[];
}
export declare class AllocationOptimizer {
    private readonly pythonApiUrl;
    constructor(pythonApiUrl?: string);
    optimize(inputs: OptimizerInputs): Promise<Record<string, number>>;
}
//# sourceMappingURL=optimizer.d.ts.map