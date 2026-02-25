import pandas as pd
import numpy as np
import os
from pathlib import Path


def calculate_features(df, index, periods_long, periods_short):
    """
    Calculates the 16 input features for a given trade signal.
    Now works with datetime indices.
    """
    # Get the datetime index position
    idx_pos = df.index.get_loc(index)
    
    # Ensure we have enough data points
    if idx_pos < periods_long:
        return None
        
    long_window = df.iloc[idx_pos - periods_long : idx_pos]
    short_window = df.iloc[idx_pos - periods_short : idx_pos]
    
    # Get the normalizer value
    normalizer = df.at[index, 'sma_240']
    
    if normalizer == 0 or pd.isna(normalizer):
        return None


    # Calculate features with error handling
    def safe_divide(x):
        return x / normalizer if x is not None and not pd.isna(x) else 0


    sma_23_hlmm_long = [
        safe_divide(long_window['sma_23'].max()),
        safe_divide(long_window['sma_23'].min()),
        safe_divide(long_window['sma_23'].mean()),
        safe_divide(long_window['sma_23'].median()),
    ]
    sma_25_hlmm_long = [
        safe_divide(long_window['sma_25'].max()),
        safe_divide(long_window['sma_25'].min()),
        safe_divide(long_window['sma_25'].mean()),
        safe_divide(long_window['sma_25'].median()),
    ]
    volume_hlmm_long = [
        safe_divide(long_window['volume'].max()),
        safe_divide(long_window['volume'].min()),
        safe_divide(long_window['volume'].mean()),
        safe_divide(long_window['volume'].median()),
    ]
    volume_hlmm_short = [
        safe_divide(short_window['volume'].max()),
        safe_divide(short_window['volume'].min()),
        safe_divide(short_window['volume'].mean()),
        safe_divide(short_window['volume'].median()),
    ]
    
    return (sma_23_hlmm_long + sma_25_hlmm_long + volume_hlmm_long + volume_hlmm_short)




def classify_trade(df, entry_iloc, exit_iloc, trade_type, profit_threshold_pct):
    """
    Classifies a trade based on its exit at the next crossover.
    """
    entry_price = df.iloc[entry_iloc]['close']
    exit_price = df.iloc[exit_iloc]['close']
    
    profit_pct = 0
    if trade_type == 'long':
        profit_pct = ((exit_price - entry_price) / entry_price) * 100
    elif trade_type == 'short':
        profit_pct = ((entry_price - exit_price) / entry_price) * 100
            
    return 1 if profit_pct > profit_threshold_pct else 0




def process_single_file(input_filepath, output_filepath, config):
    """
    Processes a single CSV file to generate features and classifications.
    """
    print(f"\n--- Processing {input_filepath.name} ---")
    
    # Define the column names based on the standard exchange data format
    column_names = [
        'Open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ]
    
    try:
        # Read the CSV with header
        df = pd.read_csv(
            input_filepath,
            header=0,  # First row is header
            names=column_names,
            low_memory=False
        )
        
        # Convert the timestamp to datetime object and set it as the index
        df['Open_time'] = pd.to_datetime(df['Open_time'])
        df.set_index('Open_time', inplace=True)
        
        # Keep only the essential columns for our calculations
        df = df[['open', 'high', 'low', 'close', 'volume']]
        
        # Convert all numeric columns to float
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
        # Drop any rows with NaN values
        df.dropna(inplace=True)
        
        print(f"Successfully loaded {len(df)} rows of data.")


    except Exception as e:
        print(f"Could not read or parse {input_filepath.name}. Error: {e}")
        # Print the first few rows to help with debugging
        try:
            with open(input_filepath, 'r') as f:
                print("\nFirst 3 lines of the file:")
                for _ in range(3):
                    print(f.readline().strip())
        except Exception as e2:
            print(f"Could not read file for debugging: {e2}")
        return


    print("Calculating indicators...")
    df['sma_23'] = df['close'].rolling(window=23).mean()
    df['sma_25'] = df['close'].rolling(window=25).mean()
    df['sma_30'] = df['close'].rolling(window=30).mean()
    df['sma_240'] = df['close'].rolling(window=240).mean()
    df.dropna(inplace=True)
    
    if df.empty:
        print(f"Skipping {input_filepath.name}: Not enough data to calculate indicators.")
        return


    print("Finding crossover signals...")
    df['signal'] = np.where(df['sma_25'] > df['sma_30'], 1, -1)
    df['crossover'] = df['signal'].diff().fillna(0)
    signal_indices = df.index[df['crossover'] != 0].tolist()
    
    print(f"Found {len(signal_indices)} signals. Processing trade pairs...")
    all_features = []
    
    for i in range(len(signal_indices) - 1):
        entry_time = signal_indices[i]
        exit_time = signal_indices[i+1]
        
        # Get the integer positions for classification
        entry_iloc = df.index.get_loc(entry_time)
        exit_iloc = df.index.get_loc(exit_time)
        
        if entry_iloc < config['lookback_long']:
            continue
            
        trade_type = 'long' if df.loc[entry_time, 'signal'] == 1 else 'short'
        features = calculate_features(df, entry_time, config['lookback_long'], config['lookback_short'])
        if features is None:
            continue


        classification = classify_trade(df, entry_iloc, exit_iloc, trade_type, config['profit_threshold'])
        features.append(classification)
        all_features.append(features)


    if not all_features:
        print(f"No valid trade pairs found in {input_filepath.name}.")
        return


    print(f"Saving {len(all_features)} processed examples to {output_filepath}...")
    columns = []
    for source in ['sma23', 'sma25', 'vol']:
        for stat in ['h', 'l', 'm', 'md']:
            columns.append(f'{source}_40_{stat}')
    for stat in ['h', 'l', 'm', 'md']:
        columns.append(f'vol_5_{stat}')
    columns.append('classification')
    
    output_df = pd.DataFrame(all_features, columns=columns)
    output_df.to_csv(output_filepath, index=False)
    print(f"Successfully saved {output_filepath.name}")




def main():
    """
    Main function scans folders and calls the processing function for each file.
    """

    input_folder = Path("input_data")
    output_folder = Path("training_data")
    
    config = {
        "lookback_long": 40,
        "lookback_short": 5,
        "profit_threshold": 1.0,
    }


    if not input_folder.exists():
        print(f"Error: Input folder '{input_folder}' not found. Please create it and add your CSV files.")
        return
    output_folder.mkdir(exist_ok=True)


    csv_files = list(input_folder.glob("*.csv"))
    if not csv_files:
        print(f"No .csv files found in '{input_folder}'.")
        return


    print(f"Found {len(csv_files)} CSV files to process.")
    
    for input_filepath in csv_files:
        output_filename = f"{input_filepath.stem}_processed.csv"
        output_filepath = output_folder / output_filename
        process_single_file(input_filepath, output_filepath, config)
    
    print("\nBatch processing complete!")




if __name__ == '__main__':
    main()

