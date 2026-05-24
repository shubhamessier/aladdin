export interface TransactionReceipt {
    status: number;
    hash: string;
    gasUsed: bigint;
    blockNumber: number;
}
export interface FeeData {
    maxFeePerGas: bigint | null;
    maxPriorityFeePerGas: bigint | null;
}
export interface Signer {
    getAddress(): Promise<string>;
    sendTransaction(tx: any): Promise<{ hash: string }>;
}
export interface Provider {
    getTransactionCount(address: string, blockTag: string): Promise<number>;
    estimateGas(tx: any): Promise<bigint>;
    getFeeData(): Promise<FeeData>;
    getTransactionReceipt(hash: string): Promise<TransactionReceipt | null>;
    getTransaction(hash: string): Promise<any>;
    call(tx: any, blockNumber: number): Promise<string>;
}
export const provider: Provider = {} as any;
export const signer: Signer = {} as any;
