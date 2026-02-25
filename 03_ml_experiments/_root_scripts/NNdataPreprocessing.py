import pandas as pd
import numpy as np
import os
from datetime import datetime
import talib
from joblib import Parallel, delayed

# Folder with Binance CSVs
data_folder = 'path/to/your/csv/folder'  # Replace
output_csv = 'tradeData.csv'

# Indicator calculation
def compute_indicators(df):
    df['sma15'] = talib.SMA(df['close'], timeperiod=15)
    df['sma45'] = talib.SMA(df['close'], timeperiod=45)
    df['sma_diff'] = df['sma15'] - df['sma45']
    df['norm_vol'] = df['volume'] / df['volume'].rolling(50).mean()
    df['rsi'] = talib.RSI(df['close'], timeperiod=14)
    df['norm_rsi'] = (df['rsi'] - df['rsi'].rolling(50).mean()) / df['rsi'].rolling(50).std()
    df['atr'] = talib.ATR(df['high'], df['low'], df['close'], timeperiod=14)
    df['atr_pct'] = (df['atr'] / df['close']) * 100
    df['macd'], df['macd_signal'], df['macd_hist'] = talib.MACD(df['close'], fastperiod=12, slowperiod=26, signalperiod=9)
    return df.dropna()

# Process single token file
def process_token_file(filename, data_folder):
    if not filename.endswith('.csv'): return []
    token = filename.split('_')[0]
    df = pd.read_csv(os.path.join(data_folder, filename),
                     names=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                            'quote_volume', 'num_trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'],
                     usecols=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['token'] = token
    df = compute_indicators(df)
    
    df['prev_sma15'] = df['sma15'].shift(1)
    df['prev_sma45'] = df['sma45'].shift(1)
    df['entry_signal'] = np.where((df['prev_sma15'] < df['prev_sma45']) & (df['sma15'] > df['sma45']), 1, 0)
    df['exit_signal'] = np.where((df['prev_sma15'] > df['prev_sma45']) & (df['sma15'] < df['sma45']), 1, 0)
    
    entries = df[df['entry_signal'] == 1].index
    samples = []
    features = ['close', 'sma15', 'sma45', 'sma_diff', 'norm_vol', 'norm_rsi', 'atr_pct', 'macd_hist']
    
    for entry_idx in entries:
        window_start = entry_idx - 10
        if window_start < 0: continue
        entry_price = df.at[entry_idx, 'close']
        entry_time = df.at[entry_idx, 'timestamp']
        window = df.iloc[window_start:entry_idx][features].copy()
        for col in ['close', 'sma15', 'sma45', 'sma_diff', 'macd_hist']:
            window[col] = window[col] / entry_price
        flat_row = window.values.flatten()
        
        next_exits = df.loc[entry_idx+1:][df['exit_signal'] == 1].index
        if not next_exits.empty:
            exit_idx = next_exits[0]
            exit_price = df.at[exit_idx, 'close']
            return_pct = (exit_price - entry_price) / entry_price * 100
            samples.append([token, entry_time, *flat_row, return_pct])
    
    return samples

# Process all files in parallel
all_samples = Parallel(n_jobs=-1)(delayed(process_token_file)(f, data_folder) for f in os.listdir(data_folder))
all_samples = [s for sublist in all_samples for s in sublist]  # Flatten

# Save to CSV
columns = ['token', 'entry_timestamp'] + [f'{col}_t-{10-i}' for i in range(10) for col in ['close', 'sma15', 'sma45', 'sma_diff', 'norm_vol', 'norm_rsi', 'atr_pct', 'macd_hist']] + ['return_pct']
samples_df = pd.DataFrame(all_samples, columns=columns)
samples_df.to_csv(output_csv, index=False)
print(f'Processed {len(samples_df)} samples into {output_csv}')