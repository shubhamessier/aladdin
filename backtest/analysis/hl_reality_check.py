import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta

def fetch_hl_data():
    print("Fetching Hyperliquid Live Data...")
    url = "https://api.hyperliquid.xyz/info"
    
    # 1. Get current L2 Book for slippage and spread modeling
    book_req = {"type": "l2Book", "coin": "BTC"}
    book_res = requests.post(url, json=book_req).json()
    
    # 2. Get recent funding rates
    end_time = int(time.time() * 1000)
    start_time = end_time - (30 * 24 * 60 * 60 * 1000) # 30 days
    fund_req = {"type": "fundingHistory", "coin": "BTC", "startTime": start_time, "endTime": end_time}
    fund_res = requests.post(url, json=fund_req).json()
    
    # 3. Get recent candles
    candle_req = {"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": start_time, "endTime": end_time}}
    candle_res = requests.post(url, json=candle_req).json()
    
    return book_res, fund_res, candle_res

def simulate_microstructure(book, funding, candles):
    print("Running Microstructure HFT Simulation on 30-day data...")
    
    # Parse Orderbook
    levels_bids = [(float(lvl['px']), float(lvl['sz'])) for lvl in book['levels'][0]]
    levels_asks = [(float(lvl['px']), float(lvl['sz'])) for lvl in book['levels'][1]]
    
    best_bid = levels_bids[0][0]
    best_ask = levels_asks[0][0]
    spread_bps = (best_ask - best_bid) / best_bid * 10000
    
    print(f"Current Spread: {spread_bps:.2f} bps")
    
    # Parse Candles
    df = pd.DataFrame([c for c in candles])
    df['c'] = df['c'].astype(float)
    returns = df['c'].pct_change().dropna()
    realized_vol = returns.std() * np.sqrt(24 * 365)
    
    # Parse Funding
    funding_rates = [float(f['fundingRate']) for f in funding]
    avg_funding_8h = np.mean(funding_rates)
    ann_funding = avg_funding_8h * 3 * 365
    
    print(f"Realized Volatility: {realized_vol:.2%}")
    print(f"Annualized Funding: {ann_funding:.2%}")

    # Simulation Parameters
    portfolio_size = 1_000_000 # $1M
    trade_size = 100_000       # $100k avg trade
    n_trades = len(df) // 12   # Rebalance twice a day
    
    taker_fee_bps = 3.5
    maker_rebate_bps = 0.2
    
    # Run Adversarial Simulation
    results = {
        'net_pnl': 0.0,
        'gross_pnl': 0.0,
        'fees_paid': 0.0,
        'slippage_loss': 0.0,
        'toxic_flow_loss': 0.0,
        'funding_pnl': 0.0,
        'latency_loss': 0.0
    }
    
    for _ in range(n_trades):
        # Gross signal assumed slightly positive (e.g. 1 bp edge per trade)
        results['gross_pnl'] += trade_size * 0.0001
        
        # Determine execution path
        is_maker = np.random.rand() < 0.7 # Attempt maker 70% of time
        
        if not is_maker:
            # TAKER
            results['fees_paid'] -= trade_size * (taker_fee_bps / 10000)
            results['slippage_loss'] -= trade_size * (spread_bps / 2 / 10000)
            
            # Latency impact (assuming 150ms delay, price drifts against us)
            latency_drift_bps = 0.5 * (realized_vol / 0.5) 
            results['latency_loss'] -= trade_size * (latency_drift_bps / 10000)
            
        else:
            # MAKER
            # Adverse selection probability scales with vol
            toxic_prob = 0.20 + (realized_vol * 0.5)
            is_toxic = np.random.rand() < toxic_prob
            
            if is_toxic:
                # We get filled, but market immediately moves against us by 2x spread
                results['toxic_flow_loss'] -= trade_size * (spread_bps * 2 / 10000)
                # Still earn maker rebate
                results['fees_paid'] += trade_size * (maker_rebate_bps / 10000)
            else:
                # Successful maker fill
                results['fees_paid'] += trade_size * (maker_rebate_bps / 10000)
                
        # Funding drag (assume long bias 50% of the time)
        results['funding_pnl'] -= (portfolio_size * 0.5) * avg_funding_8h * (12 / 3)
        
    results['net_pnl'] = sum(results.values()) - results['net_pnl']
    
    for k, v in results.items():
        print(f"{k}: ${v:,.2f}")

    return results

if __name__ == "__main__":
    b, f, c = fetch_hl_data()
    simulate_microstructure(b, f, c)
