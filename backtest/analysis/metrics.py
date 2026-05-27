import sys
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

risk_engine_path = Path(__file__).resolve().parent.parent.parent / "python-risk"
if str(risk_engine_path) not in sys.path:
    sys.path.append(str(risk_engine_path))

from risk_engine.var_models import compute_historical_var

def calculate_drawdowns(portfolio_values: pd.Series) -> pd.Series:
    """Calculates running drawdowns for a portfolio series."""
    running_max = portfolio_values.cummax()
    drawdowns = (portfolio_values - running_max) / running_max
    return drawdowns

def _infer_annualization(index: pd.DatetimeIndex) -> int:
    """
    Infer the annualization factor (steps-per-year) from a DatetimeIndex.
    Returns 365 * bars_per_day for intraday cadence, 365 for daily, 52 for weekly.
    """
    if len(index) < 2:
        return 365
    diffs = index.to_series().diff().dropna()
    median = diffs.median()
    if median <= pd.Timedelta(hours=1):
        bars_per_day = max(1, int(round(pd.Timedelta(days=1) / median)))
        return 365 * bars_per_day
    if median <= pd.Timedelta(days=1):
        return 365
    if median <= pd.Timedelta(days=7):
        return 52
    return 12


def calculate_performance_metrics(
    portfolio_history: pd.DataFrame,
    risk_free_rate: float = 0.02,
    annualization_factor: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Calculates Sharpe, Sortino, Max Drawdown, and VaR from a backtest history.
    portfolio_history must contain a 'portfolio_value' column indexed by datetime.
    Annualization factor is inferred from the index cadence unless explicitly provided.
    """
    if 'portfolio_value' not in portfolio_history.columns or len(portfolio_history) < 2:
        return {}

    values = portfolio_history['portfolio_value']
    returns = values.pct_change().dropna()

    if len(returns) == 0 or values.iloc[0] <= 0 or not np.isfinite(values.iloc[-1]):
        return {}

    if annualization_factor is None:
        annualization_factor = _infer_annualization(portfolio_history.index)

    volatility = returns.std()

    # CAGR via total return → avoids the (1+mean)^N blow-up at intraday cadence
    total_return = float(values.iloc[-1] / values.iloc[0])
    n_steps = len(values)
    annualized_return = total_return ** (annualization_factor / n_steps) - 1.0 if total_return > 0 else -1.0
    annualized_vol = float(volatility * np.sqrt(annualization_factor))

    excess_return = annualized_return - risk_free_rate
    sharpe_ratio = excess_return / annualized_vol if annualized_vol > 0 else 0.0

    # Sortino
    target = risk_free_rate / annualization_factor
    downside_diff = np.minimum(returns - target, 0)
    downside_deviation = float(np.sqrt(np.mean(downside_diff**2)) * np.sqrt(annualization_factor))
    sortino_ratio = excess_return / downside_deviation if downside_deviation > 0 else 0.0
    
    # Maximum Drawdown
    drawdowns = calculate_drawdowns(values)
    max_drawdown = drawdowns.min()
    
    # VaR using Risk Engine (BUG-03)
    current_value = values.iloc[-1]
    
    try:
        # compute_historical_var(returns: T x N, weights: N) -> (var, cvar)
        var_95, _ = compute_historical_var(
            returns=returns.values.reshape(-1, 1), 
            weights=np.array([1.0]),
            confidence_level=0.95
        )
        var_95_1d = var_95 * current_value
        
        var_99, _ = compute_historical_var(
            returns=returns.values.reshape(-1, 1), 
            weights=np.array([1.0]),
            confidence_level=0.99
        )
        var_99_1d = var_99 * current_value
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
