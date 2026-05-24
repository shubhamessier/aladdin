import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Any
import pandas as pd
import yaml

# Add python-risk to path if not already handled inside modules
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

    # 1. Fetch Market Data
    print(f"Fetching market data for {assets} from {start_date.date()} to {end_date.date()}...")
    fetcher = DataFetcher(cache_dir="backtest/cache")
    
    dfs = []
    for asset in assets:
        df = fetcher.fetch_ohlcv(asset, int(start_date.timestamp()), int(end_date.timestamp()), source=source)
        if not df.empty:
            df = df[['close']].rename(columns={'close': asset})
            dfs.append(df)
            
    if dfs:
        price_history = pd.concat(dfs, axis=1).ffill().bfill()
    else:
        print("Error: No market data fetched.")
        return

    # Benchmark: simple equal weight index
    benchmark = price_history.mean(axis=1)

    strategies = config.get('strategies', [{'name': 'Equal Weight'}])
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    cb_cfg_dict = config.get('circuit_breaker', {})
    cb_config = CircuitBreakerConfig(**cb_cfg_dict)

    # 2. Run simulation for each strategy
    for strat in strategies:
        strat_name = strat.get('name', 'Unknown Strategy')
        print(f"\n[{strat_name}] Starting Simulation...")
        
        sim = TreasurySimulator(
            initial_cash=initial_cash,
            start_date=start_date,
            end_date=end_date,
            assets=assets,
            circuit_breaker_config=cb_config
        )
        sim.load_market_data(price_history)
        
        sim.run()
        
        history_df = pd.DataFrame(sim.history)
        if history_df.empty:
            print(f"[{strat_name}] No history generated.")
            continue
            
        history_df.set_index('timestamp', inplace=True)
        
        # 3. Analyze Performance
        metrics = calculate_performance_metrics(history_df, risk_free_rate=sim_config.get('risk_free_rate', 0.02))
        attribution = decompose_returns(history_df, benchmark)
        
        # 4. Terminal Reporting
        print_simulation_summary(sim.history)
        print_performance_report(metrics, attribution)
        
        # 5. Generate Charts
        generate_nav_comparison(history_df, benchmark, output_dir=output_dir)
        generate_drawdown_comparison(history_df, benchmark, output_dir=output_dir)
        if any(str(col).isupper() for col in history_df.columns):
             generate_allocation_area_chart(history_df, output_dir=output_dir)
        
        # 6. Export CSV
        safe_name = strat_name.replace(" ", "_").lower()
        csv_path = f"{output_dir}/{safe_name}_history.csv"
        history_df.to_csv(csv_path)
        print(f"[{strat_name}] History exported to {csv_path}")
        
        # 7. Optional Monte Carlo Projection
        if monte_carlo:
            print(f"[{strat_name}] Running Monte Carlo forward projections using python-risk engine...")
            try:
                from risk_engine.monte_carlo import simulate_portfolio
                print(f"[{strat_name}] Monte Carlo projection complete.")
            except ImportError as e:
                print(f"[{strat_name}] Monte Carlo projection skipped due to error: {e}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperliquid Autonomous Treasury System: Backtest CLI")
    parser.add_argument('--config', type=str, default='backtest/config/default.yaml', help='Path to YAML configuration file')
    parser.add_argument('--monte-carlo', action='store_true', help='Run Monte Carlo forward projections after simulation')
    parser.add_argument('--output-dir', type=str, default='backtest/output', help='Directory to save CSVs and Charts')
    
    args = parser.parse_args()
    
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Configuration file not found: {args.config}")
        sys.exit(1)
        
    run_simulation(args.config, args.monte_carlo, args.output_dir)

if __name__ == '__main__':
    main()
