# Crypto Trading Portfolio — Malcolm & Anzu

Welcome to the documentation of our first year of quantitative trading. This is something that started as a product over a few weeks and quickly became a year+ project which we are working on to this day. This repository tracks our progression from Grade 9 where we experimented with news-scrapers and arbitrage to high-concurrency production bots on the Hyperliquid exchange. Along the way we have failed thousands of times, and we often circled back to old ideas but through the weeks and months we've grown an extensive knowledge on quant trading, automation, and interactting with blockchains. 

---

## Key Technical Deep-Dives

To move beyond "scripts" and into "systems," we have documented the core engineering and research challenges of this project:

*   **[Technical Abstracts](./_docs/PROJECT_ABSTRACTS.md)**: Nuanced breakdown of `asyncio` state management, ML stratification, and thin market resilience.
*   **[System Architecture](./_docs/ARCHITECTURE.md)**: Visual data flow diagrams (Mermaid) showing the lifecycle of a price tick from exchange to execution.
*   **[Quant & Systems FAQ](./_docs/TECH_DEEP_DIVE.md)**: Strategic answers to "Elite Questions" on slippage, state reconciliation, and data rigour.

---

## The Journey: Growth Through Failure

The narrative history is in **[STORY.md](./STORY.md)**, but our engineering philosophy is best summarized by the **"Leakage Discovery"**:

> [!NOTE]
> **The Honest Quant**: In early backtests, we saw returns of 5,000%+. Instead of celebrating, we performed a Root Cause Analysis and identified a "Future Data Leakage" bug in our vectorbt integration. Identifying and fixing this look-ahead bias was the most significant milestone in our growth as quantitative developers.

## Project Structure

- **`01_live_bots/`**: Production-ready codebases, including the `bananas-trading-bot` with integrated web dashboard.
- **`02_strategy_research/`**: Backtesting frameworks, Optuna optimization scripts, and historical results.
- **`03_ml_experiments/`**: Neural network training scripts (TensorFlow Metal) and stratified data prep.
- **`04_tooling/`**: Helper scripts, Pine Script to Python translators, and Helius RPC tests.

---

### 🛡️ Moral Philosophy
This project is an engineering challenge focused on identifying market patterns and capturing inefficiencies. While we jokingly refer to "card counting", our goal is the construction of robust, mathematically-sound market instruments.
