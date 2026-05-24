import { PortfolioState } from '../portfolio/state.js';
import type { PriceData } from '../blockchain/reader.js';

export interface VaRMetrics {
    var95_1d: number;
    var99_1d: number;
    cvar95_1d: number;
    cvar99_1d: number;
    lvar: number;
}

export class VaRCalculator {
    private readonly pythonApiUrl: string;

    constructor(pythonApiUrl: string = 'http://localhost:8000') {
        this.pythonApiUrl = pythonApiUrl;
    }

    public async computeVaR(portfolio: PortfolioState, prices: PriceData[]): Promise<VaRMetrics> {
        const payload = {
            allocations: portfolio.getAllocations(),
            totalValueUSD: portfolio.totalValueUSD,
            prices: prices.map(p => ({
                token: p.token,
                price: Number(p.price) / 1e18
            }))
        };

        try {
            const response = await fetch(`${this.pythonApiUrl}/risk/var`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                throw new Error(`Failed to compute VaR: ${response.statusText}`);
            }

            const data = await response.json() as VaRMetrics;
            return data;
        } catch (error) {
            console.error('VaR calculation failed, falling back to safe defaults', error);
            // In a fail-safe system, fallback to high estimates
            return {
                var95_1d: portfolio.totalValueUSD * 0.1,
                var99_1d: portfolio.totalValueUSD * 0.15,
                cvar95_1d: portfolio.totalValueUSD * 0.12,
                cvar99_1d: portfolio.totalValueUSD * 0.2,
                lvar: portfolio.totalValueUSD * 0.25,
            };
        }
    }
}
