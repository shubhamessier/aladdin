import pandas as pd
import matplotlib.pyplot as plt
import os
from pathlib import Path

def setup_style() -> None:
    """Configure matplotlib styling for reports."""
    plt.style.use('seaborn-v0_8-darkgrid')
    plt.rcParams.update({
        'figure.figsize': (10, 6),
        'axes.titlesize': 14,
        'axes.labelsize': 12,
        'lines.linewidth': 2,
        'lines.markersize': 6,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10
    })

def generate_nav_comparison(
    portfolio_history: pd.DataFrame, 
    benchmark_history: pd.Series, 
    output_dir: str = "output"
) -> str:
    """
    Generates a NAV comparison chart between the portfolio and a benchmark.
    Returns the file path.
    """
    setup_style()
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots()
    
    if 'portfolio_value' in portfolio_history.columns and not portfolio_history.empty:
        # Normalize to 100
        port_nav = portfolio_history['portfolio_value'] / portfolio_history['portfolio_value'].iloc[0] * 100
        ax.plot(portfolio_history.index, port_nav, label='Strategy Portfolio', color='#1f77b4')
        
    if not benchmark_history.empty:
        bench_nav = benchmark_history / benchmark_history.iloc[0] * 100
        ax.plot(benchmark_history.index, bench_nav, label='Benchmark', color='#ff7f0e', alpha=0.8)
        
    ax.set_title('Normalized NAV Comparison (Base 100)')
    ax.set_xlabel('Date')
    ax.set_ylabel('Normalized Value')
    ax.legend(loc='upper left')
    
    # Shade circuit breaker periods if they exist
    if 'cb_level' in portfolio_history.columns:
        cb_active = portfolio_history['cb_level'] > 0
        if cb_active.any():
            ax.fill_between(portfolio_history.index, 0, 1, where=cb_active, 
                            color='red', alpha=0.1, transform=ax.get_xaxis_transform(), label='Circuit Breaker')
            # Deduplicate labels
            handles, labels = ax.get_legend_handles_labels()
            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys(), loc='upper left')

    plt.tight_layout()
    output_path = os.path.join(output_dir, "nav_comparison.png")
    plt.savefig(output_path, dpi=300)
    plt.close()
    
    return output_path

def generate_drawdown_comparison(
    portfolio_history: pd.DataFrame, 
    benchmark_history: pd.Series, 
    output_dir: str = "output"
) -> str:
    """
    Generates a drawdown comparison chart.
    Returns the file path.
    """
    setup_style()
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots()
    
    if 'portfolio_value' in portfolio_history.columns and not portfolio_history.empty:
        running_max = portfolio_history['portfolio_value'].cummax()
        drawdowns = (portfolio_history['portfolio_value'] - running_max) / running_max * 100
        ax.fill_between(portfolio_history.index, drawdowns, 0, color='#1f77b4', alpha=0.5, label='Strategy Drawdown')
        
    if not benchmark_history.empty:
        running_max_b = benchmark_history.cummax()
        drawdowns_b = (benchmark_history - running_max_b) / running_max_b * 100
        ax.plot(benchmark_history.index, drawdowns_b, color='#ff7f0e', label='Benchmark Drawdown', linewidth=1.5, alpha=0.8)
        
    ax.set_title('Drawdown Comparison (%)')
    ax.set_xlabel('Date')
    ax.set_ylabel('Drawdown (%)')
    ax.legend(loc='lower right')
    
    plt.tight_layout()
    output_path = os.path.join(output_dir, "drawdown_comparison.png")
    plt.savefig(output_path, dpi=300)
    plt.close()
    
    return output_path

def generate_allocation_area_chart(
    history: pd.DataFrame, 
    output_dir: str = "output"
) -> str:
    """
    Generates a stacked area chart of portfolio allocations over time.
    Requires history DataFrame to have columns for each asset allocation.
    """
    setup_style()
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots()
    
    # Identify asset columns (assuming they are uppercase and length 3-5, e.g. BTC, ETH, USDC)
    # Alternatively, expecting them to be passed properly.
    asset_cols = [col for col in history.columns if isinstance(col, str) and col.isupper() and 3 <= len(col) <= 5]
    
    if asset_cols:
        ax.stackplot(history.index, [history[col] for col in asset_cols], labels=asset_cols, alpha=0.8)
        ax.set_title('Portfolio Allocation Over Time')
        ax.set_xlabel('Date')
        ax.set_ylabel('Allocation (%)')
        
        # Place legend outside
        ax.legend(loc='upper left', bbox_to_anchor=(1, 1))
        
    plt.tight_layout()
    output_path = os.path.join(output_dir, "allocation_history.png")
    plt.savefig(output_path, dpi=300)
    plt.close()
    
    return output_path
