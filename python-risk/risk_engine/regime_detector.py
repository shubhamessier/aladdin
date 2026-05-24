import numpy as np
import pandas as pd  # type: ignore
from hmmlearn.hmm import GaussianHMM  # type: ignore
from typing import Dict, Any, List

from .schemas import RegimePrediction

class RegimeDetector:
    def __init__(self) -> None:
        self.model: GaussianHMM | None = None
        self.regime_labels = {0: 'bull', 1: 'uncertain', 2: 'crisis'}
        self.training_window = 365  # days
        self.state_map: Dict[int, str] = {}
    
    def fit(self, returns: pd.Series) -> None:
        """
        Fit a 3-state Gaussian HMM to daily returns.
        Returns should be a daily return series of a broad crypto index 
        (e.g., 60% BTC + 40% ETH weighted return).
        """
        X = returns.values.reshape(-1, 1)
        
        # Fit with multiple random initializations to avoid local optima
        best_model = None
        best_score = -np.inf
        
        for _ in range(10):
            model = GaussianHMM(
                n_components=3,
                covariance_type='full',
                n_iter=200,
                random_state=np.random.randint(0, 10000),
                tol=1e-6,
            )
            try:
                model.fit(X)
                score = model.score(X)
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception:
                continue
        
        if best_model is None:
            raise RuntimeError('HMM fitting failed after 10 attempts')
        
        self.model = best_model
        
        # Label the states by their mean return and variance
        # State with highest mean and lowest var = bull
        # State with lowest mean and highest var = crisis
        means = self.model.means_.flatten()
        variances = np.array([c[0, 0] for c in self.model.covars_])
        
        # Score: high mean + low variance = bull
        scores = means - 0.5 * np.sqrt(variances)
        sorted_indices = np.argsort(scores)[::-1]  # Highest score first
        
        # Map: sorted_indices[0] → bull, sorted_indices[1] → uncertain, sorted_indices[2] → crisis
        self.state_map = {
            int(sorted_indices[0]): 'bull',
            int(sorted_indices[1]): 'uncertain',
            int(sorted_indices[2]): 'crisis',
        }
    
    def predict(self, returns: pd.Series) -> RegimePrediction:
        """
        Predict current regime and transition probabilities.
        """
        if self.model is None or not self.state_map:
            raise RuntimeError('Model not fitted')
        
        X = returns.values.reshape(-1, 1)
        
        # Get the most likely state sequence
        hidden_states = self.model.predict(X)
        current_raw_state = int(hidden_states[-1])
        current_regime = self.state_map[current_raw_state]
        
        # Get state probabilities for the most recent observation
        posteriors = self.model.predict_proba(X)
        current_probs = posteriors[-1]  # Probability of each state given all data
        
        # Transition probabilities from current state
        transmat = self.model.transmat_
        transition_probs = {
            self.state_map[j]: float(transmat[current_raw_state, j])
            for j in range(3)
        }
        
        # Early warning: probability of transitioning to crisis within 1-3 steps
        # P(crisis in next 3 steps) = 1 - P(not crisis for 3 steps)
        crisis_state = [k for k, v in self.state_map.items() if v == 'crisis'][0]
        
        trans_2 = transmat @ transmat
        trans_3 = trans_2 @ transmat
        p_crisis_within_3 = float(1.0 - np.prod([
            1.0 - transmat[current_raw_state, crisis_state],
            1.0 - trans_2[current_raw_state, crisis_state],
            1.0 - trans_3[current_raw_state, crisis_state],
        ]))
        
        return RegimePrediction(
            current_regime=current_regime,
            regime_probabilities={self.state_map[i]: float(current_probs[i]) for i in range(3)},
            transition_probabilities=transition_probs,
            crisis_probability_3step=p_crisis_within_3,
            confidence=float(np.max(current_probs)),  # How certain are we about the current state
        )
