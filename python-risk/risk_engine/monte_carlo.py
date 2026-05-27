import numpy as np
from .schemas import SimulationResult
from .covariance import nearest_psd

def simulate_portfolio(
    current_values: np.ndarray,     # Current USD value per asset
    annualized_returns: np.ndarray, # Annualized expected returns
    annualized_volatilities: np.ndarray, # Annualized volatilities
    correlation: np.ndarray,        # Correlation matrix
    horizon_days: int = 30,
    n_simulations: int = 50_000,
    use_student_t: bool = True,     # Fat tails
    df: float = 5.0,               # Degrees of freedom for Student-t
    seed: int = 42,
) -> SimulationResult:
    """
    Correlated Monte Carlo simulation of portfolio value over horizon.
    Uses Cholesky decomposition for correlation and Student-t innovations for fat tails.
    """
    rng = np.random.default_rng(seed)
    N = len(current_values)
    
    # Cholesky decomposition of correlation matrix
    try:
        L = np.linalg.cholesky(correlation)
    except np.linalg.LinAlgError:
        # Not positive definite — use nearest PSD
        correlation = nearest_psd(correlation)
        L = np.linalg.cholesky(correlation)
    
    # Daily returns for each asset
    daily_mu = annualized_returns / 252.0
    daily_sigma = annualized_volatilities / np.sqrt(252.0)
    
    # Simulate
    # Shape: (n_simulations, horizon_days, N)
    portfolio_paths = np.zeros((n_simulations, horizon_days + 1))
    portfolio_paths[:, 0] = float(current_values.sum())
    
    asset_values = np.tile(current_values, (n_simulations, 1))  # (n_sim, N)
    
    for t in range(1, horizon_days + 1):
        # Generate correlated innovations
        if use_student_t:
            # Student-t: Z = sqrt(df / chi2(df)) * Normal
            # Multiply by sqrt((df-2)/df) to give it unit variance
            z_normal = rng.standard_normal((n_simulations, N))
            chi2_samples = rng.chisquare(df, size=(n_simulations, 1))
            z = z_normal * np.sqrt(df / chi2_samples) * np.sqrt((df - 2.0) / df)
        else:
            z = rng.standard_normal((n_simulations, N))
        
        # Apply correlation via Cholesky
        correlated_z = z @ L.T  # (n_sim, N)
        
        # GBM step: S(t+1) = S(t) * exp((μ - σ²/2)dt + σ√dt * Z)
        log_returns = (daily_mu - 0.5 * daily_sigma**2) + daily_sigma * correlated_z
        asset_values = asset_values * np.exp(log_returns)
        
        portfolio_paths[:, t] = asset_values.sum(axis=1)
    
    # Extract risk metrics from the final distribution
    final_values = portfolio_paths[:, -1]
    initial_value = float(current_values.sum())
    
    if initial_value > 0:
        returns = (final_values - initial_value) / initial_value
    else:
        returns = np.zeros_like(final_values)
        
    losses = -returns  # Positive loss = negative return
    
    var_95 = float(np.percentile(losses, 95)) * initial_value
    var_99 = float(np.percentile(losses, 99)) * initial_value
    
    losses_95 = losses[losses >= np.percentile(losses, 95)]
    cvar_95 = float(losses_95.mean()) * initial_value if len(losses_95) > 0 else 0.0
    
    losses_99 = losses[losses >= np.percentile(losses, 99)]
    cvar_99 = float(losses_99.mean()) * initial_value if len(losses_99) > 0 else 0.0
    
    # Max drawdown per path
    running_max = np.maximum.accumulate(portfolio_paths, axis=1)
    # Avoid division by zero
    drawdowns = np.where(running_max > 0, (running_max - portfolio_paths) / running_max, 0.0)
    max_drawdowns = drawdowns.max(axis=1)
    expected_max_drawdown = float(max_drawdowns.mean())
    worst_max_drawdown = float(max_drawdowns.max())
    
    # Probability of ruin (portfolio drops below X% of initial)
    ruin_threshold = 0.7  # 30% loss
    prob_ruin = float((final_values < initial_value * ruin_threshold).mean())
    
    return SimulationResult(
        var_95=var_95,
        var_99=var_99,
        cvar_95=cvar_95,
        cvar_99=cvar_99,
        expected_max_drawdown=expected_max_drawdown,
        worst_max_drawdown=worst_max_drawdown,
        prob_ruin_30pct=prob_ruin,
        mean_return=float(returns.mean()),
        median_return=float(np.median(returns)),
        return_std=float(returns.std()),
        horizon_days=horizon_days,
        n_simulations=n_simulations,
        distribution='student_t' if use_student_t else 'normal',
    )
