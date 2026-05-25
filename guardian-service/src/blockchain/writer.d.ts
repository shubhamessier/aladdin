import { Decimal } from 'decimal.js';
import { type TransactionReceipt } from './ethers-types.js';
export interface ExecutionResult {
    success: boolean;
    txHash?: string;
    error?: string;
    gasUsed?: bigint;
}
export interface ProposedAction {
    type: string;
    amountUSD: Decimal;
    target?: string;
    callData?: string;
    value?: bigint;
    description?: string;
}
export interface ValidatedAction extends ProposedAction {
    simResult: any;
}
export interface ExecutionConfig {
    gasPrice: bigint;
    useMEVProtection: boolean;
    executionAlgo: 'almgren-chriss' | 'twap' | 'immediate';
    maxRetries: number;
}
export declare class TransactionSimulationError extends Error {
    description: string;
    causeError: any;
    constructor(description: string, causeError: any);
}
export declare class GasPriceTooHighError extends Error {
    gasPrice: bigint;
    threshold: bigint;
    constructor(gasPrice: bigint, threshold: bigint);
}
export declare class TransactionRevertedError extends Error {
    hash: string;
    description: string;
    reason: string;
    constructor(hash: string, description: string, reason: string);
}
export declare class TransactionTimeoutError extends Error {
    hash: string;
    description: string;
    timeout: number;
    constructor(hash: string, description: string, timeout: number);
}
export declare class TransactionManager {
    private nonceTracker;
    private pendingTxs;
    private getSigner;
    sendTransaction(params: {
        to: string;
        data: string;
        value?: bigint;
        gasLimit?: bigint;
        description: string;
    }): Promise<TransactionReceipt>;
    private waitForConfirmation;
    private speedUp;
    private decodeRevertReason;
}
export declare class BlockchainWriter {
    private gasUsedThisCycle;
    private txManager;
    execute(action: ValidatedAction, config: ExecutionConfig): Promise<ExecutionResult>;
    getGasUsedThisCycle(): bigint;
    resetGasCounter(): void;
}
//# sourceMappingURL=writer.d.ts.map