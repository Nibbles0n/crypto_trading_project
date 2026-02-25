# Solana Raydium Trading Bot Documentation

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Core Components](#core-components)
6. [Trading Strategy](#trading-strategy)
7. [Risk Management](#risk-management)
8. [Performance Optimization](#performance-optimization)
9. [Testing](#testing)
10. [Production Deployment](#production-deployment)
11. [Monitoring & Maintenance](#monitoring--maintenance)
12. [Troubleshooting](#troubleshooting)

## Overview

The Solana Raydium Trading Bot is a high-performance automated trading system designed to:
- Detect new token launches on Raydium instantly
- Execute trades within 1-2 seconds of pool creation
- Manage multiple positions concurrently
- Implement sophisticated risk management strategies
- Handle hundreds to thousands of trades daily

### Key Features
- **Ultra-low latency detection** (<3 seconds) using Helius Geyser Enhanced WebSockets
- **MEV protection** through Jito bundle transactions
- **Dynamic risk management** with rug pull detection
- **Trailing stop-loss** for maximizing profits
- **Concurrent position management**
- **Automatic retry mechanisms** for failed transactions
- **Comprehensive logging** and monitoring

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Trading Bot Core                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │   WebSocket  │  │   Raydium    │  │  Risk Management  │ │
│  │   Listener   │  │   SDK v2     │  │     Module        │ │
│  └──────┬──────┘  └──────┬───────┘  └─────────┬─────────┘ │
│         │                 │                     │           │
│         ▼                 ▼                     ▼           │
│  ┌────────────────────────────────────────────────────┐    │
│  │              Transaction Processor                  │    │
│  │  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │    │
│  │  │   Jito   │  │ Priority │  │  Retry Logic   │  │    │
│  │  │   MEV    │  │   Fees   │  │                │  │    │
│  │  └──────────┘  └──────────┘  └────────────────┘  │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Solana Network    │
                    │  ┌───────────────┐  │
                    │  │ Raydium Pools │  │
                    │  └───────────────┘  │
                    └─────────────────────┘
```

### Data Flow

1. **Pool Detection**: WebSocket monitors Raydium AMM program logs
2. **Validation**: Filters for SOL/USDC quote pairs
3. **Execution**: Immediate buy order with MEV protection
4. **Monitoring**: Real-time price tracking of positions
5. **Risk Management**: Continuous evaluation of exit conditions

## Installation

### Prerequisites
- Node.js v18+ and npm/yarn
- TypeScript 5.0+
- Solana CLI tools
- Funded Solana wallet

### Setup Steps

```bash
# Clone the repository
git clone <repository-url>
cd solana-trading-bot

# Install dependencies
npm install

# Create environment file
cp .env.example .env

# Configure your environment variables
nano .env

# Build the project
npm run build

# Run tests
npm test

# Start the bot
npm start
```

### Required Dependencies

```json
{
  "dependencies": {
    "@solana/web3.js": "^1.91.0",
    "@solana/spl-token": "^0.4.0",
    "@raydium-io/raydium-sdk-v2": "^0.1.0",
    "@debridge-finance/solana-transaction-parser": "^0.5.0",
    "ws": "^8.16.0",
    "winston": "^3.11.0",
    "dotenv": "^16.4.0",
    "bs58": "^5.0.0",
    "bn.js": "^5.2.1"
  }
}
```

## Configuration

### Environment Variables

```bash
# RPC Configuration
HELIUS_API_KEY=your_helius_api_key_here
HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
HELIUS_WS_URL=wss://mainnet.helius-rpc.com/?api-key=YOUR_KEY

# Wallet Configuration
PRIVATE_KEY=your_base58_encoded_private_key

# Trading Parameters
BUY_AMOUNT_SOL=0.1              # Amount of SOL to spend per trade
TAKE_PROFIT_PERCENT=5            # Take profit at 5% gain
STOP_LOSS_PERCENT=2              # Stop loss at 2% loss
TRAILING_STOP_PERCENT=2          # Trailing stop trigger
SLIPPAGE_BUY=5                   # Buy slippage tolerance (%)
SLIPPAGE_SELL=15                 # Sell slippage tolerance (%)

# Risk Management
MAX_CONCURRENT_POSITIONS=10      # Maximum open positions
MIN_LIQUIDITY_USD=0              # Minimum pool liquidity (0 = no limit)
MAX_POSITION_SIZE_SOL=1          # Maximum position size
RUG_PULL_THRESHOLD_USD=100       # Sell detection threshold

# MEV Protection
USE_MEV_PROTECTION=true          # Enable Jito MEV protection
JITO_TIP_LAMPORTS=10000          # Jito tip amount (0.00001 SOL)
```

### Configuration Best Practices

1. **API Keys Security**
   - Never commit API keys to version control
   - Use environment variables or secure vaults
   - Rotate keys regularly

2. **Trading Parameters**
   - Start with small amounts for testing
   - Adjust slippage based on market conditions
   - Monitor and tune parameters regularly

3. **Risk Limits**
   - Set conservative position limits initially
   - Increase gradually as you gain confidence
   - Always maintain stop-loss protection

## Core Components

### 1. WebSocket Listener

The WebSocket listener connects to Helius Geyser Enhanced WebSockets for ultra-low latency event streaming:

```typescript
// Subscription to Raydium AMM logs
{
  "method": "logsSubscribe",
  "params": [{
    "mentions": ["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"]
  }, {
    "commitment": "confirmed"
  }]
}
```

**Key Features:**
- Automatic reconnection on disconnect
- Message deduplication
- Event filtering for `initialize2` instructions

### 2. Pool Information Extractor

Extracts critical pool data from transactions:
- Pool address (account index 4)
- Base mint (account index 8)
- Quote mint (account index 9)
- Validates quote token is SOL or USDC

### 3. Transaction Builder

Constructs optimized swap transactions:
- Uses Raydium SDK v2 for swap calculations
- Adds compute budget instructions
- Implements Jito bundle protection
- Calculates appropriate slippage

### 4. Position Manager

Tracks and manages all active positions:
- Entry price and amount
- Take profit and stop loss levels
- Trailing stop activation
- Position status tracking

### 5. Risk Management Module

Monitors positions for risk events:
- Liquidity removal detection
- Large sell order detection
- Price manipulation checks
- Automatic exit triggers

## Trading Strategy

### Entry Conditions
1. **New Pool Detection**: Pool must be newly created (initialize2)
2. **Quote Token Validation**: Must be SOL or USDC
3. **Position Limit**: Below maximum concurrent positions
4. **No Liquidity Threshold**: Trades execute immediately

### Exit Strategy
1. **Take Profit (5%)**:
   - Sell 80% of position at 5% profit
   - Activate trailing stop for remaining 20%

2. **Stop Loss (2%)**:
   - Exit full position at 2% loss
   - Use higher slippage for emergency exit

3. **Trailing Stop**:
   - Tracks highest price after partial sell
   - Exits at 2% drawdown from peak

4. **Risk Triggers**:
   - Liquidity removal → Immediate exit
   - Large sell detected → Immediate exit

## Risk Management

### Rug Pull Detection

The bot monitors for several rug pull indicators:

1. **Liquidity Removal**
   ```typescript
   if (logs.includes('RemoveLiquidity')) {
     // Immediate exit with 2x slippage
   }
   ```

2. **Large Sell Detection**
   - Monitors recent transactions
   - Checks for sells > $100 threshold
   - Analyzes token balance changes

3. **Transaction Parsing**
   - Uses DeBridge parser for deep analysis
   - Identifies suspicious patterns
   - Tracks developer wallets

### Position Sizing

- Fixed position size per trade
- Maximum position limits
- Portfolio diversification rules
- Emergency exit protocols

## Performance Optimization

### 1. RPC Optimization
- **Helius Business Plan**: Required for Geyser WebSockets
- **Dedicated RPC**: Consider for <1s latency
- **Geographic Location**: Host near Solana validators

### 2. Transaction Speed
- **Priority Fees**: Dynamic adjustment based on network
- **Jito Bundles**: Guaranteed inclusion
- **Skip Preflight**: Faster submission
- **Parallel Processing**: Handle multiple positions

### 3. Code Optimization
- **Async Operations**: Non-blocking execution
- **Connection Pooling**: Reuse RPC connections
- **Caching**: Store frequently accessed data
- **Batch Operations**: Group similar requests

## Testing

### Devnet Testing

1. **Setup Devnet Environment**
   ```bash
   export SOLANA_NETWORK=devnet
   export RPC_URL=https://api.devnet.solana.com
   ```

2. **Create Test Pools**
   - Use Raydium devnet deployment
   - Create pools with test tokens
   - Simulate various scenarios

3. **Test Cases**
   - New pool detection speed
   - Transaction execution reliability
   - Risk management triggers
   - Error handling and recovery

### Performance Testing

```typescript
// Measure detection latency
const detectionTime = Date.now() - poolCreationTime;
console.log(`Detection latency: ${detectionTime}ms`);

// Track execution speed
const executionTime = Date.now() - detectionTime;
console.log(`Execution time: ${executionTime}ms`);
```

## Production Deployment

### 1. Infrastructure Requirements

- **Server**: AWS EC2 or similar (us-east-1 recommended)
- **CPU**: 4+ cores for concurrent processing
- **RAM**: 8GB minimum
- **Network**: Low-latency connection to Solana

### 2. Security Considerations

- **Private Key Storage**: Use hardware wallets or HSM
- **API Key Management**: Rotate regularly
- **Access Control**: Implement IP whitelisting
- **Monitoring**: Set up alerts for anomalies

### 3. Deployment Process

```bash
# Build production bundle
npm run build:prod

# Deploy using PM2
pm2 start ecosystem.config.js

# Monitor logs
pm2 logs trading-bot

# Set up auto-restart
pm2 startup
pm2 save
```

### 4. Backup and Recovery

- Regular wallet backups
- Transaction log archival
- Position state snapshots
- Automated recovery procedures

## Monitoring & Maintenance

### Key Metrics to Monitor

1. **Performance Metrics**
   - Detection latency (<3 seconds target)
   - Execution success rate (>95% target)
   - Profit/Loss tracking
   - Gas costs analysis

2. **System Health**
   - WebSocket connection status
   - RPC node response times
   - Memory and CPU usage
   - Error rates and types

3. **Trading Metrics**
   - Win rate percentage
   - Average profit per trade
   - Maximum drawdown
   - Position distribution

### Maintenance Tasks

- **Daily**: Review logs and performance
- **Weekly**: Analyze trading patterns
- **Monthly**: Update dependencies
- **Quarterly**: Strategy optimization

## Troubleshooting

### Common Issues and Solutions

1. **WebSocket Disconnections**
   - Check API key validity
   - Verify network connectivity
   - Review rate limits

2. **Transaction Failures**
   - Increase priority fees
   - Adjust slippage settings
   - Check wallet balance

3. **Missed Opportunities**
   - Reduce processing overhead
   - Optimize detection logic
   - Upgrade RPC plan

4. **High Slippage**
   - Reduce position size
   - Increase slippage tolerance
   - Use Jito bundles

### Debug Mode

Enable detailed logging:
```typescript
process.env.LOG_LEVEL = 'debug';
```

### Support Resources

- Helius Discord: Technical support for RPC issues
- Raydium Discord: AMM-specific questions
- Solana Stack Exchange: General Solana development
- GitHub Issues: Bot-specific problems

## Best Practices Summary

1. **Start Small**: Test with minimal amounts
2. **Monitor Closely**: Watch initial performance
3. **Iterate Quickly**: Adjust parameters based on results
4. **Stay Updated**: Follow Solana ecosystem changes
5. **Risk First**: Never trade more than you can afford to lose

## Disclaimer

This trading bot is provided for educational purposes. Cryptocurrency trading carries significant risk. Always conduct your own research and never invest more than you can afford to lose. Past performance does not guarantee future results.