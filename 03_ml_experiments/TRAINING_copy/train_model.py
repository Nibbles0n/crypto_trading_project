import argparse
import os
import glob
import json
import joblib
from tqdm import tqdm
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import SGDRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.utils import shuffle
from sklearn.base import clone
import matplotlib.pyplot as plt

# Columns to always ignore if present
IGNORE_COLS = {"Trade_ID", "Entry_Date", "Signal"}

def prepare_features(df: pd.DataFrame, target_col: str, feature_list=None):
    """Drop ignored + target columns, keep only numeric features in the same order."""
    drop_cols = set(IGNORE_COLS)
    if target_col in df.columns:
        drop_cols.add(target_col)

    X_df = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore")
    X_num = X_df.select_dtypes(include=[np.number]).copy()

    # Ensure feature alignment across multiple files
    if feature_list is None:
        feature_list = list(X_num.columns)
    for col in feature_list:
        if col not in X_num.columns:
            X_num[col] = 0.0
    X_num = X_num[feature_list]

    y = df[target_col].values if target_col in df.columns else None
    return X_num.values.astype(float), y, feature_list

def plot_predictions_vs_actuals(y_true, y_pred, save_path):
    plt.figure(figsize=(8, 6))
    plt.scatter(y_true, y_pred, alpha=0.3)
    plt.xlabel("Actual Values")
    plt.ylabel("Predicted Values")
    plt.title("Predictions vs Actuals")
    plt.savefig(save_path)
    plt.close()

def plot_feature_importance(features, importances, save_path):
    sorted_idx = np.argsort(np.abs(importances))[::-1]
    plt.figure(figsize=(10, 8))
    plt.bar(range(len(importances)), np.abs(importances)[sorted_idx])
    plt.xticks(range(len(importances)), np.array(features)[sorted_idx], rotation=90)
    plt.xlabel("Features")
    plt.ylabel("Absolute Coefficient")
    plt.title("Feature Importance")
    plt.tight_layout()
    plt.savefig(save_path)
    plt.close()

def plot_learning_curves(sizes, train_errors, val_errors, save_path):
    plt.figure(figsize=(8, 6))
    plt.plot(sizes, train_errors, label="Train MSE")
    plt.plot(sizes, val_errors, label="Validation MSE")
    plt.xlabel("Training Examples")
    plt.ylabel("MSE")
    plt.title("Learning Curves")
    plt.legend()
    plt.savefig(save_path)
    plt.close()

def get_learning_curves(X_train_s, y_train, X_val_s, y_val, train_sizes=np.linspace(0.1, 1.0, 10)):
    train_errors = []
    val_errors = []
    sizes = []
    model_cls = lambda: SGDRegressor(random_state=42, max_iter=1000, tol=1e-3)
    for frac in train_sizes:
        size = int(len(X_train_s) * frac)
        if size == 0:
            continue
        X_sub = X_train_s[:size]
        y_sub = y_train[:size]
        model = model_cls()
        model.fit(X_sub, y_sub)
        pred_train = model.predict(X_sub)
        pred_val = model.predict(X_val_s)
        train_errors.append(mean_squared_error(y_sub, pred_train))
        val_errors.append(mean_squared_error(y_val, pred_val))
        sizes.append(size)
    return sizes, train_errors, val_errors

def train_with_early_stopping(X_train_s, y_train, X_val_s, y_val, out_dir):
    batch_size = 512
    patience = 10
    min_delta = 1e-3
    max_epochs = 100
    
    # Initialize model with standard parameters
    model = SGDRegressor(
        random_state=42,
        max_iter=1000,
        tol=1e-3,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=5
    )
    
    print("Starting model training...")
    
    # Train the model
    try:
        model.fit(X_train_s, y_train)
        print("Model training completed successfully.")
        return model
    except Exception as e:
        print(f"Error during model training: {str(e)}")
        # If training fails, try a simpler model
        try:
            print("Trying with simpler model...")
            model = SGDRegressor(max_iter=100, random_state=42)
            model.fit(X_train_s, y_train)
            return model
        except Exception as e2:
            print(f"Failed to train model: {str(e2)}")
            return None

def train_on_folder(data_dir, pattern, target_col, out_dir, test_size):
    # Create output directory if it doesn't exist
    os.makedirs(out_dir, exist_ok=True)
    
    files = sorted(glob.glob(os.path.join(data_dir, pattern)))
    if not files:
        print("❌ No files found matching pattern.")
        return

    print(f"Found {len(files)} files. Starting processing...")
    print(f"Output will be saved to: {os.path.abspath(out_dir)}")
    dfs = []
    
    for i, f in enumerate(tqdm(files, desc="Loading files"), 1):
        file_name = os.path.basename(f)
        try:
            # Skip very small files (likely empty or corrupted)
            file_size = os.path.getsize(f)
            if file_size < 10:
                print(f"⚠️  [{i}/{len(files)}] Skipping small/empty file: {file_name}")
                continue
                
            # Try to read the CSV
            try:
                df = pd.read_csv(f)
            except Exception as e:
                print(f"⚠️  [{i}/{len(files)}] Error reading {file_name}: {str(e)}")
                continue
                
            # Check if dataframe is empty
            if df.empty:
                print(f"⚠️  [{i}/{len(files)}] Empty dataframe in file: {file_name}")
                continue
                
            # Check if target column exists
            if target_col not in df.columns:
                print(f"⚠️  [{i}/{len(files)}] Target column '{target_col}' not found in {file_name}")
                continue
                
            dfs.append(df)
            print(f"✅  [{i}/{len(files)}] Loaded {file_name} ({len(df)} rows, {len(df.columns)} cols)")
            
        except Exception as e:
            print(f"❌  [{i}/{len(files)}] Unexpected error with {file_name}: {str(e)}")
            continue
            
    if not dfs:
        print("❌ No valid data found in any files.")
        return
        
    print(f"\nSuccessfully loaded {len(dfs)}/{len(files)} files. Combining data...")

    big_df = pd.concat(dfs, ignore_index=True)

    # Sort by timestamp if possible
    timestamp_col = "Entry_Date"
    if timestamp_col in big_df.columns:
        big_df[timestamp_col] = pd.to_datetime(big_df[timestamp_col], errors='coerce')
        big_df = big_df.dropna(subset=[timestamp_col]).sort_values(timestamp_col).reset_index(drop=True)
    else:
        print(f"No '{timestamp_col}' column found; data not sorted by time.")

    # Get global feature list
    _, _, feature_list = prepare_features(big_df, target_col)

    # Split data time-based
    split_idx = int(len(big_df) * (1 - test_size))
    df_train = big_df.iloc[:split_idx]
    df_val = big_df.iloc[split_idx:]

    X_train, y_train, _ = prepare_features(df_train, target_col, feature_list)
    X_val, y_val, _ = prepare_features(df_val, target_col, feature_list)

    if y_train is None or len(y_train) == 0:
        print("No training data with target.")
        return
    if test_size > 0 and (y_val is None or len(y_val) == 0):
        print("No validation data; skipping evaluation.")
        test_size = 0  # Treat as no val

    scaler = StandardScaler()
    scaler.fit(X_train)
    X_train_s = scaler.transform(X_train)
    if test_size > 0:
        X_val_s = scaler.transform(X_val)
    else:
        X_val_s = None
        y_val = None

    # Train model
    model = train_with_early_stopping(X_train_s, y_train, X_val_s, y_val, out_dir)
    
    if model is None:
        print("❌ Model training failed. Exiting...")
        return
        
    # Evaluation on training set
    try:
        pred_train = model.predict(X_train_s)
        train_mse = mean_squared_error(y_train, pred_train)
        print(f"\nTraining Set Metrics:")
        print(f"MSE: {train_mse:.4f}")
        print(f"MAE: {mean_absolute_error(y_train, pred_train):.4f}")
        print(f"R²: {r2_score(y_train, pred_train):.4f}")
    except Exception as e:
        print(f"Error evaluating on training set: {str(e)}")

    # Evaluation on validation set if available
    if test_size > 0 and X_val_s is not None and len(X_val_s) > 0:
        try:
            pred_val = model.predict(X_val_s)
            mse = mean_squared_error(y_val, pred_val)
            mae = mean_absolute_error(y_val, pred_val)
            r2 = r2_score(y_val, pred_val)
            
            print("\nValidation Set Metrics:")
            print(f"MSE: {mse:.4f}")
            print(f"MAE: {mae:.4f}")
            print(f"R²: {r2:.4f}")
            
            # Plot predictions vs actuals
            plt.figure(figsize=(10, 6))
            plt.scatter(y_val, pred_val, alpha=0.3)
            plt.plot([y_val.min(), y_val.max()], [y_val.min(), y_val.max()], 'r--')
            plt.xlabel('Actual')
            plt.ylabel('Predicted')
            plt.title('Actual vs Predicted Values')
            plt.savefig(os.path.join(out_dir, 'predictions_vs_actuals.png'))
            plt.close()
            
        except Exception as e:
            print(f"Error evaluating on validation set: {str(e)}")

        print("\nEvaluation Metrics:")
        print(f"R²: {r2:.4f}")
        print(f"MAE: {mae:.4f}")
        print(f"MSE: {mse:.4f}")

        with open(os.path.join(out_dir, "metrics.txt"), "w") as f:
            f.write(f"R²: {r2:.4f}\n")
            f.write(f"MAE: {mae:.4f}\n")
            f.write(f"MSE: {mse:.4f}\n")

        plot_predictions_vs_actuals(y_val, pred_val, os.path.join(out_dir, "pred_vs_actual.png"))

    # Feature importance
    importances = model.coef_
    plot_feature_importance(feature_list, importances, os.path.join(out_dir, "feature_importance.png"))

    # Save artifacts
    os.makedirs(out_dir, exist_ok=True)
    joblib.dump(model, os.path.join(out_dir, "model.joblib"))
    joblib.dump(scaler, os.path.join(out_dir, "scaler.joblib"))
    with open(os.path.join(out_dir, "features.json"), "w") as f:
        json.dump(feature_list, f)

    print("\n✅ Training complete!")
    print(f"Samples trained: {len(y_train)}")
    if test_size > 0:
        print(f"Samples validated: {len(y_val)}")
    print(f"Saved model to {out_dir}/model.joblib")
    print(f"Saved scaler to {out_dir}/scaler.joblib")
    print(f"Saved feature list to {out_dir}/features.json")
    if test_size > 0:
        print(f"Saved metrics to {out_dir}/metrics.txt")
        print(f"Saved visualizations to {out_dir}/*.png")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", required=True, help="Folder with CSV files")
    ap.add_argument("--pattern", default="*_wide.csv", help="Glob pattern for CSV files (default: *_wide.csv)")
    ap.add_argument("--target", default="Trade_Score", help="Target column name (default: Trade_Score)")
    ap.add_argument("--out", required=True, help="Output directory for saved files")
    ap.add_argument("--test_size", default=0.2, type=float, help="Validation set size (default: 0.2)")
    args = ap.parse_args()

    train_on_folder(args.data_dir, args.pattern, args.target, args.out, args.test_size)