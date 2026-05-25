import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Any
import pandas as pd
import yaml
import json

class MockMarketData:
    def __init__(self, prices: pd.DataFrame): 
        self.prices = prices
        # Mock other needed data
        self.returns_log = prices.pct_change().fillna(0)
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

    print(f"Fetching market data for {assets} from {start_date.date()} to {end_date.date()}...")
    fetcher = DataFetcher(cache_dir="backtest/cache")
    dfs = []
    for asset in assets:
        df = fetcher.fetch_ohlcv(asset, int(start_date.timestamp()), int(end_date.timestamp()), source=source)
        if not df.empty:
            df = df[['close']].rename(columns={'close': asset})
            dfs.append(df)

    if not dfs:
        print("Error: No market data fetched.")
        return

    price_history = pd.concat(dfs, axis=1).ffill().bfill()
    fetched_assets = list(price_history.columns)
    missing = [a for a in assets if a not in fetched_assets]
    if missing:
        print(f"Warning: Could not fetch {missing}. Proceeding with {fetched_assets}.")
        assets = fetched_assets

    price_history = price_history.dropna(axis=1, how='all')
    if price_history.empty:
        print("Error: All asset price data is NaN.")
        return
    assets = [a for a in assets if a in price_history.columns]

    # BUG-13: Benchmark is normalized price index
    benchmark = (price_history / price_history.iloc[0]).mean(axis=1) * initial_cash
    
    strategies = config.get('strategies', [{'name': 'Equal Weight'}])
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cb_cfg_dict = config.get('circuit_breaker', {})
    cb_config = CircuitBreakerConfig(**cb_cfg_dict)
    summary_data = {}

    for strat in strategies:
        strat_name = strat.get('name', 'Unknown Strategy')
        print(f"\n[{strat_name}] Starting Simulation...")
        
        # BUG-01: Instantiate correct strategy
        strategy_obj = get_strategy(strat)
        
        sim = TreasurySimulator(
            initial_cash=initial_cash,
            start_date=start_date,
            end_date=end_date,
            assets=assets,
            circuit_breaker_config=cb_config,
            strategy=strategy_obj
        )
        sim.load_market_data(price_history)
        sim.run()
        
        history_df = pd.DataFrame(sim.history)
        if history_df.empty:
            print(f"[{strat_name}] No history generated.")
            continue
        history_df.set_index('timestamp', inplace=True)
        history_df.index = pd.to_datetime(history_df.index)
        
        metrics = calculate_performance_metrics(history_df, risk_free_rate=sim_config.get('risk_free_rate', 0.02))
        attribution = decompose_returns(history_df, benchmark)
        
        print_simulation_summary(sim.history)
        print_performance_report(metrics, attribution)
        
        safe_name = strat_name.replace(" ", "_").lower()
        
        # BUG-09: unique chart filenames
        generate_nav_comparison(history_df, benchmark, output_dir=output_dir, filename=f"nav_comparison_{safe_name}.png")
        generate_drawdown_comparison(history_df, benchmark, output_dir=output_dir, filename=f"drawdown_comparison_{safe_name}.png")
        
        csv_path = f"{output_dir}/{safe_name}_history.csv"
        history_df.to_csv(csv_path)
        
        monthly_returns_path = f"{output_dir}/monthly_returns_{safe_name}.csv"
        monthly_returns = history_df['portfolio_value'].resample('ME').last().pct_change().dropna()
        monthly_returns.to_csv(monthly_returns_path, header=['monthly_return'])
        
        summary_data[strat_name] = {'metrics': metrics, 'attribution': attribution}

        if monte_carlo:
            print(f"[{strat_name}] Running Monte Carlo projections...")
                
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
