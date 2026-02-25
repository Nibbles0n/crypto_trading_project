"""
Range Filter — Multi-token Optimizer

This script:
 - Loads ALL CSV files in a folder (default: ./data)
 - Implements a Pine-accurate stateful Range Filter (Cond_EMA, Cond_SMA, Pine-style Stdev)
 - Backtests the strategy (LONG only, enter next-bar open, fees configurable). Each token is simulated as a separate account.
 - Optimizes indicator parameters across ALL tokens simultaneously (objective: maximize combined Sharpe or total return).
 - Saves per-token metrics, trade lists, equity plots, and the best-found parameters.

Requirements:
 - Python 3.8+
 - pandas, numpy, matplotlib, optuna, joblib, tqdm
   Install: pip install pandas numpy matplotlib optuna joblib tqdm

Usage:
    python range_filter_optimizer.py

Configuration (top of file):
 - DATA_DIR: folder with CSVs (one CSV per token)
 - N_TRIALS: number of optuna trials
 - N_JOBS: parallel jobs for evaluating a single trial across tokens (joblib) and optuna n_jobs
 - METRIC: 'sharpe' or 'total_return' (combined metric to maximize)
 - FIXED_POSITION_SIZE: 1.0 (100% per trade as requested)

Notes & Assumptions:
 - The optimizer searches only indicator parameters (RNG_QTY, RNG_PER, SMOOTH_PER, F_TYPE, MOV_SRC, AV_VALS).
 - Position size is fixed to 100% per trade (per your recent instruction). Overlapping trades across tokens are allowed (simulated as separate accounts), so combined equity is the sum of per-token equities.
 - Objective: maximize combined annualized Sharpe (if available). If Sharpe is NaN (insufficient data), uses combined total return.

Outputs (saved under ./results_opt):
 - best_params.json
 - per_token_metrics_best.csv
 - combined_equity_best.png
 - per-token equity PNGs and trade CSVs for the best parameter set

"""

import os
import json
from pathlib import Path
from datetime import datetime
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from joblib import Parallel, delayed
from tqdm import tqdm
import optuna

# ------------------------------- CONFIG ----------------------------------
DATA_DIR = Path("data")
RESULTS_DIR = Path("results_opt")
RESULTS_DIR.mkdir(exist_ok=True)

# Backtest defaults
INITIAL_CAPITAL = 10000.0
FEE_RATE = 0.005    # 0.5% per side
SLIPPAGE = 0.0
FIXED_POSITION_SIZE = 1.0   # 100% per trade (as requested)
MIN_NOTIONAL = 1.0
RISK_FREE_RATE = 0.0

# Optimization settings
N_TRIALS = 60
N_JOBS = 1   # joblib parallel jobs for per-trial evaluation across tokens; set to -1 to use all cores
OPTUNA_N_JOBS = 1  # optuna parallel studies; set >1 for multiprocessing optimization
METRIC = 'sharpe'  # 'sharpe' or 'total_return'

# Parameter search bounds (optuna will sample within)
RNG_QTY_BOUNDS = (0.5, 8.0)
RNG_PER_BOUNDS = (3, 30)   # integer
SMOOTH_PER_BOUNDS = (3, 60) # integer

# -------------------------------------------------------------------------

# ------------------------ Pine-accurate helpers ---------------------------

def cond_ema_stateful_arr(x, cond_mask, n):
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


def cond_sma_stateful_arr(x, cond_mask, n):
    out = np.full(len(x), np.nan)
    buf = []
    for i in range(len(x)):
        if cond_mask is None or cond_mask[i]:
            buf.append(x[i])
            if len(buf) > n:
                buf.pop(0)
        out[i] = np.mean(buf) if buf else np.nan
    return out


def stdev_pine(x, n):
    x2 = np.square(x)
    s1 = cond_sma_stateful_arr(x2, [True]*len(x), n)
    s2 = cond_sma_stateful_arr(x, [True]*len(x), n)
    out = np.sqrt(np.maximum(0.0, s1 - np.square(s2)))
    return out

# -------------------------- backtest engine -------------------------------

def backtest_symbol(df, params):
    """Stateful range filter backtest for a single symbol. Returns equity series and trades+metrics."""
    # df must contain: open_time (index or column), open, high, low, close, volume
    df = df.copy()
    if 'open_time' in df.columns:
        try:
            df['open_time'] = pd.to_datetime(df['open_time'], utc=True, errors='coerce')
            df.set_index('open_time', inplace=True)
        except Exception:
            df.index = pd.to_datetime(df.index, errors='coerce')
    else:
        df.index = pd.to_datetime(df.index, errors='coerce')
    df = df.sort_index()
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    n = len(df)
    if n < 10:
        return None

    # params
    RNG_QTY = params['rng_qty']
    RNG_SCALE = params['rng_scale']
    RNG_PER = int(params['rng_per'])
    F_TYPE = params['f_type']
    MOV_SRC = params['mov_src']
    AV_VALS = params['av_vals']
    AV_SAMPLES = int(params.get('av_samples', 2))

    # movement source
    if MOV_SRC == 'Wicks':
        h_val = df['high'].values.copy()
        l_val = df['low'].values.copy()
    else:
        h_val = df['close'].values.copy()
        l_val = df['close'].values.copy()
    high = df['high'].values.copy(); low = df['low'].values.copy(); close = df['close'].values.copy(); opn = df['open'].values.copy()

    # TR and ATR (Cond_EMA with cond True)
    tr = np.zeros(n)
    for i in range(n):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    ATR = cond_ema_stateful_arr(tr, [True]*n, RNG_PER)

    mid = (h_val + l_val) / 2.0
    ac_raw = np.abs(mid - np.roll(mid, 1)); ac_raw[0] = 0.0
    AC = cond_ema_stateful_arr(ac_raw, [True]*n, RNG_PER)
    SD = stdev_pine(mid, RNG_PER)

    def compute_rng(i):
        if RNG_SCALE == 'Pips':
            return RNG_QTY * 0.0001
        if RNG_SCALE == 'Points':
            return RNG_QTY * 1.0
        if RNG_SCALE == "% of Price":
            return close[i] * RNG_QTY / 100.0
        if RNG_SCALE == 'ATR':
            return RNG_QTY * (ATR[i] if not np.isnan(ATR[i]) else 0.0)
        if RNG_SCALE == 'Average Change':
            return RNG_QTY * (AC[i] if not np.isnan(AC[i]) else 0.0)
        if RNG_SCALE == 'Standard Deviation':
            return RNG_QTY * (SD[i] if not np.isnan(SD[i]) else 0.0)
        if RNG_SCALE == 'Ticks':
            return RNG_QTY * 1.0
        return RNG_QTY

    # stateful arrays
    rf = np.full(n, np.nan); hi_band = np.full(n, np.nan); lo_band = np.full(n, np.nan); fdir = np.zeros(n)
    rf[0] = (h_val[0] + l_val[0]) / 2.0
    r0 = compute_rng(0); r0 = 0.0 if np.isnan(r0) else r0
    hi_band[0] = rf[0] + r0; lo_band[0] = rf[0] - r0; fdir[0] = 0.0
    av_rf_buf = []; hi_buf = []; lo_buf = []

    for i in range(1, n):
        r = compute_rng(i);
        if np.isnan(r): r = r0
        rfilt1 = rf[i-1]
        if F_TYPE == 'Type 1':
            if high[i] - r > rf[i-1]:
                rfilt1 = high[i] - r
            if low[i] + r < rf[i-1]:
                rfilt1 = low[i] + r
        else:
            if r>0 and high[i] >= rf[i-1] + r:
                rfilt1 = rf[i-1] + math.floor(abs(high[i] - rf[i-1]) / r) * r
            if r>0 and low[i] <= rf[i-1] - r:
                rfilt1 = rf[i-1] - math.floor(abs(low[i] - rf[i-1]) / r) * r
        hi1 = rfilt1 + r; lo1 = rfilt1 - r
        if AV_VALS:
            changed = not np.isclose(rfilt1, rf[i-1])
            if changed:
                av_rf_buf.append(rfilt1); hi_buf.append(hi1); lo_buf.append(lo1)
                if len(av_rf_buf) > AV_SAMPLES:
                    av_rf_buf.pop(0); hi_buf.pop(0); lo_buf.pop(0)
                rf[i] = float(np.mean(av_rf_buf)); hi_band[i] = float(np.mean(hi_buf)); lo_band[i] = float(np.mean(lo_buf))
            else:
                rf[i] = rfilt1; hi_band[i] = hi1; lo_band[i] = lo1
        else:
            rf[i] = rfilt1; hi_band[i] = hi1; lo_band[i] = lo1
        if rf[i] > rf[i-1]: fdir[i] = 1.0
        elif rf[i] < rf[i-1]: fdir[i] = -1.0
        else: fdir[i] = fdir[i-1]
        r0 = r

    # entries/exits
    entries = []; exits = []
    for i in range(1, n):
        if fdir[i] == 1.0 and fdir[i-1] != 1.0:
            if i+1 < n: entries.append(i+1)
        if fdir[i] == -1.0 and fdir[i-1] != -1.0:
            if i+1 < n: exits.append(i+1)
    entry_set = set(entries); exit_set = set(exits)

    # simulate
    cash = INITIAL_CAPITAL; position = 0.0; entry_price = None; entry_idx = None
    equity_rec = []; equity_idx = []
    trades = []; trade_returns = []

    for i in range(n):
        if position > 0 and i in exit_set:
            exit_price = opn[i] * (1.0 + SLIPPAGE)
            gross = position * exit_price
            fee = gross * FEE_RATE
            net = gross - fee
            pnl = net - (position * entry_price)
            ret = pnl / (position * entry_price) if (position * entry_price)!=0 else 0.0
            trades.append({'entry_time': entry_idx, 'exit_time': df.index[i], 'entry_price': entry_price, 'exit_price': exit_price, 'pnl': pnl})
            trade_returns.append(ret)
            cash = cash + net
            position = 0.0; entry_price = None; entry_idx = None
        if position == 0 and i in entry_set:
            buy_price = opn[i] * (1.0 + SLIPPAGE)
            notional = cash * FIXED_POSITION_SIZE
            if notional >= MIN_NOTIONAL:
                fee = notional * FEE_RATE
                effective = notional - fee
                position = effective / buy_price
                entry_price = buy_price; entry_idx = df.index[i]
                cash = cash - notional
        if position > 0:
            cur_equity = cash + position * close[i]
        else:
            cur_equity = cash
        equity_rec.append(cur_equity); equity_idx.append(df.index[i])

    if position > 0:
        exit_price = close[-1] * (1.0 + SLIPPAGE)
        gross = position * exit_price
        fee = gross * FEE_RATE
        net = gross - fee
        pnl = net - (position * entry_price)
        ret = pnl / (position * entry_price) if (position * entry_price)!=0 else 0.0
        trades.append({'entry_time': entry_idx, 'exit_time': df.index[-1], 'entry_price': entry_price, 'exit_price': exit_price, 'pnl': pnl})
        trade_returns.append(ret)
        cash = cash + net
        equity_rec[-1] = cash
        position = 0.0

    equity_series = pd.Series(index=pd.DatetimeIndex(equity_idx), data=equity_rec)

    # compute metrics
    def compute_metrics(equity_series, trade_returns):
        total_return = (equity_series.iloc[-1] / equity_series.iloc[0]) - 1.0
        days = (equity_series.index[-1] - equity_series.index[0]).days
        years = days / 365.25 if days>0 else np.nan
        cagr = (equity_series.iloc[-1] / equity_series.iloc[0]) ** (1/years) - 1 if years and years>0 else np.nan
        daily = equity_series.pct_change().dropna()
        ann_vol = daily.std() * np.sqrt(252) if len(daily)>1 else np.nan
        sharpe = ((daily.mean()*252) - RISK_FREE_RATE) / ann_vol if ann_vol and ann_vol>0 else np.nan
        cummax = equity_series.cummax(); dd = equity_series / cummax - 1.0; max_dd = dd.min()
        wins = [r for r in trade_returns if r>0]; losses=[r for r in trade_returns if r<=0]
        win_rate = len(wins)/len(trade_returns) if trade_returns else np.nan
        avg_win = np.mean(wins) if wins else np.nan; avg_loss = np.mean(losses) if losses else np.nan
        gross_win = sum(wins); gross_loss = -sum([r for r in losses if r<0])
        profit_factor = (gross_win / gross_loss) if gross_loss and gross_loss>0 else np.nan
        expectancy = (win_rate * (avg_win if not np.isnan(avg_win) else 0) + (1-win_rate) * (avg_loss if not np.isnan(avg_loss) else 0)) if trade_returns else np.nan
        return {'total_return': float(total_return), 'cagr': float(cagr) if not np.isnan(cagr) else None, 'annual_vol': float(ann_vol) if not np.isnan(ann_vol) else None, 'sharpe': float(sharpe) if not np.isnan(sharpe) else None, 'max_drawdown': float(max_dd) if not np.isnan(max_dd) else None, 'num_trades': len(trade_returns), 'win_rate': float(win_rate) if not np.isnan(win_rate) else None, 'avg_win': float(avg_win) if not np.isnan(avg_win) else None, 'avg_loss': float(avg_loss) if not np.isnan(avg_loss) else None, 'profit_factor': float(profit_factor) if not np.isnan(profit_factor) else None, 'expectancy': float(expectancy) if not np.isnan(expectancy) else None}
    metrics = compute_metrics(equity_series, trade_returns)
    return {'equity': equity_series, 'metrics': metrics, 'trades': trades}

# -------------------------- trial evaluation ------------------------------

def evaluate_params(params, csv_paths, n_jobs=1):
    # Evaluate params across all tokens in parallel
    results = Parallel(n_jobs=n_jobs)(delayed(_eval_one)(p, params) for p in csv_paths)
    # filter out errors
    valid = [r for r in results if r is not None]
    if not valid:
        return None
    # combine equities by SUM (separate accounts) -> align indices and sum
    combined = None
    per_token_rows = []
    for sym, r in zip([p.stem for p in csv_paths], results):
        if r is None:
            per_token_rows.append({'symbol': sym, 'error': 'failed'})
            continue
        m = r['metrics']
        per_token_rows.append({'symbol': sym, **m})
        eq = r['equity']
        if combined is None:
            combined = eq.copy()
        else:
            combined = combined.reindex(combined.index.union(eq.index)).fillna(method='ffill')
            tmp = eq.reindex(combined.index).fillna(method='ffill')
            combined = combined + tmp
    # compute combined metric
    if combined is None:
        return None
    daily = combined.pct_change().dropna()
    ann_vol = daily.std() * math.sqrt(252) if len(daily)>1 else np.nan
    ann_ret = (combined.iloc[-1] / combined.iloc[0]) ** (252.0 / len(daily)) - 1 if len(daily)>0 else np.nan
    combined_sharpe = ((daily.mean()*252) - RISK_FREE_RATE) / ann_vol if ann_vol and ann_vol>0 else np.nan
    combined_total_return = (combined.iloc[-1] / combined.iloc[0]) - 1.0
    # objective depends on METRIC
    objective_metric = combined_sharpe if (METRIC == 'sharpe' and not np.isnan(combined_sharpe)) else combined_total_return
    return {'combined_equity': combined, 'per_token': pd.DataFrame(per_token_rows), 'combined_sharpe': combined_sharpe, 'combined_total_return': combined_total_return, 'objective': objective_metric}


def _eval_one(csv_path, params):
    try:
        df = pd.read_csv(csv_path)
        out = backtest_symbol(df, params)
        if out is None:
            return None
        return out
    except Exception as e:
        print(f"Error processing {csv_path}: {e}")
        return None

# ---------------------------- Optuna objective ---------------------------

def objective(trial, csv_paths):
    # sample parameters
    rng_qty = trial.suggest_float('rng_qty', RNG_QTY_BOUNDS[0], RNG_QTY_BOUNDS[1])
    rng_per = trial.suggest_int('rng_per', RNG_PER_BOUNDS[0], RNG_PER_BOUNDS[1])
    smooth_per = trial.suggest_int('smooth_per', SMOOTH_PER_BOUNDS[0], SMOOTH_PER_BOUNDS[1])
    f_type = trial.suggest_categorical('f_type', ['Type 1', 'Type 2'])
    mov_src = trial.suggest_categorical('mov_src', ['Close', 'Wicks'])
    av_vals = trial.suggest_categorical('av_vals', [True, False])
    rng_scale = trial.suggest_categorical('rng_scale', ['Average Change', 'ATR', 'Standard Deviation', '% of Price', 'Points'])

    params = {'rng_qty': rng_qty, 'rng_per': rng_per, 'smooth_per': smooth_per, 'f_type': f_type, 'mov_src': mov_src, 'av_vals': av_vals, 'rng_scale': rng_scale, 'av_samples': 2}

    res = evaluate_params(params, csv_paths, n_jobs=N_JOBS)
    if res is None:
        # return very bad objective
        return -1e6
    # objective to maximize: return negative for optuna (which minimizes by default) OR use TP's direction
    # We will return negative of objective_metric so optuna minimizes
    obj = res['objective']
    if np.isnan(obj):
        return -1e6
    # optuna minimizes so return negative of obj to maximize original
    return -obj

# ------------------------------- main -----------------------------------

def main():
    csv_paths = sorted([p for p in DATA_DIR.glob('*.csv')])
    if not csv_paths:
        raise FileNotFoundError(f'No CSVs found in {DATA_DIR}')
    print(f'Found {len(csv_paths)} CSV files to optimize across.')

    # run optuna
    study = optuna.create_study(direction='minimize')
    # pass csv_paths via lambda
    func = lambda trial: objective(trial, csv_paths)
    study.optimize(func, n_trials=N_TRIALS, n_jobs=OPTUNA_N_JOBS)

    best = study.best_params
    print('Best params:', best)
    # evaluate best params and save results
    best_params = best.copy()
    # ensure types
    best_params['rng_per'] = int(best_params['rng_per'])
    best_params['smooth_per'] = int(best_params['smooth_per']) if 'smooth_per' in best_params else 27
    # evaluate one more time to get artifacts
    res = evaluate_params(best_params, csv_paths, n_jobs=N_JOBS)
    if res is None:
        print('Failed to evaluate best params')
        return
    # save per-token metrics
    res['per_token'].to_csv(RESULTS_DIR / 'per_token_metrics_best.csv', index=False)
    # save best params
    with open(RESULTS_DIR / 'best_params.json', 'w') as f:
        json.dump(best_params, f, indent=2)
    # save per-token trades and equity plots
    for i, p in enumerate(csv_paths):
        sym = p.stem
        out = _eval_one(p, best_params)
        if out is None:
            continue
        # trades
        try:
            pd.DataFrame(out['trades']).to_csv(RESULTS_DIR / f'{sym}_trades_best.csv', index=False)
        except Exception:
            pass
        # equity plot
        fig = plt.figure(figsize=(10,4)); ax = fig.subplots()
        out['equity'].plot(ax=ax); ax.set_title(f'Equity Curve (best) - {sym}'); ax.set_ylabel('Equity')
        fig.savefig(RESULTS_DIR / f'{sym}_equity_best.png', bbox_inches='tight'); plt.close(fig)
    # combined equity
    combined = res['combined_equity']
    fig = plt.figure(figsize=(12,5)); ax = fig.subplots()
    combined.plot(ax=ax); ax.set_title('Combined Equity (best params)'); ax.set_ylabel('Equity')
    fig.savefig(RESULTS_DIR / 'combined_equity_best.png', bbox_inches='tight'); plt.close(fig)
    # overall metrics
    overall = {'combined_total_return': res['combined_total_return'], 'combined_sharpe': res['combined_sharpe']}
    with open(RESULTS_DIR / 'overall_metrics_best.json', 'w') as f:
        json.dump(overall, f, indent=2)
    print('Saved optimization outputs to', RESULTS_DIR)

if __name__ == '__main__':
    main()
