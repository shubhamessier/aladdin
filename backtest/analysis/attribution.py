import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

def _infer_ann_factor(index: pd.DatetimeIndex) -> int:
    if len(index) < 2:
        return 365
    diffs = index.to_series().diff().dropna()
    med = diffs.median()
    if med <= pd.Timedelta(hours=1):
        return 365 * max(1, int(round(pd.Timedelta(days=1) / med)))
    if med <= pd.Timedelta(days=1):
        return 365
    return 52

def decompose_returns(
    portfolio_history: pd.DataFrame,
    benchmark_history: pd.Series,
    risk_free_rate: float = 0.02,
    annualization_factor: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Decomposes portfolio returns into Beta and Alpha.
    """
    if 'portfolio_value' not in portfolio_history.columns or len(portfolio_history) < 2:
        return {}

    port_returns = portfolio_history['portfolio_value'].pct_change().dropna()
    bench_returns = benchmark_history.pct_change().dropna()

    aligned = pd.concat([port_returns, bench_returns], axis=1, join='inner').dropna()
    aligned.columns = ['portfolio', 'benchmark']

    if len(aligned) < 2:
        return {}

    y = aligned['portfolio']
    X = aligned['benchmark']

    cov_matrix = np.cov(X, y)
    beta = cov_matrix[0, 1] / cov_matrix[0, 0] if cov_matrix[0, 0] > 0 else 0.0

    if annualization_factor is None:
        annualization_factor = _infer_ann_factor(aligned.index)

    rf_per_step = risk_free_rate / annualization_factor
    alpha_per_step = y.mean() - (rf_per_step + beta * (X.mean() - rf_per_step))
    alpha_annualized = alpha_per_step * annualization_factor
    
    attribution = {
        "beta_to_benchmark": float(beta),
        "annualized_alpha": float(alpha_annualized),
        "active_return": float((y.mean() - X.mean()) * annualization_factor),
        "tracking_error": float((y - X).std() * np.sqrt(annualization_factor)),
        "information_ratio": 0.0,
    }
    
    if attribution["tracking_error"] > 0:
        attribution["information_ratio"] = attribution["active_return"] / attribution["tracking_error"]
        
    # Additional Component Attribution if columns exist in the DataFrame
    # E.g. attributing the total PnL into Spot, Yield, and Derivatives
    if all(col in portfolio_history.columns for col in ['total_pnl', 'yield_pnl', 'derivative_pnl']):
        total_pnl = portfolio_history['total_pnl'].sum()
        if total_pnl != 0:
            attribution["yield_contribution_pct"] = portfolio_history['yield_pnl'].sum() / total_pnl
            attribution["derivative_contribution_pct"] = portfolio_history['derivative_pnl'].sum() / total_pnl
            attribution["spot_contribution_pct"] = 1.0 - (attribution["yield_contribution_pct"] + attribution["derivative_contribution_pct"])
            
    return attribution
