from fastapi import FastAPI, HTTPException
from typing import Dict, List, Tuple, Optional
import numpy as np

from .schemas import (
    MarkowitzRequest, RiskParityRequest, BlackLittermanRequest,
    OptimizationResponse, VaRRequest, VaRResponse, TierConstraint, View,
    OptimizationConstraints
)
from .portfolio_optimizer import (
    optimize_mean_variance, optimize_risk_parity, optimize_black_litterman
)
from .var_models import (
    compute_historical_var, compute_parametric_var, compute_monte_carlo_var
)

app = FastAPI(title="Hyperliquid Treasury Risk Engine")

def _get_bounds_and_tiers(req_assets: List[str], constraints: Optional[OptimizationConstraints]) -> Tuple[List[Tuple[float, float]], List[TierConstraint]]:
    n = len(req_assets)
    bounds = [(0.0, 1.0) for _ in range(n)]
    tier_constraints = []
    
    if constraints:
        for i, asset in enumerate(req_assets):
            min_w = 0.0
            max_w = 1.0
            if constraints.min_weights and asset in constraints.min_weights:
                min_w = constraints.min_weights[asset]
            if constraints.max_weights and asset in constraints.max_weights:
                max_w = constraints.max_weights[asset]
            bounds[i] = (min_w, max_w)
            
        if constraints.volatile_assets and constraints.max_volatile_allocation is not None:
            indices = [i for i, a in enumerate(req_assets) if a in constraints.volatile_assets]
            if indices:
                tier_constraints.append(TierConstraint(
                    asset_indices=indices,
                    min_total=0.0,
                    max_total=constraints.max_volatile_allocation
                ))
    return bounds, tier_constraints

@app.post("/optimize/mean-variance", response_model=OptimizationResponse)
def optimize_mv(req: MarkowitzRequest) -> OptimizationResponse:
    try:
        mu = np.array(req.expected_returns)
        cov = np.array(req.covariance_matrix)
        if len(mu) != len(req.assets) or cov.shape != (len(mu), len(mu)):
            raise ValueError("Dimension mismatch between assets, returns, and covariance.")
        
        bounds, tier_constraints = _get_bounds_and_tiers(req.assets, req.constraints)
        
        result = optimize_mean_variance(mu, cov, bounds, tier_constraints)
        
        exp_return = float(np.dot(result.weights, mu))
        
        return OptimizationResponse(
            weights={req.assets[i]: float(result.weights[i]) for i in range(len(req.assets))},
            expected_return=exp_return,
            expected_volatility=result.portfolio_volatility or 0.0,
            status="success" if result.converged else result.message or "failed"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/optimize/risk-parity", response_model=OptimizationResponse)
def optimize_rp(req: RiskParityRequest) -> OptimizationResponse:
    try:
        cov = np.array(req.covariance_matrix)
        if cov.shape != (len(req.assets), len(req.assets)):
            raise ValueError("Dimension mismatch between assets and covariance.")
        
        bounds, tier_constraints = _get_bounds_and_tiers(req.assets, req.constraints)
        
        result = optimize_risk_parity(cov, bounds, tier_constraints)
        
        return OptimizationResponse(
            weights={req.assets[i]: float(result.weights[i]) for i in range(len(req.assets))},
            expected_return=0.0,
            expected_volatility=result.portfolio_volatility or 0.0,
            status="success" if result.converged else result.message or "failed"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/optimize/black-litterman", response_model=OptimizationResponse)
def optimize_bl(req: BlackLittermanRequest) -> OptimizationResponse:
    try:
        assets = req.assets
        cov = np.array(req.covariance_matrix)
        p_views = np.array(req.views_p)
        q_views = np.array(req.views_q)
        
        w_mkt = np.array([req.market_weights.get(a, 0.0) for a in assets])
        w_mkt = w_mkt / np.sum(w_mkt)
        
        bounds, tier_constraints = _get_bounds_and_tiers(assets, None) # No constraints in request schema currently, keep simple
        
        views = []
        for i in range(len(q_views)):
            view_weights = []
            asset_indices = []
            for j in range(len(assets)):
                if p_views[i][j] != 0:
                    asset_indices.append(j)
                    view_weights.append(p_views[i][j])
            conf = req.view_confidences[i] if req.view_confidences else None
            views.append(View(
                asset_indices=asset_indices,
                asset_weights=view_weights,
                expected_return=q_views[i],
                confidence=conf
            ))
            
        result = optimize_black_litterman(
            covariance=cov,
            market_caps=w_mkt,
            views=views,
            risk_aversion=req.risk_aversion,
            tau=req.tau,
            bounds=bounds,
            tier_constraints=tier_constraints
        )
        
        # Approximate expected return based on equilibrium for the API response, since it requires pi
        # This is a simplification for the API
        pi = req.risk_aversion * cov @ w_mkt
        exp_return = float(np.dot(result.weights, pi))
        
        return OptimizationResponse(
            weights={assets[i]: float(result.weights[i]) for i in range(len(assets))},
            expected_return=exp_return,
            expected_volatility=result.portfolio_volatility or 0.0,
            status="success" if result.converged else result.message or "failed"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/risk/var", response_model=VaRResponse)
def calculate_var(req: VaRRequest) -> VaRResponse:
    try:
        assets = req.assets
        weights = np.array([req.weights.get(a, 0.0) for a in assets])
        weights = weights / np.sum(weights)
        
        returns_history = np.array(req.returns_history)
        
        if req.method == "historical":
            var, cvar = compute_historical_var(returns_history, weights, req.confidence_level)
        elif req.method == "parametric":
            mu = np.mean(returns_history, axis=0)
            cov = np.cov(returns_history, rowvar=False)
            
            port_mu = float(np.dot(weights, mu))
            port_sigma = float(np.sqrt(np.dot(weights.T, np.dot(cov, weights))))
            
            var, cvar = compute_parametric_var(port_mu, port_sigma, req.confidence_level)
        elif req.method == "monte_carlo":
            mu = np.mean(returns_history, axis=0)
            cov = np.cov(returns_history, rowvar=False)
            
            var, cvar = compute_monte_carlo_var(
                mu=mu,
                cov=cov,
                weights=weights,
                confidence_level=req.confidence_level
            )
        else:
            raise ValueError(f"Unknown method: {req.method}")
        
        scale = np.sqrt(req.horizon_days)
        
        return VaRResponse(
            var=var * scale,
            cvar=cvar * scale,
            method=req.method,
            confidence_level=req.confidence_level
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "healthy"}
