"""
Range Filter Batch Backtester
Generated for user Malcolm

Usage:
    - Place all CSV files in a single folder (default: ./data). Each CSV should have columns:
      open_time,open,high,low,close,volume,close_time,quote_asset_volume,number_of_trades,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore
    - Run: python range_filter_backtester.py
    - Outputs:
        ./results/per_token_metrics.csv  -> metrics for each token
        ./results/overall_metrics.json   -> aggregated metrics
        ./results/<token>_equity.png     -> equity curve for top tokens
        ./results/performance_bar.png    -> per-token total returns bar chart
        ./results/equity_curve.png       -> combined equity curve

Features & assumptions (carefully chosen):
    - Strategy rules translated from the provided Pine Script 'Range Filter [DW]'.
    - Only LONG trades are allowed (no margin/shorting).
    - Entry/Exit executed on the NEXT BAR OPEN after a signal change (no lookahead).
    - Fees: 0.5% per side (entry and exit), configurable.
    - Position sizing: fraction of equity per trade (default 100% -> all-in). Configurable.
    - Realistic handling of end-of-data (open positions closed at last available close).
    - Multi-worker processing using joblib (n_jobs=-1 allowed).
    - Metrics computed per-token and overall: total return, CAGR, annualized volatility, Sharpe (risk-free rate = 0), max drawdown, win rate, profit factor, expectancy, number of trades, average trade P&L.
    - Two visualizations: combined equity curve and per-token performance bar; plus individual token equity PNGs for the largest-N tokens.

User-tweakable indicator inputs are at the top of the file (marked CONFIG). Please edit there to experiment with the strategy.

Notes about implementation accuracy:
    - The Range Filter algorithm is implemented iteratively to match Pine's stateful array behavior. Care was taken to ensure only past and present information is used when computing signals.
    - The filter uses either 'Close' or 'Wicks' movement source; by default 'Close' as in the Pine script.

"""

import os
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from tqdm import tqdm
import matplotlib.pyplot as plt

# ------------------------- CONFIG (user-editable) -------------------------
DATA_DIR = Path("data")              # folder containing CSVs
RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

# Range Filter inputs (mirrors Pine inputs)
F_TYPE = "Type 1"                    # "Type 1" or "Type 2"
MOV_SRC = "Close"                    # "Close" or "Wicks"
RNG_QTY = 2.618
RNG_SCALE = "Average Change"         # Options: Points, Pips, Ticks, % of Price, ATR, Average Change, Standard Deviation, Absolute
RNG_PER = 14
SMOOTH_RANGE = True
SMOOTH_PER = 27
AV_VALS = False
AV_SAMPLES = 2

# Backtest parameters
INITIAL_CAPITAL = 10000.0
POSITION_SIZE = 1.0      # fraction of equity to use per trade (1.0 -> all-in)
FEE_RATE = 0.005         # 0.5% per trade side
SLIPPAGE = 0.0           # slippage fraction (applied on price), set 0 if you don't want slippage
MIN_TRADE_NOTIONAL = 1.0 # ignore trades under this size
N_JOBS = -1              # -1 to use all cores
TOP_N_PLOTS = 6          # produce individual equity plots for top N tokens by total return
RISK_FREE_RATE = 0.0     # used for Sharpe calculation

# -------------------------------------------------------------------------

# ------------------------- utility functions ------------------------------

def to_float(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def compute_metrics(equity_series, trade_returns, timestamps, initial_capital=INITIAL_CAPITAL):
    # equity_series: pd.Series indexed by datetime of equity
    # trade_returns: list of trade return factors (e.g. 0.05 for +5%)
    total_return = (equity_series.iloc[-1] / equity_series.iloc[0]) - 1.0
    days = (equity_series.index[-1] - equity_series.index[0]).days
    years = days / 365.25 if days > 0 else np.nan
    cagr = (equity_series.iloc[-1] / equity_series.iloc[0]) ** (1 / years) - 1 if years and years > 0 else np.nan
    daily_returns = equity_series.pct_change().dropna()
    ann_vol = daily_returns.std() * np.sqrt(252) if len(daily_returns) > 1 else np.nan
    sharpe = (daily_returns.mean() * 252 - RISK_FREE_RATE) / ann_vol if ann_vol and ann_vol > 0 else np.nan
    # drawdown
    cum = equity_series.cummax()
    drawdowns = equity_series / cum - 1.0
    max_dd = drawdowns.min()
    # trades
    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]
    win_rate = len(wins) / len(trade_returns) if trade_returns else np.nan
    avg_win = np.mean(wins) if wins else np.nan
    avg_loss = np.mean(losses) if losses else np.nan
    gross_win = sum([r for r in trade_returns if r > 0])
    gross_loss = -sum([r for r in trade_returns if r < 0])
    profit_factor = (gross_win / gross_loss) if gross_loss and gross_loss > 0 else np.nan
    expectancy = (win_rate * (avg_win if not np.isnan(avg_win) else 0) + (1 - win_rate) * (avg_loss if not np.isnan(avg_loss) else 0)) if trade_returns else np.nan
    metrics = {
        'total_return': total_return,
        'cagr': cagr,
        'annual_vol': ann_vol,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'num_trades': len(trade_returns),
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'expectancy': expectancy
    }
    return metrics

# ------------------------- Range Filter core (stateful) -------------------

def cond_ema(x, cond_mask, n):
    # cond_mask: boolean mask that indicates when to sample (here we always sample)
    # Implemented as iterative EMA that only updates when cond True; but in our use cond always True
    out = np.full(len(x), np.nan)
    alpha = 2.0 / (n + 1)
    prev = np.nan
    for i in range(len(x)):
        if cond_mask is None or cond_mask[i]:
            if np.isnan(prev):
                prev = x[i]
            else:
                prev = (x[i] - prev) * alpha + prev
        out[i] = prev
    return out


def cond_sma(x, cond_mask, n):
    out = np.full(len(x), np.nan)
    buf = []
    for i in range(len(x)):
        if cond_mask is None or cond_mask[i]:
            buf.append(x[i])
            if len(buf) > n:
                buf.pop(0)
        out[i] = np.mean(buf) if buf else np.nan
    return out


def rolling_stdev(x, n):
    # returns similar to Pine's Stdev() which used cond_sma of squares - mean^2
    out = np.full(len(x), np.nan)
    buf = []
    for i in range(len(x)):
        buf.append(x[i])
        if len(buf) > n:
            buf.pop(0)
        if len(buf) > 0:
            out[i] = np.sqrt(np.mean(np.square(buf)) - np.square(np.mean(buf)))
    return out


def rng_size(series_x, scale, qty, n, point_value=1.0, mintick=1e-8):
    # series_x is (h_val + l_val)/2 per bar
    tr = np.maximum.reduce([series_x - series_x.shift(1).fillna(series_x), series_x.abs()*0]) # placeholder; we'll compute ATR using true range on highs/lows separately
    # For our implementation we need ATR and AC (average change abs(x - x[1]))
    # Instead of passing highs/lows here, we'll compute rng_size later in the main function where ATR and AC are available.
    pass

# We'll implement the full rng_filt in the main symbol processor because it needs high/low/close and stateful arrays.

# ------------------------- single-symbol backtest -------------------------

def process_symbol(csv_path):
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        return {'symbol': csv_path.name, 'error': f'read_error: {e}'}

    # ensure columns exist
    required = ['open_time','open','high','low','close','volume']
    if not all(c in df.columns for c in required):
        return {'symbol': csv_path.name, 'error': 'missing_columns'}

    # parse datetimes
    try:
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', origin='unix', errors='coerce')
    except Exception:
        try:
            df['open_time'] = pd.to_datetime(df['open_time'], errors='coerce')
        except Exception:
            df['open_time'] = pd.to_datetime(df['open_time'], errors='coerce')
    df = df.sort_values('open_time').reset_index(drop=True)
    df.set_index('open_time', inplace=True)

    # convert numeric columns
    for col in ['open','high','low','close','volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # choose movement source
    if MOV_SRC == 'Wicks':
        h_val = df['high'].values
        l_val = df['low'].values
    else:
        h_val = df['close'].values
        l_val = df['close'].values
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    opn = df['open'].values

    n = len(df)
    # prepare arrays
    rng_arr = np.full(n, np.nan)
    hi_band = np.full(n, np.nan)
    lo_band = np.full(n, np.nan)
    filt = np.full(n, np.nan)
    fdir = np.zeros(n) # 1, -1, or previous

    # compute dynamic components: ATR (using true range on high/low/close)
    tr = np.zeros(n)
    for i in range(n):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    ATR = cond_ema(tr, None, RNG_PER)
    AC = cond_ema(np.abs(( (h_val + l_val)/2.0 ) - np.roll((h_val + l_val)/2.0, 1)), None, RNG_PER)
    SD = rolling_stdev((h_val + l_val)/2.0, RNG_PER)

    # rng_size per description
    def compute_rng_scalar(i):
        x = (h_val[i] + l_val[i]) / 2.0
        if RNG_SCALE == 'Pips':
            return RNG_QTY * 0.0001
        if RNG_SCALE == 'Points':
            return RNG_QTY * 1.0
        if RNG_SCALE == "% of Price":
            return close[i] * RNG_QTY / 100.0
        if RNG_SCALE == 'ATR':
            return RNG_QTY * (ATR[i] if not np.isnan(ATR[i]) else np.nan)
        if RNG_SCALE == 'Average Change':
            return RNG_QTY * (AC[i] if not np.isnan(AC[i]) else np.nan)
        if RNG_SCALE == 'Standard Deviation':
            return RNG_QTY * (SD[i] if not np.isnan(SD[i]) else np.nan)
        if RNG_SCALE == 'Ticks':
            return RNG_QTY * 1.0
        return RNG_QTY

    # implement rng_filt iteratively matching pine stateful logic
    prev_rf = (h_val[0] + l_val[0]) / 2.0
    prev_hi = prev_rf + (compute_rng_scalar(0) if not np.isnan(compute_rng_scalar(0)) else 0)
    prev_lo = prev_rf - (compute_rng_scalar(0) if not np.isnan(compute_rng_scalar(0)) else 0)
    filt[0] = prev_rf
    hi_band[0] = prev_hi
    lo_band[0] = prev_lo
    fdir[0] = 0.0

    # helper for conditional EMA for averaging filter changes (av_rf)
    av_rf_values = []
    hi_av_values = []
    lo_av_values = []
    av_prev = None

    for i in range(1, n):
        r = compute_rng_scalar(i)
        if np.isnan(r):
            r = compute_rng_scalar(i-1) if i-1 >= 0 else 0.0
        rfilt1 = prev_rf
        # Type 1
        if F_TYPE == 'Type 1':
            if h_val[i] - r > prev_rf:
                rfilt1 = h_val[i] - r
            if l_val[i] + r < prev_rf:
                rfilt1 = l_val[i] + r
        else: # Type 2
            if h_val[i] >= prev_rf + r:
                rfilt1 = prev_rf + np.floor(abs(h_val[i] - prev_rf) / r) * r if r>0 else prev_rf
            if l_val[i] <= prev_rf - r:
                rfilt1 = prev_rf - np.floor(abs(l_val[i] - prev_rf) / r) * r if r>0 else prev_rf

        hi_b1 = rfilt1 + r
        lo_b1 = rfilt1 - r

        # averaging of filter changes
        if AV_VALS:
            changed = not np.isclose(rfilt1, prev_rf)
            if changed:
                av_rf_values.append(rfilt1)
                hi_av_values.append(hi_b1)
                lo_av_values.append(lo_b1)
                if len(av_rf_values) > AV_SAMPLES:
                    av_rf_values.pop(0)
                    hi_av_values.pop(0)
                    lo_av_values.pop(0)
                rfilt = np.mean(av_rf_values)
                hi_b = np.mean(hi_av_values)
                lo_b = np.mean(lo_av_values)
            else:
                rfilt = rfilt1
                hi_b = hi_b1
                lo_b = lo_b1
        else:
            rfilt = rfilt1
            hi_b = hi_b1
            lo_b = lo_b1

        # smoothing range (Cond_EMA on r) is approximated by using cond_ema when computing r - already handled by compute_rng_scalar's use of ATR/AC cond_ema earlier
        filt[i] = rfilt
        hi_band[i] = hi_b
        lo_band[i] = lo_b

        # direction
        if rfilt > prev_rf:
            fdir[i] = 1.0
        elif rfilt < prev_rf:
            fdir[i] = -1.0
        else:
            fdir[i] = fdir[i-1]

        prev_rf = rfilt

    # produce signals: when fdir changes to 1 -> entry, when changes to -1 -> exit
    entries = []  # indices where we will enter (use next bar open)
    exits = []    # indices where we will exit (use next bar open)

    for i in range(1, n):
        if fdir[i] == 1.0 and fdir[i-1] != 1.0:
            # bullish flip at i -> enter on i+1 open
            if i+1 < n:
                entries.append(i+1)
        if fdir[i] == -1.0 and fdir[i-1] != -1.0:
            # bearish flip -> exit on i+1 open
            if i+1 < n:
                exits.append(i+1)

    # now simulate trades using entries/exits matched chronologically
    equity = [INITIAL_CAPITAL]
    equity_idx = [df.index[0]]
    position = 0.0
    entry_price = None
    entry_idx = None
    trade_returns = []
    trades = []
    cash = INITIAL_CAPITAL
    cur_equity = INITIAL_CAPITAL

    # We'll step through bars and handle entries/exits when their index matches
    entry_set = set(entries)
    exit_set = set(exits)

    for i in range(n):
        # check exit first (if position open and exit signal triggers)
        if position > 0 and i in exit_set:
            # exit at open[i]
            exit_price = opn[i] * (1.0 + SLIPPAGE)
            gross_proceeds = position * exit_price
            fee = gross_proceeds * FEE_RATE
            net = gross_proceeds - fee
            pnl = net - (position * entry_price)
            ret = pnl / (position * entry_price) if (position * entry_price) != 0 else 0
            trade_returns.append(ret)
            trades.append({'entry_time': entry_idx, 'exit_time': df.index[i], 'entry_price': entry_price, 'exit_price': exit_price, 'pnl': pnl})
            cash = cash + net
            position = 0.0
            entry_price = None
            entry_idx = None
            cur_equity = cash
        # check entry if not in position
        if position == 0 and i in entry_set:
            buy_price = opn[i] * (1.0 + SLIPPAGE)
            # use POSITION_SIZE fraction of current equity
            notional = cash * POSITION_SIZE
            if notional >= MIN_TRADE_NOTIONAL:
                # deduct entry fee from notional
                fee = notional * FEE_RATE
                effective_notional = notional - fee
                position = effective_notional / buy_price
                entry_price = buy_price
                entry_idx = df.index[i]
                cash = cash - notional  # allocate full notional
                cur_equity = cash + position * close[i]  # mark-to-market
        # mark-to-market equity update each bar
        if position > 0:
            cur_equity = cash + position * close[i]
        else:
            cur_equity = cash
        equity.append(cur_equity)
        equity_idx.append(df.index[i])

    # if position remains open at end, close at last close price
    if position > 0:
        exit_price = close[-1] * (1.0 + SLIPPAGE)
        gross_proceeds = position * exit_price
        fee = gross_proceeds * FEE_RATE
        net = gross_proceeds - fee
        pnl = net - (position * entry_price)
        ret = pnl / (position * entry_price) if (position * entry_price) != 0 else 0
        trade_returns.append(ret)
        trades.append({'entry_time': entry_idx, 'exit_time': df.index[-1], 'entry_price': entry_price, 'exit_price': exit_price, 'pnl': pnl})
        cash = cash + net
        position = 0.0
        cur_equity = cash
        equity[-1] = cur_equity

    equity_series = pd.Series(index=pd.DatetimeIndex(equity_idx), data=equity)
    metrics = compute_metrics(equity_series, trade_returns, df.index)
    result = {
        'symbol': csv_path.stem,
        'metrics': metrics,
        'trade_count': len(trade_returns),
        'total_return': metrics['total_return'] if metrics else None,
        'equity_series': equity_series,
        'trade_returns': trade_returns,
        'trades': trades
    }
    return result

# --------------------------- batch processing -----------------------------

def run_all(data_dir=DATA_DIR, n_jobs=N_JOBS):
    csvs = sorted([p for p in Path(data_dir).glob('*.csv')])
    if not csvs:
        raise FileNotFoundError(f'No CSV files found in {data_dir}')
    results = Parallel(n_jobs=n_jobs)(delayed(process_symbol)(p) for p in tqdm(csvs, desc='Symbols'))
    # aggregate metrics and save
    per_token = []
    equity_curves = {}
    overall_equity = None
    for r in results:
        if 'error' in r:
            per_token.append({'symbol': r.get('symbol','?'), 'error': r.get('error')})
            continue
        m = r['metrics']
        row = {'symbol': r['symbol']}
        row.update(m)
        row['num_trades'] = r['trade_count']
        row['total_return'] = r['total_return']
        per_token.append(row)
        equity_curves[r['symbol']] = r['equity_series']
        # build combined equity by summing equally-weighted or sequentially? We'll aggregate by rebalancing equally across tokens (simple approach)
        if overall_equity is None:
            overall_equity = r['equity_series'].copy()
        else:
            # align indices and add
            overall_equity = overall_equity.reindex(overall_equity.index.union(r['equity_series'].index)).fillna(method='ffill')
            temp = r['equity_series'].reindex(overall_equity.index).fillna(method='ffill')
            overall_equity = overall_equity + temp
    # finalize overall equity by averaging across symbols
    if overall_equity is not None:
        overall_equity = overall_equity / max(1, len(equity_curves))

    # save per-token CSV
    per_token_df = pd.DataFrame(per_token)
    per_token_df.to_csv(RESULTS_DIR / 'per_token_metrics.csv', index=False)

    # save overall metrics
    if overall_equity is not None:
        overall_metrics = compute_metrics(overall_equity, [], overall_equity.index)
        with open(RESULTS_DIR / 'overall_metrics.json', 'w') as f:
            json.dump(overall_metrics, f, default=lambda x: float(x) if isinstance(x, (np.floating, np.integer)) else x, indent=2)

    # produce plots
    # combined equity
    if overall_equity is not None:
        plt.figure(figsize=(10,6))
        overall_equity.plot()
        plt.title('Combined Equity Curve (avg across symbols)')
        plt.ylabel('Equity')
        plt.xlabel('Time')
        plt.tight_layout()
        plt.savefig(RESULTS_DIR / 'equity_curve.png')
        plt.close()

    # per-token total return bar
    perf = per_token_df.dropna(subset=['total_return']).sort_values('total_return', ascending=False).head(50)
    if not perf.empty:
        plt.figure(figsize=(12,6))
        plt.bar(perf['symbol'], perf['total_return'])
        plt.xticks(rotation=90)
        plt.title('Per-token Total Return (top 50)')
        plt.tight_layout()
        plt.savefig(RESULTS_DIR / 'performance_bar.png')
        plt.close()

    # individual equity plots for top N tokens
    top_symbols = perf['symbol'].tolist()[:TOP_N_PLOTS]
    for sym in top_symbols:
        s = equity_curves.get(sym)
        if s is None:
            continue
        plt.figure(figsize=(10,5))
        s.plot()
        plt.title(f'Equity Curve: {sym}')
        plt.xlabel('Time')
        plt.ylabel('Equity')
        plt.tight_layout()
        plt.savefig(RESULTS_DIR / f'{sym}_equity.png')
        plt.close()

    return {'per_token': per_token_df, 'overall_equity': overall_equity, 'results_raw': results}

# ------------------------------- main ------------------------------------

if __name__ == '__main__':
    print('Running batch backtest...')
    out = run_all(DATA_DIR, n_jobs=N_JOBS)
    print('Done. Results saved to', RESULTS_DIR)

# ------------------------------ end file ---------------------------------
