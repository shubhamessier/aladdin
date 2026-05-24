import pandas as pd
import numpy as np
from typing import List, Dict, Any
from backtest.optimizer.param_space import ParamSpec
from backtest.optimizer.grid_search import _run_single_backtest

class SensitivityAnalyzer:
    def __init__(self, market_data: Any, base_config: Any):
        self.market_data = market_data
        self.base_config = base_config

    def analyze(
        self,
        optimal_params: Dict[str, Any],
        param_specs: List[ParamSpec],
        strategy: str,
    ) -> Dict[str, Any]:
        results = {}
        for spec in param_specs:
            values = spec.grid_values()
            param_results = []
            for val in values:
                override = {**optimal_params, spec.name: val}
                res = _run_single_backtest(self.market_data, self.base_config, strategy, override)
                param_results.append({
                    "value": val,
                    "score": res.get("composite_score", -999)
                })
            
            df = pd.DataFrame(param_results)
            results[spec.name] = {
                "score_range": df["score"].max() - df["score"].min(),
                "optimal_value": optimal_params.get(spec.name)
            }
        return results
