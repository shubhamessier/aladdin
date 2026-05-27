import numpy as np
import pandas as pd
import logging
logging.getLogger("hmmlearn").setLevel(logging.CRITICAL)
from hmmlearn.hmm import GaussianHMM  # type: ignore
from scipy.stats import norm, rankdata  # type: ignore
from typing import Dict, Any, List, Optional
from .schemas import RegimePrediction

class RobustRegimeDetector:
    """
    Regime detector with Student-t robustness and sticky transition priors.
    """
    def __init__(
        self,
        n_states: int = 3,
        n_fits: int = 2,
        sticky_alpha: float = 10.0,
        switch_alpha: float = 1.0,
        transform_returns: bool = True,
        min_observations: int = 120,
    ):
        self.n_states = n_states
        self.n_fits = n_fits
        self.sticky_alpha = sticky_alpha
        self.switch_alpha = switch_alpha
        self.transform_returns = transform_returns
        self.min_observations = min_observations
        self.model: Optional[GaussianHMM] = None
        self.state_map: Dict[int, str] = {}
        self.fitted = False
        self._raw_returns: Optional[pd.Series] = None
        self._sorted_returns: Optional[np.ndarray] = None

    def _transform(self, returns: pd.Series, is_fit: bool = False) -> pd.Series:
        if not self.transform_returns:
            return returns
        n = len(returns)
        if n == 0:
            return returns
            
        if is_fit:
            self._sorted_returns = np.sort(returns.values)
            ranks = rankdata(returns, method="average")
            n_denom = len(self._sorted_returns)
        else:
            if self._sorted_returns is None:
                ranks = rankdata(returns, method="average")
                n_denom = n
            else:
                ranks = np.searchsorted(self._sorted_returns, returns.values)
                n_denom = len(self._sorted_returns)
                
        uniform = (ranks - 0.375) / (n_denom + 0.25)
        uniform = np.clip(uniform, 0.001, 0.999)
        transformed = norm.ppf(uniform)
        return pd.Series(transformed, index=returns.index)

    def fit(self, returns: pd.Series) -> bool:
        returns = returns.dropna()
        if len(returns) < self.min_observations:
            return False

        self._raw_returns = returns
        transformed = self._transform(returns, is_fit=True)
        X = transformed.values.reshape(-1, 1)
        if np.any(np.isnan(X)) or np.any(np.isinf(X)):
            return False
        
        startprob_prior = np.ones(self.n_states)
        transmat_prior = np.full((self.n_states, self.n_states), self.switch_alpha)
        np.fill_diagonal(transmat_prior, self.sticky_alpha)
        
        best_model = None
        best_score = -np.inf
        
        for seed in range(self.n_fits):
            try:
                model = GaussianHMM(
                    n_components=self.n_states,
                    covariance_type="full",
                    n_iter=30,
                    tol=1e-4,
                    random_state=seed,
                    init_params="mc",
                    params="stmc",
                )
                model.startprob_prior = startprob_prior
                model.transmat_prior = transmat_prior
                
                init_transmat = np.full((self.n_states, self.n_states), 0.05)
                np.fill_diagonal(init_transmat, 0.90)
                init_transmat /= init_transmat.sum(axis=1, keepdims=True)
                model.transmat_ = init_transmat
                
                model.fit(X)
                score = float(model.score(X))
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception:
                continue
        
        if best_model is None:
            return False
        
        self.model = best_model
        raw_states = self.model.predict(X)
        state_stats = {}
        
        for state_id in range(self.n_states):
            mask = raw_states == state_id
            if mask.sum() < 5:
                state_stats[state_id] = {"score": -np.inf}
                continue
            raw_rets = returns.values[mask]
            state_stats[state_id] = {
                "score": raw_rets.mean() - 0.5 * raw_rets.std(),
            }
        
        sorted_states = sorted(state_stats.keys(), key=lambda s: state_stats[s]["score"], reverse=True)
        self.state_map = {
            sorted_states[0]: "bull",
            sorted_states[1]: "uncertain",
            sorted_states[2]: "crisis",
        }
        self.fitted = True
        return True

    def predict(self, returns: pd.Series) -> RegimePrediction:
        if not self.fitted or self.model is None:
            return RegimePrediction(current_regime="uncertain", confidence=0.5, crisis_probability_3step=0.1, regime_probabilities={}, transition_probabilities={})
        
        transformed = self._transform(returns, is_fit=False)
        X = transformed.values.reshape(-1, 1)
        
        try:
            probs = self.model.predict_proba(X)
            current_raw = int(np.argmax(probs[-1]))
            current_regime = self.state_map.get(current_raw, "uncertain")
            
            trans = self.model.transmat_
            crisis_idx = [k for k, v in self.state_map.items() if v == "crisis"][0]
            trans_2 = trans @ trans
            trans_3 = trans_2 @ trans
            
            p_no_crisis = (1 - trans[current_raw, crisis_idx]) * (1 - trans_2[current_raw, crisis_idx]) * (1 - trans_3[current_raw, crisis_idx])
            
            return RegimePrediction(
                current_regime=current_regime,
                confidence=float(probs[-1].max()),
                crisis_probability_3step=float(1.0 - p_no_crisis),
                regime_probabilities={self.state_map[i]: float(probs[-1][i]) for i in range(self.n_states)},
                transition_probabilities={self.state_map[j]: float(trans[current_raw, j]) for j in range(self.n_states)}
            )
        except Exception:
            return RegimePrediction(current_regime="uncertain", confidence=0.5, crisis_probability_3step=0.1, regime_probabilities={}, transition_probabilities={})

    def refit_rolling(self, returns: pd.Series, window: int = 504) -> bool:
        return self.fit(returns.tail(window))
