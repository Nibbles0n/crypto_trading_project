# Quantitative Crypto Trading Bot - Development Journey

A comprehensive historical guide documenting the exploration, experiments, and learning process in building an automated cryptocurrency trading system. This document records what we tried, what failed, and what we learned over several months of development.

---

## Project Overview

**Original Objective:** Automatically buy and sell cryptocurrency to generate returns exceeding simple hodling strategies.

**Timeline:** May 2024 through December 2024

**Team:** Malcolm (coding/systems architecture) + Anzu (trading strategy/market analysis)

**Documentation Purpose:** This guide serves as a historical record of development attempts conducted several months ago. Many approaches documented here represent early-stage experiments that informed later iterations but have since been superseded as the project evolved.

---

## Phase 1: Initial Exploration & Failure (May - June 2024)

This phase represents the very beginning—trying various ideas with limited knowledge and experience.

### Attempt 1: News-Based Prediction

**Concept:** Use AI to analyze top news sources and predict market direction over weekly timeframes.

**Technology Used:** Python web scraping (CBC News)

**What Happened:**
- Attempted to scrape financial news websites
- Lacked experience processing unstructured text data
- No clear pipeline from news extraction to trading signal
- Couldn't implement the envisioned features

**Why It Failed:** 
- The data processing pipeline was fundamentally unclear
- Didn't understand how to convert news into numerical predictions
- Overestimated ability to work with NLP/sentiment analysis without background

**Key Learning:** Starting with complex ML models before understanding data fundamentals doesn't work.

**Estimated Hours Spent:** Less than 30 hours

**Repository Structure (if preserved):**
```
/phase1-news-analysis
  ├── scrapers/
  │   └── cbc_news_scraper.py (abandoned)
  ├── analysis/
  │   └── sentiment_analysis.py (abandoned)
  └── README.md
```

---

### Attempt 2: Simple Moving Average (SMA) Crossover

**Concept:** Buy when fast SMA crosses above slow SMA; sell when it crosses below.

**How It Worked in Theory:**
- Calculate two moving averages: one fast (short-term), one slow (long-term)
- Track where they intersect
- Generate buy/sell signals from crossover points

**What Went Wrong:**
- In sideways markets, both lines converge → constant false crossovers
- Each false signal triggered a trade, paying fees twice (entry + exit)
- Backtester showed promise because it didn't account for real-world friction

**Critical Discovery:** Fees matter more than the strategy itself when trading frequently

**Repository Structure:**
```
/phase1-sma-crossover
  ├── strategies/
  │   └── sma_crossover.py
  ├── backtester/
  │   ├── basic_backtester.py (very basic, AI-generated)
  │   └── results.json
  ├── analysis/
  │   └── fee_impact_analysis.md
  └── README.md
```

---

### Attempt 3: Meme Token Front-Running

**Concept:** Enter newly minted tokens faster than others, exit before the rug pull happens.

**Why We Tried This:** 
Anzu observed that meme tokens on Solana blockchain spike dramatically then collapse. The idea was to automate being first in and first out.

**Obstacles Encountered:**
- Most cryptocurrency exchanges deliberately don't support this (for good reason)
- Attempted to use Helius RPC for direct blockchain access
- Required understanding of WebSockets, transaction signing, and async code
- Documentation was poor and often incorrect
- Implementation via AI without understanding fundamentals

**What We Learned:** 
The speed advantage doesn't actually exist at retail scale. Exchanges have good reasons to prevent this behavior.

**Repository Structure:**
```
/phase1-meme-tokens
  ├── solana/
  │   ├── websocket_listener.py (abandoned)
  │   ├── transaction_signing.py (abandoned)
  │   └── helius_rpc_config.py
  ├── docs/
  │   └── why_this_failed.md
  └── README.md
```

---

## Phase 2: Strategy Research & Refinement (July - September 2024)

Shifted from creating original strategies to evaluating published trading strategies.

**Self-Assessment at This Point:**
- Cumulative time invested: approximately 100 hours
- Realized we needed better foundational understanding
- Began focusing on validated approaches rather than novel ideas

### Attempt 4: Published TradingView Strategies

**Concept:** Purchase pre-built strategies from TradingView, test them with real exchange APIs.

**How It Would Work:**
- Find strategy published on TradingView
- Pay for access to strategy code
- Strategy sends buy/sell signals via webhook
- Custom server receives signals and executes on Kraken exchange

**Implementation Details:**
- Built async Quart web server to receive webhooks
- Created Kraken exchange integration
- Implemented order management (entry/exit)

**What Went Wrong:**
- Published strategies that show perfect backtests are perfect because of survivorship bias
- Didn't understand order types and risk management (stop losses, limits)
- Fee structure made many strategies unprofitable in practice
- Each strategy was a black box—no way to understand logic

**Key Realization:** If a strategy actually works, the creator keeps it private and uses it themselves. Anything sold publicly is either not profitable or the creator found something better.

**Repository Structure:**
```
/phase2-trading-view-strategies
  ├── webhook_server/
  │   ├── quart_server.py
  │   ├── config.yaml
  │   └── webhook_handler.py
  ├── exchange_integration/
  │   ├── kraken_api_wrapper.py
  │   ├── order_management.py
  │   └── portfolio_partitioning.py (over-engineered)
  ├── frontend/
  │   ├── dashboard.html
  │   └── performance_charts.js
  ├── docs/
  │   ├── webhook_setup.md
  │   ├── order_types_explained.md
  │   └── lessons_from_failure.md
  └── README.md
```

---

### Technical Decisions During Phase 2

**Synchronous vs. Asynchronous Code:**

Initial Implementation (Flask):
- Flask is synchronous: each request blocks until it completes
- Problem: If receiving two trade signals simultaneously, one would be processed after the other
- In fast-moving markets, this lag matters

Solution (Quart):
- Quart is asynchronous: multiple requests can be processed concurrently
- Allows simultaneous entry and exit handling
- Required learning async/await syntax

**Code Complexity Mistakes:**

Built 2000+ lines of Python including:
- Manual Kraken authentication (hashing API keys)
- Portfolio partitioning for multiple investors
- Excessive code for simple tasks

Why This Was Wrong:
- Portfolio tracking is easier on a spreadsheet than in code
- Premature optimization for problems that didn't exist
- Created technical debt early

**Repository Structure for Technical Decisions:**
```
/phase2-technical-decisions
  ├── sync_vs_async/
  │   ├── flask_version.py
  │   ├── quart_version.py
  │   └── comparison.md
  ├── authentication/
  │   ├── manual_hashing.py (unnecessary complexity)
  │   ├── api_key_management.md
  │   └── why_manual_signing_is_bad.md
  ├── portfolio_management/
  │   ├── spreadsheet_approach.xlsx (works fine)
  │   ├── code_approach.py (over-engineered)
  │   └── analysis.md
  └── README.md
```

---

## Phase 3: Building Infrastructure (September - November 2024)

**Context:** Realized TradingView's limitations were holding us back. Needed custom systems.

### Why Leave TradingView

Limitations Discovered:
- Only provides 2.5 months of historical data per token (we had collected 150-200GB)
- Expensive subscription ($1000/year for comprehensive access)
- Backtesting window too short to validate strategies properly
- Pine Script (TradingView's language) doesn't transfer knowledge to other projects

**Decision:** Build custom Python-based backtesting and execution infrastructure.

---

### Challenge: Converting Pine Script to Python

**The Problem:**
- Anzu developed an entire strategy in Pine Script (TradingView's language)
- Strategy couldn't be ported to Python without complete rewrite
- Initial conversion attempts lost critical trading logic

**Initial Failures:**
- First Python version was completely unprofitable
- Second version made progress but still wasn't reliable
- Lacked understanding of what the original logic actually did

**What Eventually Worked:**
- Built proper testing environment to validate each conversion step
- Used better AI tools (Claude) with clearer prompts about strategy logic
- Implemented quick feedback loop: convert → test → validate → iterate
- Final version proved profitable in backtests

**Key Insight:** The bottleneck wasn't technology—it was clear communication between team members about what the strategy actually does.

**Repository Structure:**
```
/phase3-infrastructure
  ├── strategy_conversion/
  │   ├── pine_script_original.txt
  │   ├── python_v1_failed.py
  │   ├── python_v2_still_broken.py
  │   ├── python_v3_working.py
  │   ├── comparison_tests.py
  │   └── conversion_guide.md
  ├── backtesting_engine/
  │   ├── vectorbt_wrapper.py
  │   ├── historical_data_loader.py
  │   ├── performance_metrics.py
  │   └── docs/vectorization_explained.md
  ├── data_pipeline/
  │   ├── data_collection.py
  │   ├── data_cleaning.py
  │   └── docs/data_sources.md
  ├── web_server/
  │   ├── server.py
  │   ├── signal_detection.py
  │   ├── order_execution.py
  │   └── monitoring_dashboard.html
  └── README.md
```

---

### Technical Decision: VectorBT for Backtesting

**Evaluation Process:**

Considered Three Options:

1. Lean Engine (by QuantConnect)
   - Pros: Industrial-grade, handles multi-asset strategies
   - Cons: $700K setup fee, $1M/year, overcomplicated for our needs
   - Decision: Rejected—overkill

2. Nautilus Trader
   - Pros: Open source, optimized for speed, good documentation
   - Cons: Still complex, designed for more advanced use cases
   - Decision: Rejected—unnecessary overhead

3. VectorBT
   - Pros: Lightweight, fast, straightforward API, open source
   - Cons: Less feature-rich than enterprise solutions
   - Decision: Chosen

**Why VectorBT Worked:**
- Uses vectorization: pre-compute all indicator values rather than simulating bar-by-bar
- Vastly faster simulation (millions of trades in seconds)
- Python-native, integrates easily with existing code
- Simple enough to understand and modify
- No unnecessary complexity

**Repository Structure for Backtesting Decisions:**
```
/phase3-backtesting-comparison
  ├── vectorbt_approach/
  │   ├── implementation.py
  │   ├── speed_benchmarks.txt
  │   └── why_we_chose_it.md
  ├── lean_engine_attempt/
  │   └── why_we_rejected_it.md
  ├── nautilus_attempt/
  │   └── why_we_rejected_it.md
  └── README.md
```

---

## Phase 4: Range Filter Strategy Development (October - November 2024)

**Timeline Note:** This phase represents work from 2-3 months ago. The project has evolved beyond this.

### Strategy: Conditional Exponential Moving Average (Range Filter)

**How It Works:**

1. Calculate exponential moving average (EMA) of closing prices
2. Draw bands (upper and lower) around the EMA
3. These bands normally stay in place—only update when price breaks outside them
4. Create signals by combining two range filters and watching their middle lines cross

**Why This Approach:**
- In choppy/sideways markets: fewer false signals (bands don't update, so no whipsaws)
- In trending markets: quick signal generation when price breaks out
- Fewer total trades = lower fee impact

**Comparison to SMA Crossover:**
- SMA: Lines constantly adjust, creating false signals in choppy markets
- Range Filter: Lines stay stable until significant movement occurs
- Result: Better risk/reward in backtests

**Repository Structure:**
```
/phase4-range-filter-strategy
  ├── indicators/
  │   ├── range_filter.py
  │   │   ├── conditional_ema.py
  │   │   ├── band_calculation.py
  │   │   └── crossover_detection.py
  │   └── tests/
  │       └── test_range_filter.py
  ├── strategy/
  │   ├── dual_range_filter_strategy.py
  │   ├── entry_logic.py
  │   ├── exit_logic.py
  │   └── signal_generation.py
  ├── backtests/
  │   ├── results_2024.json
  │   ├── performance_analysis.md
  │   └── sample_trades.csv
  ├── docs/
  │   ├── strategy_explanation.md
  │   ├── why_single_filter_failed.md
  │   ├── dual_filter_improvements.md
  │   └── parameter_optimization.md
  └── README.md
```

---

### Parameter Optimization & Scoring Systems

**The Core Problem:**
When backtesting, how do you tell an optimizer what "good" means? Different metrics matter:

Metrics to Balance:
- Total Profit: How much money the strategy makes
- Max Drawdown: Largest peak-to-trough decline (measures risk)
- Sharpe Ratio: Risk-adjusted returns
- Trade Count: Need enough trades to prove consistency (not just one lucky trade)

**Mathematical Approach:**

Example: If you want optimal number of trades at 300:
```
trades_score = -(trades - 300)^2 + 300
```

At 300 trades: score = 300
At 250 or 350 trades: score = 375 (worse)

**The Weighting Problem:**

If profit_weight = 1000x and trades_weight = 1, the optimizer will:
- Ignore the trades constraint
- Hunt for one lucky trade that makes 200% profit
- Ignore actual consistency

**Solution:** Design scoring functions that balance competing metrics without letting one dominate.

**Repository Structure:**
```
/phase4-optimization
  ├── scoring_systems/
  │   ├── simple_scoring.py
  │   ├── advanced_scoring.py
  │   ├── examples.py
  │   └── docs/optimization_explained.md
  ├── parameter_space/
  │   ├── grid_search.py
  │   ├── results/
  │   │   └── optimal_parameters.json
  │   └── README.md
  └── README.md
```

---

## Phase 5: Recent Exploration (November - December 2024)

**Timeline Note:** This represents work from 1-2 months ago. Projects have evolved since.

### Exchange Fee Changes

During development, Kraken adjusted their fee structure:
- Previous: 0.25% per trade
- Updated: 0.02% per trade

This 10x reduction meant strategies could be much more aggressive (execute more trades) while remaining profitable.

### Strategy Conversion Success

The conversion of the range filter strategy from Pine Script to Python eventually succeeded after multiple iterations. This opened the door to building custom infrastructure rather than relying on TradingView.

---

## Summary: What This Historical Record Shows

This documentation captures several months of experimentation and exploration in quantitative trading. Each phase represents:

- Attempting different strategies (news analysis, SMA, meme tokens)
- Learning about cryptocurrency exchanges and blockchain mechanics
- Building infrastructure (web servers, backtesting engines)
- Understanding technical decisions (async vs sync, library choices)
- Iterating on a strategy (range filters) through multiple versions

**What's Happened Since:**
The project has moved well beyond these historical experiments. Many of these approaches have been superseded, new strategies have been developed, and the infrastructure has evolved significantly. This document exists to show the learning process and decision-making that occurred during this period.

---

## Key Files Across Phases

Understand the Decision-Making:
- Read phase README files to see what was tried and why
- Look at "why_this_failed.md" documents for learning opportunities
- Study technical_decisions documentation to see tradeoff analysis

See the Experiments:
- Deprecated code is left in place to show the journey
- Multiple versions of the same solution show iteration
- Comparison documents explain why one approach won over another

---

## Disclaimer

This is a historical record of development work conducted several months ago. Many approaches documented here were experiments conducted to gain understanding and experience. The project has evolved significantly since these phases, and this documentation should not be viewed as representative of current project direction or capabilities.

The goal of this document is to show learning process, iterative improvement, and problem-solving approaches—not to demonstrate production-ready systems.

---

**Last Updated:** December 2024  
**Document Type:** Historical Development Record
