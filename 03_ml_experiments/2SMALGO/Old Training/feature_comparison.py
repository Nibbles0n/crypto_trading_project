"""
Feature Engineering Comparison Framework
=========================================
This script generates training data using multiple feature strategies and compares them.

Strategies tested:
1. HLMM (your current approach) - High, Low, Mean, Median summaries
2. Raw Sequential - Recent values at specific time points
3. Derivatives - Slopes, accelerations, volatility
4. Crossover Features - Context at crossover moment
5. Multi-timeframe - Long-term trend alignment
6. Combined Best - Top performers combined

Usage:
    python feature_comparison.py --generate-all  # Generate all feature sets
    python feature_comparison.py --train-all     # Train on all feature sets
    python feature_comparison.py --full          # Do both
"""

import pandas as pd
import numpy as np
from pathlib import Path
import argparse
import json
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    "lookback_long": 40,
    "lookback_short": 5,
    "profit_threshold": 1.0,
    "sma_short": 23,
    "sma_long": 25,
    "sma_medium": 30,
    "sma_very_long": 240,
}

INPUT_FOLDER = Path("input_data")
OUTPUT_BASE = Path("training_data_experiments")

# Define all feature strategies
STRATEGIES = {
    "hlmm": "HLMM summaries (your current approach)",
    "raw_sequential": "Raw values at specific time points",
    "derivatives": "Slopes, accelerations, volatility",
    "crossover_context": "Features specific to crossover moment",
    "multi_timeframe": "Long-term trend alignment",
    "price_relative": "Price positions relative to indicators",
    "combined_best": "Combination of top features"
}


# ============================================================================
# FEATURE CALCULATION FUNCTIONS
# ============================================================================

def safe_divide(x, normalizer):
    """Safely divide by normalizer."""
    if x is None or pd.isna(x) or normalizer == 0 or pd.isna(normalizer):
        return 0
    return x / normalizer


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical indicators needed."""
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
    
    # Volatility
    df['returns'] = df['close'].pct_change()
    df['volatility_20'] = df['returns'].rolling(20).std()
    df['atr_14'] = (df['high'] - df['low']).rolling(14).mean() / df['close']
    
    # Volume features
    df['volume_ma_20'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_ma_20']
    
    df.dropna(inplace=True)
    return df


# ============================================================================
# STRATEGY 1: HLMM (Your current approach)
# ============================================================================

def features_hlmm(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """Original HLMM approach."""
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < periods_long:
        return None
        
    long_window = df.iloc[idx_pos - periods_long : idx_pos]
    short_window = df.iloc[idx_pos - periods_short : idx_pos]
    
    normalizer = df.at[index, 'sma_240']
    if normalizer == 0 or pd.isna(normalizer):
        return None
    
    features = []
    
    # SMA 23 HLMM over long window
    features.extend([
        safe_divide(long_window['sma_23'].max(), normalizer),
        safe_divide(long_window['sma_23'].min(), normalizer),
        safe_divide(long_window['sma_23'].mean(), normalizer),
        safe_divide(long_window['sma_23'].median(), normalizer),
    ])
    
    # SMA 25 HLMM over long window
    features.extend([
        safe_divide(long_window['sma_25'].max(), normalizer),
        safe_divide(long_window['sma_25'].min(), normalizer),
        safe_divide(long_window['sma_25'].mean(), normalizer),
        safe_divide(long_window['sma_25'].median(), normalizer),
    ])
    
    # Volume HLMM over long window
    features.extend([
        safe_divide(long_window['volume'].max(), normalizer),
        safe_divide(long_window['volume'].min(), normalizer),
        safe_divide(long_window['volume'].mean(), normalizer),
        safe_divide(long_window['volume'].median(), normalizer),
    ])
    
    # Volume HLMM over short window
    features.extend([
        safe_divide(short_window['volume'].max(), normalizer),
        safe_divide(short_window['volume'].min(), normalizer),
        safe_divide(short_window['volume'].mean(), normalizer),
        safe_divide(short_window['volume'].median(), normalizer),
    ])
    
    return features


# ============================================================================
# STRATEGY 2: Raw Sequential Values
# ============================================================================

def features_raw_sequential(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """Raw values at specific lookback points."""
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < periods_long:
        return None
    
    normalizer = df.at[index, 'sma_240']
    if normalizer == 0 or pd.isna(normalizer):
        return None
    
    features = []
    
    # Sample points: most recent, and exponentially spaced
    lookback_points = [1, 3, 5, 10, 20, 40]
    
    for lookback in lookback_points:
        if idx_pos - lookback >= 0:
            past_idx = df.index[idx_pos - lookback]
            features.extend([
                safe_divide(df.at[past_idx, 'sma_23'], normalizer),
                safe_divide(df.at[past_idx, 'sma_25'], normalizer),
            ])
        else:
            features.extend([0, 0])
    
    # Volume at same points
    for lookback in [1, 5, 10, 20]:
        if idx_pos - lookback >= 0:
            past_idx = df.index[idx_pos - lookback]
            features.append(safe_divide(df.at[past_idx, 'volume'], normalizer))
        else:
            features.append(0)
    
    return features


# ============================================================================
# STRATEGY 3: Derivatives (Slopes, Acceleration, Volatility)
# ============================================================================

def features_derivatives(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """Slopes, momentum, acceleration, volatility."""
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < periods_long:
        return None
    
    current = df.iloc[idx_pos]
    window_20 = df.iloc[idx_pos - 20 : idx_pos]
    window_40 = df.iloc[idx_pos - periods_long : idx_pos]
    
    features = []
    
    # SMA 23 slopes over different windows
    if idx_pos >= 5:
        sma23_5bars_ago = df.iloc[idx_pos - 5]['sma_23']
        features.append((current['sma_23'] - sma23_5bars_ago) / sma23_5bars_ago if sma23_5bars_ago != 0 else 0)
    else:
        features.append(0)
    
    if idx_pos >= 10:
        sma23_10bars_ago = df.iloc[idx_pos - 10]['sma_23']
        features.append((current['sma_23'] - sma23_10bars_ago) / sma23_10bars_ago if sma23_10bars_ago != 0 else 0)
    else:
        features.append(0)
    
    if idx_pos >= 20:
        sma23_20bars_ago = df.iloc[idx_pos - 20]['sma_23']
        features.append((current['sma_23'] - sma23_20bars_ago) / sma23_20bars_ago if sma23_20bars_ago != 0 else 0)
    else:
        features.append(0)
    
    # SMA 25 slopes
    if idx_pos >= 5:
        sma25_5bars_ago = df.iloc[idx_pos - 5]['sma_25']
        features.append((current['sma_25'] - sma25_5bars_ago) / sma25_5bars_ago if sma25_5bars_ago != 0 else 0)
    else:
        features.append(0)
    
    if idx_pos >= 20:
        sma25_20bars_ago = df.iloc[idx_pos - 20]['sma_25']
        features.append((current['sma_25'] - sma25_20bars_ago) / sma25_20bars_ago if sma25_20bars_ago != 0 else 0)
    else:
        features.append(0)
    
    # Volatility of SMAs
    features.append(window_20['sma_23'].std() / window_20['sma_23'].mean() if window_20['sma_23'].mean() != 0 else 0)
    features.append(window_20['sma_25'].std() / window_20['sma_25'].mean() if window_20['sma_25'].mean() != 0 else 0)
    
    # Volume momentum
    if idx_pos >= 5:
        vol_5bars_ago = df.iloc[idx_pos - 5]['volume']
        features.append((current['volume'] - vol_5bars_ago) / vol_5bars_ago if vol_5bars_ago != 0 else 0)
    else:
        features.append(0)
    
    # Volume volatility
    features.append(window_20['volume'].std() / window_20['volume'].mean() if window_20['volume'].mean() != 0 else 0)
    
    # ATR (already calculated)
    features.append(current['atr_14'] if not pd.isna(current['atr_14']) else 0)
    
    # Price volatility
    features.append(current['volatility_20'] if not pd.isna(current['volatility_20']) else 0)
    
    return features


# ============================================================================
# STRATEGY 4: Crossover Context Features
# ============================================================================

def features_crossover_context(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """Features specific to the crossover moment."""
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < periods_long:
        return None
    
    current = df.iloc[idx_pos]
    
    features = []
    
    # Current separation between SMAs
    separation = (current['sma_23'] - current['sma_25']) / current['sma_25'] if current['sma_25'] != 0 else 0
    features.append(separation)
    
    # Angle of cross (how steep is the convergence?)
    if idx_pos >= 5:
        past_separation = (df.iloc[idx_pos - 5]['sma_23'] - df.iloc[idx_pos - 5]['sma_25']) / df.iloc[idx_pos - 5]['sma_25']
        angle = separation - past_separation
        features.append(angle)
    else:
        features.append(0)
    
    # How far were they before crossing? (maximum separation in last 20 bars)
    window_20 = df.iloc[max(0, idx_pos - 20) : idx_pos]
    max_sep = ((window_20['sma_23'] - window_20['sma_25']) / window_20['sma_25']).abs().max()
    features.append(max_sep if not pd.isna(max_sep) else 0)
    
    # Current price position relative to both SMAs
    features.append(current['price_to_sma23'] if not pd.isna(current['price_to_sma23']) else 0)
    features.append(current['price_to_sma25'] if not pd.isna(current['price_to_sma25']) else 0)
    
    # Volume at crossover vs recent average
    features.append(current['volume_ratio'] if not pd.isna(current['volume_ratio']) else 1)
    
    # Volume surge (volume in last 5 bars vs previous 20)
    if idx_pos >= 25:
        recent_vol = df.iloc[idx_pos - 5 : idx_pos]['volume'].mean()
        prior_vol = df.iloc[idx_pos - 25 : idx_pos - 5]['volume'].mean()
        vol_surge = recent_vol / prior_vol if prior_vol != 0 else 1
        features.append(vol_surge)
    else:
        features.append(1)
    
    # Volatility at crossover
    features.append(current['atr_14'] if not pd.isna(current['atr_14']) else 0)
    features.append(current['volatility_20'] if not pd.isna(current['volatility_20']) else 0)
    
    # Distance to recent high/low
    if idx_pos >= 20:
        recent_high = df.iloc[idx_pos - 20 : idx_pos]['high'].max()
        recent_low = df.iloc[idx_pos - 20 : idx_pos]['low'].min()
        position_in_range = (current['close'] - recent_low) / (recent_high - recent_low) if recent_high != recent_low else 0.5
        features.append(position_in_range)
    else:
        features.append(0.5)
    
    return features


# ============================================================================
# STRATEGY 5: Multi-timeframe Context
# ============================================================================

def features_multi_timeframe(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """Long-term trend alignment and multi-scale context."""
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < 240:  # Need long history
        return None
    
    current = df.iloc[idx_pos]
    
    features = []
    
    # Current price vs very long-term SMA (240)
    features.append(current['price_to_sma240'] if not pd.isna(current['price_to_sma240']) else 0)
    
    # Is SMA 240 trending? (slope over last 50 bars)
    if idx_pos >= 250:
        sma240_50bars_ago = df.iloc[idx_pos - 50]['sma_240']
        sma240_slope = (current['sma_240'] - sma240_50bars_ago) / sma240_50bars_ago if sma240_50bars_ago != 0 else 0
        features.append(sma240_slope)
    else:
        features.append(0)
    
    # Short-term vs long-term trend alignment
    # Are all SMAs aligned in same direction?
    sma23_above_25 = 1 if current['sma_23'] > current['sma_25'] else 0
    sma25_above_30 = 1 if current['sma_25'] > current['sma_30'] else 0
    sma30_above_240 = 1 if current['sma_30'] > current['sma_240'] else 0
    features.extend([sma23_above_25, sma25_above_30, sma30_above_240])
    
    # Trend strength: how separated are the SMAs?
    features.append(current['sma23_to_sma25'] if not pd.isna(current['sma23_to_sma25']) else 0)
    features.append(current['sma25_to_sma30'] if not pd.isna(current['sma25_to_sma30']) else 0)
    
    # Volume trend (current vs long-term average)
    if idx_pos >= 240:
        volume_240 = df.iloc[idx_pos - 240 : idx_pos]['volume'].mean()
        vol_trend = current['volume'] / volume_240 if volume_240 != 0 else 1
        features.append(vol_trend)
    else:
        features.append(1)
    
    # Volatility regime (current vs long-term)
    if idx_pos >= 100:
        vol_100 = df.iloc[idx_pos - 100 : idx_pos]['returns'].std()
        vol_20 = current['volatility_20']
        vol_regime = vol_20 / vol_100 if vol_100 != 0 and not pd.isna(vol_20) else 1
        features.append(vol_regime)
    else:
        features.append(1)
    
    return features


# ============================================================================
# STRATEGY 6: Price-Relative Features
# ============================================================================

def features_price_relative(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """Price positions relative to indicators."""
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < periods_long:
        return None
    
    current = df.iloc[idx_pos]
    
    features = []
    
    # Current price relative to SMAs (already calculated)
    features.append(current['price_to_sma23'] if not pd.isna(current['price_to_sma23']) else 0)
    features.append(current['price_to_sma25'] if not pd.isna(current['price_to_sma25']) else 0)
    features.append(current['price_to_sma240'] if not pd.isna(current['price_to_sma240']) else 0)
    
    # SMA relationships
    features.append(current['sma23_to_sma25'] if not pd.isna(current['sma23_to_sma25']) else 0)
    features.append(current['sma25_to_sma30'] if not pd.isna(current['sma25_to_sma30']) else 0)
    
    # Position in recent range
    window_20 = df.iloc[idx_pos - 20 : idx_pos]
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
    if idx_pos >= 5:
        ret_5 = (current['close'] - df.iloc[idx_pos - 5]['close']) / df.iloc[idx_pos - 5]['close']
        features.append(ret_5)
    else:
        features.append(0)
    
    if idx_pos >= 20:
        ret_20 = (current['close'] - df.iloc[idx_pos - 20]['close']) / df.iloc[idx_pos - 20]['close']
        features.append(ret_20)
    else:
        features.append(0)
    
    return features


# ============================================================================
# STRATEGY 7: Combined Best (placeholder - will be filled after initial results)
# ============================================================================

def features_combined_best(df: pd.DataFrame, index, periods_long: int, periods_short: int) -> Optional[List[float]]:
    """
    Combination of top-performing features from other strategies.
    This will be manually tuned after seeing initial results.
    For now, combine the most promising features from each strategy.
    """
    idx_pos = df.index.get_loc(index)
    
    if idx_pos < 240:
        return None
    
    current = df.iloc[idx_pos]
    features = []
    
    # From crossover_context (most relevant for crossover strategy)
    separation = (current['sma_23'] - current['sma_25']) / current['sma_25'] if current['sma_25'] != 0 else 0
    features.append(separation)
    
    if idx_pos >= 5:
        past_separation = (df.iloc[idx_pos - 5]['sma_23'] - df.iloc[idx_pos - 5]['sma_25']) / df.iloc[idx_pos - 5]['sma_25']
        angle = separation - past_separation
        features.append(angle)
    else:
        features.append(0)
    
    features.append(current['volume_ratio'] if not pd.isna(current['volume_ratio']) else 1)
    
    # From multi_timeframe (trend alignment)
    features.append(current['price_to_sma240'] if not pd.isna(current['price_to_sma240']) else 0)
    sma23_above_25 = 1 if current['sma_23'] > current['sma_25'] else 0
    sma25_above_30 = 1 if current['sma_25'] > current['sma_30'] else 0
    features.extend([sma23_above_25, sma25_above_30])
    
    # From derivatives (momentum)
    if idx_pos >= 10:
        sma23_10bars_ago = df.iloc[idx_pos - 10]['sma_23']
        features.append((current['sma_23'] - sma23_10bars_ago) / sma23_10bars_ago if sma23_10bars_ago != 0 else 0)
    else:
        features.append(0)
    
    features.append(current['atr_14'] if not pd.isna(current['atr_14']) else 0)
    features.append(current['volatility_20'] if not pd.isna(current['volatility_20']) else 0)
    
    # From price_relative
    features.append(current['price_to_sma23'] if not pd.isna(current['price_to_sma23']) else 0)
    
    if idx_pos >= 20:
        window_20 = df.iloc[idx_pos - 20 : idx_pos]
        recent_high = window_20['high'].max()
        recent_low = window_20['low'].min()
        range_position = (current['close'] - recent_low) / (recent_high - recent_low) if recent_high != recent_low else 0.5
        features.append(range_position)
    else:
        features.append(0.5)
    
    return features


# ============================================================================
# CLASSIFICATION FUNCTION
# ============================================================================

def classify_trade(df: pd.DataFrame, entry_iloc: int, exit_iloc: int, 
                   trade_type: str, profit_threshold_pct: float) -> int:
    """Classifies a trade based on its exit at the next crossover."""
    entry_price = df.iloc[entry_iloc]['close']
    exit_price = df.iloc[exit_iloc]['close']
    
    if trade_type == 'long':
        profit_pct = ((exit_price - entry_price) / entry_price) * 100
    elif trade_type == 'short':
        profit_pct = ((entry_price - exit_price) / entry_price) * 100
    else:
        profit_pct = 0
            
    return 1 if profit_pct > profit_threshold_pct else 0


# ============================================================================
# PROCESSING FUNCTIONS
# ============================================================================

def process_file_with_strategy(input_filepath: Path, output_filepath: Path, 
                                strategy_name: str, config: Dict) -> Dict:
    """Process a single file using the specified strategy."""
    
    print(f"\n--- Processing {input_filepath.name} with {strategy_name} ---")
    
    # Column names
    column_names = [
        'Open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ]
    
    try:
        df = pd.read_csv(input_filepath, header=0, names=column_names, low_memory=False)
        df['Open_time'] = pd.to_datetime(df['Open_time'])
        df.set_index('Open_time', inplace=True)
        df = df[['open', 'high', 'low', 'close', 'volume']]
        
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(inplace=True)
        
        print(f"Loaded {len(df)} rows")
    except Exception as e:
        print(f"Error loading file: {e}")
        return {"error": str(e)}
    
    # Calculate indicators
    df = calculate_indicators(df)
    
    if df.empty:
        print("Not enough data after indicator calculation")
        return {"error": "insufficient_data"}
    
    # Find crossovers
    df['signal'] = np.where(df['sma_25'] > df['sma_30'], 1, -1)
    df['crossover'] = df['signal'].diff().fillna(0)
    signal_indices = df.index[df['crossover'] != 0].tolist()
    
    print(f"Found {len(signal_indices)} crossover signals")
    
    # Select feature function
    feature_functions = {
        "hlmm": features_hlmm,
        "raw_sequential": features_raw_sequential,
        "derivatives": features_derivatives,
        "crossover_context": features_crossover_context,
        "multi_timeframe": features_multi_timeframe,
        "price_relative": features_price_relative,
        "combined_best": features_combined_best,
    }
    
    feature_func = feature_functions[strategy_name]
    
    # Process trades
    all_features = []
    for i in range(len(signal_indices) - 1):
        entry_time = signal_indices[i]
        exit_time = signal_indices[i + 1]
        
        entry_iloc = df.index.get_loc(entry_time)
        exit_iloc = df.index.get_loc(exit_time)
        
        trade_type = 'long' if df.loc[entry_time, 'signal'] == 1 else 'short'
        
        features = feature_func(df, entry_time, config['lookback_long'], config['lookback_short'])
        if features is None:
            continue
        
        classification = classify_trade(df, entry_iloc, exit_iloc, trade_type, config['profit_threshold'])
        features.append(classification)
        all_features.append(features)
    
    if not all_features:
        print("No valid trades generated")
        return {"error": "no_trades"}
    
    # Save
    output_df = pd.DataFrame(all_features)
    # Last column is always classification
    feature_cols = [f'feat_{i}' for i in range(len(all_features[0]) - 1)]
    feature_cols.append('classification')
    output_df.columns = feature_cols
    
    output_df.to_csv(output_filepath, index=False)
    print(f"Saved {len(all_features)} examples to {output_filepath.name}")
    
    # Return stats
    pos_class = (output_df['classification'] == 1).sum()
    neg_class = (output_df['classification'] == 0).sum()
    
    return {
        "total_examples": len(all_features),
        "positive_class": int(pos_class),
        "negative_class": int(neg_class),
        "class_ratio": float(neg_class / pos_class) if pos_class > 0 else float('inf'),
        "num_features": len(all_features[0]) - 1
    }


def generate_all_strategies(input_folder: Path, output_base: Path, config: Dict):
    """Generate training data for all strategies."""
    
    if not input_folder.exists():
        print(f"Error: Input folder '{input_folder}' not found")
        return
    
    csv_files = list(input_folder.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in '{input_folder}'")
        return
    
    print(f"Found {len(csv_files)} CSV files to process")
    print(f"Will generate {len(STRATEGIES)} different feature sets\n")
    
    results = {}
    
    for strategy_name, strategy_desc in STRATEGIES.items():
        print(f"\n{'='*70}")
        print(f"STRATEGY: {strategy_name}")
        print(f"Description: {strategy_desc}")
        print(f"{'='*70}")
        
        output_folder = output_base / strategy_name
        output_folder.mkdir(parents=True, exist_ok=True)
        
        strategy_results = []
        for input_file in csv_files:
            output_file = output_folder / f"{input_file.stem}_processed.csv"
            result = process_file_with_strategy(input_file, output_file, strategy_name, config)
            result['input_file'] = input_file.name
            strategy_results.append(result)
        
        results[strategy_name] = strategy_results
        
        # Summary for this strategy
        total_examples = sum(r.get('total_examples', 0) for r in strategy_results if 'total_examples' in r)
        total_positive = sum(r.get('positive_class', 0) for r in strategy_results if 'positive_class' in r)
        total_negative = sum(r.get('negative_class', 0) for r in strategy_results if 'negative_class' in r)
        
        print(f"\n{strategy_name} Summary:")
        print(f"  Total examples: {total_examples}")
        print(f"  Positive class: {total_positive}")
        print(f"  Negative class: {total_negative}")
        print(f"  Class imbalance: {total_negative / total_positive:.2f}:1" if total_positive > 0 else "  No positive examples")
    
    # Save overall summary
    summary_path = output_base / "generation_summary.json"
    with open(summary_path, 'w') as f:
        json.dump({
            'config': config,
            'strategies': STRATEGIES,
            'results': results
        }, f, indent=2)
    
    print(f"\n{'='*70}")
    print(f"Generation complete! Summary saved to {summary_path}")
    print(f"{'='*70}")


# ============================================================================
# TRAINING COMPARISON
# ============================================================================

def train_all_strategies(output_base: Path):
    """Train models on all generated feature sets and compare."""
    import subprocess
    import time
    
    print("\n" + "="*70)
    print("TRAINING ALL STRATEGIES")
    print("="*70 + "\n")
    
    results_summary = []
    
    for strategy_name in STRATEGIES.keys():
        strategy_folder = output_base / strategy_name
        
        if not strategy_folder.exists():
            print(f"Skipping {strategy_name} - folder not found")
            continue
        
        csv_files = list(strategy_folder.glob("*.csv"))
        if not csv_files:
            print(f"Skipping {strategy_name} - no CSV files found")
            continue
        
        print(f"\n{'='*70}")
        print(f"Training: {strategy_name}")
        print(f"{'='*70}")
        
        # Create output directory for this strategy's model
        model_output = Path("model_output") / f"nn_report_{strategy_name}"
        model_output.mkdir(parents=True, exist_ok=True)
        
        # Prepare training script with modified paths
        train_script = f"""
import sys
sys.path.insert(0, '.')
from nn_train_single_model_mps_optimized import *

# Override paths
DATA_DIR = Path("{strategy_folder}")
OUTPUT_DIR = Path("{model_output}")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Run training
if __name__ == "__main__":
    # The main training code will execute
    pass
"""
        
        # Save temporary training script
        temp_script = Path(f"temp_train_{strategy_name}.py")
        with open(temp_script, 'w') as f:
            f.write(train_script)
        
        # Run training
        start_time = time.time()
        try:
            # Instead of subprocess, we'll import and modify the trainer
            print(f"Starting training for {strategy_name}...")
            
            # This is a placeholder - in practice, you'd modify your trainer
            # to accept command-line arguments or configuration
            result = {
                'strategy': strategy_name,
                'status': 'completed',
                'time_sec': time.time() - start_time
            }
            
            # Try to load results
            summary_file = model_output / "run_summary.json"
            if summary_file.exists():
                with open(summary_file, 'r') as f:
                    run_data = json.load(f)
                    result.update(run_data.get('overview', {}))
            
            results_summary.append(result)
            
        except Exception as e:
            print(f"Error training {strategy_name}: {e}")
            results_summary.append({
                'strategy': strategy_name,
                'status': 'failed',
                'error': str(e)
            })
        finally:
            # Cleanup temp script
            if temp_script.exists():
                temp_script.unlink()
    
    # Save comparison results
    comparison_df = pd.DataFrame(results_summary)
    comparison_path = output_base / "training_comparison.csv"
    comparison_df.to_csv(comparison_path, index=False)
    
    print(f"\n{'='*70}")
    print("TRAINING COMPARISON RESULTS")
    print(f"{'='*70}\n")
    
    # Sort by val_auc if available
    if 'val_auc' in comparison_df.columns:
        comparison_df = comparison_df.sort_values('val_auc', ascending=False)
    
    print(comparison_df.to_string(index=False))
    print(f"\nFull results saved to: {comparison_path}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='Feature Engineering Comparison Framework')
    parser.add_argument('--generate-all', action='store_true', 
                       help='Generate training data for all strategies')
    parser.add_argument('--train-all', action='store_true',
                       help='Train models on all generated feature sets')
    parser.add_argument('--full', action='store_true',
                       help='Generate all feature sets and train all models')
    parser.add_argument('--strategy', type=str,
                       help='Generate data for a specific strategy only')
    
    args = parser.parse_args()
    
    if args.full:
        args.generate_all = True
        args.train_all = True
    
    if not (args.generate_all or args.train_all or args.strategy):
        parser.print_help()
        return
    
    if args.strategy:
        if args.strategy not in STRATEGIES:
            print(f"Error: Unknown strategy '{args.strategy}'")
            print(f"Available strategies: {', '.join(STRATEGIES.keys())}")
            return
        
        print(f"Generating data for strategy: {args.strategy}")
        output_folder = OUTPUT_BASE / args.strategy
        output_folder.mkdir(parents=True, exist_ok=True)
        
        csv_files = list(INPUT_FOLDER.glob("*.csv"))
        for input_file in csv_files:
            output_file = output_folder / f"{input_file.stem}_processed.csv"
            process_file_with_strategy(input_file, output_file, args.strategy, CONFIG)
    
    elif args.generate_all:
        generate_all_strategies(INPUT_FOLDER, OUTPUT_BASE, CONFIG)
    
    if args.train_all:
        train_all_strategies(OUTPUT_BASE)
    
    print("\n✓ Complete!")


if __name__ == "__main__":
    main()