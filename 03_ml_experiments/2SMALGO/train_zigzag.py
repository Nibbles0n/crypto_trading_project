import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix

# CONFIG
DATA_FILE = "processed_data.npz"
BATCH_SIZE = 1024
EPOCHS = 50
TEST_SIZE = 0.2

# Enable Metal GPU
tf.config.set_visible_devices([], 'CPU')  # Force GPU usage
gpus = tf.config.list_physical_devices('GPU')
if gpus:
    try:
        tf.config.experimental.set_memory_growth(gpus[0], True)
        print("Metal GPU enabled for training")
    except RuntimeError as e:
        print(e)

# Load preprocessed data
data = np.load(DATA_FILE, allow_pickle=True)
X = data['X']
y = data['y']
token_stats = data['stats'].item()

# Split
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=TEST_SIZE, shuffle=True, stratify=y)

# Model
model = models.Sequential([
    layers.Input(shape=(X_train.shape[1],)),
    layers.Dense(128, activation='relu'),
    layers.Dense(64, activation='relu'),
    layers.Dense(1, activation='sigmoid')  # Binary output
])

model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
model.summary()

# Train
history = model.fit(X_train, y_train, validation_split=0.1, epochs=EPOCHS, batch_size=BATCH_SIZE)

# Evaluate
y_pred = (model.predict(X_test) > 0.5).astype(int)
print("\n=== Overall Classification Report ===")
print(classification_report(y_test, y_pred))

# Token-wise metrics
print("\n=== Token-wise Data Metrics ===")
for token, stats in token_stats.items():
    print(f"{token}: data_points={stats['data_points']}, imbalance={stats['imbalance']:.4f}")

# Save model
model.save("trained_model_metal.keras")
print("Training complete. Model saved as trained_model_metal.keras")
