import { Decimal } from 'decimal.js';
import { provider, signer, type TransactionReceipt } from './ethers-types.js';

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

interface PendingTransaction {
    hash: string;
    nonce: number;
    description: string;
    submittedAt: number;
    maxFeePerGas: bigint;
    speedUpAttempted?: boolean;
    originalParams: any;
}

export class TransactionSimulationError extends Error {
    constructor(public description: string, public causeError: any) {
        super(`Simulation failed for ${description}: ${causeError.message}`);
    }
}

export class GasPriceTooHighError extends Error {
    constructor(public gasPrice: bigint, public threshold: bigint) {
        super(`Gas price ${gasPrice} exceeds threshold ${threshold}`);
    }
}

export class TransactionRevertedError extends Error {
    constructor(public hash: string, public description: string, public reason: string) {
        super(`Tx ${hash} (${description}) reverted: ${reason}`);
    }
}

export class TransactionTimeoutError extends Error {
    constructor(public hash: string, public description: string, public timeout: number) {
        super(`Tx ${hash} (${description}) timed out after ${timeout}ms`);
    }
}

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

const logger = {
    info: (msg: any) => console.log(JSON.stringify(msg)),
    warn: (msg: any) => console.warn(JSON.stringify(msg)),
    error: (msg: any) => console.error(JSON.stringify(msg)),
};

const config = {
    maxGasPriceWei: '100000000000', // 100 gwei
    txTimeoutSeconds: 60,
};

export class TransactionManager {
    private nonceTracker: Map<string, number> = new Map();
    private pendingTxs: Map<string, PendingTransaction> = new Map();

    private async getSigner() {
        return signer;
    }

    async sendTransaction(params: {
        to: string;
        data: string;
        value?: bigint;
        gasLimit?: bigint;
        description: string;
    }): Promise<TransactionReceipt> {
        const currentSigner = await this.getSigner();
        const address = await currentSigner.getAddress();

        // 1. Get nonce
        let nonce = this.nonceTracker.get(address);
        if (nonce === undefined) {
            nonce = await provider.getTransactionCount(address, 'pending');
            this.nonceTracker.set(address, nonce);
        }

        // 2. Estimate gas with buffer
        let gasLimit = params.gasLimit;
        if (!gasLimit) {
            try {
                const estimate = await provider.estimateGas({ ...params, from: address });
                gasLimit = estimate * 120n / 100n; // 20% buffer
            } catch (err: any) {
                logger.error({ description: params.description, error: err, msg: 'Gas estimation failed — tx would revert' });
                throw new TransactionSimulationError(params.description, err);
            }
        }

        // 3. Get gas price (EIP-1559)
        const feeData = await provider.getFeeData();
        const maxFeePerGas = feeData.maxFeePerGas ? feeData.maxFeePerGas * 110n / 100n : 20000000000n; 
        const maxPriorityFeePerGas = feeData.maxPriorityFeePerGas ? feeData.maxPriorityFeePerGas * 110n / 100n : 1000000000n;

        // 4. Check gas price against guardian's max threshold
        if (maxFeePerGas > BigInt(config.maxGasPriceWei)) {
            logger.warn({ maxFeePerGas: maxFeePerGas.toString(), threshold: config.maxGasPriceWei, msg: 'Gas price above threshold — deferring tx' });
            throw new GasPriceTooHighError(maxFeePerGas, BigInt(config.maxGasPriceWei));
        }

        const originalParams = {
            to: params.to,
            data: params.data,
            value: params.value ?? 0n,
            nonce,
            gasLimit,
            maxFeePerGas,
            maxPriorityFeePerGas,
            type: 2, // EIP-1559
        };

        // 5. Send
        const tx = await currentSigner.sendTransaction(originalParams);

        this.nonceTracker.set(address, nonce + 1);
        this.pendingTxs.set(tx.hash, {
            hash: tx.hash,
            nonce,
            description: params.description,
            submittedAt: Date.now(),
            maxFeePerGas,
            originalParams
        });

        logger.info({ txHash: tx.hash, nonce, description: params.description, msg: 'Transaction submitted' });

        // 6. Wait for confirmation with timeout and speed-up
        return this.waitForConfirmation(tx.hash, params.description);
    }

    private async waitForConfirmation(txHash: string, description: string): Promise<TransactionReceipt> {
        const timeout = config.txTimeoutSeconds * 1000;
        const startTime = Date.now();
        let currentHash = txHash;

        while (Date.now() - startTime < timeout) {
            const receipt = await provider.getTransactionReceipt(currentHash);

            if (receipt) {
                this.pendingTxs.delete(currentHash);

                if (receipt.status === 0) {
                    const reason = await this.decodeRevertReason(receipt);
                    logger.error({ txHash: currentHash, description, reason, msg: 'Transaction reverted' });
                    throw new TransactionRevertedError(currentHash, description, reason);
                }

                logger.info({
                    txHash: currentHash,
                    gasUsed: receipt.gasUsed.toString(),
                    blockNumber: receipt.blockNumber,
                    description,
                    msg: 'Transaction confirmed'
                });
                return receipt;
            }

            const elapsed = Date.now() - startTime;
            if (elapsed > timeout / 2) {
                const pending = this.pendingTxs.get(currentHash);
                if (pending && !pending.speedUpAttempted) {
                    logger.warn({ txHash: currentHash, msg: 'Transaction slow — attempting speed-up' });
                    const newHash = await this.speedUp(pending);
                    if (newHash) {
                        currentHash = newHash;
                    }
                    pending.speedUpAttempted = true;
                }
            }

            await sleep(2000); // Poll every 2 seconds
        }

        logger.error({ txHash: currentHash, description, msg: 'Transaction timed out' });
        throw new TransactionTimeoutError(currentHash, description, timeout);
    }

    private async speedUp(pending: PendingTransaction): Promise<string | null> {
        try {
            const newMaxFee = pending.maxFeePerGas * 130n / 100n;
            const currentSigner = await this.getSigner();
            const tx = await currentSigner.sendTransaction({
                ...pending.originalParams,
                nonce: pending.nonce,
                maxFeePerGas: newMaxFee,
                maxPriorityFeePerGas: newMaxFee / 5n,
            });
            logger.info({ oldHash: pending.hash, newHash: tx.hash, newMaxFee: newMaxFee.toString(), msg: 'Speed-up transaction sent' });
            return tx.hash;
        } catch (err: any) {
            logger.warn({ error: err.message, msg: 'Speed-up failed' });
            return null;
        }
    }

    private async decodeRevertReason(receipt: TransactionReceipt): Promise<string> {
        try {
            const tx = await provider.getTransaction(receipt.hash);
            await provider.call(
                { to: tx.to, data: tx.data, from: tx.from, value: tx.value },
                receipt.blockNumber
            );
            return 'Unknown revert (call succeeded in simulation)';
        } catch (err: any) {
            return err.reason || err.data || 'Unknown revert';
        }
    }
}

export class BlockchainWriter {
    private gasUsedThisCycle: bigint = 0n;
    private txManager = new TransactionManager();

    public async execute(action: ValidatedAction, config: ExecutionConfig): Promise<ExecutionResult> {
        try {
            const receipt = await this.txManager.sendTransaction({
                to: action.target || '0x0000000000000000000000000000000000000000',
                data: action.callData || '0x',
                ...(action.value !== undefined ? { value: action.value } : {}),
                description: `Action: ${action.type}`,
            });
            
            this.gasUsedThisCycle += receipt.gasUsed;
            return { success: true, txHash: receipt.hash, gasUsed: receipt.gasUsed };
        } catch (err: any) {
            return { success: false, error: err.message };
        }
    }

    public getGasUsedThisCycle(): bigint {
        return this.gasUsedThisCycle;
    }

    public resetGasCounter(): void {
        this.gasUsedThisCycle = 0n;
    }
}
