"""
Price Relative Feature Converter
==================================
This script converts raw market data into price relative features for training.

Configuration:
- input_dir: Directory containing input CSV files
- output_dir: Directory to save processed CSV files
- profit_threshold: Profit threshold percentage for classification
- sma_short: Short SMA period (default: 24)
- sma_long: Long SMA period (default: 25)
- sma_medium: Medium SMA period (default: 30)
- sma_very_long: Very long SMA period (default: 240)
- lookback_long: Long lookback period (default: 40)
- lookback_short: Short lookback period (default: 5)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import warnings
import os
import json
from datetime import datetime
warnings.filterwarnings('ignore')

# Configuration and Checkpointing
CHECKPOINT_FILE = "price_relative_checkpoint.json"
DEFAULT_CONFIG = {
    "input_dir": "input_data",          # Directory containing input CSV files
    "output_dir": "price_relative", # Directory to save processed files
    "profit_threshold": 1.0,           # Profit threshold percentage
    "sma_short": 24,
    "sma_long": 25,
    "sma_medium": 30,
    "sma_very_long": 240,
    "lookback_long": 40,
    "lookback_short": 5,
}

def safe_divide(x, normalizer=1.0):
    """Safely divide by normalizer."""
    if x is None or pd.isna(x) or normalizer == 0 or pd.isna(normalizer):
        return 0
    return x / normalizer

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical indicators needed for price relative features."""
    df = df.copy()
    
    # SMAs
    df['sma_23'] = df['close'].rolling(window=23).mean()
    df['sma_25'] = df['close'].rolling(window=25).mean()
    df['sma_30'] = df['close'].rolling(window=30).mean()
    df['sma_240'] = df['close'].rolling(window=240).mean()
    
    # Price-based features
    df['price_to_sma23'] = (df['close'] - df['sma_23']) / df['sma_23']
    df['price_to_sma25'] = (df['close'] - df['sma_25']) / df['sma_25']
    df['price_to_sma240'] = (df['close'] - df['sma_240']) / df['sma_240']
    
    # SMA distances
    df['sma23_to_sma25'] = (df['sma_23'] - df['sma_25']) / df['sma_25']
    df['sma25_to_sma30'] = (df['sma_25'] - df['sma_30']) / df['sma_30']
    
    # Volatility (ATR)
    df['returns'] = df['close'].pct_change()
    df['atr_14'] = (df['high'] - df['low']).rolling(14).mean() / df['close']
    
    # Volume features
    df['volume_ma_20'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma_20']
    
    df.dropna(inplace=True)
    return df

def classify_trade(df: pd.DataFrame, entry_iloc: int, exit_iloc: int, 
                  trade_type: str, profit_threshold_pct: float) -> int:
    """Classify a trade based on its exit at the next crossover."""
    if exit_iloc >= len(df):
        return 0  # No exit signal in data
    
    entry_price = df.iloc[entry_iloc]['close']
    exit_price = df.iloc[exit_iloc]['close']
    
    if trade_type == 'long':
        profit_pct = (exit_price - entry_price) / entry_price * 100
    else:  # short
        profit_pct = (entry_price - exit_price) / entry_price * 100
    
    if profit_pct >= profit_threshold_pct:
        return 1  # Profitable trade
    return 0  # Unprofitable trade

def load_checkpoint() -> Tuple[Dict, List[str]]:
    """Load processing checkpoint if it exists."""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                data = json.load(f)
                return data['config'], data['processed_files']
        except Exception as e:
            print(f"Warning: Could not load checkpoint: {e}")
    return DEFAULT_CONFIG.copy(), []

def save_checkpoint(config: Dict, processed_files: List[str]) -> None:
    """Save processing checkpoint."""
    try:
        with open(CHECKPOINT_FILE, 'w') as f:
            json.dump({
                'config': config,
                'processed_files': processed_files,
                'last_updated': datetime.utcnow().isoformat()
            }, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save checkpoint: {e}")

def process_single_file(input_filepath: Path, output_filepath: Path, config: Dict) -> bool:
    """Process a single file to generate price relative features."""
    try:
        # Read and prepare data
        df = pd.read_csv(input_filepath)
        
        # Handle timestamp conversion
        timestamp_col = 'open_time' if 'open_time' in df.columns else 'timestamp'
        if timestamp_col not in df.columns:
            print(f"Error: Could not find timestamp column in {input_filepath.name}")
            return False
            
        # Convert timestamp to datetime, handle potential format issues
        try:
            df[timestamp_col] = pd.to_datetime(df[timestamp_col], utc=True)
            df.set_index(timestamp_col, inplace=True)
        except Exception as e:
            print(f"Error parsing timestamps in {input_filepath.name}: {e}")
            return False
        
        # Ensure we have the required columns
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required_columns if col not in df.columns]
        if missing:
            print(f"Error: Missing required columns in {input_filepath.name}: {', '.join(missing)}")
            return False
            
        # Sort by timestamp to ensure chronological order
        df = df.sort_index()
        
        # Calculate indicators
        df = calculate_indicators(df)
        
        # Skip if we don't have enough data after calculating indicators
        if len(df) < config['lookback_long'] * 2:  # Need at least 2x lookback for meaningful analysis
            print(f"Skipping {input_filepath.name}: Insufficient data points after processing")
            return False
        
        # Prepare output data
        output_data = []
        
        # Find all crossover points (where sma_23 crosses sma_25)
        df['sma_cross'] = (df['sma_23'] > df['sma_25']).astype(int).diff()
        cross_indices = df[df['sma_cross'] != 0].index.tolist()
        
        for i in range(len(cross_indices) - 1):
            entry_idx = cross_indices[i]
            exit_idx = cross_indices[i + 1]
            
            # Get the position in the DataFrame for the entry
            entry_iloc = df.index.get_loc(entry_idx)
            exit_iloc = df.index.get_loc(exit_idx)
            
            # Skip if we don't have enough data before the entry
            if entry_iloc < config['lookback_long']:
                continue
                
            # Calculate price relative features
            features = []
            current = df.iloc[entry_iloc]
            
            # Current price relative to SMAs
            features.append(safe_divide(current['price_to_sma23']))
            features.append(safe_divide(current['price_to_sma25']))
            features.append(safe_divide(current['price_to_sma240']))
            
            # SMA relationships
            features.append(safe_divide(current['sma23_to_sma25']))
            features.append(safe_divide(current['sma25_to_sma30']))
            
            # Position in recent range
            window_20 = df.iloc[entry_iloc - 20 : entry_iloc]
            recent_high = window_20['high'].max()
            recent_low = window_20['low'].min()
            range_position = (current['close'] - recent_low) / (recent_high - recent_low) if recent_high != recent_low else 0.5
            features.append(range_position)
            
            # Distance from recent high/low as % of ATR
            dist_from_high = (recent_high - current['close']) / (current['atr_14'] * current['close']) if current['atr_14'] != 0 else 0
            dist_from_low = (current['close'] - recent_low) / (current['atr_14'] * current['close']) if current['atr_14'] != 0 else 0
            features.extend([dist_from_high, dist_from_low])
            
            # Volume relative to average
            features.append(current['volume_ratio'] if not pd.isna(current['volume_ratio']) else 1)
            
            # Recent momentum (5-bar and 20-bar returns)
            if entry_iloc >= 5:
                ret_5 = (current['close'] - df.iloc[entry_iloc - 5]['close']) / df.iloc[entry_iloc - 5]['close']
                features.append(ret_5)
            else:
                features.append(0)
            
            if entry_iloc >= 20:
                ret_20 = (current['close'] - df.iloc[entry_iloc - 20]['close']) / df.iloc[entry_iloc - 20]['close']
                features.append(ret_20)
            else:
                features.append(0)
            
            # Determine if this was a long or short trade
            trade_type = 'long' if df.at[entry_idx, 'sma_cross'] > 0 else 'short'
            
            # Classify the trade
            classification = classify_trade(df, entry_iloc, exit_iloc, trade_type, config['profit_threshold'])
            
            # Add to output
            output_data.append(features + [classification])
        
        # Save to CSV
        if output_data:
            columns = [f'feat_{i}' for i in range(len(features))] + ['classification']
            output_df = pd.DataFrame(output_data, columns=columns)
            output_df.to_csv(output_filepath, index=False)
            print(f"Processed {input_filepath.name} -> {output_filepath} ({len(output_df)} samples)")
            return True
        else:
            print(f"No valid trades found in {input_filepath.name}")
            return False
            
    except Exception as e:
        print(f"Error processing {input_filepath.name}: {str(e)}")
        return False

def main():
    # Load checkpoint or use default config
    config, processed_files = load_checkpoint()
    
    # Set up directories from config
    input_dir = Path(config["input_dir"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Get all CSV files and filter out already processed ones
    all_csv_files = sorted(input_dir.glob('*.csv'), key=lambda x: x.stat().st_mtime)
    csv_files = [f for f in all_csv_files if f.name not in processed_files]
    
    if not csv_files:
        print(f"No new files to process in {input_dir}")
        if processed_files:
            print(f"{len(processed_files)} files were already processed in previous runs.")
        return
    
    print(f"Found {len(csv_files)} new files to process (out of {len(all_csv_files)} total)")
    
    success_count = 0
    for i, input_file in enumerate(csv_files, 1):
        print(f"\nProcessing file {i}/{len(csv_files)}: {input_file.name}")
        output_file = output_dir / f"{input_file.stem}_processed.csv"
        
        if process_single_file(input_file, output_file, config):
            success_count += 1
            processed_files.append(input_file.name)
            # Save checkpoint after each successful file
            save_checkpoint(config, processed_files)
    
    print(f"\nProcessing complete! {success_count}/{len(csv_files)} files processed successfully.")
    print(f"Total processed files: {len(processed_files)}")
    print(f"Output saved to: {output_dir.absolute()}")

if __name__ == "__main__":
    main()
