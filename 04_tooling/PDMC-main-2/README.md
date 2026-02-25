# Solana Token Sniping Bot - Complete Setup & Usage Guide

## 🚀 Overview

A high-performance TypeScript bot for sniping newly launched tokens on Raydium (Solana). Detects new tokens in real-time, executes trades within seconds, and manages positions with advanced risk controls.

### Key Features
- ⚡ Sub-second token detection via Helius WebSocket
- 🎯 Immediate execution with Jito MEV protection  
- 💰 80/20 profit strategy (80% at target, 20% trailing)
- 🛡️ Comprehensive risk management & rug detection
- 📊 Handles 1000+ trades daily with structured logging
- 🔧 Fully configurable via environment variables

## 📋 Prerequisites

- **Node.js** v18+ and npm v9+
- **Solana Wallet** with SOL for trading
- **Helius API Key** (free tier works)
- **RPC Endpoints** (Helius recommended)
- **Basic Command Line Knowledge**

## 🗂️ Project Structure

```
solana-token-sniping-bot/
│
├── index.ts              # Main bot implementation (single file)
├── .env                  # Configuration file (create from .env.example)
├── .env.example          # Example configuration template
├── package.json          # Dependencies and scripts
├── tsconfig.json         # TypeScript configuration
├── README.md            # This file
├── logs/                # Log files directory (auto-created)
│   ├── error.log       # Error logs
│   └── trades.log      # Trade execution logs
└── .gitignore          # Git ignore file
```

## 🛠️ Installation Guide

### Step 1: Clone or Create Project

```bash
# Create project directory
mkdir solana-token-sniping-bot
cd solana-token-sniping-bot

# Initialize npm project
npm init -y
```

### Step 2: Create Required Files

#### Create `package.json`:
```json
{
  "name": "solana-token-sniping-bot",
  "version": "1.0.0",
  "description": "High-performance Solana token sniping bot for Raydium",
  "main": "index.ts",
  "scripts": {
    "start": "tsx index.ts",
    "dev": "tsx --watch index.ts",
    "build": "tsc",
    "test": "echo \"Error: no test specified\" && exit 1"
  },
  "keywords": ["solana", "trading", "bot", "raydium"],
  "author": "",
  "license": "MIT",
  "dependencies": {
    "@solana/web3.js": "^1.95.2",
    "@solana/spl-token": "^0.4.8",
    "@raydium-io/raydium-sdk-v2": "^0.1.138-alpha",
    "@raydium-io/raydium-sdk": "^1.3.1-beta.58",
    "ws": "^8.18.0",
    "axios": "^1.7.7",
    "bs58": "^6.0.0",
    "winston": "^3.14.2",
    "dotenv": "^16.4.5"
  },
  "devDependencies": {
    "@types/node": "^22.5.4",
    "@types/ws": "^8.5.12",
    "typescript": "^5.6.2",
    "tsx": "^4.19.1"
  }
}
```

#### Create `tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "lib": ["ES2022"],
    "outDir": "./dist",
    "rootDir": "./",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "moduleResolution": "node",
    "allowSyntheticDefaultImports": true,
    "types": ["node"]
  },
  "include": ["index.ts"],
  "exclude": ["node_modules", "dist"]
}
```

#### Create `.gitignore`:
```
# Dependencies
node_modules/

# Environment
.env
.env.local
.env.*.local

# Logs
logs/
*.log

# Build
dist/
build/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Private keys - NEVER commit these!
*.pem
*.key
private-key.json
wallet.json
```

#### Create `.env.example`:
```bash
# Network Configuration
HELIUS_RPC_ENDPOINT=https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY
HELIUS_WS_ENDPOINT=wss://atlas-mainnet.helius-rpc.com/?api-key=YOUR_API_KEY
SOLANA_RPC_ENDPOINT=https://api.mainnet-beta.solana.com
COMMITMENT_LEVEL=confirmed

# Wallet Configuration
PRIVATE_KEY=your_base58_private_key_here
QUOTE_MINT=So11111111111111111111111111111111111111112  # WSOL
QUOTE_AMOUNT=10000000  # 0.01 SOL in lamports

# Trading Parameters
AUTO_BUY_DELAY=100  # ms delay before buying
TAKE_PROFIT_PERCENT=25  # 25% profit target
STOP_LOSS_PERCENT=15   # 15% stop loss
TRAILING_STOP_PERCENT=5  # 5% trailing stop for remaining 20%
SLIPPAGE_BPS=1000      # 10% slippage tolerance

# Risk Management
MIN_LIQUIDITY_SOL=1    # Minimum 1 SOL liquidity
MAX_LIQUIDITY_SOL=100  # Maximum 100 SOL liquidity
MAX_POSITION_SIZE=50000000  # 0.05 SOL max position
RUG_CHECK_ENABLED=true

# Performance Settings
COMPUTE_UNIT_LIMIT=200000
COMPUTE_UNIT_PRICE=50000  # Micro lamports
JITO_TIP_AMOUNT=10000     # Lamports
MAX_RETRIES=3
TRANSACTION_TIMEOUT=30000  # 30 seconds

# Features
ENABLE_JITO_BUNDLES=true
ENABLE_MEV_PROTECTION=true
LOG_LEVEL=info
HEALTH_CHECK_INTERVAL=30000  # 30 seconds
```

### Step 3: Install Dependencies

```bash
# Install all dependencies
npm install

# If you encounter issues, try:
npm install --legacy-peer-deps
```

### Step 4: Copy Bot Code

1. Copy the entire `index.ts` code from the implementation artifact
2. Save it as `index.ts` in your project directory

### Step 5: Configure Environment

```bash
# Copy example to create your config
cp .env.example .env

# Edit .env with your settings
nano .env  # or use your preferred editor
```

## ⚙️ Configuration Guide

### Essential Settings

#### 1. **Get Helius API Key**
- Sign up at [helius.dev](https://helius.dev)
- Get your free API key
- Replace `YOUR_API_KEY` in both RPC endpoints

#### 2. **Get Your Wallet Private Key**
- Use Phantom or Solflare wallet
- Export private key (Settings → Security)
- Convert to Base58 format if needed
- **NEVER share this key!**

#### 3. **Adjust Trading Parameters**
```bash
# Conservative settings for testing
QUOTE_AMOUNT=1000000      # 0.001 SOL per trade
TAKE_PROFIT_PERCENT=10    # 10% profit target
STOP_LOSS_PERCENT=5       # 5% stop loss

# Aggressive settings for production
QUOTE_AMOUNT=100000000    # 0.1 SOL per trade
TAKE_PROFIT_PERCENT=50    # 50% profit target
STOP_LOSS_PERCENT=20      # 20% stop loss
```

### Network Selection

#### Mainnet (Production):
```bash
HELIUS_RPC_ENDPOINT=https://mainnet.helius-rpc.com/?api-key=YOUR_API_KEY
HELIUS_WS_ENDPOINT=wss://atlas-mainnet.helius-rpc.com/?api-key=YOUR_API_KEY
```

#### Devnet (Testing):
```bash
HELIUS_RPC_ENDPOINT=https://devnet.helius-rpc.com/?api-key=YOUR_API_KEY
HELIUS_WS_ENDPOINT=wss://atlas-devnet.helius-rpc.com/?api-key=YOUR_API_KEY
```

## 🚀 Running the Bot

### Development Mode (with auto-reload):
```bash
npm run dev
```

### Production Mode:
```bash
npm start
```

### Using PM2 (recommended for production):
```bash
# Install PM2 globally
npm install -g pm2

# Start bot with PM2
pm2 start index.ts --name "solana-sniper"

# View logs
pm2 logs solana-sniper

# Monitor
pm2 monit

# Stop
pm2 stop solana-sniper

# Restart
pm2 restart solana-sniper
```

### Using Docker (optional):
```dockerfile
# Create Dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
CMD ["npm", "start"]
```

```bash
# Build and run
docker build -t solana-sniper .
docker run -d --name sniper-bot --env-file .env solana-sniper
```

## 📊 Monitoring & Logs

### Log Files Location:
- **Trade Logs**: `./logs/trades.log`
- **Error Logs**: `./logs/error.log`
- **Console Output**: Real-time status updates

### Log Format:
```json
{
  "timestamp": "2024-01-15T10:30:45.123Z",
  "level": "info",
  "message": "Buy order executed successfully",
  "signature": "5xKJ9...",
  "token": "DezXA...",
  "executionTime": 1234
}
```

### Monitoring Health:
```bash
# View real-time logs
tail -f logs/trades.log

# Monitor errors
tail -f logs/error.log | grep ERROR

# Check bot stats (logged every 30s)
grep "Health check" logs/trades.log
```

## 🛡️ Security Best Practices

### 1. **Private Key Security**
```bash
# Use environment variable instead of hardcoding
export PRIVATE_KEY=$(cat ~/secure-location/wallet-key.txt)

# Or use a key management service
# AWS Secrets Manager, HashiCorp Vault, etc.
```

### 2. **API Key Protection**
- Never commit `.env` file
- Use separate keys for dev/prod
- Rotate keys regularly
- Monitor usage on Helius dashboard

### 3. **Wallet Security**
- Use a dedicated trading wallet
- Keep minimal funds (only what you're willing to risk)
- Regular withdrawals to cold storage
- Enable 2FA on exchange accounts

### 4. **Server Security**
- Use VPS with good network connectivity
- Enable firewall (only allow necessary ports)
- Regular security updates
- Use VPN for remote access

## 🔧 Troubleshooting

### Common Issues & Solutions

#### 1. **WebSocket Connection Failed**
```
Error: WebSocket connection failed
```
**Solution**:
- Check Helius API key is valid
- Ensure WebSocket endpoint is correct
- Try switching between atlas and normal endpoints
- Check firewall isn't blocking WebSocket connections

#### 2. **Transaction Failures**
```
Error: Transaction simulation failed
```
**Solution**:
- Increase slippage tolerance
- Check wallet has enough SOL for fees
- Ensure compute units are sufficient
- Try disabling Jito bundles temporarily

#### 3. **Rate Limiting**
```
Error: 429 Too Many Requests
```
**Solution**:
- Upgrade Helius plan for higher limits
- Add delays between requests
- Use multiple RPC endpoints
- Implement exponential backoff

#### 4. **Insufficient Funds**
```
Error: Insufficient funds for transaction
```
**Solution**:
- Check wallet balance
- Reduce `QUOTE_AMOUNT`
- Account for transaction fees (~0.000005 SOL per tx)
- Ensure enough for Jito tips if enabled

## 🎯 Optimization Tips

### 1. **Network Latency**
- Use VPS in same region as Solana validators
- Recommended: AWS us-east-1, Contabo US East
- Ping test to RPC endpoints should be <50ms

### 2. **Transaction Speed**
- Enable Jito bundles for faster execution
- Increase compute unit price during congestion
- Use priority fees strategically
- Pre-create token accounts when possible

### 3. **Profitability**
- Start with small amounts to test strategy
- Monitor win rate and adjust parameters
- Focus on tokens with 1-10 SOL liquidity
- Avoid tokens with concentrated ownership

### 4. **Risk Management**
- Never invest more than you can afford to lose
- Use stop losses religiously
- Diversify across multiple positions
- Keep detailed trade logs for analysis

## 📈 Performance Metrics

### Expected Performance:
- **Token Detection**: <100ms from creation
- **Trade Execution**: <2 seconds total
- **Success Rate**: 60-80% (market dependent)
- **Daily Trades**: 500-2000+
- **ROI**: Highly variable (10-100%+ possible)

### Tracking Metrics:
```typescript
// Built-in stats tracking
{
  tokensDetected: 1523,
  tradesExecuted: 743,
  successfulTrades: 592,
  failedTrades: 151,
  successRate: "79.68%",
  totalPnL: 15.23  // SOL
}
```

## 🤝 Support & Community

### Getting Help:
1. Check logs for detailed error messages
2. Review configuration settings
3. Ensure all dependencies are installed
4. Verify Solana network status

### Useful Resources:
- [Solana Documentation](https://docs.solana.com)
- [Raydium SDK Docs](https://github.com/raydium-io/raydium-sdk-V2)
- [Helius Documentation](https://docs.helius.dev)
- [Jito Labs](https://jito.wtf)

## ⚠️ Disclaimer

**IMPORTANT**: Cryptocurrency trading carries significant risk. This bot is provided for educational purposes. Users are responsible for their own trading decisions and potential losses. Always:
- Test thoroughly on devnet first
- Start with small amounts
- Never invest more than you can afford to lose
- Understand the risks of automated trading
- Comply with local regulations

## 📝 License

MIT License - See LICENSE file for details

---

**Happy Trading! 🚀** Remember to always trade responsibly and never risk more than you can afford to lose.