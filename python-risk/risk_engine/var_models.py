import numpy as np
import scipy.stats as stats  # type: ignore
from typing import Tuple

def compute_historical_var(
    returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float = 0.95
) -> Tuple[float, float]:
    port_returns = np.dot(returns, weights)
    percentile = 1.0 - confidence_level
    var = -np.percentile(port_returns, percentile * 100)
    cvar = -np.mean(port_returns[port_returns <= -var])
    return float(var), float(cvar)

def compute_parametric_var(
    mu: float,
    sigma: float,
    confidence_level: float = 0.95
) -> Tuple[float, float]:
    z_score = stats.norm.ppf(confidence_level)
    var = -mu + z_score * sigma
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
    n = len(mu)
    try:
        l_matrix = np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        eigenvalues = np.maximum(eigenvalues, 1e-8)
        l_matrix = eigenvectors @ np.diag(np.sqrt(eigenvalues))

    z = stats.t.rvs(df, size=(n, num_simulations))
    correlated_shocks = l_matrix @ z
    sim_returns = mu.reshape(-1, 1) + correlated_shocks
    port_returns = np.dot(weights, sim_returns)
    
    percentile = 1.0 - confidence_level
    var = -np.percentile(port_returns, percentile * 100)
    cvar = -np.mean(port_returns[port_returns <= -var])
    
    return float(var), float(cvar)

def compute_jump_diffusion_var(
    returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float = 0.95,
    jump_intensity: float = 20.0,
    jump_mean: float = -0.02,
    jump_std: float = 0.05,
    num_simulations: int = 50000,
    seed: int = 42
) -> Tuple[float, float]:
    rng = np.random.default_rng(seed)
    
    port_returns = np.dot(returns, weights)
    dt = 1.0 / 252.0
    mu = np.mean(port_returns) * 252.0
    sigma = np.std(port_returns) * np.sqrt(252.0)
    
    z = rng.standard_normal(num_simulations)
    diffusion = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
    
    n_jumps = rng.poisson(jump_intensity * dt, num_simulations)
    
    max_jumps = int(n_jumps.max()) if n_jumps.max() > 0 else 0
    if max_jumps > 0:
        all_jumps = rng.normal(jump_mean, jump_std, (num_simulations, max_jumps))
        jump_mask = np.arange(max_jumps)[np.newaxis, :] < n_jumps[:, np.newaxis]
        jump_sizes = (all_jumps * jump_mask).sum(axis=1)
    else:
        jump_sizes = np.zeros(num_simulations)
    
    sim_returns = diffusion + jump_sizes
    
    percentile = 1.0 - confidence_level
    var = -np.percentile(sim_returns, percentile * 100)
    below_var = sim_returns[sim_returns <= -var]
    cvar = -float(below_var.mean()) if len(below_var) > 0 else float(var)
    
    return float(var), float(cvar)
