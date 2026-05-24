import numpy as np
import scipy.stats as stats  # type: ignore
from typing import Tuple

def compute_historical_var(
    returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float = 0.95
) -> Tuple[float, float]:
    """
    Compute Historical VaR and CVaR.
    returns: T x N array of historical returns
    weights: N array of portfolio weights
    """
    port_returns = np.dot(returns, weights)
    
    # VaR is the negative of the percentile
    percentile = 1.0 - confidence_level
    var = -np.percentile(port_returns, percentile * 100)
    
    # CVaR is the negative expected value of returns below the VaR threshold
    cvar = -np.mean(port_returns[port_returns <= -var])
    
    return float(var), float(cvar)

def compute_parametric_var(
    mu: float,
    sigma: float,
    confidence_level: float = 0.95
) -> Tuple[float, float]:
    """
    Compute Parametric (Normal) VaR and CVaR.
    mu: portfolio expected return
    sigma: portfolio volatility
    """
    z_score = stats.norm.ppf(confidence_level)
    var = -mu + z_score * sigma
    
    # CVaR for normal distribution
    cvar = -mu + sigma * (stats.norm.pdf(z_score) / (1 - confidence_level))
    
    return float(var), float(cvar)

def compute_monte_carlo_var(
    mu: np.ndarray,
    cov: np.ndarray,
    weights: np.ndarray,
    confidence_level: float = 0.95,
    num_simulations: int = 50000,
    df: float = 5.0
) -> Tuple[float, float]:
    """
    Compute Monte Carlo VaR and CVaR using Student-t innovations.
    df: degrees of freedom for fat tails (Student-t)
    """
    n = len(mu)
    
    # Cholesky decomposition for correlated draws
    try:
        l_matrix = np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        # Fallback to eigenvalue decomposition if not perfectly PSD
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        eigenvalues = np.maximum(eigenvalues, 1e-8)
        l_matrix = eigenvectors @ np.diag(np.sqrt(eigenvalues))

    # Generate independent Student-t samples
    z = stats.t.rvs(df, size=(n, num_simulations))
    
    # Correlate them
    correlated_shocks = l_matrix @ z
    
    # Simulated returns (N x num_simulations)
    sim_returns = mu.reshape(-1, 1) + correlated_shocks
    
    # Portfolio returns (1 x num_simulations)
    port_returns = np.dot(weights, sim_returns)
    
    percentile = 1.0 - confidence_level
    var = -np.percentile(port_returns, percentile * 100)
    cvar = -np.mean(port_returns[port_returns <= -var])
    
    return float(var), float(cvar)
