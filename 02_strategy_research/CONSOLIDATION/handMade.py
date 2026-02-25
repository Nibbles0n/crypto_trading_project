"""
BACKTESTER v3

THE PLAN
    load the data from the .csv file that I am going to process
    calculate the ATR for the whole dataset
    prune the data file to just hold the high, low, close, and atr
    calculate the fractal highs and lows, add it to the data frame as colums
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
from numba.np.arrayobj import np_append
import pandas as pd
import pandas_ta as ta
import numpy as np
import matplotlib.pyplot as plt
import os

# CONFIGURATION

chunk_size = 10000
DATA_FILE = 'data.csv'

atr_period = 14
fractal_period = 3
lookback_bars = 100
third_point_multiplier = 1.5
tp_multiplier = 2.0 #2x
sl_multiplier = 1.0 #1x
line_threshold = 2.0 #2%
max_skips = 3
min_trend_length = 5

initial_capital = 10000.0
position_size_pct = 0.95 #95% of capital
maker_fee = 0.0025 #0.25%
taker_fee = 0.004 #0.4%


# LOAD DATA
df = pd.DataFrame()
for chunk in pd.read_csv(DATA_FILE, chunksize=chunk_size):
    df = pd.concat([df, chunk])

# name columns
df.columns = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 
              'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 
              'taker_buy_quote_asset_volume', 'ignore']

# calculate ATR
df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=atr_period)

# get rid of unused columns
df.drop(['open_time', 'open', 'close_time', 'quote_asset_volume', 'number_of_trades',
         'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'], inplace=True)

# calculate fractal peaks and troughs
df['peak'] = ta.fractal(df['high'], length=fractal_period)
df['trough'] = ta.fractal(df['low'], length=fractal_period)

trading_history = []

def make_trend_lines(df, start_date, end_date, threshold, third_point_multiplier, skip_limit, min_length):
    """
    Generates trendlines for a given DataFrame based on peaks and troughs.

    Args:
        df (pd.DataFrame): DataFrame with a datetime index, and 'peak' and 'trough' columns.
        start_date (str): Start date for the analysis (e.g., '2025-01-01').
        end_date (str): End date for the analysis.
        threshold (float): Percentage a point can be off the line for acceptance (e.g., 0.01 for 1%).
        third_point_multiplier (float): Multiplier for the third point's threshold.
        skip_limit (int): Number of consecutive points to skip before terminating a line.
        min_length (int): Minimum number of points required for a valid trendline.

    Returns:
        tuple: A tuple containing two lists: (list of peak trendlines, list of trough trendlines).
               Each trendline is a list of (bar_index, price) tuples.
    """
    
    sliced_df = df.loc[start_date:end_date].copy()
    
    # Helper function to avoid code duplication
    def generate_line(pivot_series, is_uptrend):
        lines = []
        for start_pivot_idx in pivot_series.index:
            
            potential_line_points = []
            
            # Start a new potential line from this pivot
            start_pos = sliced_df.index.get_loc(start_pivot_idx)
            start_price = sliced_df.loc[start_pivot_idx, 'high' if is_uptrend else 'low']
            
            potential_line_points.append((start_pos, start_price))
            skipped_count = 0
            
            # Iterate through subsequent bars
            for i in range(start_pos + 1, len(sliced_df)):
                current_bar_pos = i
                current_price = sliced_df.loc[sliced_df.index[current_bar_pos], 'high' if is_uptrend else 'low']

                # Calculate line of best fit from current potential points
                x_for_fit = [p[0] for p in potential_line_points]
                y_for_fit = [p[1] for p in potential_line_points]
                
                m, b = np.polyfit(x_for_fit, y_for_fit, deg=1)
                
                # Predict price at the current bar
                predicted_price = m * current_bar_pos + b
                
                # Apply the appropriate threshold
                current_threshold = threshold
                if len(potential_line_points) == 2:
                    current_threshold *= third_point_multiplier
                
                # Check if the point falls within the tolerance
                if abs(current_price - predicted_price) / predicted_price > current_threshold:
                    skipped_count += 1
                else:
                    potential_line_points.append((current_bar_pos, current_price))
                    skipped_count = 0  # Reset skip counter
                
                # Check for termination condition
                if skipped_count > skip_limit:
                    # If the line was long enough, add it to the final list
                    if len(potential_line_points) >= min_length:
                        lines.append([(sliced_df.index[p[0]], p[1]) for p in potential_line_points])
                    break # Terminate the current line

            # Handle end of data if the loop finishes
            if skipped_count <= skip_limit and len(potential_line_points) >= min_length:
                lines.append([(sliced_df.index[p[0]], p[1]) for p in potential_line_points])

        return lines

    peak_lines_raw = generate_line(sliced_df[sliced_df['peak'] == 1], is_uptrend=True)
    trough_lines_raw = generate_line(sliced_df[sliced_df['trough'] == 1], is_uptrend=False)

    return peak_lines_raw, trough_lines_raw

# TRADING LOOP
for i in range(lookback_bars, len(df)):
    