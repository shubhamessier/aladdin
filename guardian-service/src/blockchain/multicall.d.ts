export interface MulticallRequest {
    target: string;
    callData: string;
}
export interface MulticallResult {
    success: boolean;
    returnData: string;
}
export declare function multicall(requests: MulticallRequest[]): Promise<MulticallResult[]>;
//# sourceMappingURL=multicall.d.ts.map