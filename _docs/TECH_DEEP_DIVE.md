# Technical Deep-Dive: Quantitative & Systems Rigour

This document serves as a "Technical Interview FAQ," anticipating the high-level questions posed by senior engineers at quantitative finance firms (e.g., Goldman Sachs) or AI research companies (e.g., Anthropic).

---

## 🏗️ Systems Engineering FAQ

### Q1: How does the system handle state resilience during unexpected crashes?
**The Challenge**: In live trading, a bot crash can leave positions "orphaned" on the exchange, while the local code thinks it is flat.
**Our Solution**: The `StrategyRunner` implements a mandatory `reconcile_state()` routine on every boot. It queries the Hyperliquid exchange for open positions and active limit orders, rebuilding the `AccountState` before the first processing tick. This ensures the bot "finds" its existing trades and protective stop-losses without manual intervention.

### Q2: How do you account for slippage and execution latency?
**The Challenge**: Backtests assume instant execution at the close price; live markets have spreads and latency.
**Our Solution**: We conducted **Adversarial Testing** sessions. By manually injecting "Execution Friction"—artificially increasing exchange fees in the backtest and delaying entry by 1-2 bars—we identified which strategies had a large enough "Alpha Buffer" to survive real-world conditions. Strategies that were only profitable under "perfect" conditions were discarded.

---

## 🧪 Machine Learning & Research FAQ

### Q3: How do you prove the ML model is learning market signal, not just your own indicators?
**The Challenge**: If a model is trained on a "Zigzag" heuristic label derived from the same indicators it sees as inputs, it might just learn a simplified version of those indicators (Data Leakage).
**Our Solution**: We utilized **Validation Sets** to monitor for over-fitting. Our current research identifies a need for **Token Stratification**—ensuring that rare entry signals are proportionally sampled across different token regimes—to prevent the model from converging on a "No-Trade" local maximum.

### Q4: What is the "Moral Philosophy" of your AI agent?
**The Challenge**: Unchecked AI agents in financial markets can create systemic risk or execute unintended "toxic" flow.
**Our Solution**: The AI components of this project remain in a strictly sandboxed "Shadow Mode." They generate signals that are logged and verified against human-defined risk parameters before ever hitting the production execution layer. This "Human-in-the-Loop" architecture ensures that the math is supervised by the engineer.

---

## 📈 The Evolution of Rigour

### The "Leakage" Breakthrough
Early in the project (Sept 2025), backtests showed returns in the thousands of percent. Instead of taking this at face value, we performed a **Root Cause Analysis**. We discovered "Future Data Leakage" in the `vectorbt` integration—the backtester was accidentally looking at the next bar's close to decide the current bar's entry.

**The Lesson**: Identifying this mistake was more valuable than the strategy itself. It forced us to build a **Golden Dataset Parity Test** (Python logic vs. Pine Script reference) to prove mathematical integrity.
