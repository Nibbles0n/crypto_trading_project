# Project Evolution Timeline

This document tracks the journey of the crypto trading project from inception in late Grade 9 to the current production-ready state. I have provided what AI models where available at the time to provide context.

## Phase 1: Inception & Sentiment Analysis (May 2025)
*   **Goal:** Use NLP to predict market movements based on mainstream news.
*   **Strategy:** Scraping CBC News and other sources to gauge sentiment.
*   **Tech Stack:** Python, basic web scraping.
*   **AI Context:** GPT-4 / Claude 3 Opus.
*   **Outcome:** Technical failure. Scraping was unreliable and general news sentiment proved too lagging for crypto markets.

## Phase 2: Technical Analysis Foundations (June 2025)
*   **Goal:** Implement classic algorithmic trading strategies.
*   **Strategy:** Simple SMA Crossover (Fast vs. Slow moving averages).
*   **Discovery:** Identified the "Chop" problem—sideways markets trigger frequent false crossovers, and exchange fees erode any small gains.

## Phase 3: The Solana Speed Run (July 2024)
*   **Goal:** Front-run meme coin launches on the Solana blockchain.
*   **Strategy:** Manual transaction signing using Helius RPC to buy tokens the moment they are minted.
*   **Tech Stack:** WebSockets, Helius RPC.
*   **AI Context:** GPT-4o.
*   **Outcome:** Learning experience. The documentation was poor, and relying solely on AI for low-level socket programming without the underlying networking knowledge led to a dead end.

## Phase 4: Summer Research (July - August 2025)
*   **Activity:** Project hiatus during Malcolm's canoe trip.
*   **Research:** Anzu focused on manual charting and strategy verification on TradingView, moving away from "get rich quick" launch-sniping toward sustainable trend-following.

## Phase 5: The "Elite Algo" & Kraken Integration (Sept 2025)
*   **Goal:** Scalable execution on a trusted exchange.
*   **Strategy:** Testing commercial strategies purchased online via TradingView webhooks.
*   **Tech Stack:** Flask (Python), Kraken API.
*   **Discovery:** Most commercial strategies are "meaningless" and over-optimized for backrests but fail live. High lag in webhooks and lack of proper stop-loss handling wiped out early test capital ($50).

## Phase 6: Machine Learning & Range Filters (Oct - Nov 2025)
*   **Goal:** Filter out bad signal entries using ML.
*   **Strategy:** Range Filter ([Conditional EMA](https://www.tradingview.com/script/lut7sBgG-Range-Filter-DW/)) combined with two-filter confirmation.
*   **Engineering:** Rebuilt the backend from scratch using **Quart** (Asynchronous Python) to handle **WebSockets** and multiple tokens simultaneously (~2000 lines of code).
*   **Discovery:** Discovered the "local maximum" problem in optimization—algorithms finding lucky one-off winners instead of stable, repeatable strategies.

## Phase 7: The Python Breakthrough (Jan 2026)
*   **Goal:** Complete independence from TradingView's 2.5-month data limit.
*   **Strategy:** Converting complex Pine Script logic to native Python/VectorBT.
*   **AI Context:** **Claude 3.5 Sonnet (Upgraded)**. AI acted as a primary driver ("wheelchair") for Anzu as he translated Pine Script logic into functional Python while learning the language.
*   **Outcome:** First backtests with 3+ years of data showed consistent profitability.

## Phase 8: Production & Deployment (Feb 2026 - Present)
*   **Goal:** Reliable, 24/7 execution via VPS.
*   **Current Focus:** Polishing the dashboard and preparing for the first significant capital deployment.
*   **Moral Perspective:** The project focuses on "identifying market patterns and capturing inefficiencies" rather than simple gambling. All current code is legacy and no longer has alpha, preserved here for the journey.

---

# Portfolio Caveats & Data Integrity

> [!WARNING]
> **Future Data Leakage:** Many of the historical backtests in this repository (specifically those involving the Dual Range Filter) exhibit significant **future data leakage**. 
> - The astronomical returns (thousands of percent) seen in early screenshots/logs are a result of this technical flaw and should be viewed as a "technical evolution" artifact rather than a viable financial record.
> - Recent/Active strategies have been refined to eliminate leakage, resulting in more realistic alpha expectations.
> - This codebase is preserved for **educational and evolution tracking** purposes.
