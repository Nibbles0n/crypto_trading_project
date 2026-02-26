## Crypto Trading Portfolio — Malcolm (Nibbles0n) & Anzu (AL88748)

Welcome to the documentation of our first year of quantitative trading. This is something that started as get rich quick scheme and over the past year has become a lot more than that. This repository tracks our progression from Grade 9 where we experimented with news-scrapers and arbitrage to high-concurrency production bots on the Hyperliquid exchange. From the begining Malcolm has been in charge of coding and implementation while Anzu has worked on strategy development and backtesting. The more we work, the more we understand eachothers feilds, and the faster and better our progress becomes. Along the way we have failed thousands of times, sometimes getting thrown back to square one. However we took our faliures and built on our learnings to keep progressing. We have gone from knowing nothing about web interfaces, crypto blockchains, market ineficiencies, to being fluent in these spaces.

---

### Further Reading

To move beyond "scripts" and into "systems," we have documented the core engineering and research challenges of this project so far:

*   **[Technical Abstracts](./_docs/PROJECT_ABSTRACTS.md)**: Nuanced breakdown of `asyncio` state management, ML stratification, and thin market resilience.
*   **[Example System Architecture](./_docs/ARCHITECTURE.md)**: Visual data flow diagrams (Mermaid) showing the lifecycle of a price tick from exchange to execution for our "Liquid Bananas" implementation.
*   **[Quant & Systems FAQ](./_docs/TECH_DEEP_DIVE.md)**: Strategic answers to "Elite Questions" on slippage, state reconciliation, and data rigour.

---

### The Journey: Growth Through Failure

The narrative history is in **[STORY.md](./STORY.md)**

> [!NOTE]
> In early backtests, we saw returns of 5,000%+. Instead of celebrating, we performed a Root Cause Analysis and identified a "Future Data Leakage" bug in our vectorbt integration. Be cautious with return calculations found in these documents, not all of them are acurate or repersentive of potential live results.

### Project Structure

- **`01_live_bots/`**: Production-ready codebases, including the `bananas-trading-bot` with integrated web dashboard.
- **`02_strategy_research/`**: Backtesting frameworks, Optuna optimization scripts, and historical results.
- **`03_ml_experiments/`**: Neural network training scripts (TensorFlow Metal) and stratified data prep.
- **`04_tooling/`**: Helper scripts, Pine Script to Python translators, and Helius RPC tests.

---
