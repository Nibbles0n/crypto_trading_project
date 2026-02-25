# Quick Setup Guide - Solana Trading Bot

## Prerequisites Checklist

- [ ] Node.js v18+ installed
- [ ] Solana wallet with SOL for trading
- [ ] Helius API key (Business plan required)

## Step 1: Clone and Install (1 minute)

```bash
# Create project directory
mkdir solana-trading-bot
cd solana-trading-bot

# Initialize project
npm init -y

# Install dependencies
npm install @solana/web3.js@^1.91.0 @solana/spl-token@^0.4.0 @raydium-io/raydium-sdk-v2@^0.1.0 @debridge-finance/solana-transaction-parser@^0.5.0 ws@^8.16.0 winston@^3.11.0 dotenv@^16.4.0 bs58@^5.0.0 bn.js@^5.2.1 typescript@^5.0.0 @types/node @types/ws

instalations have been changeed

# Install dev dependencies
npm install --save-dev ts-node nodemon
```

## Step 2: Create Project Structure (30 seconds)

```bash
# Create directories
mkdir src logs

# Create main files
touch src/index.ts .env .gitignore tsconfig.json
```

## Step 3: Configure TypeScript (30 seconds)

Create `tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "lib": ["ES2022"],
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "moduleResolution": "node"
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

## Step 4: Set Up Environment Variables (1 minute)

Create `.env` file:
```bash
# Get your Helius API key from https://helius.dev
HELIUS_API_KEY=your_helius_api_key_here

# Export your wallet private key (base58 format)
# Use: solana-keygen pubkey --outfile /dev/null | base58
PRIVATE_KEY=your_base58_private_key_here

# Trading Configuration (start conservative)
BUY_AMOUNT_SOL=0.01
TAKE_PROFIT_PERCENT=5
STOP_LOSS_PERCENT=2
TRAILING_STOP_PERCENT=2
SLIPPAGE_BUY=5
SLIPPAGE_SELL=15

# Risk Management
MAX_CONCURRENT_POSITIONS=3
RUG_PULL_THRESHOLD_USD=100

# MEV Protection
USE_MEV_PROTECTION=true
JITO_TIP_LAMPORTS=10000
```

## Step 5: Add the Bot Code (1 minute)

Copy the main trading bot code from the artifact above into `src/index.ts`

## Step 6: Create Helper Scripts (30 seconds)

Update `package.json`:
```json
{
  "scripts": {
    "start": "ts-node src/index.ts",
    "dev": "nodemon --exec ts-node src/index.ts",
    "build": "tsc",
    "test:connection": "ts-node src/test-connection.ts"
  }
}
```

## Step 7: Test Your Setup (1 minute)

Create `src/test-connection.ts`:
```typescript
import { Connection, LAMPORTS_PER_SOL } from '@solana/web3.js';
import dotenv from 'dotenv';

dotenv.config();

async function testConnection() {
    try {
        const connection = new Connection(
            `https://mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY}`,
            'confirmed'
        );
        
        const version = await connection.getVersion();
        console.log('✅ Connected to Solana:', version);
        
        const slot = await connection.getSlot();
        console.log('✅ Current slot:', slot);
        
        console.log('✅ All systems operational!');
    } catch (error) {
        console.error('❌ Connection failed:', error);
    }
}

testConnection();
```

Run the test:
```bash
npm run test:connection
```

## Step 8: Start Trading (30 seconds)

### Development Mode (with auto-restart):
```bash
npm run dev
```

### Production Mode:
```bash
npm start
```

## Safety Checklist Before Going Live

1. **Test on Devnet First**
   - Change RPC to devnet
   - Use test SOL from faucet
   - Verify all systems work

2. **Start Small**
   - Begin with 0.01 SOL per trade
   - Limit to 3 concurrent positions
   - Monitor closely for first 24 hours

3. **Security**
   - Never share your private key
   - Use a dedicated trading wallet
   - Keep most funds in cold storage

4. **Risk Management**
   - Set stop losses on every trade
   - Never trade more than you can afford to lose
   - Monitor the bot regularly

## Common Issues & Solutions

### Issue: "WebSocket connection failed"
**Solution**: Check your Helius API key and ensure you have the Business plan

### Issue: "Insufficient SOL balance"
**Solution**: Ensure your wallet has enough SOL for trades + gas fees (keep 0.1 SOL minimum for fees)

### Issue: "Transaction simulation failed"
**Solution**: Increase slippage settings or reduce trade size

### Issue: "Rate limit exceeded"
**Solution**: Upgrade your Helius plan or reduce monitoring frequency

## Monitoring Your Bot

Watch the logs:
```bash
# View all logs
tail -f logs/trades.log

# View only errors
tail -f logs/error.log

# Monitor in real-time
npm run dev
```

## Next Steps

1. **Optimize Performance**
   - Deploy to AWS EC2 in us-east-1
   - Use PM2 for process management
   - Set up monitoring alerts

2. **Enhance Strategy**
   - Adjust parameters based on results
   - Add custom filters for tokens
   - Implement advanced risk metrics

3. **Scale Operations**
   - Increase position limits gradually
   - Add more trading pairs
   - Consider dedicated RPC node

## Emergency Stop

To immediately stop the bot and close all positions:
```bash
# Press Ctrl+C twice
# Or kill the process
pkill -f "node.*index.ts"
```

## Support Resources

- **Helius Discord**: https://discord.gg/helius
- **Raydium Discord**: https://discord.gg/raydium
- **Solana Stack Exchange**: https://solana.stackexchange.com

---

⚠️ **Remember**: Crypto trading is risky. Start small, test thoroughly, and never invest more than you can afford to lose.