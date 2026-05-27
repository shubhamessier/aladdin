import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any
import pandas as pd
import numpy as np
import yaml
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MockMarketData:
    def __init__(self, prices: pd.DataFrame): 
        self.prices = prices
        self.returns_log = np.log(prices / prices.shift(1)).fillna(0)
    def slice_by_index(self, s: int, e: int) -> Any: 
        return MockMarketData(self.prices.iloc[s:e])

# Add python-risk to path
risk_engine_path = Path(__file__).resolve().parent.parent / "python-risk"
if str(risk_engine_path) not in sys.path:
    sys.path.append(str(risk_engine_path))

from backtest.engine.simulator import TreasurySimulator
from backtest.engine.circuit_breaker import CircuitBreakerConfig
from backtest.data.fetcher import DataFetcher
from backtest.reporting.terminal import print_performance_report, print_simulation_summary
from backtest.reporting.charts import (
    generate_nav_comparison, 
    generate_drawdown_comparison, 
    generate_allocation_area_chart
)
from backtest.analysis.metrics import calculate_performance_metrics
from backtest.analysis.attribution import decompose_returns
from backtest.optimizer.main import run_full_optimization, export_optimal_config
from backtest.engine.strategies import (
    AllocationStrategy, EqualWeightStrategy, RiskParityStrategy, 
    RegimeAdaptiveStrategy, StaticConservativeStrategy, MinVarianceStrategy, 
    BlackLittermanStrategy, StrategyConfig, RiskParityConfig, 
    RegimeAdaptiveConfig, StaticConservativeConfig
)

from backtest.data.funding import fetch_funding_rates
from backtest.data.lending import fetch_lending_rates
from backtest.engine.yield_engine import YieldEngine

def get_strategy(strat_dict: dict[str, Any]) -> AllocationStrategy:
    name = strat_dict.get('name', 'Equal Weight')
    if name == 'Risk Parity':
        return RiskParityStrategy(RiskParityConfig(**strat_dict))
    elif name == 'Regime-Adaptive':
        return RegimeAdaptiveStrategy(RegimeAdaptiveConfig(**strat_dict))
    elif name == 'Static Conservative':
        return StaticConservativeStrategy(StaticConservativeConfig(**strat_dict))
    elif name == 'Min Variance':
        return MinVarianceStrategy(StrategyConfig(**strat_dict))
    elif name == 'Black-Litterman':
        return BlackLittermanStrategy(StrategyConfig(**strat_dict))
    else:
        return EqualWeightStrategy(StrategyConfig(**strat_dict))

def load_config(config_path: str) -> dict[str, Any]:
    with open(config_path, 'r') as f:
        config: dict[str, Any] = yaml.safe_load(f)
        return config

def run_simulation(config_file: str, monte_carlo: bool, output_dir: str) -> None:
    config = load_config(config_file)
    sim_config = config.get('simulation', {})
    start_date = datetime.strptime(sim_config.get('start_date', '2023-01-01'), '%Y-%m-%d')
    end_date = datetime.strptime(sim_config.get('end_date', '2024-01-01'), '%Y-%m-%d')
    assets = sim_config.get('assets', ['BTC', 'ETH', 'USDC'])
    initial_cash = sim_config.get('initial_cash', 1000000.0)
    source = sim_config.get('data_source', 'binance')
    risk_free_rate = sim_config.get('risk_free_rate', 0.05)

    # Fetch pre-warmup data (90 days before start). datetime.timestamp() on a naive datetime
    # interprets it as local time; force UTC by using calendar.timegm.
    import calendar
    def _to_unix_utc(dt: datetime) -> int:
        return calendar.timegm(dt.timetuple())

    warmup_start = start_date - timedelta(days=90)
    
    interval = sim_config.get('interval', '1d')
    print(f"Fetching market data for {assets} (including 90d warmup) from {warmup_start.date()} to {end_date.date()} at interval={interval}...")
    fetcher = DataFetcher(cache_dir="backtest/cache")

    fetched: dict[str, pd.DataFrame] = {}
    for asset in assets:
        df = fetcher.fetch_ohlcv(asset, _to_unix_utc(warmup_start), _to_unix_utc(end_date), source=source, interval=interval)
        if not df.empty:
            fetched[asset] = df[['close']].rename(columns={'close': asset})
        else:
            logger.warning(f"No data for {asset}; dropping from universe")

    if not fetched:
        raise RuntimeError("No market data fetched for any asset. Aborting.")

    missing_assets = [a for a in assets if a not in fetched]
    if missing_assets:
        print(f"Warning: dropped {missing_assets} due to empty fetch")
        assets = [a for a in assets if a in fetched]

    # All per-asset series are already reindexed to the same grid; align by index intersection.
    common_index = None
    for asset, df in fetched.items():
        idx = df.index
        common_index = idx if common_index is None else common_index.intersection(idx)
    if common_index is None or len(common_index) == 0:
        raise RuntimeError("No overlapping timestamps across assets. Aborting.")

    price_history = pd.concat([fetched[a].reindex(common_index) for a in assets], axis=1)

    # Hard fail-loud guards: no NaN, no non-positive prices, monotonic increasing index.
    if price_history.isna().any().any():
        na_per_col = price_history.isna().sum()
        raise RuntimeError(f"price_history has NaN after reindex. NaN per column:\n{na_per_col}")
    if (price_history <= 0).any().any():
        bad = (price_history <= 0).sum()
        raise RuntimeError(f"price_history has non-positive values:\n{bad}")
    if not price_history.index.is_monotonic_increasing:
        raise RuntimeError("price_history index is not monotonic increasing")
    if price_history.index.has_duplicates:
        raise RuntimeError("price_history index has duplicates")

    # Confirm cadence
    diffs = price_history.index.to_series().diff().dropna().unique()
    if len(diffs) != 1:
        raise RuntimeError(f"price_history has mixed cadence. Unique diffs: {diffs}")
    print(f"price_history: shape={price_history.shape}, cadence={diffs[0]}, range={price_history.index[0]}..{price_history.index[-1]}")

    # Real Data Audit #3 & #4: Fetch real funding, lending, and depth
    print("Fetching real funding and lending series...")
    stable_assets_ref = ["USDC", "USDT", "DAI"]
    volatile_assets = [a for a in assets if a not in stable_assets_ref]
    
    funding_series = {}
    for asset in volatile_assets:
        try:
            df_funding = fetch_funding_rates(asset, _to_unix_utc(warmup_start), _to_unix_utc(end_date))
            if not df_funding.empty:
                funding_series[asset] = df_funding["funding_rate"]
        except Exception as e:
            print(f"Warning: Could not fetch funding for {asset}: {e}")

    lending_series = {}
    for asset in stable_assets_ref:
        if asset in assets:
            try:
                df_lending = fetch_lending_rates(asset, _to_unix_utc(warmup_start), _to_unix_utc(end_date))
                if not df_lending.empty:
                    lending_series[asset] = df_lending["lending_rate"]
            except Exception as e:
                print(f"Warning: Could not fetch lending for {asset}: {e}")

    depth_by_asset = {}
    for asset in assets:
        if asset in volatile_assets:
            try:
                # Audit #5: Use 25bps depth for conservative slippage
                book = fetcher.fetch_l2_depth_snapshot(asset)
                depth_by_asset[asset] = book.get("depth_25bps_usd", 1_000_000)
            except Exception:
                depth_by_asset[asset] = 1_000_000
        else:
            # Stables have deep books, assume $20M for USDC/USDT/DAI
            depth_by_asset[asset] = 20_000_000

    yield_engine = YieldEngine(funding_series=funding_series, lending_series=lending_series)

    # Split pre-warmup and simulation data
    pre_warmup_prices = price_history[price_history.index < start_date]
    sim_prices = price_history[price_history.index >= start_date]

    # Benchmark = equal-weight VOLATILE-only price index (stables would dilute the line).
    bench_assets = [a for a in assets if a not in ("USDC", "USDT", "DAI")]
    if not bench_assets:
        bench_assets = assets  # degenerate but well-defined
    first_row = sim_prices[bench_assets].iloc[0]
    if (first_row <= 0).any():
        raise RuntimeError(f"benchmark first row has non-positive price: {first_row.to_dict()}")
    benchmark = (sim_prices[bench_assets] / first_row).mean(axis=1) * initial_cash
    
    strategies = config.get('strategies', [{'name': 'Equal Weight'}])
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cb_cfg_dict = config.get('circuit_breaker', {})
    cb_config = CircuitBreakerConfig(**cb_cfg_dict)
    summary_data = {}

    for strat in strategies:
        strat_name = strat.get('name', 'Unknown Strategy')
        print(f"\n[{strat_name}] Starting Simulation...")
        
        strategy_obj = get_strategy(strat)
        
        sim = TreasurySimulator(
            initial_cash=initial_cash,
            start_date=start_date,
            end_date=end_date,
            assets=assets,
            circuit_breaker_config=cb_config,
            strategy=strategy_obj,
            risk_free_rate=risk_free_rate,
            yield_engine=yield_engine,
            depth_by_asset=depth_by_asset
        )
        sim.load_market_data(sim_prices)
        sim.run(pre_warmup_data=pre_warmup_prices)
        
        history_df = pd.DataFrame(sim.history)
        if history_df.empty:
            print(f"[{strat_name}] No history generated.")
            continue
        history_df.set_index('timestamp', inplace=True)
        history_df.index = pd.to_datetime(history_df.index)
        
        metrics = calculate_performance_metrics(history_df, risk_free_rate=risk_free_rate,
                                                annualization_factor=sim.annualization_factor)
        attribution = decompose_returns(history_df, benchmark, risk_free_rate=risk_free_rate,
                                        annualization_factor=sim.annualization_factor)

        if not metrics:
            raise RuntimeError(f"[{strat_name}] calculate_performance_metrics returned empty. "
                               f"history len={len(history_df)}, first_pv={history_df['portfolio_value'].iloc[0]}, "
                               f"nan_count={history_df['portfolio_value'].isna().sum()}")

        print_simulation_summary(sim.history)
        print_performance_report(metrics, attribution)

        safe_name = strat_name.replace(" ", "_").lower()

        generate_nav_comparison(history_df, benchmark, output_dir=output_dir, filename=f"nav_comparison_{safe_name}.png")
        generate_drawdown_comparison(history_df, benchmark, output_dir=output_dir, filename=f"drawdown_comparison_{safe_name}.png")

        csv_path = f"{output_dir}/{safe_name}_history.csv"
        history_df.to_csv(csv_path)

        # Sanity gate: at least 95% non-null portfolio_value
        nonnull_frac = history_df['portfolio_value'].notna().mean()
        if nonnull_frac < 0.95:
            raise RuntimeError(
                f"[{strat_name}] history has only {nonnull_frac*100:.1f}% non-null portfolio_value rows. "
                f"Refusing to publish broken output."
            )

        monthly_returns_path = f"{output_dir}/monthly_returns_{safe_name}.csv"
        monthly_returns = history_df['portfolio_value'].resample('ME').last().pct_change().dropna()
        if len(monthly_returns) == 0:
            logger.warning(f"[{strat_name}] monthly_returns empty (run too short)")
        monthly_returns.to_csv(monthly_returns_path, header=['monthly_return'])

        summary_data[strat_name] = {'metrics': metrics, 'attribution': attribution}

        if monte_carlo:
            print(f"[{strat_name}] Running Monte Carlo projections...")
            try:
                from risk_engine.monte_carlo import simulate_portfolio
                from risk_engine.covariance import build_covariance
                
                final_val = history_df['portfolio_value'].iloc[-1]
                final_weights = sim.portfolio.weights
                
                recent_returns = sim_prices.pct_change().tail(252).fillna(0)
                cov = build_covariance(recent_returns)
                corr = np.corrcoef(recent_returns.values.T)
                
                ann_returns = recent_returns.mean() * 252
                vols = recent_returns.std() * np.sqrt(252)
                
                current_values = np.array([final_weights.get(a, 0.0) * final_val for a in assets])
                mc_res = simulate_portfolio(
                    current_values=current_values,
                    annualized_returns=ann_returns.values,
                    annualized_volatilities=vols.values,
                    correlation=corr,
                    horizon_days=90,
                    n_simulations=10000
                )
                
                summary_data[strat_name]['monte_carlo'] = {
                    'var_95': mc_res.var_95,
                    'cvar_95': mc_res.cvar_95,
                    'expected_max_drawdown': mc_res.expected_max_drawdown,
                    'prob_ruin_30pct': mc_res.prob_ruin_30pct,
                    'mean_return': mc_res.mean_return
                }
                print(f"[{strat_name}] Monte Carlo projection complete.")
                
            except Exception as e:
                print(f"[{strat_name}] Monte Carlo projection failed: {e}")
                
    summary_path = f"{output_dir}/summary.json"
    with open(summary_path, 'w') as f:
        json.dump(summary_data, f, indent=4)
    print(f"\nAll summary statistics exported to {summary_path}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperliquid Autonomous Treasury System: Backtest CLI")
    parser.add_argument('--config', type=str, default='backtest/config/default.yaml', help='Path to YAML configuration file')
    parser.add_argument('--monte-carlo', action='store_true', help='Run Monte Carlo projections')
    parser.add_argument('--output-dir', type=str, default='backtest/output', help='Directory for results')
    parser.add_argument('--optimize', action='store_true', help='Run parameter optimization')
    parser.add_argument('--optimize-strategy', type=str, default='risk_parity', help='Strategy to optimize')
    parser.add_argument('--export-optimal', type=str, help='Path to export YAML')
    
    args = parser.parse_args()
    
    if args.optimize:
        config = load_config(args.config)
        sim_config = config.get('simulation', {})
        start_date = datetime.strptime(sim_config.get('start_date', '2023-01-01'), '%Y-%m-%d')
        end_date = datetime.strptime(sim_config.get('end_date', '2024-01-01'), '%Y-%m-%d')
        assets = sim_config.get('assets', ['BTC', 'ETH', 'USDC'])
        fetcher = DataFetcher(cache_dir="backtest/cache")
        dfs = []
        for asset in assets:
            df = fetcher.fetch_ohlcv(asset, int(start_date.timestamp()), int(end_date.timestamp()))
            if not df.empty:
                df = df[['close']].rename(columns={'close': asset})
                dfs.append(df)
        price_history = pd.concat(dfs, axis=1).ffill().bfill()
        
        market_data = MockMarketData(price_history)
        results = run_full_optimization(market_data, config, strategy=args.optimize_strategy)
        if args.export_optimal: export_optimal_config(results, args.export_optimal)
        return

    run_simulation(args.config, args.monte_carlo, args.output_dir)

if __name__ == '__main__':
    main()
