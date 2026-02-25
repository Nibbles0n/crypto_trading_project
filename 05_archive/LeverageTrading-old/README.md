# UT Bot Trading System - Usage Guide

## Overview
This UT Bot implementation translates the popular TradingView Pine Script indicator into a fully automated Python trading bot using the Alpaca API.

## What the UT Bot Does

The UT Bot (Ultimate Trend Bot) is a trend-following indicator that:
- Uses ATR (Average True Range) to create dynamic trailing stops
- Generates buy signals when price crosses above the trailing stop
- Generates sell signals when price crosses below the trailing stop
- **Only executes trades when signals CHANGE** (not on every signal occurrence)
- Automatically reverses positions: Long → Short or Short → Long
- Adapts to market volatility automatically

## Signal Logic

**Key Feature: Signal-Change-Only Trading**
- ✅ **BUY signal appears**: Opens LONG position (closes SHORT if exists)
- ✅ **SELL signal appears**: Opens SHORT position (closes LONG if exists)  
- ❌ **Same signal continues**: NO action taken (avoids overtrading)
- ⚪ **Signal goes neutral**: Maintains current position (unless `CLOSE_ON_NEUTRAL=True`)

This creates a true trend-following system that only trades on trend changes!

## Setup Instructions

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Get Alpaca API Keys
1. Sign up at [Alpaca](https://alpaca.markets/)
2. Go to your dashboard and generate API keys
3. Start with paper trading keys for testing

### 3. Configure Environment
1. Copy the `.env.example` to `.env`
2. Fill in your API keys and desired parameters
3. Start with `PAPER_TRADING=True` and `DRY_RUN=True` for safety

### 4. Run the Bot
```bash
python ut_bot.py
```

## Key Parameters Explained

### UT Bot Settings
- **UT_KEY_VALUE**: Controls sensitivity (lower = more signals)
- **UT_ATR_PERIOD**: ATR calculation period (lower = more responsive)
- **UT_USE_HEIKIN_ASHI**: Use Heikin Ashi candles for smoother signals

### Risk Management
- **STOP_LOSS_PCT**: Automatic stop loss percentage
- **TAKE_PROFIT_PCT**: Automatic take profit percentage
- **POSITION_SIZE**: Amount to trade per signal

### Execution Control
- **DRY_RUN**: Test mode without real trades
- **EXECUTION_DELAY**: Time between signal checks
- **CLOSE_ON_NEUTRAL**: Close positions when signal goes neutral (default: False)

## Testing Strategy

### Phase 1: Dry Run Testing
```bash
PAPER_TRADING=True
DRY_RUN=True
```
- Bot will log signals without executing trades
- Monitor signal quality and frequency

### Phase 2: Paper Trading
```bash
PAPER_TRADING=True
DRY_RUN=False
```
- Bot executes trades with fake money
- Test order execution and position management

### Phase 3: Live Trading (Small Size)
```bash
PAPER_TRADING=False
DRY_RUN=False
POSITION_SIZE=100  # Start small!
```

## Monitoring Your Bot

The bot logs important information with enhanced clarity:
- 🚀 **Signal detection**: "Signal CHANGED from buy to SELL"
- ✅ **Trade execution**: "LONG position opened: 100 shares"
- 💰 **Current status**: Price, ATR stop, signal states
- ⚠️ **Errors and warnings**: Clear problem identification

**Example Log Output:**
```
🔄 Initialized signal state: neutral
Signal CHANGED from neutral to BUY at $150.25
Closing SHORT position before going LONG
✅ LONG position opened: 100 shares at $150.25
Position REVERSED to LONG
💰 $150.30 | ATR Stop: $148.50 | Position: 1 | Previous: buy | Current: buy
```

## Common Parameter Combinations

### Conservative (Fewer, Higher Quality Signals)
```
UT_KEY_VALUE=2.0
UT_ATR_PERIOD=20
TIMEFRAME=15Min
```

### Aggressive (More Frequent Signals)
```
UT_KEY_VALUE=0.5
UT_ATR_PERIOD=5
TIMEFRAME=1Min
```

### Swing Trading (Daily Signals)
```
UT_KEY_VALUE=1.5
UT_ATR_PERIOD=14
TIMEFRAME=1Day
```

## Important Safety Notes

1. **Always test first** with paper trading
2. **Start with small position sizes** when going live
3. **Monitor performance regularly** and adjust parameters
4. **Signal-change-only trading** reduces overtrading and transaction costs
5. **Position reversal system** - bot automatically switches Long ↔ Short
6. **Have proper risk management** - never risk more than you can afford to lose
7. **Understand the strategy** - UT Bot works best in trending markets
8. **Market conditions matter** - consider pausing during high volatility events

## Troubleshooting

### Common Issues
- **Insufficient data**: Increase `LOOKBACK_DAYS`
- **Too many signals**: Increase `UT_KEY_VALUE` or `UT_ATR_PERIOD`
- **Too few signals**: Decrease `UT_KEY_VALUE` or `UT_ATR_PERIOD`
- **API errors**: Check your keys and internet connection

### Performance Optimization
- Use appropriate `EXECUTION_DELAY` for your timeframe
- Don't over-optimize parameters on limited data
- Consider transaction costs in your profit calculations

## Disclaimer

This bot is for educational purposes. Trading involves risk, and past performance doesn't guarantee future results. Always trade responsibly and consider consulting with a financial advisor.