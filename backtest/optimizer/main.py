import yaml
import json
import os
from datetime import datetime
from typing import Dict, Any
from backtest.optimizer.param_space import PARAM_SPACE
from backtest.optimizer.random_search import RandomSearchEngine
from backtest.optimizer.grid_search import GridSearchEngine, _run_single_backtest
from backtest.optimizer.walk_forward import WalkForwardValidator
from backtest.optimizer.sensitivity import SensitivityAnalyzer

def run_full_optimization(market_data: Any, base_config: Any, strategy: str = "risk_parity"):
    print(f"\n--- STAGE 1: Random Search ---")
    random_engine = RandomSearchEngine(market_data, base_config, strategy)
    coarse_results = random_engine.search(PARAM_SPACE, n_samples=100) # Reduced for speed
    
    top_configs = coarse_results.head(10)
    
    important_params = []
    for spec in PARAM_SPACE:
        if spec.name in top_configs.columns and spec.param_type != "categorical":
            if top_configs[spec.name].std() > 0:
                important_params.append(spec)
    
    print(f"\n--- STAGE 2: Grid Search on {len(important_params)} params ---")
    # Fine-tune only top 3 important params for grid tractability
    if len(important_params) > 3:
        important_params = important_params[:3]
        
    grid_engine = GridSearchEngine(market_data, base_config, strategy)
    grid_results = grid_engine.search(important_params)
    best_row = grid_results.iloc[0]
    optimal_params = {p.name: best_row.get(p.name, p.default) for p in PARAM_SPACE}

    print(f"\n--- STAGE 3: Walk-Forward Validation ---")
    validator = WalkForwardValidator(market_data, base_config, strategy)
    wf_result = validator.validate(important_params, n_random_samples=50)

    print(f"\n--- STAGE 4: Sensitivity Analysis ---")
    analyzer = SensitivityAnalyzer(market_data, base_config)
    sensitivity = analyzer.analyze(optimal_params, important_params, strategy)

    print(f"\n--- FINAL VALIDATION ---")
    final_res = _run_single_backtest(market_data, base_config, strategy, optimal_params)
    
    return {
        "strategy": strategy,
        "optimal_params": optimal_params,
        "final_metrics": final_res,
        "walk_forward": wf_result,
        "sensitivity": sensitivity
    }

def export_optimal_config(results: Dict[str, Any], output_path: str):
    config = {
        "strategy": results["strategy"],
        "optimal_params": results["optimal_params"],
        "generated_at": datetime.now().isoformat(),
        "final_metrics": {k: v for k, v in results["final_metrics"].items() if isinstance(v, (float, int, str, bool))}
    }
    with open(output_path, "w") as f:
        yaml.dump(config, f)
    print(f"Optimal config exported to {output_path}")
