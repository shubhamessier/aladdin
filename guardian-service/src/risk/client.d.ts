export interface VaRRequest {
    portfolioValueUSD: number;
}
export interface VaRResponse {
    var_95_1d: number;
    var_99_1d: number;
    cvar_95_1d: number;
    cvar_99_1d: number;
    isEstimate?: boolean;
    source?: string;
}
export declare class RiskEngineError extends Error {
}
export declare class RiskEngineClient {
    private baseUrl;
    private timeout;
    private circuitBreaker;
    private cache;
    computeVaR(params: VaRRequest): Promise<VaRResponse>;
    computeVaRWithFallback(params: VaRRequest): Promise<VaRResponse>;
    healthCheck(): Promise<boolean>;
    initializeModels(history: any): Promise<boolean>;
}
//# sourceMappingURL=client.d.ts.map