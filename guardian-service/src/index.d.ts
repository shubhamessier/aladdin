import { Decimal } from 'decimal.js';
export declare class TransientError extends Error {
    readonly context: string;
    constructor(context: string, message: string);
}
export declare class FatalStateError extends Error {
    readonly context: string;
    constructor(context: string, message: string);
}
export declare class RateLimitError extends Error {
    constructor(message: string);
}
interface PortfolioExposure {
    asset: string;
    netDeltaUSD: Decimal;
    maintenanceMarginUSD: Decimal;
    unrealizedPnL: Decimal;
    cumulativeFundingUSD: Decimal;
}
interface MarketState {
    bid: Decimal;
    ask: Decimal;
    bidDepth: Decimal;
    askDepth: Decimal;
    vpin: Decimal;
    fundingRate8H: Decimal;
}
export interface OrderState {
    id: string;
    asset: string;
    side: 'buy' | 'sell';
    size: Decimal;
    filled: Decimal;
    status: 'open' | 'partial' | 'filled' | 'canceled';
}
export interface InventoryLedger {
    cashUSD: Decimal;
    positions: Map<string, Decimal>;
}
export declare function guardianTick(marketState: MarketState, exposures: PortfolioExposure[]): Promise<void>;
export {};
//# sourceMappingURL=index.d.ts.map