import sys
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Dict, Any

risk_engine_path = Path(__file__).resolve().parent.parent.parent / "python-risk"
if str(risk_engine_path) not in sys.path:
    sys.path.append(str(risk_engine_path))

from risk_engine.var_models import compute_historical_var

def calculate_drawdowns(portfolio_values: pd.Series) -> pd.Series:
    """Calculates running drawdowns for a portfolio series."""
    running_max = portfolio_values.cummax()
    drawdowns = (portfolio_values - running_max) / running_max
    return drawdowns

def calculate_performance_metrics(
    portfolio_history: pd.DataFrame, 
    risk_free_rate: float = 0.02
) -> Dict[str, Any]:
    """
    Calculates Sharpe, Sortino, Max Drawdown, and VaR from a backtest history.
    portfolio_history must contain a 'portfolio_value' column indexed by datetime.
    """
    if 'portfolio_value' not in portfolio_history.columns or len(portfolio_history) < 2:
        return {}

    values = portfolio_history['portfolio_value']
    returns = values.pct_change().dropna()
    
    if len(returns) == 0:
        return {}
        
    # Annualization factor assuming daily data
    annualization_factor = 252
    
    # Expected return and volatility
    mean_return = returns.mean()
    volatility = returns.std()
    
    annualized_return = mean_return * annualization_factor
    annualized_vol = volatility * np.sqrt(annualization_factor)
    
    # Sharpe Ratio
    excess_return = annualized_return - risk_free_rate
    sharpe_ratio = excess_return / annualized_vol if annualized_vol > 0 else 0.0
    
    # Sortino Ratio
    negative_returns = returns[returns < 0]
    downside_deviation = np.sqrt(np.mean(negative_returns**2)) * np.sqrt(annualization_factor)
    sortino_ratio = excess_return / downside_deviation if downside_deviation > 0 else 0.0
    
    # Maximum Drawdown
    drawdowns = calculate_drawdowns(values)
    max_drawdown = drawdowns.min()
    
    # VaR using Risk Engine
    current_value = values.iloc[-1]
    
    # Var Models usually takes Returns Series or List, and portfolio value
    try:
        # Assuming compute_historical_var signature from risk_engine
        var_results = compute_historical_var(
            returns=returns.values.tolist(), 
            portfolio_value=current_value
        )
        var_95_1d = var_results.var_95_1d
        var_99_1d = var_results.var_99_1d
    except Exception:
        # Fallback if api differs
        losses = -returns.values
        var_95_1d = float(np.percentile(losses, 95)) * current_value
        var_99_1d = float(np.percentile(losses, 99)) * current_value
        
    return {
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_vol,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "max_drawdown": max_drawdown,
        "historical_var_95_1d": var_95_1d,
        "historical_var_99_1d": var_99_1d,
        "total_return": (values.iloc[-1] / values.iloc[0]) - 1.0
    }
