from typing import Dict, Any, List
from tabulate import tabulate

def print_performance_report(metrics: Dict[str, Any], attribution: Dict[str, Any] = None):
    """Prints a formatted table of performance metrics to the terminal."""
    
    print("\n" + "="*50)
    print("BACKTEST PERFORMANCE REPORT".center(50))
    print("="*50 + "\n")
    
    perf_data = [
        ["Total Return", f"{metrics.get('total_return', 0.0) * 100:.2f}%"],
        ["Annualized Return", f"{metrics.get('annualized_return', 0.0) * 100:.2f}%"],
        ["Annualized Volatility", f"{metrics.get('annualized_volatility', 0.0) * 100:.2f}%"],
        ["Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0.0):.2f}"],
        ["Sortino Ratio", f"{metrics.get('sortino_ratio', 0.0):.2f}"],
        ["Max Drawdown", f"{metrics.get('max_drawdown', 0.0) * 100:.2f}%"],
        ["Historical VaR (95%)", f"${metrics.get('historical_var_95_1d', 0.0):,.2f}"],
        ["Historical VaR (99%)", f"${metrics.get('historical_var_99_1d', 0.0):,.2f}"]
    ]
    
    print(tabulate(perf_data, headers=["Metric", "Value"], tablefmt="fancy_grid"))
    
    if attribution:
        print("\n" + "-"*50)
        print("ATTRIBUTION ANALYSIS".center(50))
        print("-"*50 + "\n")
        
        attr_data = [
            ["Beta to Benchmark", f"{attribution.get('beta_to_benchmark', 0.0):.2f}"],
            ["Annualized Alpha", f"{attribution.get('annualized_alpha', 0.0) * 100:.2f}%"],
            ["Active Return", f"{attribution.get('active_return', 0.0) * 100:.2f}%"],
            ["Tracking Error", f"{attribution.get('tracking_error', 0.0) * 100:.2f}%"],
            ["Information Ratio", f"{attribution.get('information_ratio', 0.0):.2f}"]
        ]
        
        if "yield_contribution_pct" in attribution:
            attr_data.extend([
                ["Yield Contribution", f"{attribution.get('yield_contribution_pct', 0.0) * 100:.2f}%"],
                ["Derivative Contribution", f"{attribution.get('derivative_contribution_pct', 0.0) * 100:.2f}%"],
                ["Spot Contribution", f"{attribution.get('spot_contribution_pct', 0.0) * 100:.2f}%"]
            ])
            
        print(tabulate(attr_data, headers=["Component", "Value"], tablefmt="fancy_grid"))
    print("\n" + "="*50 + "\n")

def print_simulation_summary(history: List[Dict[str, Any]]):
    """Prints a summary of the simulation run."""
    if not history:
        print("No simulation history found.")
        return
        
    start_date = history[0]["timestamp"].strftime("%Y-%m-%d")
    end_date = history[-1]["timestamp"].strftime("%Y-%m-%d")
    start_val = history[0]["portfolio_value"]
    end_val = history[-1]["portfolio_value"]
    
    total_trade_volume = sum(h.get("trade_volume_usd", 0.0) for h in history)
    cb_triggers = sum(1 for h in history if h.get("cb_level", 0) > 0)
    
    summary = [
        ["Start Date", start_date],
        ["End Date", end_date],
        ["Initial Value", f"${start_val:,.2f}"],
        ["Final Value", f"${end_val:,.2f}"],
        ["Total Trade Volume", f"${total_trade_volume:,.2f}"],
        ["Days in Circuit Breaker", cb_triggers]
    ]
    
    print("\nSimulation Summary:")
    print(tabulate(summary, tablefmt="simple"))
