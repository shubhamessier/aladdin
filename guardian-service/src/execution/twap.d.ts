export interface TWAPParams {
    tokenIn: string;
    tokenOut: string;
    totalAmountIn: bigint;
    numSlices: number;
    intervalSeconds: number;
    maxSlippageBps: number;
    randomization: boolean;
}
export interface SliceResult {
    index: number;
    amountIn: bigint;
    amountOut?: bigint;
    executionPrice?: bigint;
    gasUsed?: bigint;
    timestamp: number;
    status: 'success' | 'failed';
    error?: string;
}
export interface TWAPResult {
    totalAmountIn: bigint;
    totalAmountOut: bigint;
    twapPrice: bigint;
    sliceResults: SliceResult[];
    totalGasUsed: bigint;
    executionDurationMs: number;
}
export declare class TWAPExecutor {
    execute(params: TWAPParams): Promise<TWAPResult>;
    private computeSlices;
}
//# sourceMappingURL=twap.d.ts.map