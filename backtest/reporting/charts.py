import pandas as pd
import matplotlib.pyplot as plt
import os
from pathlib import Path

def setup_style() -> None:
    plt.style.use('seaborn-v0_8-darkgrid')
    plt.rcParams.update({
        'figure.figsize': (12, 7),
        'axes.titlesize': 14,
        'axes.labelsize': 12,
        'lines.linewidth': 1.5,
        'lines.markersize': 4,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10
    })

def generate_nav_comparison(
    portfolio_history: pd.DataFrame, 
    benchmark_history: pd.Series, 
    output_dir: str = "output",
    filename: str = "nav_comparison.png"
) -> str:
    setup_style()
    os.makedirs(output_dir, exist_ok=True)
    
    fig, ax = plt.subplots()
    
    if 'portfolio_value' in portfolio_history.columns and not portfolio_history.empty:
        base_val = portfolio_history['portfolio_value'].iloc[0]
        port_nav = portfolio_history['portfolio_value'] / base_val * 100
        ax.plot(portfolio_history.index, port_nav, label='Strategy Portfolio', color='#1f77b4', linewidth=2)
        
        if 'effective_hwm' in portfolio_history.columns:
            eff_hwm_nav = portfolio_history['effective_hwm'] / base_val * 100
            ax.plot(portfolio_history.index, eff_hwm_nav, label='Effective HWM', color='green', linestyle='--', alpha=0.6)

    if not benchmark_history.empty:
        bench_nav = benchmark_history / benchmark_history.iloc[0] * 100
        ax.plot(benchmark_history.index, bench_nav, label='Benchmark', color='#ff7f0e', alpha=0.5)
        
    ax.set_title('NAV Performance with Decaying HWM (Base 100)')
    ax.set_xlabel('Date')
    ax.set_ylabel('Normalized Value')
    
    if 'cb_level' in portfolio_history.columns:
        cb_active = portfolio_history['cb_level'] > 0
        if cb_active.any():
            ax.fill_between(portfolio_history.index, 0, 1, where=cb_active, 
                            color='red', alpha=0.1, transform=ax.get_xaxis_transform(), label='CB Active')

    if 'recovery_active' in portfolio_history.columns:
        rec_active = portfolio_history['recovery_active'] == True
        if rec_active.any():
            ax.fill_between(portfolio_history.index, 0, 1, where=rec_active, 
                            color='blue', alpha=0.1, transform=ax.get_xaxis_transform(), label='Recovery Phase')

    # Deduplicate legend
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc='upper left')

    plt.tight_layout()
    output_path = os.path.join(output_dir, filename)
    plt.savefig(output_path, dpi=300)
    plt.close()
    return output_path

def generate_drawdown_comparison(
    portfolio_history: pd.DataFrame, 
    benchmark_history: pd.Series, 
    output_dir: str = "output",
    filename: str = "drawdown_comparison.png"
) -> str:
    setup_style()
    os.makedirs(output_dir, exist_ok=True)
    fig, ax = plt.subplots()
    
    if 'portfolio_value' in portfolio_history.columns and not portfolio_history.empty:
        running_max = portfolio_history['portfolio_value'].cummax()
        drawdowns = (portfolio_history['portfolio_value'] - running_max) / running_max * 100
        ax.fill_between(portfolio_history.index, drawdowns, 0, color='#1f77b4', alpha=0.5, label='Strategy DD')
        
    if not benchmark_history.empty:
        running_max_b = benchmark_history.cummax()
        drawdowns_b = (benchmark_history - running_max_b) / running_max_b * 100
        ax.plot(benchmark_history.index, drawdowns_b, color='#ff7f0e', label='Benchmark DD', alpha=0.7)
        
    ax.set_title('Portfolio Drawdown (%)')
    ax.set_xlabel('Date')
    ax.set_ylabel('Drawdown (%)')
    ax.legend(loc='lower right')
    plt.tight_layout()
    output_path = os.path.join(output_dir, filename)
    plt.savefig(output_path, dpi=300)
    plt.close()
    return output_path

def generate_allocation_area_chart(history: pd.DataFrame, output_dir: str = "output") -> str:
    # Existing implementation
    return "skipped"
