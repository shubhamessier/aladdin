export interface FundingRate {
    market: string;
    fundingRate: number;
}

export interface OrderBook {
    coin: string;
    levels: [
        { px: string; sz: string; n: number }[], // Bids
        { px: string; sz: string; n: number }[]  // Asks
    ];
}

export interface OrderResult {
    status: string;
    response: any;
}

export interface Position {
    coin: string;
    entryPx: string;
    positionValue: string;
    returnOnEquity: string;
    unrealizedPnl: string;
    marginUsed: string;
}

export interface FundingPayment {
    coin: string;
    usdc: string;
    szi: string;
    fundingRate: string;
    time: number;
}

export class HyperliquidClient {
    private baseUrl: string = 'https://api.hyperliquid.xyz';

    // Read funding rates for all perp markets
    async getFundingRates(): Promise<FundingRate[]> {
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'metaAndAssetCtxs' }),
        });
        const data = await response.json();
        // Parse and return funding rates per market
        return data[1].map((ctx: any, index: number) => ({
            market: data[0].universe[index].name,
            fundingRate: parseFloat(ctx.funding),
        }));
    }

    // Read order book for a specific market (for liquidity depth analysis)
    async getOrderBook(market: string): Promise<OrderBook> {
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'l2Book', coin: market }),
        });
        const data = await response.json();
        // Parse bids and asks with sizes at each price level
        return data as OrderBook;
    }

    // Place a perp order (requires signing with the vault's key)
    async placeOrder(params: {
        market: string;
        isBuy: boolean;
        size: number;    // In base asset units
        price: number;   // Limit price
        orderType: 'limit' | 'market';
        reduceOnly: boolean;
    }): Promise<OrderResult> {
        // Hyperliquid uses EIP-712 typed data signing for orders
        // Sign the order with the guardian's signer key
        // Submit via /exchange endpoint
        // (Stubbed for now as we don't have the signer integration here)
        return { status: 'ok', response: { type: 'order', data: params } };
    }

    // Get all open positions for the vault's address
    async getPositions(vaultAddress: string): Promise<Position[]> {
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'clearinghouseState', user: vaultAddress }),
        });
        const data = await response.json();
        // Parse positions with entry price, size, unrealized PnL, margin
        return data.assetPositions.map((p: any) => p.position) as Position[];
    }

    // Get historical funding rate payments
    async getFundingHistory(vaultAddress: string, startTime: number): Promise<FundingPayment[]> {
        // For carry/basis trade tracking
        const response = await fetch(`${this.baseUrl}/info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'userFunding', user: vaultAddress, startTime }),
        });
        const data = await response.json();
        return data as FundingPayment[];
    }
}
