"""
Universal Neural Network Trainer for Feature Comparison
========================================================
This trainer automatically adapts to different input feature dimensions
and can be called programmatically for batch comparisons.

Usage:
    python universal_trainer.py --data-dir training_data_experiments/hlmm --output-dir model_output/hlmm
    python universal_trainer.py --compare-all  # Train all strategies and compare
"""

import os
import sys
import time
import math
import json
import argparse
import logging
import warnings
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

import numpy as np
import pandas as pd
import joblib
import matplotlib.pyplot as plt

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from tqdm.auto import tqdm

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    f1_score, precision_score, recall_score, roc_auc_score,
    precision_recall_curve, auc, confusion_matrix, classification_report,
    average_precision_score
)

warnings.filterwarnings("ignore")
plt.rcParams["figure.dpi"] = 120

# ============================================================================
# CONFIGURATION
# ============================================================================

# Device selection
DEVICE = torch.device("cpu")
try:
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        DEVICE = torch.device("mps")
    elif torch.cuda.is_available():
        DEVICE = torch.device("cuda")
except Exception:
    pass

# DataLoader settings
if DEVICE.type == "cuda":
    NUM_WORKERS = min(4, max(0, (os.cpu_count() or 1) - 1))
    PIN_MEMORY = True
elif DEVICE.type == "mps":
    NUM_WORKERS = 0
    PIN_MEMORY = False
else:
    NUM_WORKERS = 0
    PIN_MEMORY = False

RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)
torch.manual_seed(RANDOM_STATE)

# Default hyperparameters
DEFAULT_HYPER = {
    "hidden_dim": 64,
    "dropout": 0.2,
    "batch_size": 256,
    "lr": 3e-4,
    "weight_decay": 1e-5,
    "n_epochs": 100,
    "patience": 15,
    "grad_clip": 1.0,
}

# Logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATASET & MODEL
# ============================================================================

class TradingDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = X.astype(np.float32)
        self.y = y.astype(np.float32).reshape(-1, 1)
    
    def __len__(self):
        return len(self.y)
    
    def __getitem__(self, idx):
        return torch.from_numpy(self.X[idx]), torch.from_numpy(self.y[idx])


class AdaptiveNet(nn.Module):
    """Network that adapts to input dimension."""
    def __init__(self, input_dim: int, hidden_dim: int = 64, dropout: float = 0.2):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.bn1 = nn.BatchNorm1d(hidden_dim)
        self.act1 = nn.ReLU()
        self.drop1 = nn.Dropout(dropout)
        
        self.fc2 = nn.Linear(hidden_dim, hidden_dim // 2)
        self.bn2 = nn.BatchNorm1d(hidden_dim // 2)
        self.act2 = nn.ReLU()
        self.drop2 = nn.Dropout(dropout)
        
        self.out = nn.Linear(hidden_dim // 2, 1)
    
    def forward(self, x):
        x = self.fc1(x)
        x = self.bn1(x)
        x = self.act1(x)
        x = self.drop1(x)
        
        x = self.fc2(x)
        x = self.bn2(x)
        x = self.act2(x)
        x = self.drop2(x)
        
        return self.out(x)


# ============================================================================
# TRAINING FUNCTIONS
# ============================================================================

def load_data(data_dir: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Load and concatenate all CSVs in directory."""
    csv_files = sorted(data_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files in {data_dir}")
    
    dfs = []
    for f in csv_files:
        df = pd.read_csv(f)
        dfs.append(df)
    
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.dropna().reset_index(drop=True)
    
    # Assume last column is classification
    X = combined.iloc[:, :-1].values
    y = combined.iloc[:, -1].values
    
    return X, y


def train_epoch(model, loader, optimizer, loss_fn, device, grad_clip=1.0):
    """Train for one epoch."""
    model.train()
    running_loss = 0.0
    total = 0
    
    for xb, yb in loader:
        xb = xb.to(device)
        yb = yb.to(device)
        
        optimizer.zero_grad()
        logits = model(xb)
        loss = loss_fn(logits, yb)
        loss.backward()
        
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=grad_clip)
        optimizer.step()
        
        running_loss += loss.item() * xb.size(0)
        total += xb.size(0)
    
    return running_loss / max(1, total)


@torch.no_grad()
def evaluate(model, loader, device, loss_fn):
    """Evaluate model."""
    model.eval()
    ys, probs, losses = [], [], []
    
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        logits = model(xb)
        loss = loss_fn(logits, yb)
        
        losses.append(loss.item())
        probs.append(torch.sigmoid(logits).cpu().numpy().ravel())
        ys.append(yb.cpu().numpy().ravel())
    
    y_true = np.concatenate(ys)
    y_prob = np.concatenate(probs)
    val_loss = np.mean(losses)
    
    # Find best threshold
    precision_vals, recall_vals, thresholds = precision_recall_curve(y_true, y_prob)
    
    if len(thresholds) > 0:
        prec_at_thresh = precision_vals[1:]
        rec_at_thresh = recall_vals[1:]
        f1s = 2 * (prec_at_thresh * rec_at_thresh) / (prec_at_thresh + rec_at_thresh + 1e-12)
        best_idx = int(np.nanargmax(f1s))
        best_thresh = float(thresholds[best_idx])
        best_f1 = float(f1s[best_idx])
        best_prec = float(prec_at_thresh[best_idx])
        best_rec = float(rec_at_thresh[best_idx])
    else:
        best_thresh = 0.5
        y_pred = (y_prob >= 0.5).astype(int)
        best_prec = precision_score(y_true, y_pred, zero_division=0)
        best_rec = recall_score(y_true, y_pred, zero_division=0)
        best_f1 = f1_score(y_true, y_pred, zero_division=0)
    
    aucroc = roc_auc_score(y_true, y_prob)
    auprc = average_precision_score(y_true, y_prob)
    
    return {
        "val_loss": val_loss,
        "best_f1": best_f1,
        "best_thresh": best_thresh,
        "precision": best_prec,
        "recall": best_rec,
        "aucroc": aucroc,
        "auprc": auprc,
        "y_true": y_true,
        "y_prob": y_prob
    }


def train_model(data_dir: Path, output_dir: Path, hyper: Optional[Dict] = None) -> Dict:
    """Train a model on data from data_dir and save to output_dir."""
    
    if hyper is None:
        hyper = DEFAULT_HYPER.copy()
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Loading data from {data_dir}")
    X, y = load_data(data_dir)
    
    input_dim = X.shape[1]
    logger.info(f"Data shape: {X.shape}, Input features: {input_dim}")
    
    unique, counts = np.unique(y, return_counts=True)
    class_dist = dict(zip(unique, counts))
    logger.info(f"Class distribution: {class_dist}")
    
    # Scale features
    scaler = StandardScaler()
    X = scaler.fit_transform(X)
    joblib.dump(scaler, output_dir / "scaler.pkl")
    
    # Train/val split
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=RANDOM_STATE
    )
    
    # Create dataloaders
    train_dataset = TradingDataset(X_train, y_train)
    val_dataset = TradingDataset(X_val, y_val)
    
    train_loader = DataLoader(
        train_dataset, batch_size=hyper["batch_size"], 
        shuffle=True, num_workers=NUM_WORKERS, pin_memory=PIN_MEMORY
    )
    val_loader = DataLoader(
        val_dataset, batch_size=1024, shuffle=False,
        num_workers=NUM_WORKERS, pin_memory=PIN_MEMORY
    )
    
    # Build model
    model = AdaptiveNet(
        input_dim=input_dim,
        hidden_dim=hyper["hidden_dim"],
        dropout=hyper["dropout"]
    ).to(DEVICE)
    
    # Initialize output bias to log-odds
    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    if pos > 0 and neg > 0:
        bias = math.log((pos + 1e-8) / (neg + 1e-8))
        model.out.bias.data = torch.tensor([bias], dtype=torch.float32).to(DEVICE)
    
    # Optimizer and loss
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=hyper["lr"],
        weight_decay=hyper["weight_decay"]
    )
    loss_fn = nn.BCEWithLogitsLoss()
    
    # Training loop
    history = {
        "train_loss": [], "val_loss": [], "val_f1": [],
        "val_auc": [], "val_auprc": []
    }
    
    best_val_loss = float('inf')
    best_epoch = -1
    epochs_no_improve = 0
    
    start_time = time.time()
    
    for epoch in range(hyper["n_epochs"]):
        train_loss = train_epoch(model, train_loader, optimizer, loss_fn, DEVICE, hyper["grad_clip"])
        val_results = evaluate(model, val_loader, DEVICE, loss_fn)
        
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_results["val_loss"])
        history["val_f1"].append(val_results["best_f1"])
        history["val_auc"].append(val_results["aucroc"])
        history["val_auprc"].append(val_results["auprc"])
        
        logger.info(
            f"Epoch {epoch+1}/{hyper['n_epochs']} | "
            f"train_loss={train_loss:.6f} | val_loss={val_results['val_loss']:.6f} | "
            f"val_f1={val_results['best_f1']:.4f} | val_auc={val_results['aucroc']:.4f}"
        )
        
        # Save best model
        if val_results["val_loss"] < best_val_loss - 1e-6:
            best_val_loss = val_results["val_loss"]
            best_epoch = epoch
            epochs_no_improve = 0
            torch.save(model.state_dict(), output_dir / "best_model.pth")
        else:
            epochs_no_improve += 1
        
        if epochs_no_improve >= hyper["patience"]:
            logger.info(f"Early stopping at epoch {epoch+1}")
            break
    
    train_time = time.time() - start_time
    
    # Load best model and final eval
    model.load_state_dict(torch.load(output_dir / "best_model.pth"))
    final_results = evaluate(model, val_loader, DEVICE, loss_fn)
    
    # Save history
    pd.DataFrame(history).to_csv(output_dir / "training_history.csv", index=False)
    
    # Save classification report
    y_pred = (final_results["y_prob"] >= final_results["best_thresh"]).astype(int)
    with open(output_dir / "classification_report.txt", "w") as f:
        f.write(classification_report(final_results["y_true"], y_pred, zero_division=0))
    
    # Plot confusion matrix
    cm = confusion_matrix(final_results["y_true"], y_pred)
    fig, ax = plt.subplots(figsize=(5, 4))
    im = ax.imshow(cm, interpolation="nearest", cmap="viridis")
    ax.set_title(f"Confusion Matrix (th={final_results['best_thresh']:.3f})")
    ax.set_xlabel("Pred")
    ax.set_ylabel("True")
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, cm[i, j], ha="center", va="center", color="white")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(output_dir / "confusion_matrix.png")
    plt.close()
    
    # Plot training curves
    epochs_arr = np.arange(1, len(history["train_loss"]) + 1)
    
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    
    # Loss
    axes[0, 0].plot(epochs_arr, history["train_loss"], label="train", marker="o")
    axes[0, 0].plot(epochs_arr, history["val_loss"], label="val", marker="o")
    axes[0, 0].set_xlabel("Epoch")
    axes[0, 0].set_ylabel("Loss")
    axes[0, 0].set_title("Training vs Validation Loss")
    axes[0, 0].legend()
    
    # F1
    axes[0, 1].plot(epochs_arr, history["val_f1"], marker="o", color="green")
    axes[0, 1].set_xlabel("Epoch")
    axes[0, 1].set_ylabel("F1 Score")
    axes[0, 1].set_title("Validation F1")
    
    # AUC
    axes[1, 0].plot(epochs_arr, history["val_auc"], marker="o", color="orange")
    axes[1, 0].set_xlabel("Epoch")
    axes[1, 0].set_ylabel("AUC-ROC")
    axes[1, 0].set_title("Validation AUC-ROC")
    
    # AUPRC
    axes[1, 1].plot(epochs_arr, history["val_auprc"], marker="o", color="red")
    axes[1, 1].set_xlabel("Epoch")
    axes[1, 1].set_ylabel("AUC-PRC")
    axes[1, 1].set_title("Validation AUC-PRC")
    
    fig.tight_layout()
    fig.savefig(output_dir / "training_curves.png")
    plt.close()
    
    # Summary
    summary = {
        "input_features": input_dim,
        "total_examples": len(X),
        "train_examples": len(X_train),
        "val_examples": len(X_val),
        "class_distribution": class_dist,
        "best_epoch": int(best_epoch) + 1,
        "best_val_loss": float(best_val_loss),
        "final_val_f1": float(final_results["best_f1"]),
        "final_best_thresh": float(final_results["best_thresh"]),
        "final_val_auc": float(final_results["aucroc"]),
        "final_val_auprc": float(final_results["auprc"]),
        "final_precision": float(final_results["precision"]),
        "final_recall": float(final_results["recall"]),
        "train_time_sec": float(train_time),
        "hyperparameters": hyper
    }
    
    with open(output_dir / "run_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    logger.info(f"Training complete. Results saved to {output_dir}")
    
    return summary


# ============================================================================
# COMPARISON MODE
# ============================================================================

def compare_all_strategies(base_dir: Path = Path("training_data_experiments")):
    """Train and compare all feature strategies."""
    
    if not base_dir.exists():
        logger.error(f"Base directory {base_dir} not found")
        return
    
    strategies = [d for d in base_dir.iterdir() if d.is_dir()]
    
    if not strategies:
        logger.error(f"No strategy directories found in {base_dir}")
        return
    
    logger.info(f"Found {len(strategies)} strategies to compare")
    
    results = []
    
    for strategy_dir in strategies:
        strategy_name = strategy_dir.name
        output_dir = Path("model_output") / f"comparison_{strategy_name}"
        
        logger.info(f"\n{'='*70}")
        logger.info(f"Training strategy: {strategy_name}")
        logger.info(f"{'='*70}")
        
        try:
            summary = train_model(strategy_dir, output_dir)
            summary["strategy"] = strategy_name
            summary["status"] = "success"
            results.append(summary)
        except Exception as e:
            logger.error(f"Error training {strategy_name}: {e}")
            results.append({
                "strategy": strategy_name,
                "status": "failed",
                "error": str(e)
            })
    
    # Create comparison table
    comparison_df = pd.DataFrame(results)
    
    # Sort by AUC
    if "final_val_auc" in comparison_df.columns:
        comparison_df = comparison_df.sort_values("final_val_auc", ascending=False)
    
    # Save
    comparison_path = Path("model_output") / "strategy_comparison.csv"
    comparison_df.to_csv(comparison_path, index=False)
    
    logger.info(f"\n{'='*70}")
    logger.info("COMPARISON RESULTS")
    logger.info(f"{'='*70}\n")
    
    # Display key metrics
    display_cols = [
        "strategy", "input_features", "final_val_auc", "final_val_auprc",
        "final_val_f1", "final_precision", "final_recall", "status"
    ]
    display_cols = [c for c in display_cols if c in comparison_df.columns]
    
    print(comparison_df[display_cols].to_string(index=False))
    print(f"\nFull results saved to: {comparison_path}")
    
    # Create comparison visualization
    if len(results) > 1 and "final_val_auc" in comparison_df.columns:
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        
        strategies_list = comparison_df["strategy"].tolist()
        
        # AUC-ROC
        axes[0].barh(strategies_list, comparison_df["final_val_auc"])
        axes[0].set_xlabel("AUC-ROC")
        axes[0].set_title("Model Performance: AUC-ROC")
        axes[0].axvline(0.5, color='red', linestyle='--', alpha=0.5, label='Random')
        axes[0].legend()
        
        # AUC-PRC
        if "final_val_auprc" in comparison_df.columns:
            axes[1].barh(strategies_list, comparison_df["final_val_auprc"])
            axes[1].set_xlabel("AUC-PRC")
            axes[1].set_title("Model Performance: AUC-PRC")
        
        # F1 Score
        axes[2].barh(strategies_list, comparison_df["final_val_f1"])
        axes[2].set_xlabel("F1 Score")
        axes[2].set_title("Model Performance: F1 Score")
        
        fig.tight_layout()
        fig.savefig(Path("model_output") / "strategy_comparison.png", dpi=150)
        plt.close()
        
        logger.info("Comparison visualization saved")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Universal Feature Strategy Trainer")
    parser.add_argument("--data-dir", type=Path, help="Directory containing training CSVs")
    parser.add_argument("--output-dir", type=Path, help="Output directory for model and results")
    parser.add_argument("--compare-all", action="store_true", help="Train and compare all strategies")
    parser.add_argument("--hidden-dim", type=int, default=64, help="Hidden layer dimension")
    parser.add_argument("--dropout", type=float, default=0.2, help="Dropout rate")
    parser.add_argument("--epochs", type=int, default=100, help="Maximum epochs")
    parser.add_argument("--patience", type=int, default=15, help="Early stopping patience")
    
    args = parser.parse_args()
    
    if args.compare_all:
        compare_all_strategies()
    elif args.data_dir and args.output_dir:
        hyper = {
            "hidden_dim": args.hidden_dim,
            "dropout": args.dropout,
            "batch_size": 256,
            "lr": 3e-4,
            "weight_decay": 1e-5,
            "n_epochs": args.epochs,
            "patience": args.patience,
            "grad_clip": 1.0,
        }
        train_model(args.data_dir, args.output_dir, hyper)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()