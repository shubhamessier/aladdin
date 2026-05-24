const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const WAD = 1000000000000000000n;
const logger = {
    info: (msg) => console.log(JSON.stringify(msg)),
    warn: (msg) => console.warn(JSON.stringify(msg)),
    error: (msg) => console.error(JSON.stringify(msg)),
};
// Mocks for dependencies
const oracleAdapter = {
    getPrice: async (token) => ({ price: 1000n * WAD }),
};
const routerSelector = {
    getBestRouter: async (tIn, tOut, amt) => '0xRouter',
};
const vault = {
    executeSwap: async (params) => {
        // Mock successful swap
        return {
            amountOut: params.amountIn, // assume 1:1 for mock
            gasUsed: 100000n,
        };
    },
};
export class TWAPExecutor {
    async execute(params) {
        const slices = this.computeSlices(params);
        const results = [];
        let totalAmountOut = 0n;
        let totalGasUsed = 0n;
        for (let i = 0; i < slices.length; i++) {
            const slice = slices[i];
            // Wait for the scheduled time
            const waitMs = slice.scheduledTime - Date.now();
            if (waitMs > 0)
                await sleep(waitMs);
            try {
                // Get fresh price and compute minAmountOut for this slice
                const currentPriceOut = await oracleAdapter.getPrice(params.tokenOut);
                const currentPriceIn = await oracleAdapter.getPrice(params.tokenIn);
                const fairOut = (slice.amountIn * currentPriceOut.price) / currentPriceIn.price;
                const minOut = fairOut * (10000n - BigInt(params.maxSlippageBps)) / 10000n;
                // Execute the slice
                const router = await routerSelector.getBestRouter(params.tokenIn, params.tokenOut, slice.amountIn);
                const result = await vault.executeSwap({
                    tokenIn: params.tokenIn,
                    tokenOut: params.tokenOut,
                    amountIn: slice.amountIn,
                    minAmountOut: minOut,
                    deadline: BigInt(Math.floor(Date.now() / 1000) + 120),
                    router,
                });
                results.push({
                    index: i,
                    amountIn: slice.amountIn,
                    amountOut: result.amountOut,
                    executionPrice: result.amountOut * WAD / slice.amountIn,
                    gasUsed: result.gasUsed,
                    timestamp: Date.now(),
                    status: 'success',
                });
                totalAmountOut += result.amountOut;
                totalGasUsed += result.gasUsed;
            }
            catch (err) {
                logger.warn({ slice: i, error: err.message, msg: 'TWAP slice failed' });
                results.push({ index: i, amountIn: slice.amountIn, status: 'failed', error: err.message, timestamp: Date.now() });
                // Decision: retry this slice, skip it, or abort?
                // Default: skip and continue. If > 30% of slices fail, abort remaining.
                const failCount = results.filter(r => r.status === 'failed').length;
                if (failCount > slices.length * 0.3) {
                    logger.error({ failCount, msg: 'TWAP aborting — too many slice failures' });
                    break;
                }
            }
        }
        // Compute TWAP execution quality
        const executedSlices = results.filter(r => r.status === 'success');
        const executedTotalIn = executedSlices.reduce((sum, r) => sum + r.amountIn, 0n);
        const twapPrice = executedSlices.length > 0 && executedTotalIn > 0n
            ? totalAmountOut * WAD / executedTotalIn
            : 0n;
        return {
            totalAmountIn: executedTotalIn,
            totalAmountOut,
            twapPrice,
            sliceResults: results,
            totalGasUsed,
            executionDurationMs: Date.now() - (results[0]?.timestamp ?? Date.now()),
        };
    }
    computeSlices(params) {
        const baseSize = params.totalAmountIn / BigInt(params.numSlices);
        const slices = [];
        let remaining = params.totalAmountIn;
        const now = Date.now();
        for (let i = 0; i < params.numSlices; i++) {
            let size = i === params.numSlices - 1
                ? remaining // Last slice gets the remainder (no dust)
                : baseSize;
            // Randomize size ±30%
            if (params.randomization && i < params.numSlices - 1) {
                const jitter = Number(baseSize) * (Math.random() * 0.6 - 0.3);
                size = baseSize + BigInt(Math.floor(jitter));
                if (size > remaining)
                    size = remaining;
                if (size <= 0n)
                    size = 1n;
            }
            // Randomize timing ±20%
            let scheduledTime = now + i * params.intervalSeconds * 1000;
            if (params.randomization) {
                const timeJitter = params.intervalSeconds * 1000 * (Math.random() * 0.4 - 0.2);
                scheduledTime += Math.floor(timeJitter);
            }
            slices.push({ index: i, amountIn: size, scheduledTime });
            remaining -= size;
        }
        return slices;
    }
}
//# sourceMappingURL=twap.js.map