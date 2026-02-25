import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  VersionedTransaction,
  SystemProgram,
  LAMPORTS_PER_SOL,
  Commitment
} from '@solana/web3.js';
import {
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  getAssociatedTokenAddress,
  createAssociatedTokenAccountInstruction
} from '@solana/spl-token';
import { Raydium } from '@raydium-io/raydium-sdk-v2';
import bs58 from 'bs58';
import axios from 'axios';
import * as winston from 'winston';
import * as dotenv from 'dotenv';

//MARKET MONITOR Load environment variables
dotenv.config();

// ========== CONFIGURATION MANAGEMENT ==========
interface TradingConfig {
  // Network
  heliusRpcEndpoint: string;
  heliusWsEndpoint: string;
  solanaRpcEndpoint: string;
  commitmentLevel: Commitment;
  
  // Wallet
  privateKey: string;
  quoteMint: string;
  quoteAmount: number;
  
  // Trading
  autoBuyDelay: number;
  takeProfitPercent: number;
  stopLossPercent: number;
  trailingStopPercent: number;
  slippageBps: number;
  
  // Risk Management
  minLiquiditySol: number;
  maxLiquiditySol: number;
  maxPositionSize: number;
  rugCheckEnabled: boolean;
  
  // Performance
  computeUnitLimit: number;
  computeUnitPrice: number;
  jitoTipAmount: number;
  maxRetries: number;
  transactionTimeout: number;
  
  // Features
  enableJitoBundles: boolean;
  enableMevProtection: boolean;
  logLevel: string;
  healthCheckInterval: number;
}

class ConfigManager {
  static load(): TradingConfig {
    const requiredFields = [
      'HELIUS_RPC_ENDPOINT',
      'HELIUS_WS_ENDPOINT',
      'PRIVATE_KEY'
    ];

    // Validate required fields
    for (const field of requiredFields) {
      if (!process.env[field]) {
        throw new Error(`Missing required environment variable: ${field}`);
      }
    }

    return {
      heliusRpcEndpoint: process.env['HELIUS_RPC_ENDPOINT']!,
      heliusWsEndpoint: process.env['HELIUS_WS_ENDPOINT']!,
      solanaRpcEndpoint: process.env['SOLANA_RPC_ENDPOINT'] || 'https://api.mainnet-beta.solana.com',
      commitmentLevel: (process.env['COMMITMENT_LEVEL'] as Commitment) || 'confirmed',
      
      privateKey: process.env['PRIVATE_KEY']!,
      quoteMint: process.env['QUOTE_MINT'] || 'So11111111111111111111111111111111111111112',
      quoteAmount: parseInt(process.env['QUOTE_AMOUNT'] || '10000000'),
      
      autoBuyDelay: parseInt(process.env['AUTO_BUY_DELAY'] || '100'),
      takeProfitPercent: parseInt(process.env['TAKE_PROFIT_PERCENT'] || '25'),
      stopLossPercent: parseInt(process.env['STOP_LOSS_PERCENT'] || '15'),
      trailingStopPercent: parseInt(process.env['TRAILING_STOP_PERCENT'] || '5'),
      slippageBps: parseInt(process.env['SLIPPAGE_BPS'] || '1000'),
      
      minLiquiditySol: parseInt(process.env['MIN_LIQUIDITY_SOL'] || '1'),
      maxLiquiditySol: parseInt(process.env['MAX_LIQUIDITY_SOL'] || '100'),
      maxPositionSize: parseInt(process.env['MAX_POSITION_SIZE'] || '50000000'),
      rugCheckEnabled: process.env['RUG_CHECK_ENABLED'] === 'true',
      
      computeUnitLimit: parseInt(process.env['COMPUTE_UNIT_LIMIT'] || '200000'),
      computeUnitPrice: parseInt(process.env['COMPUTE_UNIT_PRICE'] || '50000'),
      jitoTipAmount: parseInt(process.env['JITO_TIP_AMOUNT'] || '10000'),
      maxRetries: parseInt(process.env['MAX_RETRIES'] || '3'),
      transactionTimeout: parseInt(process.env['TRANSACTION_TIMEOUT'] || '30000'),
      
      enableJitoBundles: process.env['ENABLE_JITO_BUNDLES'] === 'true',
      enableMevProtection: process.env['ENABLE_MEV_PROTECTION'] === 'true',
      logLevel: process.env['LOG_LEVEL'] || 'info',
      healthCheckInterval: parseInt(process.env['HEALTH_CHECK_INTERVAL'] || '30000')
    };
  }
}

// ========== LOGGING SYSTEM ==========
class Logger {
  private winston: winston.Logger;

  constructor(level: string = 'info') {
    this.winston = winston.createLogger({
      level,
      format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.json(),
        winston.format.errors({ stack: true })
      ),
      transports: [
        new winston.transports.File({ filename: 'error.log', level: 'error' }),
        new winston.transports.File({ filename: 'trades.log' }),
        new winston.transports.Console({
          format: winston.format.combine(
            winston.format.colorize(),
            winston.format.simple()
          )
        })
      ]
    });
  }

  info(message: string, meta?: any) {
    this.winston.info(message, meta);
  }

  warn(message: string, meta?: any) {
    this.winston.warn(message, meta);
  }

  error(message: string, meta?: any) {
    this.winston.error(message, meta);
  }

  debug(message: string, meta?: any) {
    this.winston.debug(message, meta);
  }
}

// ========== DATA TYPES ==========
interface TokenData {
  mint: string;
  symbol?: string;
  name?: string;
  poolId: string;
  baseMint: string;
  quoteMint: string;
  baseDecimals: number;
  quoteDecimals: number;
  liquidity: number;
  signature: string;
  timestamp: number;
}

interface Position {
  id: string;
  tokenMint: string;
  poolId: string;
  size: number;
  entryPrice: number;
  entryTime: number;
  stopLoss: number;
  takeProfit: number;
  trailingStop: number;
  highWaterMark: number;
  partialSoldAmount: number;
  status: 'ACTIVE' | 'PARTIAL' | 'CLOSED';
}

interface TradeResult {
  success: boolean;
  signature?: string;
  error?: string;
  slippage?: number;
  executionTime: number;
}

interface RiskAssessment {
  isRugPull: boolean;
  riskScore: number;
  warnings: string[];
  liquidityLocked: boolean;
  ownershipConcentration: number;
}

// ========== RISK MANAGEMENT ==========
class RiskManager {
  private logger: Logger;

  constructor(logger: Logger) {
    this.logger = logger;
  }

  async assessToken(tokenData: TokenData): Promise<RiskAssessment> {
    const warnings: string[] = [];
    let riskScore = 0;

    // Liquidity checks
    if (tokenData.liquidity < 1 * LAMPORTS_PER_SOL) {
      warnings.push('Very low liquidity');
      riskScore += 30;
    }

    if (tokenData.liquidity > 100 * LAMPORTS_PER_SOL) {
      warnings.push('Suspiciously high initial liquidity');
      riskScore += 20;
    }

    // Time-based checks
    const tokenAge = Date.now() - tokenData.timestamp;
    if (tokenAge < 60000) { // Less than 1 minute old
      warnings.push('Token created less than 1 minute ago');
      riskScore += 15;
    }

    // Basic rug pull indicators
    const rugPullRisk = await this.checkRugPullIndicators(tokenData.baseMint);
    if (rugPullRisk.hasRisk) {
      warnings.push(...rugPullRisk.indicators);
      riskScore += rugPullRisk.score;
    }

    return {
      isRugPull: riskScore > 70,
      riskScore,
      warnings,
      liquidityLocked: rugPullRisk.liquidityLocked,
      ownershipConcentration: rugPullRisk.ownershipConcentration
    };
  }

  private async checkRugPullIndicators(_mintAddress: string): Promise<{
    hasRisk: boolean;
    score: number;
    indicators: string[];
    liquidityLocked: boolean;
    ownershipConcentration: number;
  }> {
    // This would integrate with actual rug checkers like rugcheck.xyz
    // For demo purposes, returning basic structure
    return {
      hasRisk: false,
      score: 0,
      indicators: [],
      liquidityLocked: false,
      ownershipConcentration: 0
    };
  }

  shouldBlockTrade(assessment: RiskAssessment, config: TradingConfig): boolean {
    if (!config.rugCheckEnabled) return false;
    
    return assessment.isRugPull || assessment.riskScore > 60;
  }
}

// ========== CONNECTION MANAGER ==========
class ConnectionManager {
  private connections: Connection[] = [];
  private currentIndex = 0;
  private logger: Logger;


  constructor(endpoints: string[], logger: Logger) {
    this.logger = logger;
    this.connections = endpoints.map(endpoint => {
      if (!endpoint) {
        throw new Error(`Invalid endpoint provided: ${endpoint}`);
      }
      return new Connection(endpoint, {
        commitment: 'confirmed',
        confirmTransactionInitialTimeout: 30000
      });
  });
  if (this.connections.length === 0) {
    throw new Error('No valid endpoints provided');
  }
}

  getConnection(): Connection {
    const connection = this.connections[this.currentIndex];
    this.currentIndex = (this.currentIndex + 1) % this.connections.length;
    return connection;
  }

  async executeWithRetry<T>(
    operation: (connection: Connection) => Promise<T>,
    maxRetries: number = 3
  ): Promise<T> {
    let lastError: Error = new Error('Operation failed');

    for (let i = 0; i < maxRetries; i++) {
      try {
        const connection = this.getConnection();
        return await operation(connection);
      } catch (error) {
        lastError = error as Error;
        this.logger.warn(`Connection attempt ${i + 1} failed`, { error: error });
        
        if (i < maxRetries - 1) {
          await this.sleep(100 * Math.pow(2, i)); // Exponential backoff
        }
      }
    }

    throw lastError;
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// ========== TRANSACTION EXECUTOR ==========
class TransactionExecutor {
  private connection: Connection;
  private wallet: Keypair;
  private raydium: any;
  private logger: Logger;
  private config: TradingConfig;

  constructor(
    connection: Connection,
    wallet: Keypair,
    logger: Logger,
    config: TradingConfig
  ) {
    this.connection = connection;
    this.wallet = wallet;
    this.logger = logger;
    this.config = config;
  }

  async initialize() {
    try {
      this.raydium = await Raydium.load({
        connection: this.connection,
        owner: this.wallet,
        disableLoadToken: false
      });
      this.logger.info('Raydium SDK initialized successfully');
    } catch (error) {
      this.logger.error('Failed to initialize Raydium SDK', { error });
      throw error;
    }
  }

  async buyToken(tokenData: TokenData): Promise<TradeResult> {
    const startTime = Date.now();

    try {
      this.logger.info('Executing buy order', { 
        token: tokenData.baseMint,
        amount: this.config.quoteAmount 
      });

      // Get pool information
      const poolInfo = await this.raydium.api.fetchPoolById({ 
        ids: tokenData.poolId 
      });

      if (!poolInfo || poolInfo.data.length === 0) {
        throw new Error('Pool not found');
      }

      // Create swap transaction using new API
      const swapResponse = await axios.get(
        `https://api.raydium.io/v2/sdk/swap/route?inputMint=${this.config.quoteMint}&outputMint=${tokenData.baseMint}&amount=${this.config.quoteAmount}&slippageBps=${this.config.slippageBps}&txVersion=v0`
      );

      const { data: swapTransactions } = await axios.post(
        'https://api.raydium.io/v2/sdk/transaction/swap-base-in',
        {
          computeUnitPriceMicroLamports: this.config.computeUnitPrice,
          swapResponse: swapResponse.data,
          txVersion: '0',
          wallet: this.wallet.publicKey.toBase58(),
          wrapSol: true,
          unwrapSol: false
        }
      );

      // Build and sign transaction
      const transactionData = swapTransactions.data[0].transaction;
      const transaction = VersionedTransaction.deserialize(
        Buffer.from(transactionData, 'base64')
      );

      transaction.sign([this.wallet]);

      // Submit transaction
      let signature: string;
      
      if (this.config.enableJitoBundles) {
        signature = await this.submitJitoBundle([transaction]);
      } else {
        signature = await this.connection.sendTransaction(transaction, {
          skipPreflight: true,
          maxRetries: 0
        });
      }

      // Wait for confirmation
      const confirmation = await this.connection.confirmTransaction(
        signature,
        this.config.commitmentLevel
      );

      if (confirmation.value.err) {
        throw new Error(`Transaction failed: ${confirmation.value.err}`);
      }

      const executionTime = Date.now() - startTime;
      
      this.logger.info('Buy order executed successfully', {
        signature,
        executionTime,
        token: tokenData.baseMint
      });

      return {
        success: true,
        signature,
        executionTime
      };

    } catch (error) {
      const executionTime = Date.now() - startTime;
      this.logger.error('Buy order failed', { 
        error: (error as Error).message,
        executionTime,
        token: tokenData.baseMint
      });

      return {
        success: false,
        error: (error as Error).message,
        executionTime
      };
    }
  }

  async sellToken(
    position: Position, 
    sellPercentage: number
  ): Promise<TradeResult> {
    const startTime = Date.now();

    try {
      const sellAmount = Math.floor(position.size * sellPercentage);
      
      this.logger.info('Executing sell order', {
        token: position.tokenMint,
        amount: sellAmount,
        percentage: sellPercentage * 100
      });

      // Similar to buy but reverse direction
      const swapResponse = await axios.get(
        `https://api.raydium.io/v2/sdk/swap/route?inputMint=${position.tokenMint}&outputMint=${this.config.quoteMint}&amount=${sellAmount}&slippageBps=${this.config.slippageBps}&txVersion=v0`
      );

      const { data: swapTransactions } = await axios.post(
        'https://api.raydium.io/v2/sdk/transaction/swap-base-in',
        {
          computeUnitPriceMicroLamports: this.config.computeUnitPrice,
          swapResponse: swapResponse.data,
          txVersion: '0',
          wallet: this.wallet.publicKey.toBase58(),
          wrapSol: false,
          unwrapSol: true
        }
      );

      const transactionData = swapTransactions.data[0].transaction;
      const transaction = VersionedTransaction.deserialize(
        Buffer.from(transactionData, 'base64')
      );

      transaction.sign([this.wallet]);

      let signature: string;
      
      if (this.config.enableJitoBundles) {
        signature = await this.submitJitoBundle([transaction]);
      } else {
        signature = await this.connection.sendTransaction(transaction, {
          skipPreflight: true,
          maxRetries: 0
        });
      }

      const confirmation = await this.connection.confirmTransaction(
        signature,
        this.config.commitmentLevel
      );

      if (confirmation.value.err) {
        throw new Error(`Sell transaction failed: ${confirmation.value.err}`);
      }

      const executionTime = Date.now() - startTime;

      this.logger.info('Sell order executed successfully', {
        signature,
        executionTime,
        sellPercentage: sellPercentage * 100
      });

      return {
        success: true,
        signature,
        executionTime
      };

    } catch (error) {
      const executionTime = Date.now() - startTime;
      this.logger.error('Sell order failed', { 
        error: (error as Error).message,
        executionTime 
      });

      return {
        success: false,
        error: (error as Error).message,
        executionTime
      };
    }
  }

  private async submitJitoBundle(transactions: VersionedTransaction[]): Promise<string> {
    // Add tip transaction for Jito
    const tipInstruction = SystemProgram.transfer({
      fromPubkey: this.wallet.publicKey,
      toPubkey: new PublicKey('Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY'), // Jito tip account
      lamports: this.config.jitoTipAmount
    });

    const tipTransaction = new Transaction().add(tipInstruction);
    const { blockhash } = await this.connection.getLatestBlockhash();
    tipTransaction.recentBlockhash = blockhash;
    tipTransaction.feePayer = this.wallet.publicKey;
    tipTransaction.sign(this.wallet);

    // Convert tip transaction to versioned transaction
    const serializedTipTx = tipTransaction.serialize();
    const tipVersionedTx = VersionedTransaction.deserialize(serializedTipTx);

    // Submit bundle to Jito
    const bundle = [...transactions, tipVersionedTx];
    
    try {
      const response = await axios.post('https://mainnet.block-engine.jito.wtf/api/v1/bundles', {
        jsonrpc: '2.0',
        id: 1,
        method: 'sendBundle',
        params: [bundle.map(tx => bs58.encode(tx.serialize()))]
      });

      if (response.data.error) {
        throw new Error(`Jito bundle failed: ${response.data.error.message}`);
      }

      // Return the first transaction signature (the actual trade)
      const signatures = transactions[0].signatures;
      if (signatures && signatures.length > 0) {
        return bs58.encode(signatures[0]);
      }
      throw new Error('No signature found in transaction');
    } catch (error) {
      this.logger.warn('Jito bundle submission failed, falling back to regular RPC', { error });
      
      // Fallback to regular transaction submission
      if (!transactions[0]) {
        throw new Error('No transaction available to send');
      }
      return await this.connection.sendTransaction(transactions[0], {
        skipPreflight: true,
        maxRetries: 0
      });
    }
  }
}

// ========== POSITION MANAGER ==========
class PositionManager {
  private positions = new Map<string, Position>();
  private logger: Logger;
  private config: TradingConfig;
  private executor: TransactionExecutor;

  constructor(logger: Logger, config: TradingConfig, executor: TransactionExecutor) {
    this.logger = logger;
    this.config = config;
    this.executor = executor;
  }

  async addPosition(tokenData: TokenData, buyResult: TradeResult): Promise<void> {
    if (!buyResult.success || !buyResult.signature) return;

    const position: Position = {
      id: tokenData.baseMint,
      tokenMint: tokenData.baseMint,
      poolId: tokenData.poolId,
      size: this.config.quoteAmount, // Approximate - would need to calculate actual tokens received
      entryPrice: 0, // Would calculate from transaction
      entryTime: Date.now(),
      stopLoss: 0, // Calculate based on config
      takeProfit: 0, // Calculate based on config
      trailingStop: 0,
      highWaterMark: 0,
      partialSoldAmount: 0,
      status: 'ACTIVE'
    };

    this.positions.set(tokenData.baseMint, position);
    this.logger.info('Position added', { tokenMint: tokenData.baseMint });
  }

  async monitorPositions(): Promise<void> {
    const activePositions = Array.from(this.positions.values())
      .filter(p => p.status === 'ACTIVE' || p.status === 'PARTIAL');

    for (const position of activePositions) {
      try {
        await this.evaluatePosition(position);
      } catch (error) {
        this.logger.error('Error evaluating position', { 
          position: position.id, 
          error 
        });
      }
    }
  }

  private async evaluatePosition(position: Position): Promise<void> {
    // Get current token price (would implement price fetching logic)
    const currentPrice = await this.getCurrentPrice(position.tokenMint);
    if (!currentPrice) return;

    const profitPercent = ((currentPrice - position.entryPrice) / position.entryPrice) * 100;

    // Update high water mark for trailing stop
    if (currentPrice > position.highWaterMark) {
      position.highWaterMark = currentPrice;
      position.trailingStop = position.highWaterMark * (1 - this.config.trailingStopPercent / 100);
    }

    // Check exit conditions
    if (this.shouldTakeProfit(position, profitPercent)) {
      await this.executeTakeProfit(position);
    } else if (this.shouldStopLoss(position, profitPercent, currentPrice)) {
      await this.executeStopLoss(position);
    }
  }

  private shouldTakeProfit(position: Position, profitPercent: number): boolean {
    return profitPercent >= this.config.takeProfitPercent && position.partialSoldAmount === 0;
  }

  private shouldStopLoss(position: Position, profitPercent: number, currentPrice: number): boolean {
    return profitPercent <= -this.config.stopLossPercent || 
           (position.trailingStop > 0 && currentPrice <= position.trailingStop);
  }

  private async executeTakeProfit(position: Position): Promise<void> {
    // Sell 80% at profit target
    const sellResult = await this.executor.sellToken(position, 0.8);
    
    if (sellResult.success) {
      position.partialSoldAmount = position.size * 0.8;
      position.status = 'PARTIAL';
      
      this.logger.info('Take profit executed (80%)', {
        tokenMint: position.tokenMint,
        signature: sellResult.signature
      });
    }
  }

  private async executeStopLoss(position: Position): Promise<void> {
    // Sell remaining position
    const remainingPercentage = position.status === 'PARTIAL' ? 0.2 : 1.0;
    const sellResult = await this.executor.sellToken(position, remainingPercentage);
    
    if (sellResult.success) {
      position.status = 'CLOSED';
      
      this.logger.info('Stop loss executed', {
        tokenMint: position.tokenMint,
        signature: sellResult.signature,
        percentage: remainingPercentage * 100
      });
    }
  }

  private async getCurrentPrice(_tokenMint: string): Promise<number | null> {
    // Would implement Jupiter API or similar for price fetching
    // Placeholder implementation
    return null;
  }

  getActivePositionCount(): number {
    return Array.from(this.positions.values())
      .filter(p => p.status === 'ACTIVE' || p.status === 'PARTIAL').length;
  }
}

// ========== MARKET MONITOR ==========
import WebSocket from 'ws';

class MarketMonitor {
  private ws: WebSocket | null = null;
  private logger: Logger;
  private config: TradingConfig;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private pingInterval: NodeJS.Timeout | null = null;

  private readonly PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";

  constructor(logger: Logger, config: TradingConfig) {
    this.logger = logger;
    this.config = config;
  }

  async start(onNewToken: (tokenData: TokenData) => void): Promise<void> {
    this.connect(onNewToken);
  }

  private connect(onNewToken: (tokenData: TokenData) => void): void {
    try {
      this.ws = new WebSocket(this.config.heliusWsEndpoint);

      this.ws.addEventListener('open', () => {
        this.logger.info('WebSocket connected to Helius');
        this.reconnectAttempts = 0;
        this.subscribeToLogs();
        this.startPing();
      });

      this.ws.addEventListener('message', (event) => {
        // event.data instead of just data
        this.handleMessage(event.data, onNewToken);
      });

      this.ws.addEventListener('close', () => {
        this.logger.warn('WebSocket disconnected');
        this.cleanup();
        this.scheduleReconnect(onNewToken);
      });

      this.ws.addEventListener('error', (event) => {
        // event.error may not exist, so just log the event
        this.logger.error('WebSocket error', { event });
      });

    } catch (error) {
      this.logger.error('Failed to create WebSocket connection', { error });
      this.scheduleReconnect(onNewToken);
    }
  }

  private subscribeToLogs(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    const request = {
      jsonrpc: '2.0',
      id: 420,
      method: 'transactionSubscribe',
      params: [
        {
          failed: false,
          accountInclude: [this.PUMP_FUN_PROGRAM_ID]
        },
        {
          commitment: this.config.commitmentLevel,
          encoding: 'jsonParsed',
          transactionDetails: 'full',
          maxSupportedTransactionVersion: 0
        }
      ]
    };

    this.ws.send(JSON.stringify(request));
    this.logger.info('Subscribed to pump.fun transaction logs');
  }

  private handleMessage(data: any, onNewToken: (tokenData: TokenData) => void): void {
  try {
    this.logger.debug('Received WebSocket message', { data: data.toString() });
    const message = JSON.parse(data.toString());
    
    if (message.params?.result) {
      const result = message.params.result;
      const signature = result.signature;
      const instructions = result.transaction?.transaction?.message?.instructions || [];

      this.logger.debug('Processing transaction', { signature, instructionCount: instructions.length });

      for (const instruction of instructions) {
        if (instruction.programId === this.PUMP_FUN_PROGRAM_ID) {
          this.logger.debug('Found pump.fun instruction', { signature, instructionData: instruction.data });
          const instructionData = Buffer.from(instruction.data, 'base64');
          const createDiscriminator = Buffer.from([248, 198, 177, 38, 245, 192, 231, 108]);
          if (instructionData.length >= 8 && instructionData.subarray(0, 8).equals(createDiscriminator)) {
            const tokenMint = instruction.accounts[0];
            this.logger.info('Found create instruction', { signature, tokenMint }); // Changed to info for visibility
            this.processNewToken(result, signature, onNewToken, tokenMint);
            break;
          } else {
            this.logger.debug('Instruction is not a create instruction', { signature, dataPrefix: instructionData.subarray(0, 8).toJSON() });
          }
        }
      }
    } else {
      this.logger.debug('Message does not contain transaction result', { message });
    }
  } catch (error) {
    this.logger.error('Error processing WebSocket message', { error });
  }
}
  private async processNewToken(
  result: any,
  signature: string,
  onNewToken: (tokenData: TokenData) => void,
  tokenMint: string // Add tokenMint parameter
): Promise<void> {
  try {
    // Remove this as tokenMint is now passed as a parameter
    // const accountKeys = result.transaction.transaction.message.accountKeys.map(
    //   (ak: any) => ak.pubkey
    // );
    // const tokenMint = accountKeys[1]; // This is no longer needed

    // Fetch token metadata using the passed tokenMint
    const tokenData: TokenData = await this.fetchTokenData(tokenMint, signature);

    if (tokenData.quoteMint === this.config.quoteMint) {
      this.logger.info('New token detected', {
        mint: tokenData.baseMint,
        signature,
        poolId: tokenData.poolId
      });
      onNewToken(tokenData);
    }
  } catch (error) {
    this.logger.error('Error processing new token', { error, signature });
  }
}

  private async fetchTokenData(mint: string, signature: string): Promise<TokenData> {
    try {
      const response = await axios.post(this.config.heliusRpcEndpoint, {
        jsonrpc: '2.0',
        id: 1,
        method: 'getAsset',
        params: {
          id: mint
        }
      });

      const asset = response.data.result;
      return {
        mint,
        symbol: asset?.content?.metadata?.symbol || '',
        name: asset?.content?.metadata?.name || '',
        poolId: '', // Will be populated later when token is listed on Raydium
        baseMint: mint,
        quoteMint: this.config.quoteMint,
        baseDecimals: asset?.token_info?.decimals || 9,
        quoteDecimals: 9, // Assuming SOL or WSOL
        liquidity: 0, // Fetch from Raydium when pool is created
        signature,
        timestamp: Date.now()
      };
    } catch (error) {
      this.logger.error('Failed to fetch token metadata', { mint, error });
      return {
        mint,
        poolId: '',
        baseMint: mint,
        quoteMint: this.config.quoteMint,
        baseDecimals: 9,
        quoteDecimals: 9,
        liquidity: 0,
        signature,
        timestamp: Date.now()
      };
    }
  }

  private startPing(): void {
    this.pingInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.ping();
      }
    }, 30000);
  }

  private cleanup(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private scheduleReconnect(onNewToken: (tokenData: TokenData) => void): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.logger.error('Max reconnection attempts reached');
      return;
    }

    const delay = Math.pow(2, this.reconnectAttempts) * 1000;
    this.reconnectAttempts++;

    this.logger.info(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(() => {
      this.connect(onNewToken);
    }, delay);
  }

  stop(): void {
    this.cleanup();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

// ========== MAIN TRADING BOT ==========
class SolanaTokenSnipingBot {
  private config: TradingConfig;
  private logger: Logger;
  private connectionManager: ConnectionManager;
  private riskManager: RiskManager;
  private executor: TransactionExecutor;
  private positionManager: PositionManager;
  private marketMonitor: MarketMonitor;
  private wallet: Keypair;
  private isRunning = false;
  private healthCheckInterval: NodeJS.Timeout | null = null;
  private positionMonitorInterval: NodeJS.Timeout | null = null;

  // Statistics
  private stats = {
    tokensDetected: 0,
    tradesExecuted: 0,
    successfulTrades: 0,
    failedTrades: 0,
    totalPnL: 0,
    startTime: Date.now()
  };

  constructor() {
    this.config = ConfigManager.load();
    this.logger = new Logger(this.config.logLevel);
    
    // Initialize wallet
    this.wallet = Keypair.fromSecretKey(bs58.decode(this.config.privateKey));
    
    // Initialize components
    const endpoints = [
      this.config.heliusRpcEndpoint,
      this.config.solanaRpcEndpoint
    ];
    
    this.connectionManager = new ConnectionManager(endpoints, this.logger);
    this.riskManager = new RiskManager(this.logger);
    
    const primaryConnection = this.connectionManager.getConnection();
    this.executor = new TransactionExecutor(
      primaryConnection,
      this.wallet,
      this.logger,
      this.config
    );
    
    this.positionManager = new PositionManager(
      this.logger,
      this.config,
      this.executor
    );
    
    this.marketMonitor = new MarketMonitor(this.logger, this.config);
  }

  async start(): Promise<void> {
    try {
      this.logger.info('Starting Solana Token Sniping Bot', {
        wallet: this.wallet.publicKey.toBase58(),
        quoteMint: this.config.quoteMint,
        quoteAmount: this.config.quoteAmount / LAMPORTS_PER_SOL
      });

      // Initialize components
      await this.executor.initialize();
      
      // Start market monitoring
      await this.marketMonitor.start(this.handleNewToken.bind(this));
      
      // Start position monitoring
      this.startPositionMonitoring();
      
      // Start health checks
      this.startHealthChecks();
      
      this.isRunning = true;
      this.logger.info('Bot started successfully');

    } catch (error) {
      this.logger.error('Failed to start bot', { error });
      throw error;
    }
  }

  private async handleNewToken(tokenData: TokenData): Promise<void> {
    this.stats.tokensDetected++;
    
    try {
      // Add configurable delay before buying
      if (this.config.autoBuyDelay > 0) {
        await this.sleep(this.config.autoBuyDelay);
      }

      // Risk assessment
      const riskAssessment = await this.riskManager.assessToken(tokenData);
      
      if (this.riskManager.shouldBlockTrade(riskAssessment, this.config)) {
        this.logger.warn('Trade blocked due to risk assessment', {
          token: tokenData.baseMint,
          riskScore: riskAssessment.riskScore,
          warnings: riskAssessment.warnings
        });
        return;
      }

      // Check position limits
      if (this.positionManager.getActivePositionCount() >= 10) {
        this.logger.warn('Maximum positions reached, skipping trade');
        return;
      }

      // Execute buy order
      const buyResult = await this.executor.buyToken(tokenData);
      this.stats.tradesExecuted++;

      if (buyResult.success) {
        this.stats.successfulTrades++;
        await this.positionManager.addPosition(tokenData, buyResult);
        
        this.logger.info('Token purchase successful', {
          token: tokenData.baseMint,
          signature: buyResult.signature,
          executionTime: buyResult.executionTime
        });
      } else {
        this.stats.failedTrades++;
        this.logger.error('Token purchase failed', {
          token: tokenData.baseMint,
          error: buyResult.error
        });
      }

    } catch (error) {
      this.logger.error('Error handling new token', { 
        token: tokenData.baseMint, 
        error 
      });
    }
  }

  private startPositionMonitoring(): void {
    this.positionMonitorInterval = setInterval(async () => {
      try {
        await this.positionManager.monitorPositions();
      } catch (error) {
        this.logger.error('Error monitoring positions', { error });
      }
    }, 5000); // Check positions every 5 seconds
  }

  private startHealthChecks(): void {
    this.healthCheckInterval = setInterval(() => {
      this.performHealthCheck();
    }, this.config.healthCheckInterval);
  }

  private performHealthCheck(): void {
    const uptime = Date.now() - this.stats.startTime;
    const successRate = this.stats.tradesExecuted > 0 
      ? (this.stats.successfulTrades / this.stats.tradesExecuted) * 100 
      : 0;

    this.logger.info('Health check', {
      uptime: `${Math.floor(uptime / 1000)}s`,
      tokensDetected: this.stats.tokensDetected,
      tradesExecuted: this.stats.tradesExecuted,
      successRate: `${successRate.toFixed(2)}%`,
      activePositions: this.positionManager.getActivePositionCount()
    });
  }

  async stop(): Promise<void> {
    this.logger.info('Stopping bot...');
    this.isRunning = false;

    // Stop all intervals
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
    }
    
    if (this.positionMonitorInterval) {
      clearInterval(this.positionMonitorInterval);
    }

    // Stop market monitoring
    this.marketMonitor.stop();

    this.logger.info('Bot stopped');
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// ========== ENTRY POINT ==========
async function main(): Promise<void> {
  const bot = new SolanaTokenSnipingBot();

  // Graceful shutdown handlers
  process.on('SIGTERM', async () => {
    console.log('Received SIGTERM, shutting down gracefully...');
    await bot.stop();
    process.exit(0);
  });

  process.on('SIGINT', async () => {
    console.log('Received SIGINT, shutting down gracefully...');
    await bot.stop();
    process.exit(0);
  });

  process.on('unhandledRejection', (reason, promise) => {
    console.error('Unhandled Rejection at:', promise, 'reason:', reason);
  });

  try {
    await bot.start();
    
    // Keep the process running
    setInterval(() => {
      // Health check interval keeps process alive
    }, 1000);
    
  } catch (error) {
    console.error('Failed to start bot:', error);
    process.exit(1);
  }
}

// Start the bot if this file is executed directly
if (require.main === module) {
  main().catch(console.error);
}

export { SolanaTokenSnipingBot };