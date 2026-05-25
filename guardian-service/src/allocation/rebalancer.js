import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
export class Rebalancer {
    generateTrades(currentAllocations, targetAllocations, portfolio, prices, opts) {
        const trades = [];
        const totalValue = portfolio.totalValueUSD;
        if (totalValue.isZero() || Object.keys(targetAllocations).length === 0)
            return trades;
        const usdDiffs = {};
        for (const asset of new Set([...Object.keys(currentAllocations), ...Object.keys(targetAllocations)])) {
            const currentW = currentAllocations[asset] || new Decimal(0);
            const targetW = targetAllocations[asset] || new Decimal(0);
            usdDiffs[asset] = targetW.sub(currentW).mul(totalValue);
        }
        const buys = [];
        const sells = [];
        for (const [asset, diff] of Object.entries(usdDiffs)) {
            if (diff.abs().lt(1000))
                continue; // Dust trade
            if (diff.gt(0))
                buys.push({ asset, usd: diff });
            else
                sells.push({ asset, usd: diff.abs() });
        }
        const stableAsset = 'USDC'; // Simplification
        for (const sell of sells) {
            if (sell.asset === stableAsset)
                continue;
            let tradeSize = Decimal.min(sell.usd, opts.maxTradeUSD);
            trades.push({
                type: 'SWAP',
                tokenIn: sell.asset,
                tokenOut: stableAsset,
                amountUSD: tradeSize,
                amountInUSD: tradeSize,
                amountOutUSD: tradeSize
            });
        }
        for (const buy of buys) {
            if (buy.asset === stableAsset)
                continue;
            let tradeSize = Decimal.min(buy.usd, opts.maxTradeUSD);
            trades.push({
                type: 'SWAP',
                tokenIn: stableAsset,
                tokenOut: buy.asset,
                amountUSD: tradeSize,
                amountInUSD: tradeSize,
                amountOutUSD: tradeSize
            });
        }
        return trades;
    }
}
//# sourceMappingURL=rebalancer.js.map