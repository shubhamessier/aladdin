"""
Hedging engine with proper margin accounting, funding accrual, MTM, and liquidation.

Design:
- Hedges are SHORT perpetual positions against spot exposure.
- Margin is deducted from `portfolio.cash` on open; returned on close.
- Funding is booked to `cash` per step (8h cadence collapsed to per-step pro-rata).
- Realized PnL on close is booked to cash; unrealized PnL is reported via MTM only.
- A position is force-liquidated when:
      cash_segment_for_position + unrealized_pnl < maintenance_margin_fraction * margin_usd
  At liquidation the position is closed at an adverse fill (taker + crisis slippage)
  and the residual margin (after slippage) is returned to cash. If unrealized loss
  exceeds the margin, the position is "bust" — margin is fully consumed, and the
  remaining negative PnL is debited from cash (a normal-shock leverage event).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import math
import logging
import numpy as np
import pandas as pd
from pydantic import BaseModel

from .portfolio import PortfolioState, DerivativePosition

logger = logging.getLogger(__name__)


class HedgingConfig(BaseModel):
    # Hedge ratio of spot delta to net-short by regime
    regime_hedge_ratios: Dict[str, float] = {"bull": 0.20, "uncertain": 0.50, "crisis": 0.80}
    target_leverage: float = 3.0           # how much notional per $ of margin
    maintenance_margin_fraction: float = 0.5  # liquidate when equity < 0.5 * margin
    min_hedge_adjustment_usd: float = 1000.0
    open_close_taker_bps: float = 3.5      # one-side taker fee in bps
    liquidation_slippage_bps: float = 25.0 # adverse fill on forced close


class HedgingEngine:
    def __init__(self, config: HedgingConfig):
        self.config = config

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _token_for(market: str) -> str:
        return market.split("-")[0]

    def _funding_8h_for(self, portfolio: PortfolioState, asset: str, date: pd.Timestamp, regime: str, yield_engine) -> float:
        if yield_engine is None:
            return 0.0
        try:
            return float(yield_engine.get_funding_rate_8h(asset, date, regime))
        except Exception:
            return 0.0

    # ------------------------------------------------------------------ public API
    def advance_step(
        self,
        portfolio: PortfolioState,
        prices: Dict[str, float],
        date: pd.Timestamp,
        bars_per_day: int,
        regime: str = "uncertain",
        yield_engine=None,
    ) -> float:
        """
        Per-step bookkeeping on existing positions:
          - Funding accrual (8h cadence pro-rated to bar)
          - MTM update (sets pos.current_price + unrealized_pnl)
          - Liquidation check; force-close busts

        Returns net funding pnl this step (positive = received).
        """
        if not portfolio.derivative_positions:
            return 0.0

        funding_pnl_step = 0.0
        survivors: List[DerivativePosition] = []
        bars_per_day = max(1, bars_per_day)
        funding_intervals_per_bar = 3.0 / bars_per_day  # 3 funding intervals per day

        for pos in portfolio.derivative_positions:
            token = self._token_for(pos.market)
            mark = prices.get(token)
            if mark is None or mark <= 0:
                survivors.append(pos)
                continue

            # 1. MTM
            sign = 1.0 if pos.direction == "long" else -1.0
            pos.current_price = float(mark)
            pos.unrealized_pnl = pos.notional_usd * (mark / pos.entry_price - 1.0) * sign
            pos.days_open += 1.0 / bars_per_day

            # 2. Funding accrual: long pays positive funding, short receives positive funding.
            # `pos.cumulative_funding` is a running counter; per-step PnL goes to cash.
            rate_8h = self._funding_8h_for(portfolio, token, date, regime, yield_engine)
            funding_payment_step = -sign * pos.notional_usd * rate_8h * funding_intervals_per_bar
            pos.cumulative_funding += funding_payment_step
            portfolio.cash += funding_payment_step
            funding_pnl_step += funding_payment_step

            # 3. Liquidation: equity = margin + unrealized_pnl + (cross-margin spot value)
            # In a unified trading account, if you are short a perp and hold the underlying spot, 
            # the spot acts as collateral, preventing cash-drain liquidations.
            spot_value = portfolio.positions.get(token, 0.0)
            cross_margin_equity = pos.margin_usd + pos.unrealized_pnl
            
            # If we are short, and hold spot, we can use the spot value to prevent liquidation
            if pos.direction == "short":
                # The amount of underlying token we are shorting
                short_token_amount = pos.notional_usd / pos.entry_price
                # The current USD value of that shorted token amount
                current_short_value = short_token_amount * mark
                # We can collateralize up to the current value of the short
                collateral_value = min(spot_value, current_short_value)
                cross_margin_equity += collateral_value
                
            if cross_margin_equity < self.config.maintenance_margin_fraction * pos.margin_usd:
                # Force close at adverse fill
                adverse_slip = self.config.liquidation_slippage_bps / 10000.0
                liq_loss = pos.notional_usd * adverse_slip
                realized = pos.unrealized_pnl - liq_loss
                # Return margin to cash; book realized PnL
                portfolio.cash += pos.margin_usd + realized
                logger.info(
                    f"LIQUIDATED {pos.market} at {date}: notional={pos.notional_usd:.0f}, "
                    f"margin={pos.margin_usd:.0f}, realized={realized:.2f}, equity_before={cross_margin_equity:.2f}"
                )
                continue  # drop from survivors

            survivors.append(pos)

        portfolio.derivative_positions = survivors
        return funding_pnl_step

    def set_target_hedges(
        self,
        portfolio: PortfolioState,
        prices: Dict[str, float],
        regime: str,
        date: pd.Timestamp,
        cost_model,
        rolling_vol: float,
        explicit_target_ratios: Optional[Dict[str, float]] = None
    ) -> None:
        """
        Compute target short notional per asset and open/close positions to reach it.
        Margin is deducted from / returned to cash.
        If explicit_target_ratios is provided, uses those multipliers (e.g. 1.0 = 100% of spot is shorted)
        Otherwise falls back to regime-based ratio.
        """
        default_target_ratio = self.config.regime_hedge_ratios.get(regime, 0.5)
        leverage = max(1.0, self.config.target_leverage)

        # Net spot exposure per token (in USD)
        spot_exposure: Dict[str, float] = {}
        for token, amount in portfolio.positions.items():
            if token in ("USDC", "USDT", "DAI"):
                continue
            if token not in prices or prices.get(token, 0) <= 0:
                continue
            spot_exposure[token] = float(amount)

        # Current short notional per token
        current_short: Dict[str, float] = {}
        for pos in portfolio.derivative_positions:
            token = self._token_for(pos.market)
            sign = 1.0 if pos.direction == "long" else -1.0
            current_short[token] = current_short.get(token, 0.0) + sign * pos.notional_usd
        # current_short positive = net long; negative = net short.

        for token, spot in spot_exposure.items():
            if spot <= 0:
                continue
            
            if explicit_target_ratios and token in explicit_target_ratios:
                target_ratio = explicit_target_ratios[token]
            else:
                target_ratio = default_target_ratio
                
            desired_short = spot * target_ratio  # positive scalar: how much SHORT notional we want
            desired_signed = -desired_short      # signed delta in same convention as current_short
            change = desired_signed - current_short.get(token, 0.0)
            if abs(change) < self.config.min_hedge_adjustment_usd:
                continue

            market = f"{token}-PERP"
            mark = prices[token]

            # change < 0 → increase short exposure (open or grow short).
            # change > 0 → reduce short exposure (close partial or close + flip).
            if change < 0:
                # OPEN/grow a short.
                add_notional = abs(change)
                margin_required = add_notional / leverage
                taker_fee = add_notional * (self.config.open_close_taker_bps / 10000.0)
                if portfolio.cash < margin_required + taker_fee:
                    # Degrade gracefully — we'll re-attempt on the next rebalance once cash
                    # has been freed by funding accrual or spot drift. Logged at DEBUG to
                    # avoid spamming the run output.
                    logger.debug(
                        f"hedger: insufficient cash to open hedge for {token} "
                        f"({portfolio.cash:.0f} < {margin_required + taker_fee:.0f}); skipping"
                    )
                    continue
                portfolio.cash -= (margin_required + taker_fee)

                # Find existing short or open new
                existing = next((p for p in portfolio.derivative_positions
                                 if p.market == market and p.direction == "short"), None)
                if existing is not None:
                    # Weighted-average entry price
                    new_total = existing.notional_usd + add_notional
                    existing.entry_price = float((existing.entry_price * existing.notional_usd + mark * add_notional) / new_total)
                    existing.notional_usd = new_total
                    existing.margin_usd += margin_required
                else:
                    portfolio.derivative_positions.append(DerivativePosition(
                        market=market,
                        direction="short",
                        notional_usd=add_notional,
                        entry_price=float(mark),
                        current_price=float(mark),
                        margin_usd=margin_required,
                        unrealized_pnl=0.0,
                        cumulative_funding=0.0,
                        open_date=date,
                    ))
            else:
                # change > 0: reduce short exposure (partial close).
                close_amount = min(change, abs(current_short.get(token, 0.0)))
                if close_amount <= 0:
                    continue
                # Close from existing short(s) FIFO
                remaining = close_amount
                for pos in [p for p in portfolio.derivative_positions
                            if p.market == market and p.direction == "short"]:
                    if remaining <= 0:
                        break
                    take = min(remaining, pos.notional_usd)
                    fraction = take / pos.notional_usd if pos.notional_usd > 0 else 0.0
                    realized = pos.unrealized_pnl * fraction
                    margin_returned = pos.margin_usd * fraction
                    taker_fee = take * (self.config.open_close_taker_bps / 10000.0)
                    portfolio.cash += margin_returned + realized - taker_fee
                    pos.notional_usd -= take
                    pos.margin_usd -= margin_returned
                    pos.unrealized_pnl -= realized
                    remaining -= take

                # Drop empty positions
                portfolio.derivative_positions = [p for p in portfolio.derivative_positions
                                                  if p.notional_usd > 1e-6]
