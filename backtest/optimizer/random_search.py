import numpy as np
import pandas as pd
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from scipy.stats.qmc import LatinHypercube
from backtest.optimizer.param_space import ParamSpec
from backtest.optimizer.grid_search import _run_single_backtest

class RandomSearchEngine:
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

    def search(self, params: List[ParamSpec], n_samples: int = 500) -> pd.DataFrame:
        continuous_params = [p for p in params if p.param_type in ("float", "int")]
        categorical_params = [p for p in params if p.param_type == "categorical"]
        
        n_dims = len(continuous_params)
        sampler = LatinHypercube(d=n_dims, seed=42)
        unit_samples = sampler.random(n=n_samples)
        
        configs = []
        for sample in unit_samples:
            config = {}
            for i, param in enumerate(continuous_params):
                scaled = param.min_val + sample[i] * (param.max_val - param.min_val)
                if param.param_type == "int":
                    scaled = int(round(scaled))
                config[param.name] = scaled
            
            for param in categorical_params:
                config[param.name] = np.random.choice(param.min_val)
            
            configs.append(config)
        
        print(f"  Random Search (LHS): {n_samples} samples")
        
        results = []
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    _run_single_backtest, self.market_data, self.base_config, self.strategy, cfg
                ): cfg for cfg in configs
            }
            for i, future in enumerate(as_completed(futures)):
                try:
                    results.append(future.result())
                except Exception as e:
                    print(f"  [ERROR]: {e}")
                
                if (i + 1) % 50 == 0:
                    print(f"  [{i+1}/{n_samples}] completed...")
        
        return pd.DataFrame(results).sort_values("composite_score", ascending=False)
