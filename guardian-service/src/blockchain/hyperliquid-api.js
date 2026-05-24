export class HyperliquidClient {
    baseUrl = 'https://api.hyperliquid.xyz';
    // Read funding rates for all perp markets
    async getFundingRates() {
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'metaAndAssetCtxs' }),
        });
        const data = await response.json();
        // Parse and return funding rates per market
        return data[1].map((ctx, index) => ({
            market: data[0].universe[index].name,
            fundingRate: parseFloat(ctx.funding),
        }));
    }
    // Read order book for a specific market (for liquidity depth analysis)
    async getOrderBook(market) {
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'l2Book', coin: market }),
        });
        const data = await response.json();
        // Parse bids and asks with sizes at each price level
        return data;
    }
    // Place a perp order (requires signing with the vault's key)
    async placeOrder(params) {
        // Hyperliquid uses EIP-712 typed data signing for orders
        // Sign the order with the guardian's signer key
        // Submit via /exchange endpoint
        // (Stubbed for now as we don't have the signer integration here)
        return { status: 'ok', response: { type: 'order', data: params } };
    }
    // Get all open positions for the vault's address
    async getPositions(vaultAddress) {
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'clearinghouseState', user: vaultAddress }),
        });
        const data = await response.json();
        // Parse positions with entry price, size, unrealized PnL, margin
        return data.assetPositions.map((p) => p.position);
    }
    // Get historical funding rate payments
    async getFundingHistory(vaultAddress, startTime) {
        // For carry/basis trade tracking
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'userFunding', user: vaultAddress, startTime }),
        });
        const data = await response.json();
        return data;
    }
}
//# sourceMappingURL=hyperliquid-api.js.map