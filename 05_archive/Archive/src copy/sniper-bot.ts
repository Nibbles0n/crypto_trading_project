import {
  Connection,
  PublicKey,
  Keypair,
  Transaction,
  TransactionInstruction,
  ComputeBudgetProgram,
  LAMPORTS_PER_SOL,
  VersionedTransaction,
  AddressLookupTableAccount,
  TransactionMessage,
  MessageV0,
  SystemProgram,
  Logs,
  Context
} from '@solana/web3.js';
import {
  Liquidity,
  LiquidityPoolKeys,
  LiquidityPoolInfo,
  TokenAmount,
  Token,
  Currency,
  Percent,
  CurrencyAmount,
  LIQUIDITY_STATE_LAYOUT_V4,
  MARKET_STATE_LAYOUT_V3,
  SPL_MINT_LAYOUT,
  SPL_ACCOUNT_LAYOUT,
  parseBigNumberish,
  struct,
  u8,
  u64,
  publicKey as publicKeyLayout,
  buildSimpleTransaction,
  TxVersion,
  InstructionType,
  LOOKUP_TABLE_CACHE
} from '@raydium-io/raydium-sdk';
import {
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  getAssociatedTokenAddress,
  createAssociatedTokenAccountInstruction,
  getAccount,
  TokenAccountNotFoundError
} from '@solana/spl-token';
import BN from 'bn.js';
import bs58 from 'bs58';
import { logger } from './logger';

// ===== CONFIGURATION =====
const CONFIG = {
  // RPC Configuration
  RPC_ENDPOINT: process.env.HELIUS_RPC_URL || '',
  RPC_WEBSOCKET: process.env.HELIUS_WS_URL || '',
  BACKUP_RPC: process.env.BACKUP_RPC_URL || '',
  
  // Wallet
  PRIVATE_KEY: process.env.PRIVATE_KEY || '',
  
  // Raydium Program IDs (Mainnet)
  RAYDIUM_LIQUIDITY_PROGRAM_ID_V4: '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',
  RAYDIUM_MARKET_PROGRAM_ID: '9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin',
  RAYDIUM_OPENBOOK_MARKET: 'srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX',
  
  // Trading Parameters
  BUY_AMOUNT: 0.02, // SOL per trade
  GAS_AMOUNT: 0.01, // Reserve for fees
  
  // Speed Trading Parameters
  PROFIT_TARGET: 3, // 3% quick profit
  STOP_LOSS: -2, // -2% stop loss
  MAX_HOLD_TIME: 30, // 30 seconds max
  
  // Entry Criteria
  MIN_POOL_SIZE: 0.1, // Minimum 0.1 SOL liquidity
  MAX_POOL_SIZE: 1000, // Maximum 1000 SOL liquidity
  
  // Transaction Priority
  PRIORITY_FEE_LAMPORTS: 100000, // Priority fee in lamports
  COMPUTE_UNITS: 400000, // Compute units
  
  // Performance
  CHECK_INTERVAL: 500, // Price check every 500ms
  MAX_POSITIONS: 20, // Maximum concurrent positions
  
  // Risk Management
  DAILY_LOSS_LIMIT: 50, // Stop if down 50%
  CONSECUTIVE_LOSSES: 5, // Stop after 5 losses in row
};

// ===== INTERFACES =====
interface PoolData {
  id: PublicKey;
  baseMint: PublicKey;
  quoteMint: PublicKey;
  lpMint: PublicKey;
  baseDecimals: number;
  quoteDecimals: number;
  lpDecimals: number;
  version: 4;
  programId: PublicKey;
  authority: PublicKey;
  openOrders: PublicKey;
  targetOrders: PublicKey;
  baseVault: PublicKey;
  quoteVault: PublicKey;
  marketVersion: 3;
  marketProgramId: PublicKey;
  marketId: PublicKey;
  marketAuthority: PublicKey;
  marketBaseVault: PublicKey;
  marketQuoteVault: PublicKey;
  marketBids: PublicKey;
  marketAsks: PublicKey;
  marketEventQueue: PublicKey;
  withdrawQueue: PublicKey;
  lpVault: PublicKey;
  lookupTableAccount: PublicKey;
}

interface Position {
  poolId: string;
  tokenMint: PublicKey;
  entryTime: number;
  entryPrice: number;
  amount: number;
  tokenBalance: number;
  status: 'pending' | 'active' | 'exiting' | 'closed';
  buyTxId?: string;
  sellTxId?: string;
  profit?: number;
}

// ===== MAIN BOT CLASS =====
export class UltraFastSniperBot {
  private connection: Connection;
  private wallet: Keypair;
  private positions: Map<string, Position> = new Map();
  private poolCache: Map<string, PoolData> = new Map();
  private tokenAccounts: Map<string, PublicKey> = new Map();
  
  // Performance tracking
  private stats = {
    trades: 0,
    wins: 0,
    losses: 0,
    totalProfit: 0,
    consecutiveLosses: 0,
    startTime: Date.now(),
    dailyPnL: 0
  };
  
  // Risk management
  private isRunning = true;
  private monitoringIntervals: Map<string, NodeJS.Timer> = new Map();
  
  constructor() {
    // Initialize connection with custom config for speed
    this.connection = new Connection(CONFIG.RPC_ENDPOINT, {
      commitment: 'processed', // Fastest commitment
      wsEndpoint: CONFIG.RPC_WEBSOCKET,
      disableRetryOnRateLimit: false,
      confirmTransactionInitialTimeout: 5000
    });
    
    // Initialize wallet
    const privateKeyBytes = bs58.decode(CONFIG.PRIVATE_KEY);
    this.wallet = Keypair.fromSecretKey(privateKeyBytes);
    
    logger.info('🚀 Ultra-Fast Sniper Bot Initialized', {
      wallet: this.wallet.publicKey.toString(),
      config: {
        buyAmount: CONFIG.BUY_AMOUNT,
        profitTarget: CONFIG.PROFIT_TARGET,
        maxHoldTime: CONFIG.MAX_HOLD_TIME
      }
    });
  }

  // ===== MAIN START FUNCTION =====
  async start() {
    try {
      // Check wallet balance
      const balance = await this.connection.getBalance(this.wallet.publicKey);
      const solBalance = balance / LAMPORTS_PER_SOL;
      
      if (solBalance < CONFIG.BUY_AMOUNT + CONFIG.GAS_AMOUNT) {
        throw new Error(`Insufficient balance: ${solBalance} SOL`);
      }
      
      logger.info('💰 Wallet Balance', { balance: `${solBalance} SOL` });
      
      // Start monitoring for new pools
      await this.startPoolMonitoring();
      
      // Start performance monitoring
      this.startPerformanceMonitoring();
      
      logger.info('✅ Bot is running and monitoring for new pools...');
      
    } catch (error) {
      logger.error('❌ Failed to start bot', { error });
      throw error;
    }
  }

  // ===== POOL MONITORING =====
  private async startPoolMonitoring() {
    const raydiumProgram = new PublicKey(CONFIG.RAYDIUM_LIQUIDITY_PROGRAM_ID_V4);
    
    // Subscribe to logs
    this.connection.onLogs(
      raydiumProgram,
      async (logs: Logs, ctx: Context) => {
        if (!this.isRunning) return;
        
        const startTime = Date.now();
        
        try {
          // Check for pool initialization
          const initLog = logs.logs.find(log => 
            log.includes('initialize2') || 
            log.includes('init_pc_amount') ||
            log.includes('InitializeInstruction2')
          );
          
          if (!initLog) return;
          
          logger.info('🎯 New pool detected!', { 
            signature: logs.signature,
            slot: ctx.slot 
          });
          
          // Quick parse and snipe
          await this.handleNewPool(logs.signature);
          
          const elapsed = Date.now() - startTime;
          logger.debug('Pool processing time', { elapsed: `${elapsed}ms` });
          
        } catch (error) {
          logger.error('Error processing pool', { error, signature: logs.signature });
        }
      },
      'processed' // Fastest commitment level
    );
    
    logger.info('👀 Monitoring Raydium pools...');
  }

  // ===== HANDLE NEW POOL =====
  private async handleNewPool(signature: string) {
    try {
      // Fetch transaction FAST
      const tx = await this.connection.getParsedTransaction(signature, {
        maxSupportedTransactionVersion: 0,
        commitment: 'processed'
      });
      
      if (!tx?.meta || !tx.transaction) {
        logger.warn('Transaction not found', { signature });
        return;
      }
      
      // Extract pool info quickly
      const poolInfo = await this.extractPoolInfo(tx);
      if (!poolInfo) {
        logger.warn('Could not extract pool info', { signature });
        return;
      }
      
      // Quick validation
      if (!this.shouldTrade(poolInfo)) {
        logger.debug('Pool does not meet criteria', { poolId: poolInfo.id.toString() });
        return;
      }
      
      // Check position limits
      if (this.positions.size >= CONFIG.MAX_POSITIONS) {
        logger.warn('Max positions reached', { current: this.positions.size });
        return;
      }
      
      // SNIPE IT!
      await this.executeBuy(poolInfo);
      
    } catch (error) {
      logger.error('Failed to handle new pool', { error });
    }
  }

  // ===== EXTRACT POOL INFO =====
  private async extractPoolInfo(tx: any): Promise<PoolData | null> {
    try {
      // Find the instruction that creates the pool
      const instructions = tx.transaction.message.instructions;
      
      // Look for Raydium program instruction
      const raydiumIx = instructions.find(
        (ix: any) => ix.programId.toString() === CONFIG.RAYDIUM_LIQUIDITY_PROGRAM_ID_V4
      );
      
      if (!raydiumIx || !raydiumIx.accounts || raydiumIx.accounts.length < 18) {
        return null;
      }
      
      const accounts = raydiumIx.accounts;
      
      // Standard Raydium pool account structure
      const poolData: PoolData = {
        id: accounts[4],
        baseMint: accounts[8],
        quoteMint: accounts[9],
        lpMint: accounts[7],
        baseDecimals: 9, // Will fetch actual decimals
        quoteDecimals: 9,
        lpDecimals: 9,
        version: 4,
        programId: new PublicKey(CONFIG.RAYDIUM_LIQUIDITY_PROGRAM_ID_V4),
        authority: accounts[5],
        openOrders: accounts[6],
        targetOrders: accounts[13],
        baseVault: accounts[10],
        quoteVault: accounts[11],
        marketVersion: 3,
        marketProgramId: new PublicKey(CONFIG.RAYDIUM_OPENBOOK_MARKET),
        marketId: accounts[16],
        marketAuthority: accounts[0],
        marketBaseVault: accounts[0],
        marketQuoteVault: accounts[0],
        marketBids: accounts[0],
        marketAsks: accounts[0],
        marketEventQueue: accounts[0],
        withdrawQueue: accounts[0],
        lpVault: accounts[0],
        lookupTableAccount: PublicKey.default
      };
      
      // Cache pool data
      this.poolCache.set(poolData.id.toString(), poolData);
      
      return poolData;
      
    } catch (error) {
      logger.error('Failed to extract pool info', { error });
      return null;
    }
  }

  // ===== VALIDATION =====
  private shouldTrade(poolInfo: PoolData): boolean {
    // Quick checks only - speed matters
    
    // Check if SOL pool (we want to trade SOL pairs)
    const isSOLPool = 
      poolInfo.quoteMint.toString() === 'So11111111111111111111111111111111111111112' ||
      poolInfo.baseMint.toString() === 'So11111111111111111111111111111111111111112';
    
    if (!isSOLPool) {
      logger.debug('Not a SOL pool, skipping');
      return false;
    }
    
    return true;
  }

  // ===== EXECUTE BUY =====
  private async executeBuy(poolInfo: PoolData) {
    const startTime = Date.now();
    
    try {
      // Determine which is SOL and which is token
      const isBaseSOL = poolInfo.baseMint.toString() === 'So11111111111111111111111111111111111111112';
      const tokenMint = isBaseSOL ? poolInfo.quoteMint : poolInfo.baseMint;
      
      logger.info('💸 Executing BUY', {
        pool: poolInfo.id.toString(),
        token: tokenMint.toString(),
        amount: `${CONFIG.BUY_AMOUNT} SOL`
      });
      
      // Create position entry
      const position: Position = {
        poolId: poolInfo.id.toString(),
        tokenMint,
        entryTime: Date.now(),
        entryPrice: 0, // Will calculate after swap
        amount: CONFIG.BUY_AMOUNT,
        tokenBalance: 0,
        status: 'pending'
      };
      
      this.positions.set(poolInfo.id.toString(), position);
      
      // Get or create token account
      const tokenAccount = await this.getOrCreateTokenAccount(tokenMint);
      
      // Build swap transaction
      const swapTx = await this.buildSwapTransaction(
        poolInfo,
        tokenAccount,
        CONFIG.BUY_AMOUNT,
        'buy'
      );
      
      // Send transaction with high priority
      const signature = await this.sendTransactionWithPriority(swapTx);
      
      if (signature) {
        position.buyTxId = signature;
        position.status = 'active';
        
        const elapsed = Date.now() - startTime;
        logger.info('✅ BUY SUCCESS', {
          pool: poolInfo.id.toString(),
          signature,
          time: `${elapsed}ms`
        });
        
        // Start monitoring immediately
        this.startPositionMonitoring(poolInfo.id.toString());
        
        // Force exit timer
        setTimeout(() => {
          this.forceExit(poolInfo.id.toString());
        }, CONFIG.MAX_HOLD_TIME * 1000);
      } else {
        // Failed to buy, remove position
        this.positions.delete(poolInfo.id.toString());
        logger.error('Buy transaction failed');
      }
      
    } catch (error) {
      logger.error('Buy execution error', { error });
      this.positions.delete(poolInfo.id.toString());
    }
  }

  // ===== POSITION MONITORING =====
  private startPositionMonitoring(poolId: string) {
    let checkCount = 0;
    
    const interval = setInterval(async () => {
      try {
        checkCount++;
        const position = this.positions.get(poolId);
        
        if (!position || position.status !== 'active') {
          clearInterval(interval);
          this.monitoringIntervals.delete(poolId);
          return;
        }
        
        // Get current price
        const currentPrice = await this.getCurrentPrice(poolId);
        if (!currentPrice) return;
        
        // Set entry price on first check
        if (position.entryPrice === 0) {
          position.entryPrice = currentPrice;
        }
        
        // Calculate PnL
        const priceChange = ((currentPrice - position.entryPrice) / position.entryPrice) * 100;
        
        // Log price update
        if (checkCount % 5 === 0) { // Log every 2.5 seconds
          logger.debug('Price update', {
            pool: poolId.substring(0, 8),
            change: `${priceChange.toFixed(2)}%`,
            time: `${(Date.now() - position.entryTime) / 1000}s`
          });
        }
        
        // Check exit conditions
        if (priceChange >= CONFIG.PROFIT_TARGET) {
          logger.info('🎯 PROFIT TARGET HIT!', {
            pool: poolId.substring(0, 8),
            profit: `${priceChange.toFixed(2)}%`
          });
          clearInterval(interval);
          await this.executeSell(poolId, priceChange);
        } else if (priceChange <= CONFIG.STOP_LOSS) {
          logger.info('🛑 STOP LOSS HIT!', {
            pool: poolId.substring(0, 8),
            loss: `${priceChange.toFixed(2)}%`
          });
          clearInterval(interval);
          await this.executeSell(poolId, priceChange);
        }
        
      } catch (error) {
        logger.error('Monitoring error', { error, poolId });
      }
    }, CONFIG.CHECK_INTERVAL);
    
    this.monitoringIntervals.set(poolId, interval);
  }

  // ===== GET CURRENT PRICE =====
  private async getCurrentPrice(poolId: string): Promise<number | null> {
    try {
      const poolData = this.poolCache.get(poolId);
      if (!poolData) return null;
      
      // Fetch pool account
      const accountInfo = await this.connection.getAccountInfo(
        new PublicKey(poolId),
        'processed'
      );
      
      if (!accountInfo || !accountInfo.data) return null;
      
      // Decode pool state
      const poolState = LIQUIDITY_STATE_LAYOUT_V4.decode(accountInfo.data);
      
      // Calculate price from reserves
      const baseAmount = new BN(poolState.baseReserve.toString());
      const quoteAmount = new BN(poolState.quoteReserve.toString());
      
      // Determine price based on which is SOL
      const isBaseSOL = poolData.baseMint.toString() === 'So11111111111111111111111111111111111111112';
      
      if (isBaseSOL) {
        // Price = SOL per token = base / quote
        return baseAmount.toNumber() / quoteAmount.toNumber();
      } else {
        // Price = SOL per token = quote / base
        return quoteAmount.toNumber() / baseAmount.toNumber();
      }
      
    } catch (error) {
      logger.error('Failed to get price', { error });
      return null;
    }
  }

  // ===== EXECUTE SELL =====
  private async executeSell(poolId: string, profitPercent: number) {
    const startTime = Date.now();
    const position = this.positions.get(poolId);
    
    if (!position || position.status !== 'active') return;
    
    try {
      position.status = 'exiting';
      const poolData = this.poolCache.get(poolId);
      if (!poolData) throw new Error('Pool data not found');
      
      logger.info('💰 Executing SELL', {
        pool: poolId.substring(0, 8),
        profit: `${profitPercent.toFixed(2)}%`,
        holdTime: `${(Date.now() - position.entryTime) / 1000}s`
      });
      
      // Get token account
      const tokenAccount = this.tokenAccounts.get(position.tokenMint.toString());
      if (!tokenAccount) throw new Error('Token account not found');
      
      // Get token balance
      const tokenBalance = await this.getTokenBalance(tokenAccount);
      if (tokenBalance === 0) {
        logger.warn('No token balance to sell');
        position.status = 'closed';
        return;
      }
      
      // Build sell transaction (sell all tokens)
      const sellTx = await this.buildSwapTransaction(
        poolData,
        tokenAccount,
        tokenBalance,
        'sell'
      );
      
      // Send transaction
      const signature = await this.sendTransactionWithPriority(sellTx);
      
      if (signature) {
        position.sellTxId = signature;
        position.profit = profitPercent;
        position.status = 'closed';
        
        // Update stats
        this.updateStats(profitPercent, Date.now() - position.entryTime);
        
        const elapsed = Date.now() - startTime;
        logger.info('✅ SELL SUCCESS', {
          pool: poolId.substring(0, 8),
          profit: `${profitPercent.toFixed(2)}%`,
          signature,
          time: `${elapsed}ms`
        });
      }
      
    } catch (error) {
      logger.error('Sell execution error', { error });
      position.status = 'active'; // Revert status
    } finally {
      // Cleanup
      this.cleanupPosition(poolId);
    }
  }

  // ===== FORCE EXIT =====
  private async forceExit(poolId: string) {
    const position = this.positions.get(poolId);
    if (!position || position.status !== 'active') return;
    
    logger.warn('⏰ FORCE EXIT - Max hold time reached', {
      pool: poolId.substring(0, 8),
      holdTime: `${CONFIG.MAX_HOLD_TIME}s`
    });
    
    const currentPrice = await this.getCurrentPrice(poolId);
    const priceChange = currentPrice && position.entryPrice 
      ? ((currentPrice - position.entryPrice) / position.entryPrice) * 100
      : 0;
    
    await this.executeSell(poolId, priceChange);
  }

  // ===== BUILD SWAP TRANSACTION =====
  private async buildSwapTransaction(
    poolData: PoolData,
    tokenAccount: PublicKey,
    amount: number,
    direction: 'buy' | 'sell'
  ): Promise<Transaction> {
    try {
      // Fetch pool keys
      const poolKeys: LiquidityPoolKeys = {
        ...poolData,
        withdrawQueue: PublicKey.default,
        lpVault: PublicKey.default,
        lookupTableAccount: PublicKey.default
      };
      
      // Create swap instruction
      const { innerTransactions } = await Liquidity.makeSwapInstructionSimple({
        connection: this.connection,
        poolKeys,
        userKeys: {
          tokenAccounts: [tokenAccount],
          owner: this.wallet.publicKey
        },
        amountIn: direction === 'buy' 
          ? new TokenAmount(new Token(TOKEN_PROGRAM_ID, poolKeys.quoteMint, poolKeys.quoteDecimals), amount * LAMPORTS_PER_SOL)
          : new TokenAmount(new Token(TOKEN_PROGRAM_ID, poolKeys.baseMint, poolKeys.baseDecimals), amount),
        amountOut: new TokenAmount(
          new Token(TOKEN_PROGRAM_ID, direction === 'buy' ? poolKeys.baseMint : poolKeys.quoteMint, 9),
          0
        ),
        fixedSide: 'in',
        config: {
          bypassAssociatedCheck: false,
          checkTransaction: false
        }
      });
      
      // Build transaction with priority fees
      const transaction = new Transaction();
      
      // Add compute budget instructions
      transaction.add(
        ComputeBudgetProgram.setComputeUnitLimit({
          units: CONFIG.COMPUTE_UNITS
        }),
        ComputeBudgetProgram.setComputeUnitPrice({
          microLamports: CONFIG.PRIORITY_FEE_LAMPORTS
        })
      );
      
      // Add swap instructions
      transaction.add(...innerTransactions[0].instructions);
      
      // Set fee payer and recent blockhash
      transaction.feePayer = this.wallet.publicKey;
      const { blockhash } = await this.connection.getLatestBlockhash('processed');
      transaction.recentBlockhash = blockhash;
      
      return transaction;
      
    } catch (error) {
      logger.error('Failed to build swap transaction', { error });
      throw error;
    }
  }

  // ===== SEND TRANSACTION WITH PRIORITY =====
  private async sendTransactionWithPriority(transaction: Transaction): Promise<string | null> {
    try {
      // Sign transaction
      transaction.sign(this.wallet);
      
      // Serialize
      const rawTx = transaction.serialize();
      
      // Send with specific options for speed
      const signature = await this.connection.sendRawTransaction(rawTx, {
        skipPreflight: true, // Skip simulation for speed
        preflightCommitment: 'processed',
        maxRetries: 1
      });
      
      // Don't wait for confirmation - monitor separately
      this.confirmTransactionAsync(signature);
      
      return signature;
      
    } catch (error) {
      logger.error('Failed to send transaction', { error });
      return null;
    }
  }

  // ===== ASYNC CONFIRMATION =====
  private async confirmTransactionAsync(signature: string) {
    try {
      const confirmation = await this.connection.confirmTransaction(
        signature,
        'processed'
      );
      
      if (confirmation.value.err) {
        logger.error('Transaction failed', { 
          signature,
          error: confirmation.value.err 
        });
      } else {
        logger.debug('Transaction confirmed', { signature });
      }
    } catch (error) {
      logger.error('Confirmation error', { error, signature });
    }
  }

  // ===== TOKEN ACCOUNT MANAGEMENT =====
  private async getOrCreateTokenAccount(mint: PublicKey): Promise<PublicKey> {
    try {
      // Check cache
      const cached = this.tokenAccounts.get(mint.toString());
      if (cached) return cached;
      
      // Get associated token address
      const ata = await getAssociatedTokenAddress(
        mint,
        this.wallet.publicKey,
        false,
        TOKEN_PROGRAM_ID,
        ASSOCIATED_TOKEN_PROGRAM_ID
      );
      
      // Check if exists
      try {
        await getAccount(this.connection, ata, 'processed');
        this.tokenAccounts.set(mint.toString(), ata);
        return ata;
      } catch (error) {
        if (error instanceof TokenAccountNotFoundError) {
          // Create if doesn't exist
          const createIx = createAssociatedTokenAccountInstruction(
            this.wallet.publicKey,
            ata,
            this.wallet.publicKey,
            mint,
            TOKEN_PROGRAM_ID,
            ASSOCIATED_TOKEN_PROGRAM_ID
          );
          
          const tx = new Transaction().add(createIx);
          tx.feePayer = this.wallet.publicKey;
          const { blockhash } = await this.connection.getLatestBlockhash();
          tx.recentBlockhash = blockhash;
          
          await this.sendTransactionWithPriority(tx);
          
          this.tokenAccounts.set(mint.toString(), ata);
          return ata;
        }
        throw error;
      }
    } catch (error) {
      logger.error('Failed to get/create token account', { error });
      throw error;
    }
  }

  // ===== GET TOKEN BALANCE =====
  private async getTokenBalance(tokenAccount: PublicKey): Promise<number> {
    try {
      const account = await getAccount(this.connection, tokenAccount, 'processed');
      return Number(account.amount);
    } catch (error) {
      logger.error('Failed to get token balance', { error });
      return 0;
    }
  }

  // ===== CLEANUP =====
  private cleanupPosition(poolId: string) {
    // Clear monitoring interval
    const interval = this.monitoringIntervals.get(poolId);
    if (interval) {
      clearInterval(interval);
      this.monitoringIntervals.delete(poolId);
    }
    
    // Remove position after delay
    setTimeout(() => {
      this.positions.delete(poolId);
    }, 5000);
  }

  // ===== STATISTICS =====
  private updateStats(profit: number, holdTime: number) {
    this.stats.trades++;
    
    if (profit > 0) {
      this.stats.wins++;
      this.stats.consecutiveLosses = 0;
    } else {
      this.stats.losses++;
      this.stats.consecutiveLosses++;
    }
    
    this.stats.totalProfit += profit;
    this.stats.dailyPnL += profit;
    
    // Log stats every 10 trades
    if (this.stats.trades % 10 === 0) {
      this.logStats();
    }
    
    // Risk management checks
    if (this.stats.consecutiveLosses >= CONFIG.CONSECUTIVE_LOSSES) {
      logger.error('🚨 CONSECUTIVE LOSS LIMIT REACHED - STOPPING BOT');
      this.stop();
    }
    
    if (this.stats.dailyPnL <= -CONFIG.DAILY_LOSS_LIMIT) {
      logger.error('🚨 DAILY LOSS LIMIT REACHED - STOPPING BOT');
      this.stop();
    }
  }

  private logStats() {
    const runtime = (Date.now() - this.stats.startTime) / 1000 / 60; // minutes
    const winRate = this.stats.trades > 0 
      ? (this.stats.wins / this.stats.trades * 100).toFixed(1)
      : '0';
    
    logger.info('📊 PERFORMANCE STATS', {
      trades: this.stats.trades,
      wins: this.stats.wins,
      losses: this.stats.losses,
      winRate: `${winRate}%`,
      totalProfit: `${this.stats.totalProfit.toFixed(2)}%`,
      dailyPnL: `${this.stats.dailyPnL.toFixed(2)}%`,
      runtime: `${runtime.toFixed(1)} min`,
      tradesPerMin: (this.stats.trades / runtime).toFixed(1)
    });
  }

  // ===== PERFORMANCE MONITORING =====
  private startPerformanceMonitoring() {
    // Log stats every minute
    setInterval(() => {
      this.logStats();
      
      // Reset daily PnL at midnight
      const now = new Date();
      if (now.getHours() === 0 && now.getMinutes() === 0) {
        this.stats.dailyPnL = 0;
        logger.info('📅 Daily PnL reset');
      }
    }, 60000);
    
    // Monitor system health
    setInterval(() => {
      const memUsage = process.memoryUsage();
      logger.debug('System health', {
        positions: this.positions.size,
        memory: `${(memUsage.heapUsed / 1024 / 1024).toFixed(1)}MB`,
        uptime: `${(process.uptime() / 3600).toFixed(1)}h`
      });
    }, 300000); // Every 5 minutes
  }

  // ===== GRACEFUL SHUTDOWN =====
  async stop() {
    logger.info('🛑 Stopping bot...');
    this.isRunning = false;
    
    // Close all positions
    const activePositions = Array.from(this.positions.entries())
      .filter(([_, pos]) => pos.status === 'active');
    
    if (activePositions.length > 0) {
      logger.info(`Closing ${activePositions.length} active positions...`);
      
      for (const [poolId, position] of activePositions) {
        try {
          const currentPrice = await this.getCurrentPrice(poolId);
          const priceChange = currentPrice && position.entryPrice
            ? ((currentPrice - position.entryPrice) / position.entryPrice) * 100
            : 0;
          
          await this.executeSell(poolId, priceChange);
        } catch (error) {
          logger.error('Failed to close position', { error, poolId });
        }
      }
    }
    
    // Clear all intervals
    this.monitoringIntervals.forEach(interval => clearInterval(interval));
    
    // Final stats
    this.logStats();
    logger.info('👋 Bot stopped');
    
    process.exit(0);
  }
}

// ===== HELPER FUNCTIONS =====

// Raydium pool state layout decoder
const LIQUIDITY_STATE_LAYOUT_V4 = struct([
  u64('status'),
  u64('nonce'),
  u64('maxOrder'),
  u64('depth'),
  u64('baseDecimal'),
  u64('quoteDecimal'),
  u64('state'),
  u64('resetFlag'),
  u64('minSize'),
  u64('volMaxCutRatio'),
  u64('amountWaveRatio'),
  u64('baseLotSize'),
  u64('quoteLotSize'),
  u64('minPriceMultiplier'),
  u64('maxPriceMultiplier'),
  u64('systemDecimalValue'),
  publicKeyLayout('baseMint'),
  publicKeyLayout('quoteMint'),
  publicKeyLayout('baseVault'),
  publicKeyLayout('quoteVault'),
  u64('baseNeedTakePnl'),
  u64('quoteNeedTakePnl'),
  u64('quoteTotalPnl'),
  u64('baseTotalPnl'),
  u64('poolOpenTime'),
  u64('punishPcAmount'),
  u64('punishCoinAmount'),
  u64('orderbookToInitTime'),
  publicKeyLayout('swapBase2QuoteFee'),
  publicKeyLayout('swapQuote2BaseFee'),
  u64('baseNeedTakePnlPc'),
  u64('quoteNeedTakePnlPc'),
  u64('baseTotalPnlPc'),
  u64('quoteTotalPnlPc'),
  u64('poolTotalDepositPc'),
  u64('poolTotalDepositCoin'),
  u64('depth3'),
  publicKeyLayout('depth3Pc'),
  publicKeyLayout('depth3Coin')
]);

// ===== ENTRY POINT =====
async function main() {
  try {
    // Validate environment
    if (!CONFIG.RPC_ENDPOINT || !CONFIG.PRIVATE_KEY) {
      throw new Error('Missing required environment variables');
    }
    
    // Create and start bot
    const bot = new UltraFastSniperBot();
    
    // Handle shutdown signals
    process.on('SIGINT', () => bot.stop());
    process.on('SIGTERM', () => bot.stop());
    
    // Handle uncaught errors
    process.on('uncaughtException', (error) => {
      logger.error('Uncaught exception', { error });
      bot.stop();
    });
    
    process.on('unhandledRejection', (error) => {
      logger.error('Unhandled rejection', { error });
      bot.stop();
    });
    
    // Start the bot
    await bot.start();
    
  } catch (error) {
    logger.error('Failed to start', { error });
    process.exit(1);
  }
}

// Run if called directly
if (require.main === module) {
  main();
}

// Export for testing
export { CONFIG };