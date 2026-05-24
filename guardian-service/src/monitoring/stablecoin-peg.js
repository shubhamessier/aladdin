const stablecoinConfigs = new Map([
    ['0xUSDC', {}],
    ['0xUSDT', {}],
    ['0xDAI', {}]
]);
export class StablecoinPegMonitor {
    previousPrices = new Map();
    assess(prices) {
        const reports = [];
        let priceMap;
        if (Array.isArray(prices)) {
            priceMap = new Map();
            for (const p of prices) {
                priceMap.set(p.token, p);
            }
        }
        else {
            priceMap = prices;
        }
        for (const [token, config] of stablecoinConfigs.entries()) {
            const priceData = priceMap.get(token);
            if (!priceData)
                continue;
            const deviation = Math.abs(Number(priceData.price) / 1e18 - 1.0);
            const deviationBps = Math.round(deviation * 10000);
            // Classify
            let status;
            if (deviationBps <= 10) { // <= 0.10%
                status = 'HEALTHY';
            }
            else if (deviationBps <= 50) { // <= 0.50%
                status = 'WATCH';
            }
            else if (deviationBps <= 200) { // <= 2.00%
                status = 'WARNING';
            }
            else { // > 2.00%
                status = 'CRITICAL';
            }
            // Rate of change: compare to the price from the last cycle
            const prevPrice = this.previousPrices.get(token);
            let rateOfChange = 0;
            if (prevPrice) {
                rateOfChange = (Number(priceData.price) - Number(prevPrice)) / Number(prevPrice);
            }
            // A stablecoin that's depegging AND accelerating is much worse
            if (status === 'WATCH' && rateOfChange < -0.001) {
                status = 'WARNING'; // Upgrade severity if actively falling
            }
            if (status === 'WARNING' && rateOfChange < -0.005) {
                status = 'CRITICAL'; // 0.5% drop per cycle while already depegged
            }
            reports.push({ token, status, deviationBps, rateOfChange, price: priceData.price });
            this.previousPrices.set(token, priceData.price);
        }
        // Recommendations based on report
        const actions = [];
        for (const report of reports) {
            if (report.status === 'WARNING') {
                actions.push({
                    type: 'REDUCE_ALLOCATION',
                    token: report.token,
                    targetReductionPct: 50,
                    urgency: 'HIGH',
                    reason: `Stablecoin ${report.token} deviating ${report.deviationBps} bps from peg`,
                });
            }
            if (report.status === 'CRITICAL') {
                actions.push({
                    type: 'EXIT_POSITION',
                    token: report.token,
                    urgency: 'CRITICAL',
                    reason: `CRITICAL DEPEG: ${report.token} at ${report.deviationBps} bps from peg, rate ${(report.rateOfChange * 100).toFixed(2)}%/cycle`,
                });
            }
        }
        return { reports, actions, overallHealth: this.worstStatus(reports) };
    }
    // For legacy index.ts compatibility
    checkPegs(prices) {
        return this.assess(prices).overallHealth;
    }
    worstStatus(reports) {
        let worst = 0;
        const levels = { 'HEALTHY': 0, 'WATCH': 1, 'WARNING': 2, 'CRITICAL': 3 };
        for (const report of reports) {
            if (levels[report.status] > worst) {
                worst = levels[report.status];
            }
        }
        return Object.keys(levels).find((key) => levels[key] === worst) || 'HEALTHY';
    }
}
export const stablecoinMonitor = new StablecoinPegMonitor();
//# sourceMappingURL=stablecoin-peg.js.map