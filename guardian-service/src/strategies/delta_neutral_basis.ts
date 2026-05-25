import { Decimal } from 'decimal.js';

export interface BasisMarket {
    asset: string;
    spotPrice: Decimal;
    perpPrice: Decimal;
    fundingRate8H: Decimal;
}

export interface BasisPosition {
    asset: string;
    spotSize: Decimal;
    perpSize: Decimal; // Should match spotSize but short (negative)
}

export class DeltaNeutralBasisStrategy {
    private readonly ENTRY_THRESHOLD_APY = new Decimal('0.12'); // 12% annualized
    private readonly EXIT_THRESHOLD_APY = new Decimal('0.02');  // 2% annualized

    public evaluateMarket(market: BasisMarket, currentPosition: BasisPosition | null): { action: 'ENTER' | 'EXIT' | 'HOLD', targetSizeUSD?: Decimal } {
        // Annualize funding rate (3 periods per day * 365 days)
        const annualizedFunding = market.fundingRate8H.mul(3 * 365);
        
        // Calculate basis premium/discount
        const basisPremium = market.perpPrice.sub(market.spotPrice).div(market.spotPrice);

        if (currentPosition) {
            // Already in a trade. Evaluate exit conditions.
            // If funding crashes below exit threshold, or basis inverts heavily (backwardation).
            if (annualizedFunding.lt(this.EXIT_THRESHOLD_APY) || basisPremium.lt(new Decimal('-0.005'))) {
                return { action: 'EXIT' };
            }
            return { action: 'HOLD' };
        } else {
            // Not in a trade. Evaluate entry conditions.
            // Positive funding > 12% and contango (perp > spot)
            if (annualizedFunding.gt(this.ENTRY_THRESHOLD_APY) && basisPremium.gt(new Decimal('0.001'))) {
                return { action: 'ENTER', targetSizeUSD: new Decimal('100000') }; // Allocating $100k per leg
            }
            return { action: 'HOLD' };
        }
    }
}
