"""
Price Relative Model Trainer
===========================
Specialized trainer for price relative prediction with QPU optimization.

Configuration:
- data_dir: Directory containing training data (default: "price_relative")
- output_dir: Directory to save model and results (default: "model_output/price_relative")
"""

import os
import time
import math
import json
import logging
import argparse
from pathlib import Path
from typing import Dict, Optional, Tuple
from datetime import datetime

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

# Checkpoint file names
CHECKPOINT_FILE = "training_checkpoint.pth"
BEST_MODEL_FILE = "best_model.pth"

# Configuration - Optimized for M2 with 24GB unified memory
DEFAULT_CONFIG = {
    # Data and output directories
    "data_dir": "price_relative",
    "output_dir": "model_output/price_relative",
    
    # Training hyperparameters
    "hidden_dim": 512,           # Increased for better learning capacity
    "dropout": 0.3,              # Slightly reduced for faster training
    "batch_size": 2048,          # Increased batch size for M2
    "lr": 5e-4,                  # Slightly higher learning rate
    "weight_decay": 1e-5,
    "n_epochs": 200,
    "patience": 20,              # Reduced patience for faster experimentation
    "grad_clip": 1.0,
}

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Constants
RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)
torch.manual_seed(RANDOM_STATE)

# Device setup - Optimized for M2
if torch.backends.mps.is_available():
    device = torch.device("mps")
    try:
        # Set memory fraction (0.9 = 90% of available memory)
        torch.mps.set_per_process_memory_fraction(0.9)
        torch.mps.empty_cache()
        print("\n✅ M2 Mac detected - Using MPS with 90% memory limit")
    except Exception as e:
        print(f"⚠️  MPS memory optimization failed: {e}. Continuing with default settings.")
    
    # MPS-optimized settings
    num_workers = min(4, os.cpu_count() - 2)  # Fewer workers for stability
    prefetch_factor = 1  # Reduce prefetch to save memory
    persistent_workers = False  # Disable persistent workers for MPS
    
    # Disable gradient checkpointing for MPS as it can cause issues
    torch.backends.mps.grad_enabled = False
    
else:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    num_workers = min(8, os.cpu_count() - 1)
    prefetch_factor = 2
    persistent_workers = num_workers > 0
    if device.type == 'cuda':
        torch.backends.cudnn.benchmark = True
        torch.cuda.empty_cache()

print(f"\n🚀 Training on {device} with batch size {DEFAULT_CONFIG['batch_size']}")
print(f"   - Num workers: {num_workers}")
print(f"   - Prefetch factor: {prefetch_factor}")
print(f"   - Persistent workers: {persistent_workers}")

class PriceRelativeDataset(Dataset):
    """Dataset for price relative prediction."""
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = X.astype(np.float32)
        self.y = y.astype(np.float32).reshape(-1, 1)
    
    def __len__(self):
        return len(self.y)
    
    def __getitem__(self, idx):
        return torch.from_numpy(self.X[idx]), torch.from_numpy(self.y[idx])

class PriceRelativeModel(nn.Module):
    """Neural network model for price relative prediction with QPU optimizations."""
    def __init__(self, input_dim: int, hidden_dim: int = 256, dropout: float = 0.4):
        super().__init__()
        self.model = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.LeakyReLU(0.1),
            nn.Dropout(dropout),
            
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.BatchNorm1d(hidden_dim // 2),
            nn.LeakyReLU(0.1),
            nn.Dropout(dropout),
            
            nn.Linear(hidden_dim // 2, hidden_dim // 4),
            nn.BatchNorm1d(hidden_dim // 4),
            nn.LeakyReLU(0.1),
            nn.Dropout(dropout),
            
            nn.Linear(hidden_dim // 4, 1),
            nn.Sigmoid()
        )
    
    def forward(self, x):
        return self.model(x)

def load_data(data_dir: Path) -> Tuple[np.ndarray, np.ndarray]:
    """Load and preprocess price relative data."""
    all_data = []
    
    # Load all CSV files in the data directory
    for csv_file in data_dir.glob("*.csv"):
        if not csv_file.name.endswith('_processed.csv'):
            continue
        try:
            df = pd.read_csv(csv_file)
            all_data.append(df)
            logger.info(f"Loaded {len(df)} samples from {csv_file.name}")
        except Exception as e:
            logger.warning(f"Error loading {csv_file}: {e}")
    
    if not all_data:
        raise ValueError(f"No valid data files found in {data_dir}")
    
    # Combine all data
    combined = pd.concat(all_data, ignore_index=True)
    
    # Separate features and target
    X = combined.drop('classification', axis=1).values
    y = combined['classification'].values
    
    logger.info(f"Loaded {len(X)} total samples")
    return X, y

def train_epoch(model, loader, optimizer, loss_fn, device, epoch, total_epochs):
    """Train for one epoch with gradient accumulation."""
    model.train()
    total_loss = 0
    
    # Only show progress bar for the first and last epoch or every 10th epoch
    disable_pbar = (epoch + 1) % 10 != 0 and epoch != 0 and epoch != total_epochs - 1
    progress = tqdm(loader, 
                   desc=f"Epoch {epoch+1:03d}/{total_epochs:03d} [Training]", 
                   leave=False,
                   disable=disable_pbar)
    
    for batch_idx, (X_batch, y_batch) in enumerate(progress):
        X_batch, y_batch = X_batch.to(device), y_batch.to(device)
        
        # Forward pass
        optimizer.zero_grad()
        outputs = model(X_batch)
        loss = loss_fn(outputs, y_batch)
        
        # Backward pass and optimize
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), DEFAULT_CONFIG['grad_clip'])
        optimizer.step()
        
        # Update progress
        total_loss += loss.item() * X_batch.size(0)
        
        # Only update progress bar every 10 batches to reduce overhead
        if not disable_pbar and batch_idx % 10 == 0:
            progress.set_postfix({
                'loss': f"{loss.item():.4f}",
                'lr': f"{optimizer.param_groups[0]['lr']:.2e}"
            })
    
    return total_loss / len(loader.dataset)

def evaluate(model, loader, device, loss_fn):
    """Evaluate model on validation set."""
    model.eval()
    all_probs = []
    all_targets = []
    total_loss = 0
    
    # Use a single progress bar for the entire evaluation
    with torch.no_grad():
        for X_batch, y_batch in loader:  # Removed tqdm from here
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            
            # Forward pass
            outputs = model(X_batch)
            loss = loss_fn(outputs, y_batch)
            total_loss += loss.item() * X_batch.size(0)
            
            # Store predictions and targets in a more memory-efficient way
            all_probs.append(outputs.detach().cpu().numpy())
            all_targets.append(y_batch.cpu().numpy())
    
    # Concatenate all batches at once
    all_probs = np.concatenate(all_probs, axis=0)
    all_targets = np.concatenate(all_targets, axis=0)
    
    # Calculate metrics
    val_loss = total_loss / len(loader.dataset)
    all_probs = np.array(all_probs).flatten()
    all_targets = np.array(all_targets).flatten()
    
    # Calculate ROC AUC
    auc_roc = roc_auc_score(all_targets, all_probs)
    
    # Calculate precision-recall curve
    precision, recall, thresholds = precision_recall_curve(all_targets, all_probs)
    auc_prc = auc(recall, precision)
    
    # Find best F1 score
    f1_scores = 2 * (precision * recall) / (precision + recall + 1e-8)
    best_idx = np.argmax(f1_scores)
    best_f1 = f1_scores[best_idx]
    best_thresh = thresholds[best_idx] if best_idx < len(thresholds) else 0.5
    
    return {
        'val_loss': val_loss,
        'best_f1': best_f1,
        'best_thresh': float(best_thresh),
        'aucroc': auc_roc,
        'auprc': auc_prc,
        'y_true': all_targets,
        'y_prob': all_probs,
        'precision': precision[best_idx],
        'recall': recall[best_idx]
    }

def train():
    """Main training function with checkpointing support."""
    # Use directories from DEFAULT_CONFIG
    data_dir = Path(DEFAULT_CONFIG["data_dir"])
    output_dir = Path(DEFAULT_CONFIG["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(output_dir / 'training.log'),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)
    
    logger.info(f"Using device: {device}")
    logger.info(f"Data directory: {data_dir.absolute()}")
    logger.info(f"Output directory: {output_dir.absolute()}")
    logger.info(f"Hyperparameters: {json.dumps(DEFAULT_CONFIG, indent=2)}")
    
    # Load and preprocess data
    logger.info(f"Loading data from {data_dir}")
    X, y = load_data(data_dir)
    
    # Scale features
    scaler = StandardScaler()
    X = scaler.fit_transform(X)
    joblib.dump(scaler, output_dir / "scaler.pkl")
    
    # Split data
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=RANDOM_STATE
    )
    
    # Create datasets with optimized memory layout
    train_dataset = PriceRelativeDataset(X_train, y_train)
    val_dataset = PriceRelativeDataset(X_val, y_val)
    
    # Optimized DataLoader for M2
    train_loader = DataLoader(
        train_dataset, 
        batch_size=DEFAULT_CONFIG['batch_size'],
        shuffle=True,
        num_workers=num_workers,
        pin_memory=False,  # Disable pin_memory for MPS
        persistent_workers=persistent_workers,
        prefetch_factor=prefetch_factor,
        drop_last=True
    )
    
    val_batch_size = min(DEFAULT_CONFIG['batch_size'] * 2, len(val_dataset))
    val_loader = DataLoader(
        val_dataset,
        batch_size=val_batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=False,  # Disable pin_memory for MPS
        persistent_workers=persistent_workers,
        prefetch_factor=prefetch_factor
    )
    
    logger.info(f"Training batch size: {DEFAULT_CONFIG['batch_size']}, Validation batch size: {val_batch_size}")
    
    # Initialize model
    model = PriceRelativeModel(
        input_dim=X.shape[1],
        hidden_dim=DEFAULT_CONFIG['hidden_dim'],
        dropout=DEFAULT_CONFIG['dropout']
    ).to(device)
    
    # Initialize output bias for class imbalance
    pos = (y_train == 1).sum()
    neg = (y_train == 0).sum()
    if pos > 0 and neg > 0:
        bias = math.log((pos + 1e-8) / (neg + 1e-8))
        for layer in model.model:
            if isinstance(layer, nn.Linear) and layer.out_features == 1:
                layer.bias.data = torch.tensor([bias], dtype=torch.float32).to(device)
    
    # Optimizer and loss
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=DEFAULT_CONFIG['lr'],
        weight_decay=DEFAULT_CONFIG['weight_decay']
    )
    
    # Learning rate scheduler
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='min', factor=0.5, patience=10
    )
    
    loss_fn = nn.BCEWithLogitsLoss()
    
    # Initialize training state
    start_epoch = 0
    best_val_loss = float('inf')
    best_epoch = 0
    epochs_no_improve = 0
    history = {
        'train_loss': [],
        'val_loss': [],
        'val_f1': [],
        'val_auc': [],
        'val_auprc': []
    }
    
    # Load checkpoint if exists
    checkpoint_path = output_dir / CHECKPOINT_FILE
    best_model_path = output_dir / BEST_MODEL_FILE
    
    if checkpoint_path.exists():
        try:
            checkpoint = torch.load(checkpoint_path, map_location=device)
            model.load_state_dict(checkpoint['model_state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
            
            start_epoch = checkpoint['epoch'] + 1
            best_val_loss = checkpoint['best_val_loss']
            best_epoch = checkpoint['best_epoch']
            epochs_no_improve = checkpoint.get('epochs_no_improve', 0)
            history = checkpoint['history']
            
            logger.info(f"Resuming training from epoch {start_epoch}")
            logger.info(f"Previous best val_loss: {best_val_loss:.6f} at epoch {best_epoch}")
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}. Starting from scratch.")
    
    # Training loop with improved logging
    logger.info(f"Starting training for {DEFAULT_CONFIG['n_epochs']} epochs...")
    start_time = time.time()
    best_f1 = 0
    epochs_no_improve = 0
    
    for epoch in range(start_epoch, DEFAULT_CONFIG['n_epochs']):
        epoch_start = time.time()
        
        # Train for one epoch
        train_loss = train_epoch(model, train_loader, optimizer, loss_fn, device, epoch, DEFAULT_CONFIG['n_epochs'])
        
        # Evaluate on validation set
        val_results = evaluate(model, val_loader, device, loss_fn)
        
        # Update learning rate
        scheduler.step(val_results['val_loss'])
        
        # Update history
        history['train_loss'].append(train_loss)
        history['val_loss'].append(val_results['val_loss'])
        history['val_f1'].append(val_results['best_f1'])
        history['val_auc'].append(val_results['aucroc'])
        history['val_auprc'].append(val_results['auprc'])
        
        # Check for improvement
        if val_results['best_f1'] > best_f1:
            best_f1 = val_results['best_f1']
            epochs_no_improve = 0
            # Save best model
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'scheduler_state_dict': scheduler.state_dict(),
                'val_loss': val_results['val_loss'],
                'val_f1': val_results['best_f1'],
                'best_threshold': val_results['best_thresh']
            }, os.path.join(output_dir, 'best_model.pth'))
        else:
            epochs_no_improve += 1
        
        # Log metrics (only every 5 epochs or on last epoch)
        if (epoch + 1) % 5 == 0 or epoch == DEFAULT_CONFIG['n_epochs'] - 1:
            epoch_time = time.time() - epoch_start
            logger.info(
                f"Epoch {epoch+1:03d}/{DEFAULT_CONFIG['n_epochs']:03d} | "
                f"Time: {epoch_time:.1f}s | "
                f"Train Loss: {train_loss:.4f} | "
                f"Val Loss: {val_results['val_loss']:.4f} | "
                f"Val F1: {val_results['best_f1']:.4f} | "
                f"AUC: {val_results['aucroc']:.4f} | "
                f"LR: {optimizer.param_groups[0]['lr']:.2e}"
            )
        
        # Early stopping
        if epochs_no_improve >= DEFAULT_CONFIG['patience']:
            logger.info(f"Early stopping at epoch {epoch+1}")
            break
        
        # Save best model
        if val_results['val_loss'] < best_val_loss - 1e-6:
            best_val_loss = val_results['val_loss']
            best_epoch = epoch
            epochs_no_improve = 0
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_loss': val_results['val_loss'],
                'val_f1': val_results['best_f1'],
                'best_threshold': val_results['best_thresh'],
                'hyperparameters': DEFAULT_CONFIG
            }, best_model_path)
            logger.info(f"New best model saved with val_loss: {best_val_loss:.6f}")
        else:
            epochs_no_improve += 1
        
        # Save checkpoint
        torch.save({
            'epoch': epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'scheduler_state_dict': scheduler.state_dict(),
            'best_val_loss': best_val_loss,
            'best_epoch': best_epoch,
            'epochs_no_improve': epochs_no_improve,
            'history': history,
            'hyperparameters': DEFAULT_CONFIG
        }, checkpoint_path)
        
        # Early stopping
        if epochs_no_improve >= DEFAULT_CONFIG['patience']:
            logger.info(f"Early stopping at epoch {epoch+1}")
            break
    
    # Training complete
    training_time = time.time() - start_time
    logger.info(f"Training completed in {training_time/60:.2f} minutes")
    
    # Load best model for final evaluation
    if best_model_path.exists():
        try:
            checkpoint = torch.load(best_model_path, map_location=device)
            model.load_state_dict(checkpoint['model_state_dict'])
            logger.info(f"Loaded best model from epoch {checkpoint.get('epoch', 'unknown')}")
        except Exception as e:
            logger.warning(f"Failed to load best model for final evaluation: {e}")
    
    # Final evaluation
    final_results = evaluate(model, val_loader, device, loss_fn)
    
    # Save final model with additional metadata
    final_model_path = output_dir / 'final_model.pth'
    torch.save({
        'model_state_dict': model.state_dict(),
        'hyperparameters': DEFAULT_CONFIG,
        'final_metrics': {
            'val_loss': final_results['val_loss'],
            'val_f1': final_results['best_f1'],
            'val_auc': final_results['aucroc'],
            'val_auprc': final_results['auprc'],
            'best_threshold': final_results['best_thresh']
        },
        'training_history': history,
        'best_epoch': best_epoch,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }, final_model_path)
    
    # Save training history
    pd.DataFrame(history).to_csv(output_dir / 'training_history.csv', index=False)
    
    # Save classification report
    y_pred = (final_results['y_prob'] >= final_results['best_thresh']).astype(int)
    with open(output_dir / 'classification_report.txt', 'w') as f:
        f.write(classification_report(final_results['y_true'], y_pred, zero_division=0))
    
    # Clean up checkpoint files
    if checkpoint_path.exists():
        try:
            os.remove(checkpoint_path)
            logger.info("Removed temporary checkpoint file")
        except Exception as e:
            logger.warning(f"Failed to remove checkpoint file: {e}")
    
    logger.info(f"Training completed. Best model saved to {best_model_path}")
    logger.info(f"Final model saved to {final_model_path}")

if __name__ == '__main__':
    # Create necessary directories
    data_dir = Path(DEFAULT_CONFIG["data_dir"])
    output_dir = Path(DEFAULT_CONFIG["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(output_dir / 'training.log'),
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info(f"Starting training with data from: {data_dir.absolute()}")
    logger.info(f"Saving outputs to: {output_dir.absolute()}")
    
    train()