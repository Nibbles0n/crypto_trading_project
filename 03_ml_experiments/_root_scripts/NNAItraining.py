import pandas as pd
import numpy as np
import optuna
from sklearn.metrics import mean_squared_error
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.regularizers import l2
from tensorflow.keras.optimizers import Adam, RMSprop
from tensorflow.keras.models import save_model
import matplotlib.pyplot as plt
import json
import tensorflow as tf

# Detect GPU if available
physical_devices = tf.config.list_physical_devices('GPU')
if physical_devices:
    tf.config.experimental.set_memory_growth(physical_devices[0], True)
    print('Using GPU for training.')

# Load data (optimized for large datasets ~100k)
df = pd.read_csv('trade_samples.csv', parse_dates=['entry_timestamp'], low_memory=False)
df.sort_values('entry_timestamp', inplace=True)  # Chronological order

# Features: All columns except token, entry_timestamp, return_pct
feature_cols = [col for col in df.columns if col not in ['token', 'entry_timestamp', 'return_pct']]
num_features = 8  # close, sma15, sma45, sma_diff, norm_vol, norm_rsi, atr_pct, macd_hist
num_timesteps = 10
X = df[feature_cols].values.reshape(-1, num_timesteps, num_features)
y = df['return_pct'].values

# Chronological split: 70% train, 15% val, 15% test
train_size = int(0.7 * len(df))
val_size = int(0.15 * len(df))
X_train, y_train = X[:train_size], y[:train_size]
X_val, y_val = X[train_size:train_size+val_size], y[train_size:train_size+val_size]
X_test, y_test = X[train_size+val_size:], y[train_size+val_size:]

# Optuna objective function (expanded for ~100k data)
def objective(trial):
    # Expanded search space
    num_layers = trial.suggest_int('num_layers', 1, 3)
    lstm_units = trial.suggest_int('lstm_units', 64, 512, log=True)
    bottleneck_units = trial.suggest_int('bottleneck_units', 32, 128)
    dropout_rate = trial.suggest_float('dropout_rate', 0.1, 0.3)
    l2_reg = trial.suggest_float('l2_reg', 0.001, 0.05, log=True)
    learning_rate = trial.suggest_float('learning_rate', 0.0001, 0.01, log=True)
    batch_size = trial.suggest_categorical('batch_size', [128, 256, 512])  # Larger for big data
    optimizer_name = trial.suggest_categorical('optimizer', ['Adam', 'RMSprop'])

    # Build model with variable layers
    model = Sequential()
    model.add(LSTM(lstm_units, input_shape=(num_timesteps, num_features), return_sequences=True, kernel_regularizer=l2(l2_reg)))
    model.add(Dropout(dropout_rate))
    
    for _ in range(num_layers - 1):
        model.add(LSTM(lstm_units // 2, return_sequences=True, kernel_regularizer=l2(l2_reg)))
        model.add(Dropout(dropout_rate))
    
    model.add(LSTM(bottleneck_units))
    model.add(Dropout(dropout_rate))
    model.add(Dense(1))
    
    optimizer = Adam(learning_rate=learning_rate) if optimizer_name == 'Adam' else RMSprop(learning_rate=learning_rate)
    model.compile(optimizer=optimizer, loss='mse')  # Or 'huber' for robustness

    # Train
    early_stop = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
    history = model.fit(X_train, y_train, epochs=50, batch_size=batch_size, validation_data=(X_val, y_val),
                        callbacks=[early_stop], verbose=0)

    # Return validation MSE
    val_mse = min(history.history['val_loss'])
    return val_mse

# Run Optuna optimization (more trials for larger data)
study = optuna.create_study(direction='minimize')
study.optimize(objective, n_trials=150)  # Increased for better optima
best_params = study.best_params
print('Best Hyperparameters:', best_params)
with open('best_params.json', 'w') as f:
    json.dump(best_params, f)
print('Saved best_params.json')
study.trials_dataframe().to_csv('optuna_trials.csv', index=False)
print('Saved optuna_trials.csv for analysis')

# Train final model with best hyperparameters
model = Sequential()
model.add(LSTM(best_params['lstm_units'], input_shape=(num_timesteps, num_features), return_sequences=True,
               kernel_regularizer=l2(best_params['l2_reg'])))
model.add(Dropout(best_params['dropout_rate']))

for _ in range(best_params['num_layers'] - 1):
    model.add(LSTM(best_params['lstm_units'] // 2, return_sequences=True, kernel_regularizer=l2(best_params['l2_reg'])))
    model.add(Dropout(best_params['dropout_rate']))

model.add(LSTM(best_params['bottleneck_units']))
model.add(Dropout(best_params['dropout_rate']))
model.add(Dense(1))

optimizer = Adam(learning_rate=best_params['learning_rate']) if best_params['optimizer'] == 'Adam' else RMSprop(learning_rate=best_params['learning_rate'])
model.compile(optimizer=optimizer, loss='mse')
model.summary()

early_stop = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
history = model.fit(X_train, y_train, epochs=50, batch_size=best_params['batch_size'],
                    validation_data=(X_val, y_val), callbacks=[early_stop])

# Save model
save_model(model, 'best_model.h5')
print('Saved best_model.h5')

# Save learning history
history_df = pd.DataFrame(history.history)
history_df['epoch'] = history_df.index + 1
history_df.to_csv('learning_history.csv', index=False)
print('Saved learning_history.csv')

# Plot learning curve
plt.figure()
plt.plot(history.history['loss'], label='Train Loss')
plt.plot(history.history['val_loss'], label='Val Loss')
plt.title('Learning Curve: Train vs Val Loss')
plt.xlabel('Epoch')
plt.ylabel('MSE Loss')
plt.legend()
plt.savefig('learning_curve.png')
print('Saved learning_curve.png')

# Evaluate on train, val, test
y_train_pred = model.predict(X_train, batch_size=best_params['batch_size']).flatten()
y_val_pred = model.predict(X_val, batch_size=best_params['batch_size']).flatten()
y_test_pred = model.predict(X_test, batch_size=best_params['batch_size']).flatten()

train_mse = mean_squared_error(y_train, y_train_pred)
val_mse = mean_squared_error(y_val, y_val_pred)
test_mse = mean_squared_error(y_test, y_test_pred)
print(f'Train MSE: {train_mse} | Val MSE: {val_mse} | Test MSE: {test_mse}')

# Save predictions
pred_df = pd.DataFrame({
    'set': ['train']*len(y_train) + ['val']*len(y_val) + ['test']*len(y_test),
    'y_true': np.concatenate([y_train, y_val, y_test]),
    'y_pred': np.concatenate([y_train_pred, y_val_pred, y_test_pred])
})
pred_df.to_csv('predictions.csv', index=False)
print('Saved predictions.csv')

# Residual plot (test)
plt.figure()
residuals = y_test - y_test_pred
plt.scatter(y_test_pred, residuals)
plt.title('Residual Plot (Test Set)')
plt.xlabel('Predicted Return %')
plt.ylabel('Residual (Actual - Pred)')
plt.axhline(0, color='r', linestyle='--')
plt.savefig('residual_plot.png')
print('Saved residual_plot.png')

# Simulate strategy with fees
fee_pct = 0.2  # 0.1% per side
threshold = 1.5  # Tunable; consider optimizing separately

# Test set trades
test_preds = y_test_pred
test_trades_idx = test_preds > threshold
test_trades_actual = y_test[test_trades_idx]
test_effective = test_trades_actual - fee_pct

# Train set trades (for in-sample comparison)
train_preds = y_train_pred
train_trades_idx = train_preds > threshold
train_trades_actual = y_train[train_trades_idx]
train_effective = train_trades_actual - fee_pct

# Metrics function (added max drawdown)
def compute_metrics(returns, name):
    if len(returns) == 0:
        print(f'No {name} trades triggered.')
        return 0, 0, 0, 0, 0, 0, 0
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    num_wins = len(wins)
    num_losses = len(losses)
    avg_win = np.mean(wins) if num_wins > 0 else 0
    avg_loss = np.mean(losses) if num_losses > 0 else 0
    returns_dec = returns / 100
    sharpe = np.mean(returns_dec) / np.std(returns_dec) * np.sqrt(252) if np.std(returns_dec) != 0 else 0
    cum_returns = np.cumprod(1 + returns_dec) - 1
    annualized_profit = (cum_returns[-1] + 1) ** (252 / len(returns)) - 1 if len(returns) > 0 else 0
    # Max drawdown
    peak = np.maximum.accumulate(cum_returns)
    drawdown = (cum_returns - peak) / (peak + 1e-8)  # Avoid div by zero
    max_dd = np.min(drawdown) * 100 if len(drawdown) > 0 else 0
    return sharpe, annualized_profit, avg_win, avg_loss, num_wins, num_losses, max_dd

# Compute metrics
test_sharpe, test_annualized, test_avg_win, test_avg_loss, test_num_wins, test_num_losses, test_max_dd = compute_metrics(test_effective, 'test')
train_sharpe, train_annualized, train_avg_win, train_avg_loss, train_num_wins, train_num_losses, train_max_dd = compute_metrics(train_effective, 'train')

print(f'Train Sharpe: {train_sharpe:.2f} | Test Sharpe: {test_sharpe:.2f}')
print(f'Train Annualized Profit: {train_annualized * 100:.2f}% | Test Annualized Profit: {test_annualized * 100:.2f}%')
print(f'Train Avg Win: {train_avg_win:.2f}% | Train Avg Loss: {train_avg_loss:.2f}% | Train #Wins/#Losses: {train_num_wins}/{train_num_losses}')
print(f'Test Avg Win: {test_avg_win:.2f}% | Test Avg Loss: {test_avg_loss:.2f}% | Test #Wins/#Losses: {test_num_wins}/{test_num_losses}')
print(f'Train Max Drawdown: {train_max_dd:.2f}% | Test Max Drawdown: {test_max_dd:.2f}%')

# Save test trades
if len(test_effective) > 0:
    trades_df = pd.DataFrame({
        'pred_return': test_preds[test_trades_idx],
        'actual_return': test_trades_actual,
        'effective_return': test_effective
    })
    trades_df.to_csv('test_trades.csv', index=False)
    print('Saved test_trades.csv')

    # Cumulative returns plot
    test_cum_returns = np.cumprod(1 + test_effective / 100) - 1
    plt.figure()
    plt.plot(test_cum_returns)
    plt.title('Cumulative Returns on Test Trades (with Fees)')
    plt.xlabel('Trade #')
    plt.ylabel('Cumulative Return')
    plt.savefig('cum_returns.png')

    # Predicted vs actual
    plt.figure()
    plt.scatter(y_test, y_test_pred)
    plt.title('Predicted vs Actual Returns (Test)')
    plt.xlabel('Actual % Return')
    plt.ylabel('Predicted % Return')
    plt.savefig('pred_vs_actual.png')

    # Returns histogram
    plt.figure()
    plt.hist(test_effective, bins=20)
    plt.title('Distribution of Effective Trade Returns (with Fees)')
    plt.xlabel('% Return')
    plt.ylabel('Frequency')
    plt.savefig('returns_hist.png')

    print('Charts saved: cum_returns.png, pred_vs_actual.png, returns_hist.png')