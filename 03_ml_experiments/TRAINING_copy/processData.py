import pandas as pd
import ta
import os
from tqdm import tqdm
import multiprocessing as mp
import logging

# Setup logging to file
logging.basicConfig(filename='process_log.txt', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

DATA_DIR = 'data'                # Folder with original CSV files
OUTPUT_DIR = 'processed_data'    # Folder where processed files will go

features = ['Open', 'High', 'Low', 'Close', 'Volume', 'SMA_short', 'SMA_long', 'SMA_diff', 'ATR', 'ADX', 'RSI']
X = 10  # number of past bars

MIN_ROWS = 60  # Minimum raw rows to process a file (to skip tiny files)

def process_file(filepath):
    try:
        base_name = os.path.basename(filepath)
        logging.info(f"Processing {base_name}")
        print(f"Processing {base_name}")  # Still print to console for immediate feedback

        df = pd.read_csv(filepath)
        raw_rows = len(df)
        logging.info(f"Raw rows: {raw_rows}")
        if raw_rows < MIN_ROWS:
            logging.warning(f"Skipping {base_name}: too few rows ({raw_rows})")
            print(f"⚠️ Skipping {base_name}: too few rows ({raw_rows})")
            return None
        if df.empty:
            logging.warning(f"Empty or unreadable file: {base_name}")
            print(f"⚠️ Empty or unreadable file: {base_name}")
            return None

        # ---- PREPROCESSING ----
        df['Date'] = pd.to_datetime(df['Timestamp'], unit='s')
        df = df.set_index('Date')
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']]

        df['SMA_short'] = df['Close'].rolling(window=15).mean()
        df['SMA_long'] = df['Close'].rolling(window=45).mean()
        df['SMA_diff'] = df['SMA_short'] - df['SMA_long']

        df['ATR'] = ta.volatility.AverageTrueRange(
            df['High'], df['Low'], df['Close'], window=14
        ).average_true_range()
        df['ADX'] = ta.trend.ADXIndicator(
            df['High'], df['Low'], df['Close'], window=14
        ).adx()
        df['RSI'] = ta.momentum.RSIIndicator(
            df['Close'], window=14
        ).rsi()

        df['Signal'] = 0
        df.loc[df['SMA_short'] > df['SMA_long'], 'Signal'] = 1
        df.loc[df['SMA_short'] < df['SMA_long'], 'Signal'] = -1

        df.dropna(inplace=True)
        rows_after_dropna = len(df)
        logging.info(f"After dropna: {rows_after_dropna} rows")
        print(f"After dropna: {rows_after_dropna} rows")

        if rows_after_dropna < X + 1:  # Need at least X lags + current
            logging.warning(f"Skipping {base_name}: insufficient rows after dropna ({rows_after_dropna})")
            print(f"⚠️ Skipping {base_name}: insufficient rows after dropna ({rows_after_dropna})")
            return None

        # ---- TRADE SCORE ----
        df['Trade_Score'] = 0.0
        in_position = False
        entry_price = 0.0
        entry_idx = None

        for idx, row in df.iterrows():
            if row['Signal'] == 1 and not in_position:
                in_position = True
                entry_price = row['Close']
                entry_idx = idx
            elif row['Signal'] == -1 and in_position:
                exit_price = row['Close']
                profit = (exit_price - entry_price) / entry_price
                df.at[entry_idx, 'Trade_Score'] = profit * 100
                in_position = False
                entry_price = 0.0
                entry_idx = None

        if in_position:
            exit_price = df['Close'].iloc[-1]
            profit = (exit_price - entry_price) / entry_price
            df.at[entry_idx, 'Trade_Score'] = profit * 100

        trade_df = df[df['Trade_Score'] != 0].copy()
        num_trades = len(trade_df)
        logging.info(f"{base_name} → {num_trades} trades found")
        print(f"{base_name} → {num_trades} trades found")

        # ---- FLATTEN TRADES ----
        trade_rows = []
        for i, (idx, row) in enumerate(trade_df.iterrows()):
            past_loc = df.index.get_loc(idx) - X
            if past_loc < 0:
                logging.warning(f"Skipping trade {i} in {base_name}: insufficient past bars")
                continue

            past_data = df.iloc[past_loc: df.index.get_loc(idx)]
            flat_dict = {}

            for lag in range(1, X + 1):
                lag_row = past_data.iloc[-lag]
                for feat in features:
                    flat_dict[f'{feat}_lag{lag}'] = lag_row[feat]

            for feat in features:
                flat_dict[feat] = row[feat]

            flat_dict['Trade_ID'] = i
            flat_dict['Entry_Date'] = idx
            flat_dict['Signal'] = row['Signal']
            flat_dict['Trade_Score'] = row['Trade_Score']

            trade_rows.append(flat_dict)

        grouped_df = pd.DataFrame(trade_rows)
        num_flattened = len(grouped_df)

        if grouped_df.empty:
            logging.warning(f"No valid trades found in {base_name}. Skipping file.")
            print(f"⚠️ No valid trades found in {base_name}. Skipping file.")
            return None

        # ---- WRITE TO PROCESSED_DATA FOLDER ----
        out_base = os.path.splitext(base_name)[0]
        out_path = os.path.join(OUTPUT_DIR, f"{out_base}_trade_data.csv")
        grouped_df.to_csv(out_path, index=False)
        logging.info(f"Saved {out_path} with {num_flattened} trades")
        print(f"Saved {out_path} with {num_flattened} trades")

        return None

    except Exception as e:
        err_msg = f"{base_name}: {str(e)}"
        logging.error(err_msg)
        print(f"Error in {base_name}: {str(e)}")
        return err_msg

if __name__ == "__main__":
    # Create output folder if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files = [
        os.path.join(DATA_DIR, f)
        for f in os.listdir(DATA_DIR)
        if f.endswith('.csv')
    ]

    logging.info(f"Found {len(files)} files to process")
    print(f"Found {len(files)} files to process")
    print("Saving to:", os.path.abspath(OUTPUT_DIR))

    # Use multiprocessing to parallelize (adjust processes based on your CPU)
    num_workers = min(mp.cpu_count(), 8)  # Limit to 8 workers to avoid memory issues
    with mp.Pool(processes=num_workers) as pool:
        errors = list(tqdm(pool.imap(process_file, files), total=len(files), desc="Processing files"))

    errors = [e for e in errors if e]  # Filter None
    if errors:
        logging.info("\nErrors encountered:")
        print("\nErrors encountered:")
        for e in errors:
            logging.info(e)
            print(e)
    else:
        logging.info("\nAll files processed successfully.")
