import numpy as np
import pandas as pd  # type: ignore
from sklearn.covariance import LedoitWolf  # type: ignore
from typing import Tuple, Any, cast

def cov_to_corr(cov: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Convert covariance matrix to correlation matrix and standard deviations."""
    std = np.sqrt(np.diag(cov))
    corr = cov / np.outer(std, std)
    corr[corr > 1] = 1
    corr[corr < -1] = -1
    return corr, std

def corr_to_cov(corr: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Convert correlation matrix and standard deviations to covariance matrix."""
    return corr * np.outer(std, std)

def marchenko_pastur_denoise(corr: np.ndarray, t: int, n: int) -> np.ndarray:
    """
    De-noise correlation matrix using Marchenko-Pastur Random Matrix Theory.
    
    t: number of observations
    n: number of assets
    """
    if t <= n:
        return corr # MP is not directly applicable or stable if T < N
    
    q = t / n
    lambda_plus = (1 + np.sqrt(1/q))**2
    
    eigenvalues, eigenvectors = np.linalg.eigh(corr)
    
    # Identify noise eigenvalues
    noise_idx = eigenvalues < lambda_plus
    if not np.any(noise_idx):
        return corr
    
    # Constant residual eigenvalue method: replace noise eigenvalues with their average
    avg_eigenvalue = np.mean(eigenvalues[noise_idx])
    eigenvalues[noise_idx] = avg_eigenvalue
    
    denoised_corr = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
    
    # Rescale to have unit diagonal
    d = np.diag(denoised_corr)
    denoised_corr = denoised_corr / np.sqrt(np.outer(d, d))
    
    return cast(np.ndarray, denoised_corr)

def nearest_psd(a: np.ndarray) -> np.ndarray:
    """Find the nearest positive semi-definite matrix."""
    vals, vecs = np.linalg.eigh(a)
    vals = np.maximum(vals, 1e-8)
    return cast(np.ndarray, vecs @ np.diag(vals) @ vecs.T)

def build_covariance(returns: pd.DataFrame, ewma_halflife: int = 63) -> np.ndarray:
    """
    Robust covariance pipeline:
    1. EWMA
    2. Ledoit-Wolf Shrinkage
    3. RMT De-noising
    4. Nearest PSD
    """
    t, n = returns.shape
    
    # 1. EWMA Covariance
    # We use EWMA to capture recent volatility shifts
    ewma_cov = returns.ewm(halflife=ewma_halflife).cov().iloc[-n:].values
    
    # 2. Ledoit-Wolf Shrinkage
    # Ledoit-Wolf is typically applied to sample covariance, but here we can 
    # use it to shrink the EWMA cov towards a constant correlation target 
    # or just use it as a standalone stabilizer.
    # For simplicity and following 4.2: apply LW then MP.
    lw = LedoitWolf().fit(returns.values)
    shrunk_cov = lw.covariance_
    
    # 3. RMT De-noising
    corr, std = cov_to_corr(shrunk_cov)
    denoised_corr = marchenko_pastur_denoise(corr, t, n)
    
    # 4. Final Covariance & PSD Check
    denoised_cov = corr_to_cov(denoised_corr, std)
    return nearest_psd(denoised_cov)
