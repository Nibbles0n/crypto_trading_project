# Bananas Trading Bot

A production-ready crypto trading system with Python trading engine and Node.js web dashboard.

## Table of Contents
1. [What's Complete](#whats-complete)
2. [What Needs Attention](#what-needs-attention)
3. [Quick Setup](#quick-setup)
4. [Configuration](#configuration)
5. [Running the Bot](#running-the-bot)
6. [Architecture Overview](#architecture-overview)
7. [API Reference](#api-reference)
8. [Troubleshooting](#troubleshooting)

---

## What's Complete

### Trading Engine (bot.py)
- [x] Binance data fetching (5-minute OHLCV bars for USDT pairs)
- [x] Kraken order execution (limit orders at bid/ask)
- [x] Telegram notifications (trade entries, exits, errors, startup)
- [x] Integration with strategy.py (Dual Range Filter Pro V5.0)
- [x] Position management and state persistence
- [x] LONG ONLY mode (short signals ignored - Ontario restrictions)
- [x] Order timeout handling (25 minute limit order cancellation)
- [x] Flask REST API for dashboard communication
- [x] Connection status monitoring (Binance, Kraken, Telegram)
- [x] P&L tracking and history

### Web Dashboard (web/)
- [x] Professional dark theme UI with glow effects
- [x] Login authentication with bcrypt password hashing
- [x] Rate-limited login attempts (5 per 15 minutes)
- [x] Session-based authentication with secure cookies
- [x] Real-time status display (running/stopped)
- [x] Connection status indicators (Binance, Kraken, Telegram)
- [x] LIVE/SIMULATION mode badge
- [x] Token list with prices
- [x] Open positions with P&L display
- [x] P&L performance chart (line graph with Chart.js)
- [x] Activity log
- [x] Start/Stop/Exit All controls
- [x] Auto-refresh every 10 seconds

### Strategy (strategy.py)
- [x] Dual Range Filter Pro V5.0 implementation
- [x] Signal generation (SignalGenerator class)
- [x] Exit management (ExitManager class)
- [x] All filter options (quality, profit potential, price distance, cooldown)
- [x] Multiple exit modes (Signal Only, Peak Protection, etc.)

---

## What Needs Attention

### Binance Geographic Restriction
The bot currently gets a 451 error from Binance due to geographic restrictions in Ontario/Canada. Solutions:
1. **Use a VPS** in an unrestricted region (recommended for production)
2. **Use a VPN** during development/testing
3. **Switch to Binance.US** if available in your region (requires code modification)

### Live Trading Requirements
Before enabling live trading:
1. Configure Kraken API keys with proper permissions (trade, query)
2. Test in simulation mode first
3. Start with small capital allocation
4. Monitor initial trades closely

---

## Quick Setup

### Prerequisites
- Python 3.10+ 
- Node.js 18+
- npm

### Step 1: Install Python Dependencies
```bash
cd BANANAS-TRADING-BOT-FOR-DEVELOPER
pip install -r requirements.txt
```

### Step 2: Install Node.js Dependencies
```bash
cd web
npm install
cd ..
```

### Step 3: Configure Environment
```bash
# Copy example config
cp .env.example .env

# Edit .env with your settings (see Configuration section)
```

### Step 4: Generate Password Hash
```bash
cd web
node -e "require('bcrypt').hash('YOUR_PASSWORD_HERE', 10, (e,h) => console.log(h))"
# Copy the output hash to WEB_PASSWORD_HASH in .env
cd ..
```

### Step 5: Start Services
```bash
# Terminal 1: Start Python bot
python3 bot.py

# Terminal 2: Start web dashboard
cd web && node server.js
```

### Step 6: Access Dashboard
Open http://localhost:3001 and login with your password.

---

## Configuration

### .env File Structure

```env
# =============================================================================
# EXCHANGE APIS
# =============================================================================
# Kraken (leave empty for simulation mode)
KRAKEN_API_KEY=your_api_key
KRAKEN_API_SECRET=your_api_secret

# =============================================================================
# TELEGRAM NOTIFICATIONS
# =============================================================================
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# =============================================================================
# TRADING CONFIG
# =============================================================================
MAX_HOLDINGS=5                 # Max simultaneous positions
TOTAL_CAPITAL=10000            # Starting capital in USD
TRADING_TOKENS=JUP,RAD,AUDIO,WIF,BONK,PEPE,HOOK,DOGE

# =============================================================================
# RANGE FILTER 1 (Optimized Settings)
# =============================================================================
RF1_RANGE_SCALE=Normalized Average Change
RF1_RANGE_SIZE=1.1
RF1_RANGE_PERIOD=19
RF1_FILTER_TYPE=Type 1
RF1_MOVEMENT_SOURCE=Wicks
RF1_SMOOTH_RANGE=true
RF1_SMOOTHING_PERIOD=95
RF1_AVG_FILTER_CHANGES=true
RF1_CHANGES_TO_AVG=2

# =============================================================================
# RANGE FILTER 2 (Optimized Settings)
# =============================================================================
RF2_RANGE_SCALE=Normalized Average Change
RF2_RANGE_SIZE=6.2
RF2_RANGE_PERIOD=24
RF2_FILTER_TYPE=Type 1
RF2_MOVEMENT_SOURCE=Wicks
RF2_SMOOTH_RANGE=true
RF2_SMOOTHING_PERIOD=60
RF2_AVG_FILTER_CHANGES=false
RF2_CHANGES_TO_AVG=2

# =============================================================================
# SIGNAL FILTERS
# =============================================================================
EXIT_MODE=Signal Only          # Options: "Signal Only", "Signal + Peak Protection"
ENABLE_PROFIT_POTENTIAL=true
MIN_PROFIT_POTENTIAL=1.4
ENABLE_QUALITY_FILTER=true
MIN_QUALITY_SCORE=0
QUALITY_LOOKBACK=21
MIN_SIGNAL_RATING=1
SHOW_ALL_SIGNALS=false
USE_COOLDOWN=false
COOLDOWN_BARS=0
ENABLE_PRICE_DISTANCE_FILTER=true
MIN_PRICE_DISTANCE_PCT=0.5
USE_ALTERNATE_SIGNALS=false
ENABLE_SIGNAL_SIZING=true

# =============================================================================
# EXIT SETTINGS
# =============================================================================
MAX_PROFIT_CAP=100.0
USE_PROFIT_CAP=false
MAX_LOSS_CAP=100.0
USE_LOSS_CAP=false
PEAK_PROFIT_TRIGGER=100.0
PEAK_DRAWDOWN_PCT_INPUT=100.0
PEAK_LOOKBACK_BARS=2
MIN_PROFIT_THRESHOLD=0.0
ENABLE_SAME_DIRECTION_AUTOCLOSE=false
USE_REGIME_ADAPTIVE_EXITS=false

# =============================================================================
# WEB DASHBOARD
# =============================================================================
WEB_PASSWORD_HASH=$2b$10$...   # bcrypt hash of your password
SESSION_SECRET=change-this-to-a-random-string
WEB_PORT=3001
BOT_API_PORT=5001
BOT_API_URL=http://localhost:5001
```

---

## Running the Bot

### Development Mode (Simulation)
Leave KRAKEN_API_KEY and KRAKEN_API_SECRET empty in .env:
```bash
# Terminal 1
python3 bot.py

# Terminal 2
cd web && node server.js
```

### Production Mode (Live Trading)
1. Add Kraken API credentials to .env
2. Add Telegram credentials for alerts
3. Deploy to a VPS in an unrestricted region
4. Use a process manager (pm2, systemd) for reliability

### Using PM2 (Recommended for Production)
```bash
# Install PM2
npm install -g pm2

# Start bot
pm2 start bot.py --interpreter python3 --name "trading-bot"

# Start dashboard
cd web && pm2 start server.js --name "dashboard"

# Save process list
pm2 save

# Setup auto-start
pm2 startup
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         BANANAS TRADING BOT                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐         │
│  │   BINANCE    │────▶│   bot.py     │────▶│   KRAKEN     │         │
│  │  (5m Data)   │     │  (Engine)    │     │  (Execution) │         │
│  └──────────────┘     └──────────────┘     └──────────────┘         │
│                              │                                       │
│                              │ Flask API                             │
│                              ▼                                       │
│                       ┌──────────────┐     ┌──────────────┐         │
│                       │  server.js   │────▶│  Dashboard   │         │
│                       │  (Node.js)   │     │   (HTML)     │         │
│                       └──────────────┘     └──────────────┘         │
│                              │                                       │
│                              ▼                                       │
│                       ┌──────────────┐                               │
│                       │  TELEGRAM    │                               │
│                       │  (Alerts)    │                               │
│                       └──────────────┘                               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

Data Flow:
1. Binance provides 5-minute OHLCV data (USDT pairs)
2. strategy.py processes bars and generates signals
3. LONG signals trigger limit buy orders on Kraken (at ASK price)
4. Exit signals trigger limit sell orders on Kraken (at BID price)
5. Telegram sends notifications for all trades and errors
6. Dashboard displays real-time status via API proxy
```

---

## API Reference

### Bot API (Port 5001)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/status` | GET | Bot status, connections, config |
| `/api/positions` | GET | Open positions with P&L |
| `/api/tokens` | GET | Token list with prices |
| `/api/pnl_history` | GET | P&L history for chart |
| `/api/logs` | GET | Recent activity logs |
| `/api/start` | POST | Start trading loop |
| `/api/stop` | POST | Stop trading loop |
| `/api/exit_all` | POST | Emergency exit all positions |

### Dashboard (Port 3001)

| Route | Description |
|-------|-------------|
| `/` | Redirects to /login or /dashboard |
| `/login` | Login page |
| `/dashboard` | Main dashboard (requires auth) |
| `/logout` | Logout |
| `/api/*` | Proxied to bot API (requires auth) |

---

## Troubleshooting

### Binance 451 Error
```
"Service unavailable from a restricted location"
```
**Solution:** Use a VPS or VPN in an unrestricted region.

### Port Already in Use
```
Address already in use
Port 5001 is in use by another program
```
**Solution:** 
```bash
# Find process using port
lsof -i :5001

# Kill it
kill -9 <PID>
```

### Login Not Working
1. Verify password hash was generated correctly
2. Check WEB_PASSWORD_HASH in .env matches the hash
3. Ensure .env is in parent directory of web/

### Bot Not Fetching Data
1. Check Binance connection status in dashboard
2. Verify tokens are in USDT pairs on Binance
3. Check logs for specific errors

### Orders Not Executing
1. Verify Kraken API keys have trade permissions
2. Check Kraken connection status in dashboard
3. Ensure sufficient balance on Kraken
4. Verify token symbol mapping (BTC -> XBT for Kraken)

---

## File Structure

```
BANANAS-TRADING-BOT-FOR-DEVELOPER/
├── bot.py              # Main trading engine
├── strategy.py         # Dual Range Filter Pro V5.0
├── requirements.txt    # Python dependencies
├── .env.example        # Environment template
├── .gitignore          # Git ignore rules
├── README.md           # This file
├── logs/               # Log files directory
└── web/
    ├── server.js       # Express.js backend
    ├── dashboard.html  # Dashboard UI
    ├── login.html      # Login page
    └── package.json    # Node.js dependencies
```

---

## Backtest Configuration Reference

The current configuration matches the "Ultra-Optimized High Win Rate Config" backtest:

| Metric | Result |
|--------|--------|
| Total Return | 4,789,348% |
| Win Rate | 76.45% |
| Max Drawdown | 26.92% |
| Profit Factor | 3.86 |
| Total Trades | 692 |
| Sharpe Ratio | 7.79 |

Validated across 8 tokens: JUP, RAD, AUDIO, WIF, BONK, PEPE, HOOK, DOGE

---

## Support

For issues or questions, review the troubleshooting section or check the logs in the `logs/` directory.
