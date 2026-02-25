"""
Random Forest trainer for folder of trading CSVs
- Assumes all CSVs in `trading_data/` share the same header you provided.
- Concatenates all CSVs, computes explicit class weights proportional to inverse class frequency
  using the formula: weight[c] = total_samples / (n_classes * count_c)
  (this makes minority class get proportionally higher weight)
- Trains RandomForest with those weights, saves model and diagnostics to /mnt/data/rf_report
- Saves: model (.joblib), confusion matrix, ROC/PR curves, permutation importances CSV+plot,
  prediction sample CSV, and a summary_metrics.txt

Usage:
- Put this file somewhere (or run from repo root).
- Ensure your folder `trading_data/` is next to the script (or change DATA_DIR).
- Run: python rf_train_trading_data.py

Notes:
- For extremely large datasets you may want to increase memory or stream files in chunks.
- If you prefer sklearn's automatic balancing, set USE_AUTO_CLASS_WEIGHT=True to use `class_weight='balanced'`.
"""

import os
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.metrics import (confusion_matrix, classification_report, roc_curve, auc,
                             precision_recall_curve, accuracy_score, precision_score, recall_score, f1_score)
from sklearn.preprocessing import label_binarize
from sklearn.inspection import permutation_importance
import matplotlib.pyplot as plt
import joblib
import warnings
warnings.filterwarnings("ignore")

# ---------- USER CONFIG ----------
DATA_DIR = Path("training_data")   # folder containing many CSVs with the provided header
OUTPUT_DIR = Path("model_output/rf_report")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
USE_AUTO_CLASS_WEIGHT = False     # If True: use 'balanced' (sklearn), else compute explicit weights
N_ESTIMATORS = 200
CV_FOLDS = 5
RANDOM_STATE = 42
# ----------------------------------

# Expected columns (header from your message)
EXPECTED_COLS = [
    "sma23_40_h","sma23_40_l","sma23_40_m","sma23_40_md",
    "sma25_40_h","sma25_40_l","sma25_40_m","sma25_40_md",
    "vol_40_h","vol_40_l","vol_40_m","vol_40_md",
    "vol_5_h","vol_5_l","vol_5_m","vol_5_md","classification"
]

# 1) Load all CSVs
if not DATA_DIR.exists() or not DATA_DIR.is_dir():
    raise FileNotFoundError(f"Data directory not found: {DATA_DIR}")

csv_files = sorted(DATA_DIR.glob("*.csv"))
if len(csv_files) == 0:
    raise FileNotFoundError(f"No CSV files found in {DATA_DIR}")

print(f"Found {len(csv_files)} csv files. Reading and concatenating...")

df_list = []
for p in csv_files:
    try:
        d = pd.read_csv(p)
    except Exception as e:
        print(f"Warning: failed to read {p}: {e}")
        continue
    # Keep only expected columns (will raise later if classification missing)
    missing = [c for c in EXPECTED_COLS if c not in d.columns]
    if missing:
        raise ValueError(f"File {p} is missing expected columns: {missing}")
    df_list.append(d[EXPECTED_COLS])

if len(df_list) == 0:
    raise ValueError("No valid CSVs loaded.")

df = pd.concat(df_list, ignore_index=True)
print("Concatenated dataframe shape:", df.shape)

# 2) Basic cleaning
df = df.dropna().reset_index(drop=True)
print("After dropna shape:", df.shape)

# 3) Features / target
X = df.drop(columns=["classification"]).copy()
y = df["classification"].copy()

# Ensure y is integer labels
if y.dtype.kind not in "biufc":
    y = pd.factorize(y)[0]

unique, counts = np.unique(y, return_counts=True)
class_counts = dict(zip(unique, counts))
print("Class distribution:")
for k,v in class_counts.items():
    print(f"  class {k}: {v} samples")

n_samples = len(y)
n_classes = len(unique)

# 4) Compute class weights (if not using sklearn auto)
if USE_AUTO_CLASS_WEIGHT:
    class_weight = 'balanced'
    print("Using sklearn's automatic class_weight='balanced'.")
else:
    # weight[c] = total_samples / (n_classes * count_c)
    class_weight = {int(c): float(n_samples) / (n_classes * int(counts[i])) for i,c in enumerate(unique)}
    print("Using explicit class_weight computed from inverse frequency:")
    for c,w in class_weight.items():
        print(f"  class {c}: weight={w:.4f}")

# 5) Train/test split (stratified)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.25, stratify=y, random_state=RANDOM_STATE
)
print("Train/test sizes:", X_train.shape, X_test.shape)

# 6) Train RandomForest
rf = RandomForestClassifier(n_estimators=N_ESTIMATORS, random_state=RANDOM_STATE,
                            class_weight=class_weight, n_jobs=-1)
print("Training RandomForest...")
rf.fit(X_train, y_train)
joblib.dump(rf, OUTPUT_DIR / "random_forest_model.joblib")
print("Saved model to", OUTPUT_DIR / "random_forest_model.joblib")

# 7) Cross-validation (F1-macro) on whole dataset (light parallelism)
cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
cv_scores = cross_val_score(rf, X, y, cv=cv, scoring='f1_macro', n_jobs=1)
print(f"{CV_FOLDS}-fold CV F1-macro scores: {np.round(cv_scores,4)} mean={cv_scores.mean():.4f}")

# 8) Test-set predictions and metrics
y_pred = rf.predict(X_test)
if hasattr(rf, 'predict_proba'):
    y_proba = rf.predict_proba(X_test)
else:
    y_proba = None

acc = accuracy_score(y_test, y_pred)
prec = precision_score(y_test, y_pred, average='binary' if n_classes==2 else 'macro', zero_division=0)
rec = recall_score(y_test, y_pred, average='binary' if n_classes==2 else 'macro', zero_division=0)
f1 = f1_score(y_test, y_pred, average='binary' if n_classes==2 else 'macro', zero_division=0)
print(f"Test acc={acc:.4f} prec={prec:.4f} rec={rec:.4f} f1={f1:.4f}")

# Save classification report
report_text = classification_report(y_test, y_pred, zero_division=0)
with open(OUTPUT_DIR / 'classification_report.txt', 'w') as fh:
    fh.write(report_text)

# Confusion matrix plot
cm = confusion_matrix(y_test, y_pred)
fig, ax = plt.subplots(figsize=(5,4))
ax.imshow(cm, interpolation='nearest')
ax.set_title('Confusion Matrix')
ax.set_xlabel('Predicted')
ax.set_ylabel('True')
for i in range(cm.shape[0]):
    for j in range(cm.shape[1]):
        ax.text(j, i, cm[i,j], ha='center', va='center')
fig.tight_layout()
fig.savefig(OUTPUT_DIR / 'confusion_matrix.png')
plt.close(fig)

# ROC and PR if binary
if n_classes == 2 and y_proba is not None:
    fpr, tpr, _ = roc_curve(y_test, y_proba[:,1])
    roc_auc = auc(fpr, tpr)
    fig, ax = plt.subplots()
    ax.plot(fpr, tpr)
    ax.plot([0,1],[0,1], linestyle='--')
    ax.set_xlabel('FPR')
    ax.set_ylabel('TPR')
    ax.set_title(f'ROC (AUC={roc_auc:.3f})')
    fig.savefig(OUTPUT_DIR / 'roc_curve.png')
    plt.close(fig)

    precision_vals, recall_vals, _ = precision_recall_curve(y_test, y_proba[:,1])
    pr_auc = auc(recall_vals, precision_vals)
    fig, ax = plt.subplots()
    ax.plot(recall_vals, precision_vals)
    ax.set_xlabel('Recall')
    ax.set_ylabel('Precision')
    ax.set_title(f'Precision-Recall (AUC={pr_auc:.3f})')
    fig.savefig(OUTPUT_DIR / 'pr_curve.png')
    plt.close(fig)

# Feature importances (MDI)
feat_imp = pd.Series(rf.feature_importances_, index=X.columns).sort_values(ascending=False)
fig, ax = plt.subplots(figsize=(8,4))
ax.bar(range(len(feat_imp)), feat_imp.values)
ax.set_xticks(range(len(feat_imp)))
ax.set_xticklabels(feat_imp.index, rotation=45, ha='right')
ax.set_title('Feature importances (MDI)')
fig.tight_layout()
fig.savefig(OUTPUT_DIR / 'feature_importances.png')
plt.close(fig)

# Permutation importance (lighter)
perm = permutation_importance(rf, X_test, y_test, n_repeats=10, random_state=RANDOM_STATE, n_jobs=1)
perm_idx = perm.importances_mean.argsort()[::-1]
perm_df = pd.DataFrame({
    'feature': X.columns[perm_idx],
    'importance_mean': perm.importances_mean[perm_idx],
    'importance_std': perm.importances_std[perm_idx]
})
perm_df.to_csv(OUTPUT_DIR / 'permutation_importances.csv', index=False)
fig, ax = plt.subplots(figsize=(8,4))
ax.bar(range(len(perm_idx)), perm.importances_mean[perm_idx])
ax.set_xticks(range(len(perm_idx)))
ax.set_xticklabels(X.columns[perm_idx], rotation=45, ha='right')
ax.set_title('Permutation importances (test set)')
fig.tight_layout()
fig.savefig(OUTPUT_DIR / 'permutation_importances.png')
plt.close(fig)

# Save prediction sample
pred_sample = X_test.reset_index(drop=True).copy()
pred_sample['true'] = y_test.reset_index(drop=True)
pred_sample['pred'] = y_pred
if y_proba is not None:
    if y_proba.shape[1] == 2:
        pred_sample['proba_class_1'] = y_proba[:,1]
    else:
        for i in range(y_proba.shape[1]):
            pred_sample[f'proba_class_{i}'] = y_proba[:,i]
pred_sample.to_csv(OUTPUT_DIR / 'prediction_sample.csv', index=False)

# Save summary metrics
with open(OUTPUT_DIR / 'summary_metrics.txt', 'w') as f:
    f.write(f"n_samples: {n_samples}\n")
    f.write(f"class_counts: {class_counts}\n")
    f.write(f"test_acc: {acc:.6f}\n")
    f.write(f"test_precision: {prec:.6f}\n")
    f.write(f"test_recall: {rec:.6f}\n")
    f.write(f"test_f1: {f1:.6f}\n")
    f.write(f"cv_f1_macro_mean: {cv_scores.mean():.6f}\n")
    f.write(f"cv_f1_macro_std: {cv_scores.std():.6f}\n")

print("All outputs saved to:", OUTPUT_DIR)
print("Files:")
for p in sorted(OUTPUT_DIR.glob('*')):
    print('-', p.name)

# End of script
