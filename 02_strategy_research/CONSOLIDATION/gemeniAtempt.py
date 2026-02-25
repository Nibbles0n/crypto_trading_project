"""
BACKTESTER with Walk-Forward Optimization

THE PLAN
    load the data from the .csv file that I am going to process
    calculate the ATR for the whole dataset
    prune the data file to just hold the high, low, close, and atr
    calculate the fractal highs and lows, add it to the data frame as colums
    run walk-forward optimization loop
        split data into in-sample (IS) and out-of-sample (OOS) windows
        optimize parameters on IS window
        test optimized parameters on OOS window
        store OOS results
    create trading simulation loop
        start bar more than lookback period so that we are not missing infromation
        use peaks and troughs to calculate the 2 trend lines
        calculate the breakout threshold
        check if the latest close is above the threshold value
        if it is and we are not in a position, enter the trade
            take profit is current value of high line x atr x tp multiplier
            stop loss is current value of low line x atr x sl multiplier
            save values and wait until high or low hits either of those values, and check stop loss first to be pessemistic
            apply fees to the entry and exit
            save position to trading history every time a trade is executed

    save metrics as a .csv file labled by them token that was tested and the date
"""
import pandas as pd
import pandas_ta as ta
import numpy as np
import warnings
from datetime import datetime, timedelta
from itertools import product

# CONFIGURATION
chunk_size = 10000
DATA_FILE = 'other data/ARBUSDT.csv'

atr_period = 14
fractal_period = 3
lookback_bars = 100
third_point_multiplier = 1.5
tp_multiplier = 2.0  # 2x
sl_multiplier = 1.0  # 1x
entry_multiplier = 1.0 # New multiplier for entry condition
line_threshold = 0.02  # 2% expressed as a decimal
max_skips = 3
min_trend_length = 5

initial_capital = 10000.0
position_size_pct = 0.95  # 95% of capital
maker_fee = 0.0025  # 0.25%
taker_fee = 0.004  # 0.4%

# Walk-Forward Optimization Parameters
param_space = {
    'line_threshold': [0.01, 0.02, 0.03],
    'entry_multiplier': [0.5, 1.0, 1.5],
    'tp_multiplier': [1.5, 2.0, 2.5],
    'sl_multiplier': [0.8, 1.0, 1.2]
}
walk_forward_window = 120  # Optimization window in days
walk_forward_step = 30     # Step forward in days

# LOAD DATA
print("Loading data...")
df = pd.DataFrame()
for chunk in pd.read_csv(DATA_FILE, chunksize=chunk_size):
    df = pd.concat([df, chunk])

df.columns = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
              'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
              'taker_buy_quote_asset_volume', 'ignore']

df['open_time'] = pd.to_datetime(df['open_time'])
df.set_index('open_time', inplace=True)
df.drop(['open', 'close_time', 'volume', 'quote_asset_volume', 'number_of_trades',
         'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'], axis=1, inplace=True)

df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=atr_period)

fractal_window = 2 * fractal_period + 1
df['peak'] = np.where(df['high'] == df['high'].rolling(fractal_window, center=True).max(), 1, 0)
df['trough'] = np.where(df['low'] == df['low'].rolling(fractal_window, center=True).min(), -1, 0)
df['fractal'] = df['peak'] + df['trough']

print("Data preprocessing complete.")

def generate_line(df_subset, pivot_series, is_uptrend, threshold, third_point_multiplier, skip_limit, min_length):
    lines = []
    
    if pivot_series.empty:
        return lines
        
    for start_pivot_idx in pivot_series.index:
        
        potential_line_points = []
        start_pos = df_subset.index.get_loc(start_pivot_idx)
        start_price = df_subset.loc[start_pivot_idx, 'high' if is_uptrend else 'low']
        potential_line_points.append((start_pos, start_price))
        skipped_count = 0
        
        for i in range(start_pos + 1, len(df_subset)):
            current_bar_pos = i
            current_bar_index = df_subset.index[current_bar_pos]
            current_price = df_subset.loc[current_bar_index, 'high' if is_uptrend else 'low']
            
            if len(potential_line_points) >= 2:
                x_for_fit = np.array([p[0] for p in potential_line_points])
                y_for_fit = np.array([p[1] for p in potential_line_points])
                

                m, b = np.polyfit(x_for_fit, y_for_fit, deg=1)
                
                predicted_price = m * current_bar_pos + b
            else:
                predicted_price = potential_line_points[0][1] # Use the single point's price
            
            current_threshold = threshold
            if len(potential_line_points) == 2:
                current_threshold *= third_point_multiplier
            
            if predicted_price != 0 and abs(current_price - predicted_price) / predicted_price > current_threshold:
                skipped_count += 1
            else:
                potential_line_points.append((current_bar_pos, current_price))
                skipped_count = 0
            
            if skipped_count > skip_limit:
                if len(potential_line_points) >= min_length:
                    lines.append([(df_subset.index[p[0]], p[1]) for p in potential_line_points])
                break 

        if skipped_count <= skip_limit and len(potential_line_points) >= min_length:
            lines.append([(df_subset.index[p[0]], p[1]) for p in potential_line_points])

    return lines[-1] if lines else []

def predict_line_value(line_points, current_bar_pos, df):
    if not line_points:
        return None
    
    x_for_fit = np.array([df.index.get_loc(p[0]) for p in line_points])
    y_for_fit = np.array([p[1] for p in line_points])
    

    m, b = np.polyfit(x_for_fit, y_for_fit, deg=1)
    
    return m * current_bar_pos + b

def run_single_backtest(df_data, params):
    capital = initial_capital
    in_position = False
    trading_history = []
    
    active_peak_line = []
    active_trough_line = []
    
    for i in range(lookback_bars, len(df_data)):
        current_time = df_data.index[i]
        current_high = df_data.loc[current_time, 'high']
        current_low = df_data.loc[current_time, 'low']
        current_close = df_data.loc[current_time, 'close']
        current_atr = df_data.loc[current_time, 'atr']
        current_bar_pos = df.index.get_loc(current_time)
        
        df_window = df_data.iloc[i - lookback_bars:i]
        
        new_peak_line = generate_line(df_window, df_window[df_window['peak'] == 1], True, params['line_threshold'], third_point_multiplier, max_skips, min_trend_length)
        new_trough_line = generate_line(df_window, df_window[df_window['trough'] == -1], False, params['line_threshold'], third_point_multiplier, max_skips, min_trend_length)
        
        if new_peak_line:
            active_peak_line = new_peak_line
        if new_trough_line:
            active_trough_line = new_trough_line

        if not in_position:
            if active_peak_line and active_trough_line and current_atr:
                predicted_high_line = predict_line_value(active_peak_line, current_bar_pos, df)
                predicted_low_line = predict_line_value(active_trough_line, current_bar_pos, df)

                if predicted_high_line and predicted_low_line:
                    entry_threshold = predicted_high_line + (current_atr * params['entry_multiplier'])
                    
                    if current_close > entry_threshold:
                        distance_between_lines = predicted_high_line - predicted_low_line
                        take_profit_value = current_close + (distance_between_lines * params['tp_multiplier'])
                        stop_loss_value = predicted_low_line

                        potential_profit = take_profit_value - current_close
                        total_fees = current_close * taker_fee + take_profit_value * maker_fee
                        
                        if potential_profit > total_fees:
                            in_position = True
                            position_entry_price = current_close * (1 + taker_fee)
                            position_size = (capital * position_size_pct) / position_entry_price
                            capital -= (position_size * position_entry_price)
                            
                            take_profit = take_profit_value
                            stop_loss = stop_loss_value

                            trading_history.append({
                                'entry_date': current_time,
                                'entry_price': position_entry_price,
                                'type': 'long',
                                'position_size': position_size,
                                'take_profit': take_profit,
                                'stop_loss': stop_loss,
                                'exit_date': None,
                                'exit_price': None,
                                'profit': None
                            })

        else: # In position
            exit_price = 0.0
            if current_low < stop_loss:
                exit_price = stop_loss * (1 - maker_fee)
            elif current_high > take_profit:
                exit_price = take_profit * (1 - maker_fee)
            
            if exit_price != 0.0:
                capital += (position_size * exit_price)
                trading_history[-1]['exit_date'] = current_time
                trading_history[-1]['exit_price'] = exit_price
                trading_history[-1]['profit'] = (exit_price - position_entry_price) * position_size
                in_position = False
    
    df_history = pd.DataFrame(trading_history)
    
    # Calculate Sharpe Ratio for optimization
    if not df_history.empty:
        df_history['profit_pct'] = df_history['profit'] / (df_history['position_size'] * df_history['entry_price'])
        sharpe_ratio = df_history['profit_pct'].mean() / df_history['profit_pct'].std() if df_history['profit_pct'].std() != 0 else 0
    else:
        sharpe_ratio = 0
    
    return sharpe_ratio, df_history

# --- WALK-FORWARD OPTIMIZATION ---
print("Starting Walk-Forward Optimization...")
all_oos_results = pd.DataFrame()
all_params = list(product(*param_space.values()))
start_date = df.index.min()
end_date = df.index.max()

current_walk_start = start_date + timedelta(days=lookback_bars)
while current_walk_start < end_date:
    is_end = current_walk_start + timedelta(days=walk_forward_window)
    oos_end = is_end + timedelta(days=walk_forward_step)
    
    if oos_end > end_date:
        oos_end = end_date
    
    is_data = df.loc[current_walk_start:is_end]
    oos_data = df.loc[is_end:oos_end]
    
    if len(is_data) < lookback_bars or len(oos_data) == 0:
        current_walk_start += timedelta(days=walk_forward_step)
        continue
    
    best_sharpe = -np.inf
    best_params = {}
    
    print(f"Optimizing for period: {current_walk_start.date()} to {is_end.date()}")
    for params_combo in all_params:
        params = dict(zip(param_space.keys(), params_combo))
        sharpe, _ = run_single_backtest(is_data, params)
        
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = params
    
    print(f"Best parameters for IS period: {best_params} with Sharpe: {best_sharpe}")
    
    # Test on OOS period with best parameters
    if best_params:
        print(f"Testing on OOS period: {is_end.date()} to {oos_end.date()}")
        _, oos_history = run_single_backtest(oos_data, best_params)
        
        if not oos_history.empty:
            all_oos_results = pd.concat([all_oos_results, oos_history])
    
    current_walk_start += timedelta(days=walk_forward_step)

print("\nWalk-Forward Optimization complete.")

# --- Save metrics to CSV ---
if not all_oos_results.empty:
    token_name = DATA_FILE.split('.')[0]
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_filename = f"{token_name}_{date_str}_walk_forward_metrics.csv"
    all_oos_results.to_csv(output_filename, index=False)
    print(f"Walk-forward history saved to {output_filename}")
else:
    print("No profitable trades found in any OOS period.")
