import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';
import type { ProposedAction } from '../blockchain/writer.js';

export interface RebalanceOptions {
    maxTradeUSD: Decimal;
    maxSlippageBps: Decimal;
}

export interface RebalanceTrade extends ProposedAction {
    type: 'SWAP';
    tokenIn: string;
    tokenOut: string;
    amountInUSD: Decimal;
    amountOutUSD: Decimal;
}

export class Rebalancer {
    public generateTrades(
        currentAllocations: Record<string, Decimal>,
        targetAllocations: Record<string, Decimal>,
        portfolio: PortfolioState,
        prices: PriceData[],
        opts: RebalanceOptions
    ): RebalanceTrade[] {
        const trades: RebalanceTrade[] = [];
        const totalValue = portfolio.totalValueUSD;

        if (totalValue.isZero() || Object.keys(targetAllocations).length === 0) return trades;

        const usdDiffs: Record<string, Decimal> = {};
        
        for (const asset of new Set([...Object.keys(currentAllocations), ...Object.keys(targetAllocations)])) {
            const currentW = currentAllocations[asset] || new Decimal(0);
            const targetW = targetAllocations[asset] || new Decimal(0);
            usdDiffs[asset] = targetW.sub(currentW).mul(totalValue);
        }

        const buys: { asset: string; usd: Decimal }[] = [];
        const sells: { asset: string; usd: Decimal }[] = [];

        for (const [asset, diff] of Object.entries(usdDiffs)) {
            if (diff.abs().lt(1000)) continue; // Dust trade
            
            if (diff.gt(0)) buys.push({ asset, usd: diff });
            else sells.push({ asset, usd: diff.abs() });
        }

        const stableAsset = 'USDC'; // Simplification

        for (const sell of sells) {
            if (sell.asset === stableAsset) continue;
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
            if (buy.asset === stableAsset) continue;
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
