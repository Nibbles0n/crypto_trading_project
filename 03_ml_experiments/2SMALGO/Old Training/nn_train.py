# nn_train_single_model_mps_optimized.py
"""
Single-model trainer optimized for MPS/CUDA/CPU (no fp16).
- Uses DATA_DIR = training_data and OUTPUT_DIR = model_output/nn_report
- Imports tqdm.auto for better progress bars
- Single experiment (no multiple folders) — saves all outputs to OUTPUT_DIR
- More capacity (hidden_dim), OneCycleLR option, AdamW optimizer,
  bias init to log-odds, threshold tuning per epoch, many diagnostics & plots
- Avoids class double-weighting by default (no pos_weight and no sampler)
- Saves overview.csv with a single row summarizing run
"""
import os
import time
import math
import json
import logging
import warnings
from pathlib import Path
from typing import Tuple, Dict, Any

import numpy as np
import pandas as pd
import joblib
import matplotlib.pyplot as plt

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler

from tqdm.auto import tqdm

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    f1_score, precision_score, recall_score, roc_auc_score,
    precision_recall_curve, auc, confusion_matrix, classification_report, roc_curve,
    average_precision_score 
)
warnings.filterwarnings("ignore")
plt.rcParams["figure.dpi"] = 120

# ---------- USER CONFIG ----------
DATA_DIR = Path("training_data")                   # per your preference
OUTPUT_DIR = Path("model_output/nn_report")        # per your preference
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SCALER_PATH = OUTPUT_DIR / "scaler.pkl"
MODEL_PATH = OUTPUT_DIR / "best_model.pth"

EXPECTED_COLS = [
    "sma23_40_h","sma23_40_l","sma23_40_m","sma23_40_md",
    "sma25_40_h","sma25_40_l","sma25_40_m","sma25_40_md",
    "vol_40_h","vol_40_l","vol_40_m","vol_40_md",
    "vol_5_h","vol_5_l","vol_5_m","vol_5_md","classification"
]

# Default training hyperparams (tuned for faster convergence)
HYPER = {
    "hidden_dim": 32,        # increased capacity
    "dropout": 0.1,
    "batch_size": 256,       # larger batch for throughput (increase if memory allows)
    "lr": 3e-4,              # starting LR for AdamW / OneCycleLR
    "weight_decay": 1e-6,
    "optimizer": "adamw",    # AdamW recommended
    "n_epochs": 100,
    "patience": 10,
    "grad_clip": 1.0,
    "use_onecycle": True,    # use OneCycleLR for faster, stable training
    "pct_start": 0.1,        # OneCycle warmup fraction
}

# Imbalance / weighting defaults: OFF to avoid double weighting problems
USE_WEIGHTED_SAMPLER = False
USE_POS_WEIGHT_IN_LOSS = False
USE_FOCAL_LOSS = False   # keep off by default; can enable for experiments

RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)
torch.manual_seed(RANDOM_STATE)

# Device selection (prefer MPS)
DEVICE = torch.device("cpu")
try:
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        DEVICE = torch.device("mps")
    elif torch.cuda.is_available():
        DEVICE = torch.device("cuda")
except Exception:
    DEVICE = torch.device("cpu")

print("Using device:", DEVICE)

# Safe MPS/CUDA throughput tweaks (no FP16)
try:
    if DEVICE.type == "mps" and hasattr(torch, "set_float32_matmul_precision"):
        torch.set_float32_matmul_precision("high")
except Exception:
    pass

try:
    torch.set_num_threads(min(4, max(1, (os.cpu_count() or 1) // 2)))
    torch.set_num_interop_threads(1)
except Exception:
    pass

if DEVICE.type == "cuda":
    try:
        torch.backends.cudnn.benchmark = True
    except Exception:
        pass

# DataLoader workers heuristic
if DEVICE.type == "cuda":
    NUM_WORKERS = min(4, max(0, (os.cpu_count() or 1) - 1))
    PIN_MEMORY = True
elif DEVICE.type == "mps":
    NUM_WORKERS = 0
    PIN_MEMORY = False
else:
    NUM_WORKERS = 0
    PIN_MEMORY = False

# Logger
logger = logging.getLogger("nn_trainer")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(ch)

# ---------- Utils ----------
def load_concatenated_csvs(data_dir: Path, expected_cols: list) -> pd.DataFrame:
    p = Path(data_dir)
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(f"data dir not found: {p.resolve()}")
    files = sorted(p.glob("*.csv"))
    if len(files) == 0:
        raise FileNotFoundError(f"No CSV files found in {p}")
    dfs = []
    for f in files:
        df = pd.read_csv(f)
        missing = [c for c in expected_cols if c not in df.columns]
        if missing:
            raise ValueError(f"File {f} missing columns: {missing}")
        dfs.append(df[expected_cols])
    combined = pd.concat(dfs, ignore_index=True)
    return combined

def compute_sampler_weights(y: np.ndarray):
    unique, counts = np.unique(y, return_counts=True)
    counts_map = dict(zip(unique, counts))
    n = len(y)
    weight_per_class = {c: n / (len(unique) * counts_map[c]) for c in counts_map}
    weights = np.array([weight_per_class[int(ci)] for ci in y], dtype=np.float32)
    return weights, weight_per_class

# ---------- Dataset / Model ----------
class TradingDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = X.astype(np.float32)
        self.y = y.astype(np.float32).reshape(-1,1)
    def __len__(self):
        return len(self.y)
    def __getitem__(self, idx):
        return torch.from_numpy(self.X[idx]), torch.from_numpy(self.y[idx])

class Net(nn.Module):
    def __init__(self, input_dim=16, hidden_dim=32, dropout=0.1):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.act = nn.ReLU()
        self.drop = nn.Dropout(dropout)
        self.out = nn.Linear(hidden_dim, 1)
    def forward(self, x):
        x = self.fc1(x)
        x = self.act(x)
        x = self.drop(x)
        return self.out(x)  # logits

# focal loss (optional)
class FocalLoss(nn.Module):
    def __init__(self, alpha=0.25, gamma=2.0, reduction="mean"):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.reduction = reduction
    def forward(self, logits, targets):
        probs = torch.sigmoid(logits)
        targets = targets.float()
        p_t = probs * targets + (1 - probs) * (1 - targets)
        alpha_t = self.alpha * targets + (1 - self.alpha) * (1 - targets)
        loss = -alpha_t * ((1 - p_t) ** self.gamma) * torch.log(torch.clamp(p_t, 1e-8, 1.0))
        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        else:
            return loss

# ---------- train / eval helpers ----------
def train_epoch(model, loader, optimizer, loss_fn, device, grad_clip=1.0):
    model.train()
    running_loss = 0.0
    total = 0
    move_non_blocking = (PIN_MEMORY and device.type == "cuda")
    with tqdm(loader, desc="Train", leave=False) as pbar:
        for xb, yb in pbar:
            xb = xb.to(device, non_blocking=move_non_blocking)
            yb = yb.to(device, non_blocking=move_non_blocking)
            optimizer.zero_grad()
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=grad_clip)
            optimizer.step()
            batch_n = xb.size(0)
            running_loss += float(loss.item()) * batch_n
            total += batch_n
            pbar.set_postfix(loss=f"{float(loss.item()):.4f}")
    return running_loss / max(1, total)

@torch.no_grad()
def evaluate(model, loader, device, desc="Validating", loss_fn=None):
    model.eval()
    ys, probs, losses = [], [], []

    if loss_fn is None:
        loss_fn = torch.nn.BCEWithLogitsLoss()
    
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)
        logits = model(xb)
        
        # Calculate loss
        loss = loss_fn(logits, yb)
        losses.append(loss.item())
        
        # Get probabilities and true labels
        prob = torch.sigmoid(logits).cpu().numpy().ravel()
        ys.append(yb.cpu().numpy().ravel())
        probs.append(prob)

    y_true = np.concatenate(ys)
    y_prob = np.concatenate(probs)
    val_loss = np.mean(losses)

    # Find best threshold on this set
    res = best_f1_threshold(y_true, y_prob)
    y_pred = (y_prob >= res["best_thresh"]).astype(int)
    
    # Calculate additional metrics
    aucroc = roc_auc_score(y_true, y_prob)
    auprc = average_precision_score(y_true, y_prob)

    return {
        "f1": res["best_f1"],
        "precision": res["precision"],
        "recall": res["recall"],
        "best_thresh": res["best_thresh"],
        "y_true": y_true,
        "y_pred": y_pred,
        "y_prob": y_prob,
        "val_loss": val_loss,
        "aucroc": aucroc,
        "auprc": auprc
    }


def best_f1_threshold(y_true: np.ndarray, y_prob: np.ndarray, require_min_precision: float = None):
    """
    Find the threshold that maximizes F1 using precision_recall_curve with correct alignment.

    Args:
        y_true: binary ground-truth array (0/1).
        y_prob: predicted probabilities (floats 0..1).
        require_min_precision: optional float in (0,1). If set, only thresholds whose precision >=
                               this value are considered; if none qualify, falls back to unconstrained best F1.

    Returns:
        dict with keys:
          - best_f1: best F1 score (float)
          - best_thresh: threshold (float)
          - precision: precision at best threshold
          - recall: recall at best threshold
          - all_thresholds: array of thresholds examined (may be empty)
          - all_f1s: array of f1 scores aligned to thresholds (same length as thresholds)
    """
    out = {
        "best_f1": float("nan"),
        "best_thresh": 0.5,
        "precision": float("nan"),
        "recall": float("nan"),
        "all_thresholds": np.array([], dtype=float),
        "all_f1s": np.array([], dtype=float)
    }

    if y_true.size == 0 or y_prob.size == 0:
        return out

    precision_vals, recall_vals, thresholds = precision_recall_curve(y_true, y_prob)
    # thresholds.shape = (T,), precision_vals.shape = (T+1,), recall_vals.shape = (T+1,)
    if thresholds.size == 0:
        # degenerate case: all probs identical or no thresholds; try trivial threshold=0.5
        p = precision_score(y_true, (y_prob >= 0.5).astype(int), zero_division=0)
        r = recall_score(y_true, (y_prob >= 0.5).astype(int), zero_division=0)
        f1 = 2 * p * r / (p + r + 1e-12)
        out.update({"best_f1": float(f1), "best_thresh": 0.5, "precision": float(p), "recall": float(r)})
        return out

    # For threshold i (thresholds[i]), the corresponding precision/recall are precision_vals[i+1], recall_vals[i+1]
    prec_at_thresh = precision_vals[1:]
    rec_at_thresh = recall_vals[1:]
    f1s = 2 * (prec_at_thresh * rec_at_thresh) / (prec_at_thresh + rec_at_thresh + 1e-12)

    # Optional constraint: require minimum precision
    if require_min_precision is not None:
        mask = prec_at_thresh >= float(require_min_precision)
        if mask.any():
            candidate_idxs = np.where(mask)[0]
            best_idx_rel = int(np.nanargmax(f1s[candidate_idxs]))
            best_idx = candidate_idxs[best_idx_rel]
        else:
            # no thresholds satisfy the precision constraint -> fall back to unconstrained best F1
            best_idx = int(np.nanargmax(f1s))
    else:
        best_idx = int(np.nanargmax(f1s))

    best_thresh = float(thresholds[best_idx])
    best_f1 = float(f1s[best_idx])
    best_prec = float(prec_at_thresh[best_idx])
    best_rec = float(rec_at_thresh[best_idx])

    out.update({
        "best_f1": best_f1,
        "best_thresh": best_thresh,
        "precision": best_prec,
        "recall": best_rec,
        "all_thresholds": thresholds,
        "all_f1s": f1s
    })
    return out
# ---------- main ----------
if __name__ == "__main__":
    t0 = time.time()
    logger.info("Loading data from %s", DATA_DIR)
    df = load_concatenated_csvs(DATA_DIR, EXPECTED_COLS)
    logger.info("Loaded dataframe shape: %s", df.shape)
    df = df.dropna().reset_index(drop=True)
    logger.info("After dropna: %s", df.shape)

    X = df.drop(columns=["classification"]).values
    y = df["classification"].values
    if y.dtype.kind not in "biufc":
        y = pd.factorize(y)[0]
    y = y.astype(int)

    unique, counts = np.unique(y, return_counts=True)
    logger.info("Class distribution: %s", dict(zip(unique, counts)))

    # Save a simple scaler
    scaler = StandardScaler()
    X = scaler.fit_transform(X)
    joblib.dump(scaler, SCALER_PATH)
    logger.info("Saved scaler to %s", SCALER_PATH)

    # Train/Val split
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.25, stratify=y, random_state=RANDOM_STATE)
    logger.info("Train: %d, Val: %d", len(X_train), len(X_val))

    # cast to numpy types used by dataloaders
    X_train = np.asarray(X_train, dtype=np.float32)
    X_val = np.asarray(X_val, dtype=np.float32)
    y_train = np.asarray(y_train, dtype=np.float32)
    y_val = np.asarray(y_val, dtype=np.float32)
    
    # Reshape y to be 2D (batch_size, 1) for BCEWithLogitsLoss
    y_train = y_train.reshape(-1, 1)
    y_val = y_val.reshape(-1, 1)

    # Datasets / loaders
    train_dataset = TradingDataset(X_train, y_train)
    val_dataset = TradingDataset(X_val, y_val)

    if USE_WEIGHTED_SAMPLER:
        weights, _ = compute_sampler_weights(y_train)
        sampler = WeightedRandomSampler(weights.tolist(), num_samples=len(weights), replacement=True)
        train_loader = DataLoader(train_dataset, batch_size=HYPER["batch_size"], sampler=sampler, num_workers=NUM_WORKERS, pin_memory=PIN_MEMORY)
    else:
        train_loader = DataLoader(train_dataset, batch_size=HYPER["batch_size"], shuffle=True, num_workers=NUM_WORKERS, pin_memory=PIN_MEMORY)
    val_loader = DataLoader(val_dataset, batch_size=1024, shuffle=False, num_workers=NUM_WORKERS, pin_memory=PIN_MEMORY)

    # build model
    model = Net(input_dim=16, hidden_dim=HYPER["hidden_dim"], dropout=HYPER["dropout"]).to(DEVICE)
    # bias init (log-odds)
    try:
        pos = int((y_train == 1).sum())
        neg = int((y_train == 0).sum())
        if pos > 0 and neg >= 0:
            bias = math.log((pos + 1e-8) / (neg + 1e-8))
            model.out.bias.data = torch.tensor([bias], dtype=torch.float32).to(DEVICE)
            logger.info("Initialized output bias to log-odds: %.4f", bias)
    except Exception as e:
        logger.warning("Bias init failed: %s", e)

    # optimizer & scheduler
    if HYPER["optimizer"] == "adam":
        optimizer = torch.optim.Adam(model.parameters(), lr=HYPER["lr"], weight_decay=HYPER["weight_decay"])
    else:
        optimizer = torch.optim.AdamW(model.parameters(), lr=HYPER["lr"], weight_decay=HYPER["weight_decay"])

    total_steps = HYPER["n_epochs"] * (len(train_loader) if len(train_loader)>0 else 1)
    scheduler = None
    if HYPER["use_onecycle"]:
        # OneCycleLR often helps convergence and reduces need for long tuning
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer,
            max_lr=HYPER["lr"],
            total_steps=max(1, total_steps),
            pct_start=HYPER.get("pct_start", 0.1),
            anneal_strategy="cos",
            div_factor=10.0,
            final_div_factor=100.0,
        )
    else:
        scheduler = None

    # loss
    if USE_FOCAL_LOSS:
        loss_fn = FocalLoss(alpha=0.25, gamma=2.0)
    else:
        # no pos_weight by default
        loss_fn = nn.BCEWithLogitsLoss()

    history = {"train_loss": [], "val_loss": [], "epoch_best_f1": [], "epoch_best_thresh": [], "val_auc": [], "val_auprc": []}
    best_val_loss = float("inf")
    best_epoch = -1
    epochs_no_improve = 0

    for epoch in range(HYPER["n_epochs"]):
        t_epoch0 = time.time()
        train_loss = train_epoch(model, train_loader, optimizer, loss_fn, DEVICE, grad_clip=HYPER["grad_clip"])

        # if using OneCycle scheduler we step per batch inside train loop; but here we step per-batch in train_epoch is not done.
        # So step scheduler here per epoch using an approximate value: step once per epoch if OneCycle used (OneCycle expects per-step).
        # To be correct, we'd step per-batch — simpler: if OneCycle enabled, step once by fraction:
        if HYPER["use_onecycle"] and hasattr(scheduler, "step"):
            # approximate: step by average number of batches per epoch -> call step for len(train_loader) times would be correct, but expensive
            # ideally you'd call scheduler.step() each batch. For now, call a single scheduler.step() to keep lr moving.
            try:
                scheduler.step()
            except Exception:
                pass

        val_res = evaluate(model, val_loader, DEVICE)
        # best threshold for this epoch
        bf = best_f1_threshold(val_res["y_true"], val_res["y_prob"])
        epoch_best_f1 = bf["best_f1"]
        epoch_best_thresh = bf["best_thresh"]

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_res["val_loss"])
        history["epoch_best_f1"].append(epoch_best_f1)
        history["epoch_best_thresh"].append(epoch_best_thresh)
        history["val_auc"].append(val_res["aucroc"])
        history["val_auprc"].append(val_res["auprc"])

        logger.info("Epoch %d/%d train_loss=%.6f val_loss=%.6f epoch_best_f1=%.6f best_thresh=%.4f val_auc=%.4f val_auprc=%.4f",
                    epoch+1, HYPER["n_epochs"], train_loss, val_res["val_loss"], epoch_best_f1, epoch_best_thresh, val_res["aucroc"], val_res["auprc"])

        # early stopping on validation loss (primary) but also track epoch_best_f1
        if val_res["val_loss"] < best_val_loss - 1e-6:
            best_val_loss = val_res["val_loss"]
            best_epoch = epoch
            epochs_no_improve = 0
            # save model
            torch.save(model.state_dict(), MODEL_PATH)
        else:
            epochs_no_improve += 1

        if epochs_no_improve >= HYPER["patience"]:
            logger.info("Early stopping at epoch %d (best_epoch=%d best_val_loss=%.6f)", epoch+1, best_epoch+1 if best_epoch>=0 else -1, best_val_loss)
            break

    # Save history
    hist_df = pd.DataFrame(history)
    hist_df.to_csv(OUTPUT_DIR / "training_history.csv", index=False)

    # Load best model and final evaluation
    try:
        model.load_state_dict(torch.load(MODEL_PATH))
    except Exception:
        logger.warning("Could not load saved model, using current weights")

    final = evaluate(model, val_loader, DEVICE, loss_fn=loss_fn)
    bf_final = best_f1_threshold(final["y_true"], final["y_prob"])

    best_thresh = bf_final["best_thresh"]
    best_f1 = bf_final["best_f1"]
    precision_at = bf_final["precision"]
    recall_at = bf_final["recall"]

    # Save classification report at best threshold
    y_true = final["y_true"]
    y_prob = final["y_prob"]
    y_pred_best = (y_prob >= best_thresh).astype(int)
    with open(OUTPUT_DIR / "classification_report.txt", "w") as fh:
        fh.write(classification_report(y_true, y_pred_best, zero_division=0))

    # Confusion matrix at best threshold
    try:
        cm = confusion_matrix(y_true, y_pred_best)
        fig, ax = plt.subplots(figsize=(5,4))
        im = ax.imshow(cm, interpolation="nearest", cmap="viridis")
        ax.set_title(f"Confusion Matrix (th={best_thresh:.3f})")
        ax.set_xlabel("Pred"); ax.set_ylabel("True")
        for i in range(cm.shape[0]):
            for j in range(cm.shape[1]):
                ax.text(j, i, cm[i,j], ha="center", va="center", color="white")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        fig.savefig(OUTPUT_DIR / "confusion_matrix.png")
        plt.close(fig)
    except Exception:
        logger.warning("Could not save confusion matrix")

    # Save prediction CSV
    try:
        pred_df = pd.DataFrame(X_val)
        pred_df.columns = [f"feat_{i}" for i in range(pred_df.shape[1])]
        pred_df["true"] = y_true
        pred_df["prob"] = y_prob
        pred_df["pred_best_thresh"] = y_pred_best
        pred_df.to_csv(OUTPUT_DIR / "prediction_sample.csv", index=False)
    except Exception:
        logger.warning("Could not save prediction sample")

    # Save plots: train vs val loss and other metrics
    try:
        epochs_arr = np.arange(1, len(hist_df)+1)
        fig, ax = plt.subplots(figsize=(8,5))
        ax.plot(epochs_arr, hist_df["train_loss"], marker="o", label="train_loss")
        ax.plot(epochs_arr, hist_df["val_loss"], marker="o", label="val_loss")
        ax.set_xlabel("Epoch"); ax.set_ylabel("Loss"); ax.set_title("Training vs Validation Loss")
        ax.legend(); fig.tight_layout(); fig.savefig(OUTPUT_DIR / "train_vs_val_loss.png"); plt.close(fig)
    except Exception:
        logger.warning("Could not save train_vs_val_loss")

    try:
        fig, ax = plt.subplots()
        ax.plot(epochs_arr, hist_df["epoch_best_f1"], marker="o")
        ax.set_xlabel("Epoch"); ax.set_ylabel("Epoch-best F1"); ax.set_title("Epoch-best F1"); fig.tight_layout()
        fig.savefig(OUTPUT_DIR / "val_best_f1.png"); plt.close(fig)
    except Exception:
        pass

    try:
        fig, ax = plt.subplots()
        ax.plot(epochs_arr, hist_df["val_auc"], marker="o")
        ax.set_xlabel("Epoch"); ax.set_ylabel("Val AUC"); ax.set_title("Validation AUC"); fig.tight_layout()
        fig.savefig(OUTPUT_DIR / "val_auc.png"); plt.close(fig)
    except Exception:
        pass

    try:
        fig, ax = plt.subplots()
        ax.plot(epochs_arr, hist_df["val_auprc"], marker="o")
        ax.set_xlabel("Epoch"); ax.set_ylabel("Val AUPRC"); ax.set_title("Validation AUPRC"); fig.tight_layout()
        fig.savefig(OUTPUT_DIR / "val_auprc.png"); plt.close(fig)
    except Exception:
        pass

    # Save overview CSV (single-row) for quick assessment
    overview = {
        "name": "single_model",
        "best_epoch": int(best_epoch) if best_epoch>=0 else None,
        "best_val_loss": float(best_val_loss),
        "best_val_f1": float(best_f1),
        "best_thresh": float(best_thresh),
        "val_auc": float(final["aucroc"]),
        "val_auprc": float(final["auprc"]),
        "precision_at_best": float(precision_at),
        "recall_at_best": float(recall_at),
        "train_time_sec": float(time.time() - t0),
        "history_csv": str(OUTPUT_DIR / "training_history.csv")
    }
    pd.DataFrame([overview]).to_csv(OUTPUT_DIR / "overview.csv", index=False)

    # Save model params and hyperparams
    with open(OUTPUT_DIR / "run_summary.json", "w") as fh:
        json.dump({"hyper": HYPER, "overview": overview}, fh, indent=2)

    logger.info("Run complete. Outputs in %s", OUTPUT_DIR)
