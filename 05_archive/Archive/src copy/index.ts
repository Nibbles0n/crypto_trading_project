import { EventEmitter } from 'events';
import { config } from 'dotenv';
import { Connection, Keypair, PublicKey, LAMPORTS_PER_SOL } from '@solana/web3.js';
import bs58 from 'bs58';

// Import all modules
import { UltraFastSniperBot } from './sniper-bot';
import { AdvancedPoolParser } from './pool-parser';
import { OptimizedTransactionBuilder } from './transaction-builder';
import { BotDashboardIntegration } from './websocket-server';
import { logger } from './logger';

// Load environment variables
config();

// ===== COMPLETE BOT CLASS =====
export class CompleteSniperBot extends EventEmitter {
  private sniperBot: UltraFastSniperBot;
  private poolParser: AdvancedPoolParser;
  private txBuilder: OptimizedTransactionBuilder;
  private dashboard?: BotDashboardIntegration;
  private connection: Connection;
  private wallet: Keypair;
  
  // Configuration
  private config = {
    // RPC Configuration
    RPC_ENDPOINT: process.env.HELIUS_RPC_URL || '',
    RPC_WEBSOCKET: process.env.HELIUS_WS_URL || '',
    
    // Trading Configuration
    ENABLE_TRADING: process.env.ENABLE_TRADING === 'true',
    USE_DASHBOARD: process.env.USE_DASHBOARD !== 'false',
    DASHBOARD_PORT: parseInt(process.env.DASHBOARD_PORT || '8080'),
    
    // Risk Limits
    MAX_DAILY_LOSS: parseFloat(process.env.MAX_DAILY_LOSS || '50'),
    MAX_POSITION_SIZE: parseFloat(process.env.MAX_POSITION_SIZE || '0.05'),
    
    // Performance
    PARALLEL_CONNECTIONS: parseInt(process.env.PARALLEL_CONNECTIONS || '3'),
  };
  
  constructor() {
    super();
    
    // Validate configuration
    this.validateConfig();
    
    // Initialize connection
    this.connection = new Connection(this.config.RPC_ENDPOINT, {
      commitment: 'processed',
      wsEndpoint: this.config.RPC_WEBSOCKET,
      confirmTransactionInitialTimeout: 5000
    });
    
    // Initialize wallet
    const privateKey = process.env.PRIVATE_KEY;
    if (!privateKey) {
      throw new Error('PRIVATE_KEY not set in environment');
    }
    
    this.wallet = Keypair.fromSecretKey(bs58.decode(privateKey));
    
    // Initialize components
    this.sniperBot = new UltraFastSniperBot();
    this.poolParser = new AdvancedPoolParser(this.connection);
    this.txBuilder = new OptimizedTransactionBuilder(this.connection);
    
    // Setup dashboard if enabled
    if (this.config.USE_DASHBOARD) {
      this.dashboard = new BotDashboardIntegration(this, this.config.DASHBOARD_PORT);
    }
    
    this.setupEventHandlers();
    
    logger.info('🚀 Complete Sniper Bot initialized', {
      wallet: this.wallet.publicKey.toString(),
      rpc: this.config.RPC_ENDPOINT,
      dashboard: this.config.USE_DASHBOARD
    });
  }
  
  /**
   * Validate configuration
   */
  private validateConfig() {
    if (!this.config.RPC_ENDPOINT) {
      throw new Error('HELIUS_RPC_URL not configured');
    }
    
    if (!process.env.PRIVATE_KEY) {
      throw new Error('PRIVATE_KEY not configured');
    }
    
    logger.info('Configuration validated');
  }
  
  /**
   * Setup internal event handlers
   */
  private setupEventHandlers() {
    // Forward sniper bot events
    this.sniperBot.on('pool_detected', async (poolId: string) => {
      this.emit('pool_detected', poolId);
    });
    
    this.sniperBot.on('trade_executed', (trade: any) => {
      this.emit('trade', trade);
    });
    
    this.sniperBot.on('position_closed', (position: any) => {
      this.emit('position_closed', position);
    });
    
    this.sniperBot.on('stats_update', (stats: any) => {
      this.emit('stats_update', stats);
    });
  }
  
  /**
   * Start the bot
   */
  async start() {
    try {
      logger.info('Starting Complete Sniper Bot...');
      
      // Check prerequisites
      await this.checkPrerequisites();
      
      // Start dashboard
      if (this.dashboard) {
        this.dashboard.start();
      }
      
      // Pre-warm connections
      await this.warmupConnections();
      
      // Start sniper bot
      await this.sniperBot.start();
      
      this.emit('started');
      logger.info('✅ Bot started successfully');
      
      // Start monitoring
      this.startMonitoring();
      
    } catch (error) {
      logger.error('Failed to start bot', { error });
      throw error;
    }
  }
  
  /**
   * Stop the bot
   */
  async stop() {
    logger.info('Stopping bot...');
    
    try {
      // Stop sniper bot
      await this.sniperBot.stop();
      
      // Stop dashboard
      if (this.dashboard) {
        this.dashboard.stop();
      }
      
      this.emit('stopped');
      logger.info('Bot stopped');
      
    } catch (error) {
      logger.error('Error stopping bot', { error });
    }
  }
  
  /**
   * Emergency stop
   */
  async emergencyStop() {
    logger.warn('EMERGENCY STOP TRIGGERED');
    
    this.emit('alert', 'Emergency stop triggered', 'error');
    
    // Force close all positions
    await this.sniperBot.emergencyStop();
    
    // Stop everything
    await this.stop();
  }
  
  /**
   * Check prerequisites before starting
   */
  private async checkPrerequisites() {
    // Check wallet balance
    const balance = await this.connection.getBalance(this.wallet.publicKey);
    const solBalance = balance / LAMPORTS_PER_SOL;
    
    if (solBalance < 0.1) {
      throw new Error(`Insufficient balance: ${solBalance} SOL (minimum 0.1 SOL required)`);
    }
    
    logger.info('Wallet balance check passed', { balance: `${solBalance} SOL` });
    
    // Check RPC connection
    try {
      const slot = await this.connection.getSlot();
      logger.info('RPC connection verified', { slot });
    } catch (error) {
      throw new Error('Failed to connect to RPC');
    }
    
    // Test WebSocket connection
    try {
      const testSub = this.connection.onAccountChange(
        this.wallet.publicKey,
        () => {},
        'processed'
      );
      await this.connection.removeAccountChangeListener(testSub);
      logger.info('WebSocket connection verified');
    } catch (error) {
      throw new Error('Failed to establish WebSocket connection');
    }
  }
  
  /**
   * Warm up connections for better performance
   */
  private async warmupConnections() {
    logger.info('Warming up connections...');
    
    // Pre-fetch some data to establish connections
    const promises = [];
    
    for (let i = 0; i < this.config.PARALLEL_CONNECTIONS; i++) {
      promises.push(
        this.connection.getSlot(),
        this.connection.getLatestBlockhash()
      );
    }
    
    await Promise.all(promises);
    logger.info('Connections warmed up');
  }
  
  /**
   * Start monitoring systems
   */
  private startMonitoring() {
    // Monitor wallet balance
    setInterval(async () => {
      try {
        const balance = await this.connection.getBalance(this.wallet.publicKey);
        this.emit('wallet_update', balance / LAMPORTS_PER_SOL);
      } catch (error) {
        logger.error('Failed to fetch wallet balance', { error });
      }
    }, 30000); // Every 30 seconds
    
    // Monitor performance
    setInterval(() => {
      const memUsage = process.memoryUsage();
      const stats = {
        memory: (memUsage.heapUsed / 1024 / 1024).toFixed(1),
        uptime: process.uptime(),
        connections: this.connection['_rpcWebSocketConnected'] ? 'connected' : 'disconnected'
      };
      
      logger.debug('System health', stats);
    }, 60000); // Every minute
    
    // Reset daily stats at midnight
    const resetDailyStats = () => {
      const now = new Date();
      const tomorrow = new Date(now);
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(0, 0, 0, 0);
      
      const msUntilMidnight = tomorrow.getTime() - now.getTime();
      
      setTimeout(() => {
        logger.info('Resetting daily statistics');
        this.emit('daily_reset');
        resetDailyStats(); // Schedule next reset
      }, msUntilMidnight);
    };
    
    resetDailyStats();
  }
}

// ===== MAIN ENTRY POINT =====
async function main() {
  logger.info('🚀 ULTRA-FAST SNIPER BOT STARTING...');
  logger.info('Version: 2.0.0');
  logger.info('Mode: ' + (process.env.ENABLE_TRADING === 'true' ? 'LIVE TRADING' : 'SIMULATION'));
  
  try {
    // Create bot instance
    const bot = new CompleteSniperBot();
    
    // Handle shutdown signals
    process.on('SIGINT', async () => {
      logger.info('Received SIGINT, shutting down gracefully...');
      await bot.stop();
      process.exit(0);
    });
    
    process.on('SIGTERM', async () => {
      logger.info('Received SIGTERM, shutting down gracefully...');
      await bot.stop();
      process.exit(0);
    });
    
    // Handle uncaught errors
    process.on('uncaughtException', async (error) => {
      logger.error('Uncaught exception', { error });
      await bot.emergencyStop();
      process.exit(1);
    });
    
    process.on('unhandledRejection', async (reason, promise) => {
      logger.error('Unhandled rejection', { reason, promise });
      await bot.emergencyStop();
      process.exit(1);
    });
    
    // Start the bot
    await bot.start();
    
    logger.info('🎯 Bot is running and hunting for opportunities...');
    
  } catch (error) {
    logger.error('Failed to start bot', { error });
    process.exit(1);
  }
}

// Run if called directly
if (require.main === module) {
  main();
}

// Export for testing
export { main };