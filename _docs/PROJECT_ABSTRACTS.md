# Project Technical Abstracts

This document provides a nuanced, engineering-focused breakdown of the major components in the crypto trading portfolio. It is designed for technical reviewers at quantitative finance or AI research firms.

---

## 🍌 LiquidBananas: High-Concurrency Asynchronous Trading
**Focus**: Performance Engineering & State Reliability

*   **Core Architecture**: Built on **Quart** (Asynchronous Python) to manage the inherently concurrent nature of crypto markets. The system maintains a localized "Source of Truth" in `AccountState`, synchronized with the exchange via `asyncio.gather` for parallelized ohlcv/position fetching.
*   **State Integrity**: Implements `asyncio.Lock` to prevent race conditions during tick processing and state reconciliation. This ensures that the Signal Generator and the Risk Manager never operate on stale or conflicting account data.
*   **Thin Market Resilience**: Uses a custom **Forward-Filling** algorithm in the `runner.py` gap-filling loop. This handles illiquid tokens by synthetically projecting price data across missing 5m bars, ensuring indicator continuity and preventing "ghost signals" during low-volatility regimes.
*   **Execution Infrastructure**: Integrated with **Hyperliquid** via a streamlined CCXT wrapper that handles precision rounding, leverage normalization, and non-blocking order status polling.

---

## 🧠 2SMALGO: Signal Confirmation via Deep Learning
**Focus**: Machine Learning Methodology & Data Rigour

*   **Heuristic Labeling**: Leverages a custom **Zigzag heuristic** to label historical data. Rather than simple price targets, the model attempts to learn the underlying structure of mean-reversion and trend-exhaustion points identified by the range filter.
*   **Experimental Rigour**: Employs **Stratified Training Splits** in the `train_zigzag.py` pipeline. This is critical for trading data, ensuring that the rare "perfect entry" signals are proportionally FX represented in both training and validation sets, preventing the "Zero-Class Convergence" trap common in imbalanced financial datasets.
*   **Hardware Acceleration**: Optimized for **TensorFlow Metal (macOS GPU)** to enable rapid iteration on 3+ years of historical market data.
*   **Normalization Strategy**: Features a rolling window normalization (N=200) to account for non-stationary market environments, ensuring the model reacts to relative volatility rather than absolute price levels.

---

## 🖥️ Bananas Trading Bot: Full-Stack Observability
**Focus**: Human-in-the-Loop Systems

*   **Asynchronous Dashboard**: Extends the core runner with a real-time monitoring API. It utilizes `uvicorn.Server` to host a non-blocking diagnostic interface.
*   **Telemetry Streaming**: The system broadcasts live indicators (ATR, Trend EMA, Rating) to a frontend via the `AccountState` telemetry bridge. This allows for visual verification of high-frequency decision making without stopping the bot.
*   **Position Lifecycle Management**: Implements a robust `PositionManager` that handles the delicate transition from "Signal Received" to "Exit Triggered," including automatic Telegram notification triggers for out-of-band monitoring.

---

## 🛠️ Tooling & Research Infrastructure
**Focus**: Innovation Pipeline

*   **Pine Agent**: A specialized utility for bridging the gap between TradingView's Pine Script and native Python. This was the "Force Multiplier" that allowed for moving past the 2.5-month backtesting limit.
*   **Helius RPC Integration**: Exploration of low-latency Solana transaction signing and WebSocket listening, demonstrating an understanding of RPC-layer communication and the trade-offs of blockchain-direct trading.
