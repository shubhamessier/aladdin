from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field

class PortfolioWeights(BaseModel):
    weights: Dict[str, float]

class ReturnsData(BaseModel):
    assets: List[str]
    returns: List[float]

class CovarianceRequest(BaseModel):
    assets: List[str]
    returns_history: List[List[float]]  # T observations x N assets

class OptimizationConstraints(BaseModel):
    min_weights: Optional[Dict[str, float]] = None
    max_weights: Optional[Dict[str, float]] = None
    target_return: Optional[float] = None
    max_volatile_allocation: Optional[float] = None
    volatile_assets: Optional[List[str]] = None

class TierConstraint(BaseModel):
    asset_indices: List[int]
    min_total: float
    max_total: float

class View(BaseModel):
    asset_indices: List[int]
    asset_weights: List[float]
    expected_return: float
    confidence: Optional[float] = None

class OptimizationResult(BaseModel):
    weights: List[float]
    method: str
    converged: bool
    portfolio_volatility: Optional[float] = None
    risk_contributions: Optional[List[float]] = None
    message: Optional[str] = None

class SimulationResult(BaseModel):
    var_95: float
    var_99: float
    cvar_95: float
    cvar_99: float
    expected_max_drawdown: float
    worst_max_drawdown: float
    prob_ruin_30pct: float
    mean_return: float
    median_return: float
    return_std: float
    horizon_days: int
    n_simulations: int
    distribution: str

class RegimePrediction(BaseModel):
    current_regime: str
    regime_probabilities: Dict[str, float]
    transition_probabilities: Dict[str, float]
    crisis_probability_3step: float
    confidence: float

class MarkowitzRequest(BaseModel):
    assets: List[str]
    expected_returns: List[float]
    covariance_matrix: List[List[float]]
    constraints: Optional[OptimizationConstraints] = None

class RiskParityRequest(BaseModel):
    assets: List[str]
    covariance_matrix: List[List[float]]
    constraints: Optional[OptimizationConstraints] = None

class BlackLittermanRequest(BaseModel):
    assets: List[str]
    market_weights: Dict[str, float]
    covariance_matrix: List[List[float]]
    risk_aversion: float = 2.5
    tau: float = 0.05
    views_p: List[List[float]]  # K views x N assets
    views_q: List[float]        # K views
    view_confidences: Optional[List[float]] = None  # Diagonal of Omega

class OptimizationResponse(BaseModel):
    weights: Dict[str, float]
    expected_return: float
    expected_volatility: float
    status: str

class VaRRequest(BaseModel):
    assets: List[str]
    weights: Dict[str, float]
    returns_history: List[List[float]]
    confidence_level: float = 0.95
    horizon_days: int = 1
    method: Literal["historical", "parametric", "monte_carlo"] = "historical"

class VaRResponse(BaseModel):
    var: float
    cvar: float
    method: str
    confidence_level: float
