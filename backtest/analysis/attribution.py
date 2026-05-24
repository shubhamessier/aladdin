import pandas as pd
import numpy as np
from typing import Dict, Any

def decompose_returns(
    portfolio_history: pd.DataFrame, 
    benchmark_history: pd.Series, 
    risk_free_rate: float = 0.02
) -> Dict[str, Any]:
    """
    Decomposes portfolio returns into Beta (market exposure) and Alpha (active return).
    Also performs basic attribution if sub-components like yield/hedges are present.
    
    portfolio_history: DataFrame containing 'portfolio_value' and ideally 
                       columns like 'yield_pnl', 'hedge_pnl', 'spot_pnl'
    benchmark_history: Series of benchmark values (e.g. BTC or equal weight index)
    """
    if 'portfolio_value' not in portfolio_history.columns or len(portfolio_history) < 2:
        return {}

    port_returns = portfolio_history['portfolio_value'].pct_change().dropna()
    bench_returns = benchmark_history.pct_change().dropna()
    
    # Align dates
    aligned = pd.concat([port_returns, bench_returns], axis=1, join='inner').dropna()
    aligned.columns = ['portfolio', 'benchmark']
    
    if len(aligned) < 2:
        return {}
        
    y = aligned['portfolio']
    X = aligned['benchmark']
    
    # Calculate Beta and Alpha via linear regression
    cov_matrix = np.cov(X, y)
    beta = cov_matrix[0, 1] / cov_matrix[0, 0] if cov_matrix[0, 0] > 0 else 0.0
    
    # Annualized Alpha
    daily_rf = risk_free_rate / 252
    alpha_daily = y.mean() - (daily_rf + beta * (X.mean() - daily_rf))
    alpha_annualized = alpha_daily * 252
    
    attribution = {
        "beta_to_benchmark": beta,
        "annualized_alpha": alpha_annualized,
        "active_return": (y.mean() - X.mean()) * 252,
        "tracking_error": (y - X).std() * np.sqrt(252),
        "information_ratio": 0.0
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
