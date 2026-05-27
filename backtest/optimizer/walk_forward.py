import pandas as pd
import numpy as np
from typing import List, Dict, Any
from backtest.optimizer.param_space import ParamSpec
from backtest.optimizer.random_search import RandomSearchEngine
from backtest.optimizer.grid_search import _run_single_backtest

class WalkForwardValidator:
    def __init__(
        self,
        market_data: Any,
        base_config: Any,
        strategy: str,
        train_months: int = 18,
        test_months: int = 6,
        step_months: int = 6,
        embargo_months: int = 1,
    ):
        self.market_data = market_data
        self.base_config = base_config
        self.strategy = strategy
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months
        self.embargo_months = embargo_months

    def validate(
        self,
        params_to_optimize: List[ParamSpec],
        n_random_samples: int = 200,
    ) -> Dict[str, Any]:
        dates = self.market_data.prices.index
        total_days = len(dates)
        train_days = self.train_months * 21
        test_days = self.test_months * 21
        step_days = self.step_months * 21
        embargo_days = self.embargo_months * 21
        
        folds = []
        start = 0
        
        while start + train_days + embargo_days + test_days <= total_days:
            train_end = start + train_days
            test_start = train_end + embargo_days
            test_end = test_start + test_days
            
            train_data = self.market_data.slice_by_index(start, train_end)
            test_data = self.market_data.slice_by_index(test_start, test_end)
            
            searcher = RandomSearchEngine(train_data, self.base_config, self.strategy)
            train_results = searcher.search(params_to_optimize, n_samples=n_random_samples)
            
            best_row = train_results.iloc[0]
            best_params = {p.name: best_row[p.name] for p in params_to_optimize if p.name in best_row}
            
            test_result = _run_single_backtest(test_data, self.base_config, self.strategy, best_params)
            
            folds.append({
                "train_score": best_row["composite_score"],
                "test_score": test_result.get("composite_score", -999),
                "best_params": best_params
            })
            
            start += step_days
            
        fold_df = pd.DataFrame(folds)
        return {
            "avg_oos_score": fold_df["test_score"].mean(),
            "avg_overfit_ratio": (fold_df["test_score"] / fold_df["train_score"]).mean(),
            "folds": fold_df
        }
