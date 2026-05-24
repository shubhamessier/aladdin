export class PortfolioState {
    totalValueUSD = 0;
    totalStableValueUSD = 0;
    drawdownFromHWM = 0;
    derivativePositions = [];
    positions = new Map();
    bounds = new Map();
    static reconstruct(onChainState) {
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
    getAllocations() {
        const allocations = {};
        if (this.totalValueUSD === 0)
            return allocations;
        for (const [asset, position] of this.positions.entries()) {
            allocations[asset] = position.valueUSD / this.totalValueUSD;
        }
        return allocations;
    }
    getAllocationBounds() {
        return Object.fromEntries(this.bounds.entries());
    }
    getTierLimits() {
        // Placeholder for tier limits logic
        return {};
    }
}
//# sourceMappingURL=state.js.map