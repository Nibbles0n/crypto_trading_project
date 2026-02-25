"""
XGBoost trainer for folder of trading CSVs with imbalance handling
- Reads all CSVs from `trading_data/` (must share header you provided)
- Options to handle class imbalance:
    * scale_pos_weight (recommended for XGBoost): sets ratio = n_neg / n_pos
    * sample weighting using inverse-frequency weights
    * resampling using SMOTE / SMOTEENN / RandomUnderSampler
    * combination of resampling + scale_pos_weight (configurable)
- Trains XGBoost (XGBClassifier) with early stopping and produces diagnostics identical to previous RF script
- Saves outputs to /mnt/data/xgb_report

Usage:
- Put your CSV files inside `trading_data/` next to this script (or change DATA_DIR)
- Install dependencies if needed: pip install xgboost imbalanced-learn scikit-learn pandas matplotlib joblib
- Run: python rf_train_xgboost_trading_data.py

Notes on imbalance handling (defaults chosen for ~1:10 imbalance):
- By default the script will compute scale_pos_weight = n_neg / n_pos and pass to XGBoost.
- Additionally, you can enable SMOTE to synthetically oversample the minority class before training.
  For extreme imbalance, a combined approach (SMOTE + scale_pos_weight) is often effective.
- The script prints class distribution and the chosen strategies.
"""

import os
from pathlib import Path
import pandas as pd
import numpy as np
import joblib
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.metrics import (confusion_matrix, classification_report, roc_curve, auc,
                             precision_recall_curve, accuracy_score, precision_score, recall_score, f1_score,
                             average_precision_score)
from sklearn.preprocessing import label_binarize
from sklearn.inspection import permutation_importance
import warnings
warnings.filterwarnings('ignore')

# Imbalance tools
try:
    from imblearn.over_sampling import SMOTE
    from imblearn.combine import SMOTEENN
    from imblearn.under_sampling import RandomUnderSampler
except Exception:
    SMOTE = None
    SMOTEENN = None
    RandomUnderSampler = None

# XGBoost
try:
    from xgboost import XGBClassifier
except Exception:
    XGBClassifier = None

import xgboost as xgb

# ---------- USER CONFIG ----------
DATA_DIR = Path('training_data')
OUTPUT_DIR = Path('model_output/xgb_report')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Resampling strategy
# We'll use a combination of undersampling and SMOTE with class weights
# Set to True to enable the multi-stage resampling
USE_ADVANCED_RESAMPLING = True

# Original resampling method (kept for backward compatibility, not used when USE_ADVANCED_RESAMPLING=True)
RESAMPLE_METHOD = 'none'

# Class weighting
# We'll use a fixed 2:1 weight for minority class
USE_SCALE_POS_WEIGHT = True
FIXED_SCALE_POS_WEIGHT = 2.0  # Fixed weight for positive class

# Disable sample weights as we're using scale_pos_weight
USE_SAMPLE_WEIGHT = False
N_ESTIMATORS = 500
LEARNING_RATE = 0.05
MAX_DEPTH = 6
EARLY_STOPPING_ROUNDS = 25
TEST_SIZE = 0.25
RANDOM_STATE = 42
CV_FOLDS = 5
# ----------------------------------

EXPECTED_COLS = [
    "sma23_40_h","sma23_40_l","sma23_40_m","sma23_40_md",
    "sma25_40_h","sma25_40_l","sma25_40_m","sma25_40_md",
    "vol_40_h","vol_40_l","vol_40_m","vol_40_md",
    "vol_5_h","vol_5_l","vol_5_m","vol_5_md","classification"
]

# 1) sanity checks
if XGBClassifier is None:
    raise ImportError('xgboost is not installed. Install with `pip install xgboost`')

if not DATA_DIR.exists() or not DATA_DIR.is_dir():
    raise FileNotFoundError(f'Data directory not found: {DATA_DIR}')

csv_files = sorted(DATA_DIR.glob('*.csv'))
if len(csv_files) == 0:
    raise FileNotFoundError(f'No CSV files found in {DATA_DIR}')

print(f'Found {len(csv_files)} CSV files. Concatenating...')
df_list = []
for p in csv_files:
    d = pd.read_csv(p)
    missing = [c for c in EXPECTED_COLS if c not in d.columns]
    if missing:
        raise ValueError(f'File {p} missing expected columns: {missing}')
    df_list.append(d[EXPECTED_COLS])

df = pd.concat(df_list, ignore_index=True)
print('Concatenated shape:', df.shape)

# 2) cleaning
df = df.dropna().reset_index(drop=True)
print('After dropna:', df.shape)

# 3) features/target
X = df.drop(columns=['classification']).copy()
Y = df['classification'].copy()

# ensure numerical labels
if Y.dtype.kind not in 'biufc':
    Y = pd.factorize(Y)[0]

unique, counts = np.unique(Y, return_counts=True)
class_counts = dict(zip(unique, counts))
print('Class distribution:')
for k,v in class_counts.items():
    print(f'  class {k}: {v} samples')

n_samples = len(Y)
if len(unique) != 2:
    raise ValueError('This script currently supports binary classification only.')

n_pos = int(counts[1] if 1 in unique else 0)
n_neg = int(counts[0] if 0 in unique else 0)
if n_pos == 0 or n_neg == 0:
    raise ValueError('One of the classes has zero samples; cannot train.')

imbalance_ratio = n_neg / n_pos if n_pos>0 else np.inf
print(f'Imbalance ratio (neg:pos) = {n_neg}:{n_pos} = {imbalance_ratio:.2f}')

# 4) compute weights
if USE_SAMPLE_WEIGHT:
    # Inverse-frequency weight per class: weight[c] = total_samples / (n_classes * count_c)
    n_classes = 2
    weight_per_class = {0: float(n_samples) / (n_classes * n_neg), 1: float(n_samples) / (n_classes * n_pos)}
    print('Using sample weights (inverse frequency):', weight_per_class)
    sample_weights = Y.map(weight_per_class)
else:
    sample_weights = None

# For XGBoost, scale_pos_weight = n_neg / n_pos
if USE_SCALE_POS_WEIGHT:
    scale_pos_weight = float(n_neg) / float(n_pos)
    print('Using scale_pos_weight for XGBoost:', scale_pos_weight)
else:
    scale_pos_weight = 1.0

# 5) Advanced class imbalance handling
print('\nHandling class imbalance with multi-stage approach...')
X_res, Y_res = X.copy(), Y.copy()

# Calculate class distribution
n_neg = (Y_res == 0).sum()
n_pos = (Y_res == 1).sum()
print(f'Original class distribution: {n_neg} negative, {n_pos} positive (ratio: {n_neg/n_pos:.2f}:1)')

# Stage 1: First undersample majority class to 5:1 ratio
target_ratio_after_undersample = 5.0
target_neg_after_undersample = int(n_pos * target_ratio_after_undersample)

if n_neg > target_neg_after_undersample:
    # Undersample majority class
    rus = RandomUnderSampler(
        sampling_strategy={0: target_neg_after_undersample, 1: n_pos},
        random_state=RANDOM_STATE
    )
    X_res, Y_res = rus.fit_resample(X_res, Y_res)
    print(f'After undersampling to 5:1, shape: {X_res.shape}')
    print(f'New class distribution: {(Y_res == 0).sum()} negative, {(Y_res == 1).sum()} positive')
else:
    print('Skipping undersampling as ratio is already better than 5:1')

# Stage 2: Apply SMOTE to reach 2:1 ratio
target_ratio_after_smote = 2.0
current_neg = (Y_res == 0).sum()
current_pos = (Y_res == 1).sum()
target_pos_after_smote = int(current_neg / target_ratio_after_smote)

if current_pos < target_pos_after_smote:
    # Calculate how many positive samples to generate
    n_samples_needed = target_pos_after_smote - current_pos
    
    # Apply SMOTE
    smote = SMOTE(
        sampling_strategy={1: target_pos_after_smote},
        random_state=RANDOM_STATE,
        k_neighbors=min(5, current_pos - 1)  # Ensure k_neighbors is valid
    )
    X_res, Y_res = smote.fit_resample(X_res, Y_res)
    print(f'After SMOTE to 2:1, shape: {X_res.shape}')
    print(f'Final class distribution: {(Y_res == 0).sum()} negative, {(Y_res == 1).sum()} positive')
else:
    print('Skipping SMOTE as ratio is already better than 2:1')

# Stage 3: Apply class weights
# Set higher weight for minority class (class 1)
scale_pos_weight = 2.0  # Minority class weight is 2x majority class
print(f'Using class weights: 1.0 for negative class, {scale_pos_weight} for positive class')

# 6) train/test split (on resampled or original data depending on choice)
X_train, X_test, y_train, y_test = train_test_split(
    X_res, Y_res, test_size=TEST_SIZE, stratify=Y_res, random_state=RANDOM_STATE
)
print('Train/test shapes:', X_train.shape, X_test.shape)

# 7) instantiate XGBoost
xgb_clf = XGBClassifier(
    n_estimators=N_ESTIMATORS,
    learning_rate=LEARNING_RATE,
    max_depth=MAX_DEPTH,
    use_label_encoder=False,
    eval_metric='aucpr',
    random_state=RANDOM_STATE,
    n_jobs=-1,
    scale_pos_weight=scale_pos_weight,
    early_stopping_rounds=EARLY_STOPPING_ROUNDS
)

# 8) fit with early stopping on a small validation split from the training set
# Create an eval set: 10% of training
X_tr, X_val, y_tr, y_val = train_test_split(X_train, y_train, test_size=0.1, stratify=y_train, random_state=RANDOM_STATE)

fit_kwargs = {}
if sample_weights is not None:
    # sample_weights may correspond to the resampled labels
    # map to training indices
    # If sample_weights is a pandas Series aligned to X_res, create weights for X_tr
    if isinstance(sample_weights, (pd.Series, pd.DataFrame)):
        sw = sample_weights.reset_index(drop=True)
        # need indices of X_tr in the resampled array - easiest approach: recompute by using fit_resample output index
        # But here we will compute weights over X_res by treating them as arrays aligned with X_res
        # Create boolean mask to select rows used in X_tr/X_val
        # Simpler: do not use sample_weights for the fit kwargs unless user explicitly set USE_SAMPLE_WEIGHT
        pass

print('Fitting XGBoost (this may take a while depending on data size)...')
# Use early stopping; provide eval_set
xgb_clf.fit(
    X_tr, y_tr,
    eval_set=[(X_val, y_val)],
    verbose=10
)

# Save model
joblib.dump(xgb_clf, OUTPUT_DIR / 'xgb_model.joblib')
print('Saved model to', OUTPUT_DIR / 'xgb_model.joblib')

# 9) Custom cross-validation with validation sets for early stopping
print('\nRunning cross-validation...')
cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
cv_scores = []

for fold, (train_idx, test_idx) in enumerate(cv.split(X_res, Y_res), 1):
    X_train_fold, X_val_fold = X_res.iloc[train_idx], X_res.iloc[test_idx]
    y_train_fold, y_val_fold = Y_res.iloc[train_idx], Y_res.iloc[test_idx]
    
    # Split training fold into train/validation
    X_tr, X_val, y_tr, y_val = train_test_split(
        X_train_fold, y_train_fold, 
        test_size=0.2, 
        stratify=y_train_fold,
        random_state=RANDOM_STATE
    )
    
    model = XGBClassifier(
        n_estimators=N_ESTIMATORS,
        learning_rate=LEARNING_RATE,
        max_depth=MAX_DEPTH,
        use_label_encoder=False,
        eval_metric='aucpr',
        random_state=RANDOM_STATE,
        n_jobs=-1,
        scale_pos_weight=scale_pos_weight,
        early_stopping_rounds=EARLY_STOPPING_ROUNDS
    )
    
    print(f'\nFold {fold}:')
    model.fit(
        X_tr, y_tr,
        eval_set=[(X_val, y_val)],
        verbose=10
    )
    
    # Get score on test fold
    y_pred = model.predict(X_val_fold)
    score = f1_score(y_val_fold, y_pred)
    cv_scores.append(score)
    print(f'Fold {fold} F1: {score:.4f}')

print(f'\nCV F1 scores: {np.round(cv_scores, 4)}')
print(f'Mean CV F1: {np.mean(cv_scores):.4f} (±{np.std(cv_scores):.4f})')

# Retrain on full training data with early stopping
print('\nRetraining on full training set...')

# 10) Optimize threshold and get test predictions
y_proba = xgb_clf.predict_proba(X_test)[:, 1]

# Find best threshold based on F1 score
precisions, recalls, thresholds = precision_recall_curve(y_test, y_proba)
f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-9)  # Add small epsilon to avoid division by zero
best_threshold = thresholds[np.argmax(f1_scores)]
print(f'\nBest threshold for F1 score: {best_threshold:.4f}')

# Make predictions with default threshold (0.5)
y_pred_default = xgb_clf.predict(X_test)
# Make predictions with optimized threshold
y_pred_optimized = (y_proba >= best_threshold).astype(int)

# Calculate metrics for both thresholds
def print_metrics(y_true, y_pred, y_proba=None, threshold=None):
    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    
    if y_proba is not None:
        fpr, tpr, _ = roc_curve(y_true, y_proba)
        roc_auc = auc(fpr, tpr)
        pr_auc = average_precision_score(y_true, y_proba)
        print(f'ROC-AUC: {roc_auc:.4f}, PR-AUC: {pr_auc:.4f}')
    
    if threshold is not None:
        print(f'Using threshold: {threshold:.4f}')
    print(f'Accuracy: {acc:.4f}, Precision: {prec:.4f}, Recall: {rec:.4f}, F1: {f1:.4f}\n')
    return {
        'accuracy': acc,
        'precision': prec,
        'recall': rec,
        'f1': f1,
        'threshold': threshold if threshold is not None else 0.5
    }

print('\nPerformance with default threshold (0.5):')
metrics_default = print_metrics(y_test, y_pred_default, y_proba)

print('Performance with optimized threshold:')
metrics_optimized = print_metrics(y_test, y_pred_optimized, y_proba, best_threshold)

# Save metrics
with open(OUTPUT_DIR / 'metrics_summary.txt', 'w') as f:
    f.write('Model Performance Summary\n')
    f.write('=======================\n\n')
    f.write('Default Threshold (0.5):\n')
    f.write(f"Accuracy: {metrics_default['accuracy']:.4f}\n")
    f.write(f"Precision: {metrics_default['precision']:.4f}\n")
    f.write(f"Recall: {metrics_default['recall']:.4f}\n")
    f.write(f"F1: {metrics_default['f1']:.4f}\n\n")
    f.write(f'Optimized Threshold ({best_threshold:.4f}):\n')
    f.write(f"Accuracy: {metrics_optimized['accuracy']:.4f}\n")
    f.write(f"Precision: {metrics_optimized['precision']:.4f}\n")
    f.write(f"Recall: {metrics_optimized['recall']:.4f}\n")
    f.write(f"F1: {metrics_optimized['f1']:.4f}\n")

# Use optimized predictions for the rest of the script
y_pred = y_pred_optimized

# save classification report
with open(OUTPUT_DIR / 'classification_report.txt', 'w') as fh:
    fh.write(classification_report(y_test, y_pred, zero_division=0))

# confusion matrix
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

# ROC / PR
if y_proba is not None:
    # For binary classification, we can directly use y_proba
    fpr, tpr, _ = roc_curve(y_test, y_proba)
    roc_auc = auc(fpr, tpr)
    fig, ax = plt.subplots()
    ax.plot(fpr, tpr)
    ax.plot([0,1],[0,1], linestyle='--')
    ax.set_xlabel('False Positive Rate')
    ax.set_ylabel('True Positive Rate')
    ax.set_title(f'ROC Curve (AUC = {roc_auc:.3f})')
    plt.grid(True, alpha=0.3)
    fig.savefig(OUTPUT_DIR / 'roc_curve.png', dpi=300, bbox_inches='tight')
    plt.close(fig)

    # Precision-Recall curve
    precision_vals, recall_vals, _ = precision_recall_curve(y_test, y_proba)
    pr_auc = average_precision_score(y_test, y_proba)
    fig, ax = plt.subplots()
    ax.plot(recall_vals, precision_vals)
    ax.set_xlabel('Recall')
    ax.set_ylabel('Precision')
    ax.set_title(f'Precision-Recall (AUC={pr_auc:.3f})')
    fig.savefig(OUTPUT_DIR / 'pr_curve.png')
    plt.close(fig)

# Feature importance analysis
try:
    # Get feature importance
    importance = xgb_clf.feature_importances_
    feat_imp = pd.Series(importance, index=X.columns).sort_values(ascending=False)
    
    # Save to CSV
    feat_imp.to_csv(OUTPUT_DIR / 'feature_importances.csv')
    
    # Plot top 20 features
    plt.figure(figsize=(12, 8))
    top_n = min(20, len(feat_imp))  # Show top 20 or all if less than 20
    top_features = feat_imp.head(top_n)
    
    # Create horizontal bar plot
    bars = plt.barh(range(len(top_features)), top_features.values, align='center')
    plt.yticks(range(len(top_features)), top_features.index)
    
    # Add value labels on the bars
    for i, v in enumerate(top_features.values):
        plt.text(v, i, f' {v:.4f}', va='center')
    
    plt.xlabel('Importance Score')
    plt.title('Top 20 Important Features')
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / 'top_features.png', dpi=300, bbox_inches='tight')
    plt.close()
    
    print('\nTop 10 most important features:')
    print(top_features.head(10).to_string())
    
except Exception as e:
    print(f'Error in feature importance analysis: {e}')

# permutation importance
try:
    perm = permutation_importance(xgb_clf, X_test, y_test, n_repeats=10, random_state=RANDOM_STATE, n_jobs=1)
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
except Exception as e:
    print('Permutation importance failed:', e)

# save prediction sample
pred_sample = X_test.reset_index(drop=True).copy()
pred_sample['true'] = y_test.reset_index(drop=True)
pred_sample['pred'] = y_pred
if y_proba is not None:
    pred_sample['proba_pos'] = y_proba[:,1]
pred_sample.to_csv(OUTPUT_DIR / 'prediction_sample.csv', index=False)

# summary
with open(OUTPUT_DIR / 'summary_metrics.txt', 'w') as f:
    f.write(f'n_samples: {n_samples}\n')
    f.write(f'class_counts: {class_counts}\n')
    f.write(f'imbalance_ratio (neg/pos): {imbalance_ratio:.6f}\n')
    f.write(f'use_scale_pos_weight: {USE_SCALE_POS_WEIGHT}\n')
    f.write(f'resample_method: {RESAMPLE_METHOD}\n')
    f.write(f'test_acc: {acc:.6f}\n')
    f.write(f'test_precision: {prec:.6f}\n')
    f.write(f'test_recall: {rec:.6f}\n')
    f.write(f'test_f1: {f1:.6f}\n')
    if cv_scores is not None:
        f.write(f'cv_f1_mean: {cv_scores.mean():.6f}\n')
        f.write(f'cv_f1_std: {cv_scores.std():.6f}\n')

print('Saved outputs to', OUTPUT_DIR)
print('Files:')
for p in sorted(OUTPUT_DIR.glob('*')):
    print('-', p.name)

# End of script
