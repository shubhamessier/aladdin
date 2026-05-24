import { PortfolioState } from '../portfolio/state.js';
export class VaRCalculator {
    pythonApiUrl;
    constructor(pythonApiUrl = 'http://localhost:8000') {
        this.pythonApiUrl = pythonApiUrl;
    }
    async computeVaR(portfolio, prices) {
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
            const data = await response.json();
            return data;
        }
        catch (error) {
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
//# sourceMappingURL=var.js.map