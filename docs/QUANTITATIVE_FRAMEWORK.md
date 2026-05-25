# Quantitative Framework

This document outlines the mathematical models and quantitative strategies implemented in the Python Risk Engine to guide the Autonomous Treasury Management System.

## 1. Portfolio Optimization

### 1.1 Risk Parity Allocation
- **Formula**: Minimize $\sum_{i=1}^N \left( RC_i - \frac{\sigma_p}{N} \right)^2$ subject to constraints, where $RC_i = \frac{w_i \cdot (\Sigma w)_i}{\sigma_p}$ is the risk contribution of asset $i$, and $\sigma_p = \sqrt{w^T \Sigma w}$ is the portfolio volatility.
- **Assumptions**: Assumes that all assets should contribute equally to the total portfolio risk. Assumes covariance matrix $\Sigma$ is stable and accurately reflects future risk.
- **Calibration Method**: Uses SLSQP minimization with bounds and tier constraints. Initial guess is equal weighting adjusted to satisfy bounds.
- **Known Limitations**: Does not consider expected returns. Can overallocate to low-volatility assets if not bounded properly.
- **Fallback**: If optimization fails to converge, falls back to inverse-volatility weighting: $w_i = \frac{1/\sigma_i}{\sum (1/\sigma_j)}$.
- **Reference**: Asness, C. S., Frazzini, A., & Pedersen, L. H. (2012). Leverage aversion and risk parity. *Financial Analysts Journal*, 68(1), 47-59.

### 1.2 Black-Litterman Model
- **Formula**: 
  - Posterior Returns: $E[R] = [(\tau\Sigma)^{-1} + P^T \Omega^{-1} P]^{-1} [(\tau\Sigma)^{-1} \Pi + P^T \Omega^{-1} Q]$
  - Posterior Covariance: $\Sigma_p = \Sigma + [(\tau\Sigma)^{-1} + P^T \Omega^{-1} P]^{-1}$
  - Implied Equilibrium Returns: $\Pi = \delta \Sigma w_{mkt}$
- **Assumptions**: Returns are normally distributed. Investors agree on the covariance matrix but have different views on expected returns.
- **Calibration Method**: Market-implied equilibrium returns calculated from market caps ($w_{mkt}$) and risk aversion factor ($\delta \approx 2.5$). View confidence calibrated using Idzorek's method. Default $\tau = 0.05$.
- **Known Limitations**: Sensitive to the choice of $\tau$ and the view confidence scalar $\Omega$.
- **Fallback**: If no views are provided, optimizes using market-implied equilibrium returns (standard Mean-Variance Optimization).
- **Reference**: Black, F., & Litterman, R. (1992). Global portfolio optimization. *Financial Analysts Journal*, 48(5), 28-43. Idzorek, T. (2005). A step-by-step guide to the Black-Litterman model.

## 2. Risk Modeling (Value at Risk)

### 2.1 Monte Carlo Simulation (Correlated, Fat-Tailed)
- **Formula**: GBM Step: $S(t+1) = S(t) \cdot \exp\left((\mu - \frac{\sigma^2}{2})dt + \sigma \sqrt{dt} Z\right)$ where $Z$ is a correlated Student-t innovation.
- **Assumptions**: Asset returns follow a Geometric Brownian Motion (GBM) but with Student-t distributed innovations to account for fat tails (leptokurtic).
- **Calibration Method**: Cholesky decomposition ($L$) of the correlation matrix is used to correlate independent Student-t samples. Default degrees of freedom $df = 5.0$. Simulates 50,000 paths over a defined horizon (e.g., 30 days).
- **Known Limitations**: Computationally expensive. The assumption of constant volatility over the simulation horizon is a simplification (unless coupled with GARCH).
- **Fallback**: If Cholesky decomposition fails due to non-positive definite matrix, the nearest positive-definite matrix is calculated and used.
- **Reference**: Glasserman, P. (2004). *Monte Carlo Methods in Financial Engineering*.

### 2.2 Expected Shortfall (CVaR)
- **Formula**: $CVaR_{\alpha} = E[L | L > VaR_{\alpha}]$
- **Assumptions**: Same assumptions as the underlying VaR model (Historical, Parametric, or Monte Carlo).
- **Calibration Method**: Calculated as the mean of the losses that exceed the VaR threshold.
- **Known Limitations**: Can be noisy if the tail sample size is small (e.g., in historical simulation with limited data).
- **Fallback**: N/A (inherently linked to the VaR calculation method).
- **Reference**: Artzner, P., Delbaen, F., Eber, J. M., & Heath, D. (1999). Coherent measures of risk. *Mathematical finance*, 9(3), 203-228.

## 3. Covariance & Filtering

### 3.1 Ledoit-Wolf Shrinkage & Random Matrix Theory (RMT)
- **Formula**: Shrinks the sample covariance matrix towards a structured estimator (e.g., identity matrix or constant correlation matrix) to reduce estimation error.
- **Assumptions**: Sample covariance matrix is noisy, especially when the number of assets $N$ is comparable to the number of observations $T$.
- **Calibration Method**: Utilizes the Ledoit-Wolf optimal shrinkage intensity.
- **Known Limitations**: Shrinkage target might introduce bias if the true covariance structure significantly deviates from the target.
- **Fallback**: Uses standard sample covariance if shrinkage algorithms fail.
- **Reference**: Ledoit, O., & Wolf, M. (2004). Honey, I shrunk the sample covariance matrix. *The Journal of Portfolio Management*, 30(4), 110-119.

## 4. Market State Classification

### 4.1 Gaussian Hidden Markov Model (HMM) Regime Detector
- **Formula**: Fits a 3-state (bull, uncertain, crisis) Gaussian HMM to a broad crypto index daily return series.
- **Assumptions**: Market conditions switch between distinct, unobservable states (regimes), each characterized by a different mean and variance of returns. Transitions follow a Markov process.
- **Calibration Method**: Fitted using the Baum-Welch algorithm (EM) with multiple random initializations to avoid local optima. `tol=1e-6`, `n_iter=200`. States are labeled based on mean and variance (highest mean/lowest variance = bull).
- **Known Limitations**: Assumes returns within a regime are normally distributed. Can lag in detecting sudden regime shifts.
- **Fallback**: If fitting fails after 10 attempts, raises a `RuntimeError` (handled by the Guardian shifting to conservative settings).
- **Reference**: Hamilton, J. D. (1989). A new approach to the economic analysis of nonstationary time series and the business cycle. *Econometrica*, 357-384.
