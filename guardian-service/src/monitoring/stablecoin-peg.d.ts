import type { PriceData } from '../blockchain/reader.js';
export interface StablecoinReport {
    token: string;
    status: 'HEALTHY' | 'WATCH' | 'WARNING' | 'CRITICAL';
    deviationBps: number;
    rateOfChange: number;
    price: bigint;
}
export interface RecommendedAction {
    type: 'REDUCE_ALLOCATION' | 'EXIT_POSITION';
    token: string;
    targetReductionPct?: number;
    urgency: 'HIGH' | 'CRITICAL';
    reason: string;
}
export interface StablecoinHealthReport {
    reports: StablecoinReport[];
    actions: RecommendedAction[];
    overallHealth: 'HEALTHY' | 'WATCH' | 'WARNING' | 'CRITICAL';
}
export declare class StablecoinPegMonitor {
    private previousPrices;
    assess(prices: Map<string, PriceData> | PriceData[]): StablecoinHealthReport;
    checkPegs(prices: PriceData[]): 'HEALTHY' | 'WATCH' | 'WARNING' | 'CRITICAL';
    private worstStatus;
}
export declare const stablecoinMonitor: StablecoinPegMonitor;
//# sourceMappingURL=stablecoin-peg.d.ts.map