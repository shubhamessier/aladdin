import { Decimal } from 'decimal.js';
import { PortfolioState } from '../portfolio/state.js';
export class VaRCalculator {
    pythonApiUrl;
    constructor(pythonApiUrl = 'http://localhost:8000') {
        this.pythonApiUrl = pythonApiUrl;
    }
    async computeVaR(portfolio, prices) {
        const payload = {
            allocations: Object.fromEntries(Object.entries(portfolio.getAllocations()).map(([k, v]) => [k, v.toNumber()])),
            totalValueUSD: portfolio.totalValueUSD.toNumber(),
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
            return {
                var95_1d: new Decimal(data.var95_1d),
                var99_1d: new Decimal(data.var99_1d),
                cvar95_1d: new Decimal(data.cvar95_1d),
                cvar99_1d: new Decimal(data.cvar99_1d),
                lvar: new Decimal(data.lvar),
            };
        }
        catch (error) {
            console.error('VaR calculation failed, falling back to safe defaults', error);
            // In a fail-safe system, fallback to high estimates
            return {
                var95_1d: portfolio.totalValueUSD.mul(0.1),
                var99_1d: portfolio.totalValueUSD.mul(0.15),
                cvar95_1d: portfolio.totalValueUSD.mul(0.12),
                cvar99_1d: portfolio.totalValueUSD.mul(0.2),
                lvar: portfolio.totalValueUSD.mul(0.25),
            };
        }
    }
}
//# sourceMappingURL=var.js.map