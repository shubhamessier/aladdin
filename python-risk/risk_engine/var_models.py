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

def compute_jump_diffusion_var(
    returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float = 0.95,
    jump_intensity: float = 20.0, # Expected jumps per year
    jump_mean: float = -0.02,     # Average jump size (-2%)
    jump_std: float = 0.05,       # Jump volatility
    num_simulations: int = 50000
) -> Tuple[float, float]:
    """
    Compute VaR using a Merton Jump-Diffusion model to explicitly price tail risk.
    Unlike standard Monte Carlo, this models the sudden, non-continuous liquidations
    common in crypto markets.
    """
    port_returns = np.dot(returns, weights)
    
    # Historical baseline parameters
    dt = 1.0 / 252.0
    mu = np.mean(port_returns) * 252.0
    sigma = np.std(port_returns) * np.sqrt(252.0)
    
    # Simulate diffusion component (Geometric Brownian Motion)
    z = np.random.normal(0, 1, num_simulations)
    diffusion = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
    
    # Simulate jump component (Poisson process)
    # Number of jumps in dt
    n_jumps = np.random.poisson(jump_intensity * dt, num_simulations)
    
    # Jump sizes (log-normal approximation via normal sum)
    jump_sizes = np.zeros(num_simulations)
    for i in range(num_simulations):
        if n_jumps[i] > 0:
            jump_sizes[i] = np.sum(np.random.normal(jump_mean, jump_std, n_jumps[i]))
            
    sim_returns = diffusion + jump_sizes
    
    percentile = 1.0 - confidence_level
    var = -np.percentile(sim_returns, percentile * 100)
    cvar = -np.mean(sim_returns[sim_returns <= -var])
    
    return float(var), float(cvar)
