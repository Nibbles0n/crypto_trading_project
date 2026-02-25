import {
  Connection,
  PublicKey,
  Transaction,
  TransactionInstruction,
  Keypair,
  SystemProgram,
  ComputeBudgetProgram,
  LAMPORTS_PER_SOL,
  Signer,
  VersionedTransaction,
  MessageV0,
  AddressLookupTableAccount
} from '@solana/web3.js';
import {
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  createAssociatedTokenAccountInstruction,
  createCloseAccountInstruction,
  getAssociatedTokenAddress,
  createSyncNativeInstruction,
  NATIVE_MINT
} from '@solana/spl-token';
import {
  Liquidity,
  LiquidityPoolKeys,
  jsonInfo2PoolKeys,
  LiquidityPoolJsonInfo,
  TokenAmount,
  Token,
  Percent,
  Currency,
  CurrencyAmount,
  SwapSide,
  AmountSide,
  LIQUIDITY_FEES_NUMERATOR,
  LIQUIDITY_FEES_DENOMINATOR
} from '@raydium-io/raydium-sdk';
import BN from 'bn.js';
import { logger } from './logger';

// ===== INTERFACES =====
export interface SwapParams {
  poolKeys: LiquidityPoolKeys;
  userKeys: {
    owner: PublicKey;
    tokenAccounts: {
      baseMint: PublicKey;
      quoteMint: PublicKey;
    };
  };
  amountIn: number;
  tokenIn: 'base' | 'quote';
  slippageBps: number; // Basis points (100 = 1%)
  computeUnitPrice?: number;
  jitoTipLamports?: number;
}

export interface TransactionMetadata {
  computeUnits: number;
  priorityFee: number;
  estimatedFee: number;
  slippage: number;
  priceImpact: number;
}

// ===== CONSTANTS =====
const JITO_TIP_ACCOUNTS = [
  '96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5',
  'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe',
  'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY',
  'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49',
  'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh',
  'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt',
  'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL',
  'HLmqeL62xR1QoZ1HKKbXRrdN1p3phKpxRMb2VVopvBBz'
];

// ===== MAIN BUILDER CLASS =====
export class OptimizedTransactionBuilder {
  private connection: Connection;
  private jitoTipAccount: PublicKey;
  
  // Pre-computed values for speed
  private precomputedATAs: Map<string, PublicKey> = new Map();
  private recentBlockhash: string = '';
  private blockhashLastUpdate: number = 0;
  
  constructor(connection: Connection) {
    this.connection = connection;
    // Randomly select Jito tip account
    this.jitoTipAccount = new PublicKey(
      JITO_TIP_ACCOUNTS[Math.floor(Math.random() * JITO_TIP_ACCOUNTS.length)]
    );
  }

  /**
   * Build optimized swap transaction
   */
  async buildSwapTransaction(params: SwapParams): Promise<{
    transaction: Transaction;
    metadata: TransactionMetadata;
  }> {
    const startTime = Date.now();
    
    try {
      // Calculate amounts
      const { amountIn, amountOut, priceImpact } = await this.calculateSwapAmounts(params);
      
      // Build swap instruction
      const swapInstruction = await this.buildSwapInstruction(
        params.poolKeys,
        params.userKeys,
        amountIn,
        amountOut,
        params.tokenIn
      );
      
      // Create transaction
      const transaction = new Transaction();
      
      // Add priority fee
      const computeUnitPrice = params.computeUnitPrice || 50000;
      transaction.add(
        ComputeBudgetProgram.setComputeUnitLimit({ units: 400000 }),
        ComputeBudgetProgram.setComputeUnitPrice({ microLamports: computeUnitPrice })
      );
      
      // Add Jito tip if specified
      if (params.jitoTipLamports && params.jitoTipLamports > 0) {
        transaction.add(
          SystemProgram.transfer({
            fromPubkey: params.userKeys.owner,
            toPubkey: this.jitoTipAccount,
            lamports: params.jitoTipLamports
          })
        );
      }
      
      // Add swap instruction
      transaction.add(swapInstruction);
      
      // Set recent blockhash
      transaction.recentBlockhash = await this.getRecentBlockhash();
      transaction.feePayer = params.userKeys.owner;
      
      // Calculate metadata
      const metadata: TransactionMetadata = {
        computeUnits: 400000,
        priorityFee: computeUnitPrice,
        estimatedFee: 5000 + (computeUnitPrice * 400000) / 1000000,
        slippage: params.slippageBps / 100,
        priceImpact: priceImpact.toNumber()
      };
      
      const elapsed = Date.now() - startTime;
      logger.debug('Transaction built', { elapsed: `${elapsed}ms` });
      
      return { transaction, metadata };
      
    } catch (error) {
      logger.error('Failed to build swap transaction', { error });
      throw error;
    }
  }

  /**
   * Build swap instruction using Raydium SDK
   */
  private async buildSwapInstruction(
    poolKeys: LiquidityPoolKeys,
    userKeys: any,
    amountIn: TokenAmount,
    amountOut: TokenAmount,
    tokenIn: 'base' | 'quote'
  ): Promise<TransactionInstruction> {
    const { instruction } = Liquidity.makeSwapInstruction({
      poolKeys,
      userKeys: {
        tokenAccountIn: tokenIn === 'base' 
          ? userKeys.tokenAccounts.baseMint 
          : userKeys.tokenAccounts.quoteMint,
        tokenAccountOut: tokenIn === 'base'
          ? userKeys.tokenAccounts.quoteMint
          : userKeys.tokenAccounts.baseMint,
        owner: userKeys.owner
      },
      amountIn: amountIn.raw,
      amountOut: amountOut.raw,
      fixedSide: 'in'
    });
    
    return instruction;
  }

  /**
   * Calculate swap amounts with slippage
   */
  private async calculateSwapAmounts(params: SwapParams): Promise<{
    amountIn: TokenAmount;
    amountOut: TokenAmount;
    priceImpact: Percent;
  }> {
    // Get pool info
    const poolInfo = await Liquidity.fetchInfo({
      connection: this.connection,
      poolKeys: params.poolKeys
    });
    
    // Create token amounts
    const baseToken = new Token(
      TOKEN_PROGRAM_ID,
      params.poolKeys.baseMint,
      params.poolKeys.baseDecimals
    );
    const quoteToken = new Token(
      TOKEN_PROGRAM_ID,
      params.poolKeys.quoteMint,
      params.poolKeys.quoteDecimals
    );
    
    const tokenIn = params.tokenIn === 'base' ? baseToken : quoteToken;
    const tokenOut = params.tokenIn === 'base' ? quoteToken : baseToken;
    
    const amountIn = new TokenAmount(
      tokenIn,
      new BN(params.amountIn * Math.pow(10, tokenIn.decimals))
    );
    
    // Calculate output amount
    const { amountOut, priceImpact } = Liquidity.computeAmountOut({
      poolKeys: params.poolKeys,
      poolInfo,
      amountIn,
      currencyOut: tokenOut,
      slippage: new Percent(params.slippageBps, 10000)
    });
    
    return { amountIn, amountOut, priceImpact };
  }

  /**
   * Build versioned transaction for better performance
   */
  async buildVersionedTransaction(
    instructions: TransactionInstruction[],
    payer: PublicKey,
    lookupTables?: AddressLookupTableAccount[]
  ): Promise<VersionedTransaction> {
    const blockhash = await this.getRecentBlockhash();
    
    const messageV0 = new MessageV0({
      payerKey: payer,
      recentBlockhash: blockhash,
      instructions,
      addressLookupTableAccounts: lookupTables
    });
    
    return new VersionedTransaction(messageV0);
  }

  /**
   * Pre-create token accounts for speed
   */
  async prepareTokenAccounts(
    owner: PublicKey,
    mints: PublicKey[]
  ): Promise<Map<string, PublicKey>> {
    const accounts = new Map<string, PublicKey>();
    const instructions: TransactionInstruction[] = [];
    
    for (const mint of mints) {
      const ata = await getAssociatedTokenAddress(
        mint,
        owner,
        false,
        TOKEN_PROGRAM_ID,
        ASSOCIATED_TOKEN_PROGRAM_ID
      );
      
      accounts.set(mint.toString(), ata);
      
      // Check if account exists
      const accountInfo = await this.connection.getAccountInfo(ata);
      if (!accountInfo) {
        instructions.push(
          createAssociatedTokenAccountInstruction(
            owner,
            ata,
            owner,
            mint,
            TOKEN_PROGRAM_ID,
            ASSOCIATED_TOKEN_PROGRAM_ID
          )
        );
      }
    }
    
    // Create accounts if needed
    if (instructions.length > 0) {
      const tx = new Transaction().add(...instructions);
      tx.recentBlockhash = await this.getRecentBlockhash();
      tx.feePayer = owner;
      // Send transaction to create accounts
      // This should be done before trading starts
    }
    
    return accounts;
  }

  /**
   * Get recent blockhash with caching
   */
  private async getRecentBlockhash(): Promise<string> {
    const now = Date.now();
    
    // Cache for 1 second
    if (now - this.blockhashLastUpdate < 1000 && this.recentBlockhash) {
      return this.recentBlockhash;
    }
    
    const { blockhash } = await this.connection.getLatestBlockhash('processed');
    this.recentBlockhash = blockhash;
    this.blockhashLastUpdate = now;
    
    return blockhash;
  }

  /**
   * Build transaction for closing token accounts
   */
  buildCloseAccountTransaction(
    tokenAccount: PublicKey,
    owner: PublicKey
  ): Transaction {
    const transaction = new Transaction();
    
    transaction.add(
      createCloseAccountInstruction(
        tokenAccount,
        owner,
        owner,
        [],
        TOKEN_PROGRAM_ID
      )
    );
    
    return transaction;
  }

  /**
   * Estimate transaction fee
   */
  async estimateTransactionFee(
    transaction: Transaction
  ): Promise<number> {
    const { feeCalculator } = await this.connection.getRecentBlockhash();
    const fee = feeCalculator.lamportsPerSignature * transaction.signatures.length;
    return fee;
  }

  /**
   * Build optimized bundle for Jito
   */
  async buildJitoBundle(
    transactions: Transaction[],
    tipLamports: number
  ): Promise<Transaction[]> {
    // Add tip to last transaction
    const lastTx = transactions[transactions.length - 1];
    lastTx.add(
      SystemProgram.transfer({
        fromPubkey: lastTx.feePayer!,
        toPubkey: this.jitoTipAccount,
        lamports: tipLamports
      })
    );
    
    return transactions;
  }
}

// ===== HELPER FUNCTIONS =====

/**
 * Pre-build common transaction templates
 */
export class TransactionTemplates {
  private templates: Map<string, Transaction> = new Map();
  
  /**
   * Create buy transaction template
   */
  createBuyTemplate(
    amount: number,
    computeUnits: number = 400000,
    priorityFee: number = 50000
  ): Transaction {
    const key = `buy_${amount}_${computeUnits}_${priorityFee}`;
    
    if (!this.templates.has(key)) {
      const tx = new Transaction();
      
      tx.add(
        ComputeBudgetProgram.setComputeUnitLimit({ units: computeUnits }),
        ComputeBudgetProgram.setComputeUnitPrice({ microLamports: priorityFee })
      );
      
      this.templates.set(key, tx);
    }
    
    return this.templates.get(key)!;
  }
  
  /**
   * Clone template and add specific instructions
   */
  cloneAndComplete(
    template: Transaction,
    instructions: TransactionInstruction[],
    feePayer: PublicKey,
    blockhash: string
  ): Transaction {
    const tx = new Transaction();
    
    // Copy template instructions
    tx.add(...template.instructions);
    
    // Add new instructions
    tx.add(...instructions);
    
    // Set transaction properties
    tx.feePayer = feePayer;
    tx.recentBlockhash = blockhash;
    
    return tx;
  }
}

/**
 * Transaction sending utilities
 */
export class TransactionSender {
  private connection: Connection;
  
  constructor(connection: Connection) {
    this.connection = connection;
  }
  
  /**
   * Send transaction with retries
   */
  async sendWithRetry(
    transaction: Transaction,
    signers: Signer[],
    maxRetries: number = 3
  ): Promise<string | null> {
    let lastError: any;
    
    for (let i = 0; i < maxRetries; i++) {
      try {
        const signature = await this.connection.sendTransaction(
          transaction,
          signers,
          {
            skipPreflight: true,
            preflightCommitment: 'processed',
            maxRetries: 0
          }
        );
        
        return signature;
        
      } catch (error: any) {
        lastError = error;
        
        // Don't retry certain errors
        if (error.message?.includes('insufficient funds')) {
          throw error;
        }
        
        // Exponential backoff
        if (i < maxRetries - 1) {
          await new Promise(resolve => setTimeout(resolve, Math.pow(2, i) * 100));
        }
      }
    }
    
    logger.error('Transaction failed after retries', { error: lastError });
    return null;
  }
  
  /**
   * Send raw transaction for maximum speed
   */
  async sendRawTransaction(
    rawTransaction: Buffer | Uint8Array
  ): Promise<string> {
    return await this.connection.sendRawTransaction(rawTransaction, {
      skipPreflight: true,
      preflightCommitment: 'processed',
      maxRetries: 0
    });
  }
  
  /**
   * Batch send transactions
   */
  async batchSend(
    transactions: Transaction[],
    signers: Signer[][]
  ): Promise<(string | null)[]> {
    const promises = transactions.map((tx, i) => 
      this.sendWithRetry(tx, signers[i], 1)
    );
    
    return await Promise.all(promises);
  }
}

/**
 * Slippage calculator
 */
export class SlippageCalculator {
  /**
   * Calculate optimal slippage based on liquidity
   */
  static calculateOptimalSlippage(
    liquidityUSD: number,
    tradeAmountUSD: number,
    urgency: 'low' | 'medium' | 'high'
  ): number {
    // Base slippage
    let slippageBps = 100; // 1%
    
    // Adjust for liquidity
    if (liquidityUSD < 1000) {
      slippageBps += 500; // +5%
    } else if (liquidityUSD < 10000) {
      slippageBps += 200; // +2%
    }
    
    // Adjust for trade size
    const tradeImpact = (tradeAmountUSD / liquidityUSD) * 100;
    slippageBps += Math.floor(tradeImpact * 100);
    
    // Adjust for urgency
    const urgencyMultiplier = {
      low: 1,
      medium: 1.5,
      high: 2
    };
    
    slippageBps = Math.floor(slippageBps * urgencyMultiplier[urgency]);
    
    // Cap at 20%
    return Math.min(slippageBps, 2000);
  }
  
  /**
   * Calculate price impact
   */
  static calculatePriceImpact(
    reserveIn: BN,
    reserveOut: BN,
    amountIn: BN
  ): number {
    const k = reserveIn.mul(reserveOut);
    const newReserveIn = reserveIn.add(amountIn);
    const newReserveOut = k.div(newReserveIn);
    const amountOut = reserveOut.sub(newReserveOut);
    
    const idealPrice = reserveOut.mul(new BN(10000)).div(reserveIn);
    const executionPrice = amountOut.mul(new BN(10000)).div(amountIn);
    
    const priceImpact = idealPrice.sub(executionPrice).mul(new BN(10000)).div(idealPrice);
    
    return priceImpact.toNumber() / 100; // Return as percentage
  }
}