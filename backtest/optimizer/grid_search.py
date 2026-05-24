from datetime import datetime
import itertools
import copy
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd
from typing import List, Dict, Any, Optional
from backtest.optimizer.param_space import ParamSpec
from backtest.optimizer.scorer import OptimizationObjectives
from backtest.engine.simulator import TreasurySimulator

def _run_single_backtest(
    market_data: Any,
    base_config: Any,
    strategy: str,
    param_overrides: Dict[str, Any],
) -> Dict[str, Any]:
    """Module-level function for multiprocessing pickling."""
    config = copy.deepcopy(base_config)
    for key, value in param_overrides.items():
        if hasattr(config, key):
            setattr(config, key, value)
        elif key in config: # Handle if it's a dict
            config[key] = value
            
    
    sim_config = config.get('simulation', {})
    start_date = datetime.strptime(sim_config.get('start_date', '2023-01-01'), '%Y-%m-%d')
    end_date = datetime.strptime(sim_config.get('end_date', '2024-01-01'), '%Y-%m-%d')
    assets = sim_config.get('assets', ['BTC', 'ETH', 'USDC'])
    initial_cash = sim_config.get('initial_cash', 1000000.0)
    
    from backtest.engine.circuit_breaker import CircuitBreakerConfig
    cb_cfg_dict = config.get('circuit_breaker', {})
    cb_config = CircuitBreakerConfig(**cb_cfg_dict)

    simulator = TreasurySimulator(
        initial_cash=initial_cash,
        start_date=start_date,
        end_date=end_date,
        assets=assets,
        circuit_breaker_config=cb_config
    )
    simulator.load_market_data(market_data.prices)

    simulator.run(verbose=False)
    summary = simulator.summary()
    
    objectives = OptimizationObjectives(
        sharpe_ratio=summary.get("sharpe_ratio", 0.0),
        sortino_ratio=summary.get("sortino_ratio", 0.0),
        calmar_ratio=summary.get("calmar_ratio", 0.0),
        total_return_pct=summary.get("total_return_pct", 0.0),
        max_drawdown_pct=abs(summary.get("max_drawdown_pct", 0.0)) / 100.0,
        annualized_volatility=summary.get("annualized_volatility", 0.0),
        cb_days=summary.get("cb_days", 0),
        net_yield_usd=summary.get("net_yield", 0.0),
        rebalance_count=summary.get("rebalance_count", 0),
        cost_drag_annual_pct=summary.get("cumulative_costs", 0.0) / max(summary.get("initial_capital", 1e7), 1) / 4.0,
    )
    
    passes, reason = objectives.passes_hard_constraints()
    
    return {
        **summary,
        **param_overrides,
        "composite_score": objectives.composite_score() if passes else -999.0,
        "passes_constraints": passes,
        "constraint_failure": reason if not passes else "",
    }

class GridSearchEngine:
    def __init__(
        self,
        market_data: Any,
        base_config: Any,
        strategy: str,
        max_workers: Optional[int] = None,
    ):
        self.market_data = market_data
        self.base_config = base_config
        self.strategy = strategy
        self.max_workers = max_workers or max(1, os.cpu_count() - 1)
        self.results: List[Dict[str, Any]] = []

    def search(self, params_to_vary: List[ParamSpec]) -> pd.DataFrame:
        param_names = [p.name for p in params_to_vary]
        param_values = [p.grid_values() for p in params_to_vary]
        all_combos = list(itertools.product(*param_values))
        
        total = len(all_combos)
        print(f"  Grid Search: {total} combinations")
        
        results = []
        completed = 0
        
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_combo = {
                executor.submit(
                    _run_single_backtest, self.market_data, self.base_config, self.strategy, dict(zip(param_names, combo))
                ): combo for combo in all_combos
            }
            
            for future in as_completed(future_to_combo):
                completed += 1
                try:
                    result = future.result()
                    results.append(result)
                    if completed % 10 == 0 or completed == total:
                        print(f"  [{completed}/{total}] completed...")
                except Exception as e:
                    print(f"  [ERROR]: {e}")
        
        df = pd.DataFrame(results)
        return df.sort_values("composite_score", ascending=False)
