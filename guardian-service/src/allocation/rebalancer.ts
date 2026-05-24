import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
import type { ProposedAction } from '../blockchain/writer.js';

export interface RebalanceOptions {
    maxTradeUSD: number;
    maxSlippageBps: number;
}

export interface RebalanceTrade extends ProposedAction {
    type: 'SWAP';
    tokenIn: string;
    tokenOut: string;
    amountInUSD: number;
    amountOutUSD: number;
}

export class Rebalancer {
    public generateTrades(
        currentAllocations: Record<string, number>,
        targetAllocations: Record<string, number>,
        portfolio: PortfolioState,
        prices: PriceData[],
        opts: RebalanceOptions
    ): RebalanceTrade[] {
        const trades: RebalanceTrade[] = [];
        const totalValue = portfolio.totalValueUSD;

        if (totalValue === 0 || Object.keys(targetAllocations).length === 0) return trades;

        const usdDiffs: Record<string, number> = {};
        
        for (const asset of new Set([...Object.keys(currentAllocations), ...Object.keys(targetAllocations)])) {
            const currentW = currentAllocations[asset] || 0;
            const targetW = targetAllocations[asset] || 0;
            usdDiffs[asset] = (targetW - currentW) * totalValue;
        }

        const buys: { asset: string; usd: number }[] = [];
        const sells: { asset: string; usd: number }[] = [];

        for (const [asset, diff] of Object.entries(usdDiffs)) {
            if (Math.abs(diff) < 1000) continue; // Dust trade
            
            if (diff > 0) buys.push({ asset, usd: diff });
            else sells.push({ asset, usd: Math.abs(diff) });
        }

        const stableAsset = 'USDC'; // Simplification

        for (const sell of sells) {
            if (sell.asset === stableAsset) continue;
            let tradeSize = Math.min(sell.usd, opts.maxTradeUSD);
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
            if (buy.asset === stableAsset) continue;
            let tradeSize = Math.min(buy.usd, opts.maxTradeUSD);
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
