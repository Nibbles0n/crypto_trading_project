# The Bot Story: A Journey Through Quantitative Trading

This is the story of how Anzu and I started building a quantitative trading bot. It’s not just a list of code updates; it’s a record of a year-long obsession that started in Grade 9, surviving everything from canoe trips to high school exams.

### 1. The "News Scraper" Disaster
At the very beginning, we didn't really know what we were doing. Our first big idea was: "What if we use AI to analyze CBC news and predict where the market goes over the next week?" 

I started scraping websites in Python, but I didn't know how to process the data or build the features. I couldn't even get it to make a guess. It was a complete dead end, but it was the first time I realized that the markets move way faster than the news cycle.

### 2. SMA Crossover & Sideways Markets
Next, we tried the classic "SMA Crossover"—two moving averages crossing each other. It looks perfect on a backtest until the market goes sideways. Then, those two lines get close together, any tiny move triggers a trade, and suddenly you've done 100 trades in a day, made $0, and lost everything to exchange fees. 

### 3. Shadow Boxing on Solana
By summer, we got ambitious. We wanted to buy meme coins the second they were minted on the Solana blockchain. We were using the Helius RPC to manually sign transactions. 

I was opening two-way WebSocket connections without really understanding how "web stuff" worked. I was being blindly led by AI, and the documentation was horrible. I spent 100+ hours on it, and it never quite worked. It was the "blind leading the blind," but it was where I learned that documentation is often a liar.

### 4. The Summer Pivot
I went on a canoe trip for most of the summer. While I was off-grid, Anzu (who doesn't code but loves day trading) was obsessively looking at charts on TradingView. He realized that trying to exit faster than everyone else on new tokens was a game we couldn't win. We needed a real strategy.

### 5. The Webhook Valley & "Burner Cash"
When we got back, we fell into the trap of buying "proven" strategies online. These were clearly over-optimized to look good in advertisements but were meaningless in the real market. We set up a Kraken account with $50 of "test money" (my own savings). 

Between laggy webhooks and me not understanding how limit orders worked, our $50 "stop loss" would just vanish. We realized that if a strategy isn't good enough to profit despite high fees, it's not a real strategy.

### 6. Range Filters & The Asynchrony Breakthrough
Anzu found the "Range Filter"—a conditional moving average that stays flat during chop but moves during trends. It was better, but TradingView was limiting us to only 2.5 months of data. 

I spent the next month building a 2,000-line backend from scratch using **Quart**. I needed it to be asynchronous because I was handling real-time WebSockets and I couldn't have one token update blocking another. This was the moment coding got real—I moved past just copy-pasting AI code and started manually writing logic line-by-line using the AI more as a "distilled library" than a crutch.

### 7. The Python Conversion
The biggest hurdle was getting Anzu’s complex Pine Script logic out of TradingView and into native Python. This is where Claude 3.5 Sonnet came in. It allowed Anzu to bridge the gap between his trading logic and my Python environment as he started to actually understand the code himself.

Suddenly, we weren't limited to 2 months of data. We were running backtests on 3+ years of history—the equivalent of 600 years of trading across our token list. 

### 8. Card Counting in a Casino
People ask why we do this if it’s risky. For us, it’s about the engineering challenge. We're "card counting at a casino." We aren't just gambling; we're identifying market inefficiencies and building the math to capture them. 

The goal isn't just the money—it’s the ability to build cool stuff. If this works, maybe I can finally buy that vibration-dampened lightboard I've wanted to experiment with optics on. For now, we're just focused on identifying the patterns, refining the math, and seeing if we can play the game better than the "suckers" on the other side of the trade.
