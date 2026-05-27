import numpy as np
import scipy.optimize as sco  # type: ignore
from typing import Dict, List, Optional, Tuple, Any, cast

from .schemas import OptimizationConstraints, OptimizationResponse, OptimizationResult, TierConstraint, View

def compute_risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray:
    port_vol = np.sqrt(w @ cov @ w)
    if port_vol == 0:
        return np.zeros_like(w)
    marginal_contrib = cov @ w
    risk_contrib = w * marginal_contrib / port_vol
    return cast(np.ndarray, risk_contrib)

def optimize_mean_variance(
    expected_returns: np.ndarray,
    covariance: np.ndarray,
    bounds: Optional[List[Tuple[float, float]]] = None,
    tier_constraints: Optional[List[TierConstraint]] = None,
    method_label: str = 'mean_variance'
) -> OptimizationResult:
    N = covariance.shape[0]
    if bounds is None:
        bounds = [(0.0, 1.0) for _ in range(N)]
    
    def objective(w: np.ndarray) -> float:
        w = np.array(w)
        port_return = float(np.dot(w, expected_returns))
        port_var = float(np.dot(w, np.dot(covariance, w)))
        risk_aversion = 2.5
        return 0.5 * risk_aversion * port_var - port_return

    constraints: List[Dict[str, Any]] = [
        {'type': 'eq', 'fun': lambda w: float(np.sum(w)) - 1.0}
    ]
    
    if tier_constraints:
        for tc in tier_constraints:
            asset_indices = tc.asset_indices
            constraints.append({
                'type': 'ineq',
                'fun': lambda w, idx=asset_indices, mx=tc.max_total: mx - float(np.sum(w[idx]))
            })
            if tc.min_total > 0:
                constraints.append({
                    'type': 'ineq',
                    'fun': lambda w, idx=asset_indices, mn=tc.min_total: float(np.sum(w[idx])) - mn
                })

    w0 = np.array([max(b[0], 1.0 / N) for b in bounds])
    w0 = w0 / w0.sum()
    
    result = sco.minimize(
        objective, w0,
        method='SLSQP',
        bounds=bounds,
        constraints=constraints,
        options={'maxiter': 1000, 'ftol': 1e-12},
    )
    
    if not result.success:
        return OptimizationResult(
            weights=w0.tolist(),
            method=f'{method_label}_failed',
            converged=False,
            message=result.message
        )
        
    return OptimizationResult(
        weights=result.x.tolist(),
        method=method_label,
        converged=True,
        portfolio_volatility=float(np.sqrt(result.x @ covariance @ result.x))
    )

def optimize_risk_parity(
    covariance: np.ndarray,
    bounds: List[Tuple[float, float]],
    tier_constraints: List[TierConstraint],
) -> OptimizationResult:
    """
    Risk parity: each asset contributes equally to portfolio risk.
    """
    N = covariance.shape[0]
    
    def objective(w: np.ndarray) -> float:
        w = np.array(w)
        port_vol = np.sqrt(w @ covariance @ w)
        if port_vol == 0:
            return 1e9
        marginal_contrib = covariance @ w
        risk_contrib = w * marginal_contrib / port_vol
        target_rc = port_vol / N
        return float(np.sum((risk_contrib - target_rc) ** 2))
    
    constraints: List[Dict[str, Any]] = [
        {'type': 'eq', 'fun': lambda w: float(np.sum(w)) - 1.0},
    ]
    
    for tc in tier_constraints:
        asset_indices = tc.asset_indices
        constraints.append({
            'type': 'ineq',
            'fun': lambda w, idx=asset_indices, mx=tc.max_total: mx - float(np.sum(w[idx]))
        })
        if tc.min_total > 0:
            constraints.append({
                'type': 'ineq',
                'fun': lambda w, idx=asset_indices, mn=tc.min_total: float(np.sum(w[idx])) - mn
            })
    
    w0 = np.array([max(b[0], 1.0 / N) for b in bounds])
    w0 = w0 / w0.sum()
    
    result = sco.minimize(
        objective, w0,
        method='SLSQP',
        bounds=bounds,
        constraints=constraints,
        options={'maxiter': 1000, 'ftol': 1e-12},
    )
    
    if not result.success:
        vols = np.sqrt(np.diag(covariance))
        vols = np.maximum(vols, 1e-8)
        w_fallback = (1.0 / vols) / np.sum(1.0 / vols)
        w_fallback = np.clip(w_fallback, [b[0] for b in bounds], [b[1] for b in bounds])
        w_fallback = w_fallback / w_fallback.sum()
        return OptimizationResult(
            weights=w_fallback.tolist(),
            method='risk_parity_fallback_inverse_vol',
            converged=False,
            message=result.message,
        )
    
    return OptimizationResult(
        weights=result.x.tolist(),
        method='risk_parity',
        converged=True,
        portfolio_volatility=float(np.sqrt(result.x @ covariance @ result.x)),
        risk_contributions=compute_risk_contributions(result.x, covariance).tolist(),
    )


def optimize_black_litterman(
    covariance: np.ndarray,
    market_caps: np.ndarray,
    views: List[View],
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    bounds: Optional[List[Tuple[float, float]]] = None,
    tier_constraints: Optional[List[TierConstraint]] = None,
) -> OptimizationResult:
    """
    Black-Litterman: combine equilibrium returns with subjective views.
    """
    N = covariance.shape[0]
    
    # Step 1: Market-implied equilibrium returns
    w_mkt = market_caps / market_caps.sum()
    pi = risk_aversion * covariance @ w_mkt
    
    if not views:
        return optimize_mean_variance(pi, covariance, bounds, tier_constraints)
    
    # Step 2: Construct view matrices
    P = np.zeros((len(views), N))
    Q = np.zeros(len(views))
    omega_diag = np.zeros(len(views))
    
    for i, view in enumerate(views):
        for asset_idx, weight in zip(view.asset_indices, view.asset_weights):
            P[i, asset_idx] = weight
        Q[i] = view.expected_return
        
        if view.confidence is not None:
            omega_diag[i] = ((1.0 / max(view.confidence, 0.01)) - 1.0) * float(P[i] @ covariance @ P[i].T)
        else:
            omega_diag[i] = float(P[i] @ (tau * covariance) @ P[i].T)
    
    Omega = np.diag(omega_diag)
    
    # Step 3: Posterior returns
    tau_sigma_inv = np.linalg.inv(tau * covariance)
    omega_inv = np.linalg.inv(Omega)
    
    posterior_cov = np.linalg.inv(tau_sigma_inv + P.T @ omega_inv @ P)
    posterior_mean = posterior_cov @ (tau_sigma_inv @ pi + P.T @ omega_inv @ Q)
    
    # Step 4: Optimize with posterior returns
    return optimize_mean_variance(
        expected_returns=posterior_mean,
        covariance=covariance + posterior_cov,
        bounds=bounds,
        tier_constraints=tier_constraints,
        method_label='black_litterman',
    )
