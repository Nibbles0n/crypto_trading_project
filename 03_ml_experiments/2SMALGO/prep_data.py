import numpy as np
import pandas as pd
import os
from tqdm import tqdm

NORMALIZE_WINDOW = 200  # your original rolling window

def load_token_csv(file_path):
    """Load CSV into DataFrame and parse timestamps"""
    df = pd.read_csv(file_path)
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def compute_features(df):
    """Compute any features your original script computed"""
    # Example: rolling means, stds, price changes, etc.
    # Replace/add with your exact original features
    df['close_change'] = df['close'].pct_change().fillna(0)
    df['high_low_diff'] = df['high'] - df['low']
    df['volume_change'] = df['volume'].pct_change().fillna(0)
    
    # Add any other features you had originally
    return df

def normalize_features(df):
    """Normalize only numeric columns to avoid rolling object error"""
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    df[numeric_cols] = df[numeric_cols].rolling(NORMALIZE_WINDOW).mean().fillna(method='bfill')
    return df

def process_token(file_path):
    """Process one token CSV: compute features, normalize, return X/y"""
    df = load_token_csv(file_path)
    df = compute_features(df)
    df = normalize_features(df)

    # Keep numeric feature columns except 'entry_signal'
    feature_cols = df.select_dtypes(include=[np.number]).columns.drop('entry_signal', errors='ignore')
    X = df[feature_cols].to_numpy()
    y = df['entry_signal'].to_numpy()  # binary 0/1
    return X, y, len(df)

def main(data_dir="data"):
    all_X, all_y, counts = [], [], []

    files = [f for f in os.listdir(data_dir) if f.endswith(".csv")]
    for file in tqdm(files):
        file_path = os.path.join(data_dir, file)
        X, y, count = process_token(file_path)
        all_X.append(X)
        all_y.append(y)
        counts.append(count)

    # Stack and save as normalized .npz
    X_full = np.vstack(all_X)
    y_full = np.concatenate(all_y)
    np.savez("processed_data.npz", X=X_full, y=y_full)
    print(f"Saved processed data: {X_full.shape}, {y_full.shape}")

if __name__ == "__main__":
    main()
