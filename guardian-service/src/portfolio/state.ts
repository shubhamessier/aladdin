import type { OnChainState, PriceData } from '../blockchain/reader.js';

export interface Position {
    asset: string;
    amount: bigint;
    valueUSD: number;
}

export interface DerivativePosition {
    market: string;
    isLong: boolean;
    sizeUSD: number;
    margin: number;
    unrealizedPnL: number;
}

export interface AssetAllocationBounds {
    maxBps: number;
    minBps: number;
}

export interface PortfolioLimits {
    maxHHI: number;
    minStableReserveBps: number;
}

export class PortfolioState {
    public totalValueUSD: number = 0;
    public totalStableValueUSD: number = 0;
    public drawdownFromHWM: number = 0;
    public derivativePositions: DerivativePosition[] = [];
    private positions: Map<string, Position> = new Map();
    private bounds: Map<string, AssetAllocationBounds> = new Map();

    public static reconstruct(onChainState: OnChainState): PortfolioState {
        const state = new PortfolioState();
        
        // Mock reconstruction logic
        state.totalValueUSD = onChainState.totalValueUSD || 0;
        state.drawdownFromHWM = onChainState.drawdown || 0;
        state.derivativePositions = onChainState.derivativePositions || [];

        for (const [asset, balance] of Object.entries(onChainState.balances || {})) {
            const priceData = onChainState.prices.find(p => p.token === asset);
            const price = priceData ? Number(priceData.price) / 1e18 : 0;
            const amount = BigInt(balance);
            const valueUSD = Number(amount) / 1e18 * price;
            
            state.positions.set(asset, { asset, amount, valueUSD });
        }

        return state;
    }

    public getAllocations(): Record<string, number> {
        const allocations: Record<string, number> = {};
        if (this.totalValueUSD === 0) return allocations;

        for (const [asset, position] of this.positions.entries()) {
            allocations[asset] = position.valueUSD / this.totalValueUSD;
        }
        return allocations;
    }

    public getAllocationBounds(): Record<string, AssetAllocationBounds> {
        return Object.fromEntries(this.bounds.entries());
    }

    public getTierLimits(): Record<string, unknown> {
        // Placeholder for tier limits logic
        return {};
    }
}
