"""
pepe_sma_cooldown_backtest.py
- Fetches PEPE/USDT 5m from Binance (via ccxt)
- Builds 2-SMA signals
- Implements directional cooldown filtering
- Backtests simple entries (enter next candle open on signal) and exits on opposite signal
- Compares metrics with different cooldown lengths
- Outputs CSV and plots

Usage:
    python pepe_sma_cooldown_backtest.py
"""

import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone
import matplotlib.pyplot as plt
from tqdm import tqdm
import os

# ========== USER PARAMETERS ==========
SYMBOL = "PEPE/USDT"              # symbol for ccxt (Binance formatting)
TIMEFRAME = "5m"
LIMIT = 5000                      # number of candles to fetch (max depends on API)
FAST_SMA = 9
SLOW_SMA = 21
COOLDOWNS = [0, 10, 20, 30]       # cooldown lengths to test (in candles)
FEE = 0.001                       # round-trip fee (0.1% typical -> set as needed)
MIN_TRADE_SIZE = 1.0              # nominal units per trade (we'll use percent returns, so any constant works)
OUTPUT_DIR = "pepe_backtest_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)
# ====================================

def fetch_ohlcv_ccxt(symbol=SYMBOL, timeframe=TIMEFRAME, limit=LIMIT, exchange_id='binance'):
    ex = getattr(ccxt, exchange_id)()
    ex.load_markets()
    # Binance uses uppercase symbol with /; ccxt expects "PEPE/USDT"
    # ccxt returns [timestamp, open, high, low, close, volume]
    print(f"fetching {symbol} {timeframe} candles from {exchange_id} (limit={limit}) ...")
    since = None
    all_klines = []
    # ccxt can fetch limited amounts per request; we'll try to get `limit` candles in one call if supported
    try:
        klines = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        all_klines = klines
    except Exception as e:
        # fallback: iterative fetch
        print("single fetch failed, falling back to iterative fetch:", e)
        # fetch iteratively (be mindful of rate limits)
        now = ex.milliseconds()
        oldest = now - (limit * ex.parse_timeframe(timeframe) * 1000)
        since = oldest
        while True:
            piece = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
            if not piece:
                break
            all_klines += piece
            since = piece[-1][0] + 1
            if len(all_klines) >= limit:
                break
            time.sleep(0.5)
        all_klines = all_klines[-limit:]
    df = pd.DataFrame(all_klines, columns=["ts","open","high","low","close","volume"])
    df['date'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.set_index('date', inplace=True)
    df = df[["open","high","low","close","volume"]].astype(float)
    print(f"fetched {len(df)} candles (index: {df.index[0]} -> {df.index[-1]})")
    return df

def generate_sma_signals(df, fast=FAST_SMA, slow=SLOW_SMA):
    df = df.copy()
    df['sma_fast'] = df['close'].rolling(fast, min_periods=1).mean()
    df['sma_slow'] = df['close'].rolling(slow, min_periods=1).mean()
    # signal: 1 for buy (fast > slow), -1 for sell (fast < slow), 0 neutral (equal)
    df['raw_sig'] = 0
    df.loc[df['sma_fast'] > df['sma_slow'], 'raw_sig'] = 1
    df.loc[df['sma_fast'] < df['sma_slow'], 'raw_sig'] = -1
    # only register changes on cross (optional): make signal only when cross happens
    df['sig_change'] = df['raw_sig'].diff().fillna(0)
    # We'll consider a "signal" to be the raw_sig but we will treat entry only when raw_sig changes from previous or at least matches direction and previous not same trade (handled in backtest)
    return df

def apply_directional_cooldown(signals, cooldown):
    """
    signals: pd.Series of raw_sig (1, -1, 0)
    cooldown: int number of candles to block same-direction signal after taking one
    returns: pd.Series filtered_sig where repeated same-side signals within cooldown are suppressed (set to 0)
    Direction-specific: blocking only repeats of same side.
    """
    s = signals.copy().astype(int)
    out = s.copy()*0
    last_taken_dir = 0
    last_taken_idx = None
    for i, (ts, val) in enumerate(s.items()):
        if val == 0:
            out.iloc[i] = 0
            continue
        if last_taken_dir == 0:
            # no active cooldown
            out.iloc[i] = val
            last_taken_dir = val
            last_taken_idx = i
        else:
            # check if same direction and within cooldown
            if val == last_taken_dir:
                if (i - last_taken_idx) <= cooldown:
                    out.iloc[i] = 0  # blocked
                else:
                    out.iloc[i] = val
                    last_taken_idx = i
            else:
                # opposite direction: allow immediately and reset
                out.iloc[i] = val
                last_taken_dir = val
                last_taken_idx = i
    return out

def compute_chop_stats(raw_sig_series):
    """
    Define 'chop sequences' as consecutive flips between directions where signals change quickly.
    We'll compute the distribution of same-direction streak lengths and the mean/median.
    Also compute 'average time between directional changes' (in candles).
    """
    s = raw_sig_series.replace(0, np.nan).ffill().fillna(0).astype(int)  # forward-fill neutral to last dir where possible
    # find indexes where direction changes
    changes = s[s != s.shift(1)].index
    # compute distances in candles between changes
    idxs = list(changes)
    if len(idxs) < 2:
        return {"changes": len(idxs), "mean_candles_between_changes": None, "median": None}
    # map to integer distances
    candle_numbers = np.arange(len(s))
    change_positions = [candle_numbers[s.index.get_loc(i)] for i in idxs]
    diffs = np.diff(change_positions)
    return {"changes": len(idxs), "mean_candles_between_changes": float(np.mean(diffs)), "median_candles_between_changes": float(np.median(diffs)), "diffs": diffs}

def backtest_signals(df, sig_series, fee=FEE):
    """
    Simple backtest:
     - Enter at the next candle OPEN when a non-zero signal appears (1 => long, -1 => short)
     - Exit when an opposite non-zero signal is taken (enter opposite), or at end of data
     - Position size normalized to 1 unit; returns computed as pct change (long: (exit/open)-1, short: (open/exit)-1)
     - Fees applied as percentage on entry+exit (assumed symmetric)
    returns:
     - DataFrame of trades and summary stats
    """
    df = df.copy()
    sig = sig_series.reindex(df.index).fillna(0).astype(int)
    trades = []
    pos = 0
    entry_price = None
    entry_idx = None
    for i in range(len(df)-1):  # we will enter on i -> price at next candle open (i+1)
        cur_sig = sig.iloc[i]
        next_open = df['open'].iloc[i+1]
        # If currently flat and a signal appears -> enter next candle open
        if pos == 0 and cur_sig != 0:
            pos = cur_sig
            entry_price = next_open
            entry_idx = df.index[i+1]
            entry_fee = entry_price * fee
        # If in a position and a new opposite signal appears -> exit at next candle open (i+1), then optionally open new pos
        elif pos != 0 and cur_sig == -pos:
            exit_price = next_open
            exit_fee = exit_price * fee
            if pos == 1:
                ret = (exit_price - entry_price) / entry_price - (fee*2)
            else:
                # short return (we assume we borrow and P&L is inverse)
                ret = (entry_price - exit_price) / entry_price - (fee*2)
            trades.append({
                "entry_time": entry_idx, "exit_time": df.index[i+1],
                "entry_price": entry_price, "exit_price": exit_price,
                "dir": pos, "return": ret
            })
            # now enter opposite immediately at same candle open (i+1) if signal still indicates (we will treat new pos as taken)
            pos = cur_sig
            entry_price = next_open
            entry_idx = df.index[i+1]
    # close any open position at last candle close
    if pos != 0 and entry_price is not None:
        exit_price = df['close'].iloc[-1]
        if pos == 1:
            ret = (exit_price - entry_price) / entry_price - (fee*2)
        else:
            ret = (entry_price - exit_price) / entry_price - (fee*2)
        trades.append({
            "entry_time": entry_idx, "exit_time": df.index[-1],
            "entry_price": entry_price, "exit_price": exit_price,
            "dir": pos, "return": ret
        })
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return trades_df, {}
    # metrics
    total_return = np.prod(1 + trades_df['return'].values) - 1
    avg_return = trades_df['return'].mean()
    winrate = (trades_df['return'] > 0).mean()
    profit_factor = trades_df[trades_df['return'] > 0]['return'].sum() / (abs(trades_df[trades_df['return'] < 0]['return'].sum()) + 1e-12)
    # equity curve
    eq = (1 + trades_df['return']).cumprod()
    dd = np.maximum.accumulate(eq) - eq
    max_dd = dd.max() if len(dd)>0 else 0
    stats = {
        "n_trades": len(trades_df),
        "total_return": total_return,
        "avg_return": avg_return,
        "winrate": winrate,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd
    }
    return trades_df, stats

def run_full_experiment():
    df = fetch_ohlcv_ccxt()
    df = generate_sma_signals(df)
    # Use raw_sig only when the cross happened (we'll take raw_sig values; the backtest entry rule uses non-zero raw_sig at candle i to enter at i+1)
    raw_sig = df['raw_sig']

    # compute chop stats for raw signals
    chop_raw = compute_chop_stats(raw_sig)

    results = []
    trades_summary = {}

    for cd in COOLDOWNS:
        filtered = apply_directional_cooldown(raw_sig, cooldown=cd)
        chop_filtered = compute_chop_stats(filtered)
        trades_df, stats = backtest_signals(df, filtered)
        stats['cooldown'] = cd
        stats['mean_chop_raw'] = chop_raw.get('mean_candles_between_changes', None)
        stats['mean_chop_filtered'] = chop_filtered.get('mean_candles_between_changes', None)
        results.append(stats)
        trades_summary[cd] = trades_df
        # save trades
        trades_df.to_csv(f"{OUTPUT_DIR}/trades_cd_{cd}.csv", index=False)
        print(f"cooldown {cd} -> trades: {stats['n_trades']}, total_return: {stats['total_return']:.4f}, winrate: {stats['winrate']:.3f}")

    results_df = pd.DataFrame(results).sort_values('cooldown')
    results_df.to_csv(f"{OUTPUT_DIR}/summary_results.csv", index=False)

    # plots
    plt.figure(figsize=(10,6))
    for cd in COOLDOWNS:
        tr = trades_summary[cd]
        if tr.empty:
            continue
        cum = (1 + tr['return']).cumprod()
        plt.plot(cum.values, label=f"cd={cd} (n={len(tr)})")
    plt.legend()
    plt.title("Equity curves by cooldown (trade-by-trade returns)")
    plt.xlabel("trade index")
    plt.ylabel("cumulative return (x)")
    plt.savefig(f"{OUTPUT_DIR}/equity_curves.png")
    plt.close()

    # show simple bar chart metrics
    plt.figure(figsize=(10,5))
    plt.subplot(1,2,1)
    plt.bar(results_df['cooldown'].astype(str), results_df['n_trades'])
    plt.title("Number of trades per cooldown")
    plt.subplot(1,2,2)
    plt.bar(results_df['cooldown'].astype(str), results_df['total_return'])
    plt.title("Total return per cooldown")
    plt.tight_layout()
    plt.savefig(f"{OUTPUT_DIR}/metrics_bar.png")
    plt.close()

    print("\nExperiment complete. Outputs in:", OUTPUT_DIR)
    print(results_df.to_string(index=False))
    return df, results_df, trades_summary

if __name__ == "__main__":
    start = time.time()
    df, results_df, trades = run_full_experiment()
    end = time.time()
    print("Done in %.1f s" % (end - start))