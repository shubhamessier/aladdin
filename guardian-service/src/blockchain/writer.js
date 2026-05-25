import { Decimal } from 'decimal.js';
import { provider, signer } from './ethers-types.js';
export class TransactionSimulationError extends Error {
    description;
    causeError;
    constructor(description, causeError) {
        super(`Simulation failed for ${description}: ${causeError.message}`);
        this.description = description;
        this.causeError = causeError;
    }
}
export class GasPriceTooHighError extends Error {
    gasPrice;
    threshold;
    constructor(gasPrice, threshold) {
        super(`Gas price ${gasPrice} exceeds threshold ${threshold}`);
        this.gasPrice = gasPrice;
        this.threshold = threshold;
    }
}
export class TransactionRevertedError extends Error {
    hash;
    description;
    reason;
    constructor(hash, description, reason) {
        super(`Tx ${hash} (${description}) reverted: ${reason}`);
        this.hash = hash;
        this.description = description;
        this.reason = reason;
    }
}
export class TransactionTimeoutError extends Error {
    hash;
    description;
    timeout;
    constructor(hash, description, timeout) {
        super(`Tx ${hash} (${description}) timed out after ${timeout}ms`);
        this.hash = hash;
        this.description = description;
        this.timeout = timeout;
    }
}
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const logger = {
    info: (msg) => console.log(JSON.stringify(msg)),
    warn: (msg) => console.warn(JSON.stringify(msg)),
    error: (msg) => console.error(JSON.stringify(msg)),
};
const config = {
    maxGasPriceWei: '100000000000', // 100 gwei
    txTimeoutSeconds: 60,
};
export class TransactionManager {
    nonceTracker = new Map();
    pendingTxs = new Map();
    async getSigner() {
        return signer;
    }
    async sendTransaction(params) {
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
            }
            catch (err) {
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
    async waitForConfirmation(txHash, description) {
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
    async speedUp(pending) {
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
        }
        catch (err) {
            logger.warn({ error: err.message, msg: 'Speed-up failed' });
            return null;
        }
    }
    async decodeRevertReason(receipt) {
        try {
            const tx = await provider.getTransaction(receipt.hash);
            await provider.call({ to: tx.to, data: tx.data, from: tx.from, value: tx.value }, receipt.blockNumber);
            return 'Unknown revert (call succeeded in simulation)';
        }
        catch (err) {
            return err.reason || err.data || 'Unknown revert';
        }
    }
}
export class BlockchainWriter {
    gasUsedThisCycle = 0n;
    txManager = new TransactionManager();
    async execute(action, config) {
        try {
            const receipt = await this.txManager.sendTransaction({
                to: action.target || '0x0000000000000000000000000000000000000000',
                data: action.callData || '0x',
                ...(action.value !== undefined ? { value: action.value } : {}),
                description: `Action: ${action.type}`,
            });
            this.gasUsedThisCycle += receipt.gasUsed;
            return { success: true, txHash: receipt.hash, gasUsed: receipt.gasUsed };
        }
        catch (err) {
            return { success: false, error: err.message };
        }
    }
    getGasUsedThisCycle() {
        return this.gasUsedThisCycle;
    }
    resetGasCounter() {
        this.gasUsedThisCycle = 0n;
    }
}
//# sourceMappingURL=writer.js.map