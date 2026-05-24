export interface FundingRate {
    market: string;
    fundingRate: number;
}
export interface OrderBook {
    coin: string;
    levels: [
        {
            px: string;
            sz: string;
            n: number;
        }[],
        {
            px: string;
            sz: string;
            n: number;
        }[]
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
export declare class HyperliquidClient {
    private baseUrl;
    getFundingRates(): Promise<FundingRate[]>;
    getOrderBook(market: string): Promise<OrderBook>;
    placeOrder(params: {
        market: string;
        isBuy: boolean;
        size: number;
        price: number;
        orderType: 'limit' | 'market';
        reduceOnly: boolean;
    }): Promise<OrderResult>;
    getPositions(vaultAddress: string): Promise<Position[]>;
    getFundingHistory(vaultAddress: string, startTime: number): Promise<FundingPayment[]>;
}
//# sourceMappingURL=hyperliquid-api.d.ts.map