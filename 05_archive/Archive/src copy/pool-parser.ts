import {
  Connection,
  PublicKey,
  ParsedTransactionWithMeta,
  ParsedInstruction,
  PartiallyDecodedInstruction
} from '@solana/web3.js';
import {
  LIQUIDITY_STATE_LAYOUT_V4,
  MARKET_STATE_LAYOUT_V3,
  SPL_MINT_LAYOUT,
  LiquidityPoolKeys,
  Market,
  Token,
  TokenAmount,
  Liquidity
} from '@raydium-io/raydium-sdk';
import BN from 'bn.js';
import { logger } from './logger';

// ===== INTERFACES =====
export interface ParsedPoolInfo {
  poolId: PublicKey;
  baseMint: PublicKey;
  quoteMint: PublicKey;
  lpMint: PublicKey;
  baseVault: PublicKey;
  quoteVault: PublicKey;
  lpSupply: BN;
  baseReserve: BN;
  quoteReserve: BN;
  openTime: number;
  poolType: 'SOL' | 'USDC' | 'OTHER';
  initialLiquiditySOL: number;
  lpBurned: boolean;
  marketId: PublicKey;
}

export interface ValidationResult {
  isValid: boolean;
  score: number;
  warnings: string[];
  metadata: {
    liquidityUSD: number;
    pricePerToken: number;
    fdv: number;
    devWalletPercent: number;
    topHolderPercent: number;
    uniqueHolders: number;
  };
}

// ===== CONSTANTS =====
const SOL_MINT = new PublicKey('So11111111111111111111111111111111111111112');
const USDC_MINT = new PublicKey('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v');
const WSOL_MINT = new PublicKey('So11111111111111111111111111111111111111112');

const RAYDIUM_AUTHORITY_V4 = new PublicKey('5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1');

// ===== MAIN PARSER CLASS =====
export class AdvancedPoolParser {
  private connection: Connection;
  private tokenPriceCache: Map<string, number> = new Map();
  
  constructor(connection: Connection) {
    this.connection = connection;
    
    // Pre-populate known prices
    this.tokenPriceCache.set(SOL_MINT.toString(), 100); // Update with real price
    this.tokenPriceCache.set(USDC_MINT.toString(), 1);
  }

  /**
   * Parse pool creation from transaction
   */
  async parsePoolCreation(
    signature: string
  ): Promise<ParsedPoolInfo | null> {
    try {
      const tx = await this.connection.getParsedTransaction(signature, {
        maxSupportedTransactionVersion: 0,
        commitment: 'confirmed'
      });
      
      if (!tx || !tx.meta || !tx.transaction) {
        return null;
      }
      
      // Find Raydium init instruction
      const instructions = tx.transaction.message.instructions;
      const raydiumIx = this.findRaydiumInstruction(instructions);
      
      if (!raydiumIx) {
        return null;
      }
      
      // Extract accounts
      const accounts = (raydiumIx as PartiallyDecodedInstruction).accounts;
      if (accounts.length < 18) {
        logger.warn('Insufficient accounts in instruction');
        return null;
      }
      
      // Parse pool info from accounts
      const poolInfo = await this.extractPoolInfoFromAccounts(accounts, tx);
      
      return poolInfo;
      
    } catch (error) {
      logger.error('Failed to parse pool creation', { error });
      return null;
    }
  }

  /**
   * Validate pool for trading
   */
  async validatePool(poolInfo: ParsedPoolInfo): Promise<ValidationResult> {
    const warnings: string[] = [];
    let score = 100; // Start with perfect score
    
    try {
      // 1. Check liquidity
      const liquiditySOL = poolInfo.poolType === 'SOL' 
        ? poolInfo.baseReserve.toNumber() / 1e9
        : poolInfo.quoteReserve.toNumber() / 1e9;
      
      const liquidityUSD = liquiditySOL * (this.tokenPriceCache.get(SOL_MINT.toString()) || 100);
      
      if (liquidityUSD < 100) {
        warnings.push('Very low liquidity (<$100)');
        score -= 30;
      } else if (liquidityUSD < 1000) {
        warnings.push('Low liquidity (<$1000)');
        score -= 10;
      }
      
      // 2. Check LP burned
      if (!poolInfo.lpBurned) {
        warnings.push('LP tokens not burned');
        score -= 20;
      }
      
      // 3. Check token distribution
      const tokenDistribution = await this.analyzeTokenDistribution(
        poolInfo.poolType === 'SOL' ? poolInfo.quoteMint : poolInfo.baseMint
      );
      
      if (tokenDistribution.devWalletPercent > 10) {
        warnings.push(`High dev wallet ownership: ${tokenDistribution.devWalletPercent.toFixed(1)}%`);
        score -= 25;
      }
      
      if (tokenDistribution.topHolderPercent > 50) {
        warnings.push(`Concentrated holdings: Top holder has ${tokenDistribution.topHolderPercent.toFixed(1)}%`);
        score -= 20;
      }
      
      if (tokenDistribution.uniqueHolders < 10) {
        warnings.push(`Low holder count: ${tokenDistribution.uniqueHolders}`);
        score -= 15;
      }
      
      // 4. Calculate price metrics
      const pricePerToken = this.calculateTokenPrice(poolInfo);
      const fdv = await this.calculateFDV(poolInfo, pricePerToken);
      
      if (fdv > 10000000) { // $10M
        warnings.push(`High FDV: $${(fdv / 1000000).toFixed(1)}M`);
        score -= 10;
      }
      
      // 5. Check pool age
      const poolAge = Date.now() / 1000 - poolInfo.openTime;
      if (poolAge > 300) { // 5 minutes
        warnings.push('Pool is not brand new');
        score -= 5;
      }
      
      return {
        isValid: score >= 50, // Minimum score threshold
        score,
        warnings,
        metadata: {
          liquidityUSD,
          pricePerToken,
          fdv,
          ...tokenDistribution
        }
      };
      
    } catch (error) {
      logger.error('Pool validation error', { error });
      return {
        isValid: false,
        score: 0,
        warnings: ['Validation failed'],
        metadata: {
          liquidityUSD: 0,
          pricePerToken: 0,
          fdv: 0,
          devWalletPercent: 0,
          topHolderPercent: 0,
          uniqueHolders: 0
        }
      };
    }
  }

  /**
   * Quick validation for speed
   */
  quickValidate(poolInfo: ParsedPoolInfo): boolean {
    // Ultra-fast checks only
    const liquiditySOL = poolInfo.poolType === 'SOL'
      ? poolInfo.baseReserve.toNumber() / 1e9
      : poolInfo.quoteReserve.toNumber() / 1e9;
    
    // Basic checks
    if (liquiditySOL < 0.1) return false; // Too low
    if (liquiditySOL > 1000) return false; // Probably not new
    
    // Pool must be SOL or USDC
    if (poolInfo.poolType === 'OTHER') return false;
    
    return true;
  }

  /**
   * Extract pool info from instruction accounts
   */
  private async extractPoolInfoFromAccounts(
    accounts: PublicKey[],
    tx: ParsedTransactionWithMeta
  ): Promise<ParsedPoolInfo | null> {
    try {
      // Standard Raydium V4 account layout
      const poolId = accounts[4];
      const lpMint = accounts[7];
      const baseMint = accounts[8];
      const quoteMint = accounts[9];
      const baseVault = accounts[10];
      const quoteVault = accounts[11];
      const marketId = accounts[16];
      
      // Determine pool type
      let poolType: 'SOL' | 'USDC' | 'OTHER';
      if (baseMint.equals(SOL_MINT) || quoteMint.equals(SOL_MINT)) {
        poolType = 'SOL';
      } else if (baseMint.equals(USDC_MINT) || quoteMint.equals(USDC_MINT)) {
        poolType = 'USDC';
      } else {
        poolType = 'OTHER';
      }
      
      // Get initial reserves from logs
      const { baseReserve, quoteReserve, lpSupply } = this.parseReservesFromLogs(tx);
      
      // Check if LP burned
      const lpBurned = await this.checkLPBurned(lpMint, tx);
      
      // Calculate initial liquidity
      const initialLiquiditySOL = poolType === 'SOL'
        ? baseReserve.toNumber() / 1e9
        : 0; // Would need price for USDC pools
      
      return {
        poolId,
        baseMint,
        quoteMint,
        lpMint,
        baseVault,
        quoteVault,
        lpSupply,
        baseReserve,
        quoteReserve,
        openTime: Math.floor(Date.now() / 1000),
        poolType,
        initialLiquiditySOL,
        lpBurned,
        marketId
      };
      
    } catch (error) {
      logger.error('Failed to extract pool info', { error });
      return null;
    }
  }

  /**
   * Parse reserves from transaction logs
   */
  private parseReservesFromLogs(tx: ParsedTransactionWithMeta): {
    baseReserve: BN;
    quoteReserve: BN;
    lpSupply: BN;
  } {
    // Default values
    let baseReserve = new BN(0);
    let quoteReserve = new BN(0);
    let lpSupply = new BN(0);
    
    try {
      // Look for init amounts in logs
      const logs = tx.meta?.logMessages || [];
      
      for (const log of logs) {
        // Parse Raydium init logs
        if (log.includes('init_pc_amount')) {
          const match = log.match(/init_pc_amount: (\d+)/);
          if (match) quoteReserve = new BN(match[1]);
        }
        if (log.includes('init_coin_amount')) {
          const match = log.match(/init_coin_amount: (\d+)/);
          if (match) baseReserve = new BN(match[1]);
        }
      }
      
      // If not found in logs, check post balances
      if (baseReserve.isZero() || quoteReserve.isZero()) {
        // This would require more complex parsing of pre/post balances
        // For speed, we'll fetch current state instead
      }
      
    } catch (error) {
      logger.error('Failed to parse reserves from logs', { error });
    }
    
    return { baseReserve, quoteReserve, lpSupply };
  }

  /**
   * Check if LP tokens were burned
   */
  private async checkLPBurned(lpMint: PublicKey, tx: ParsedTransactionWithMeta): Promise<boolean> {
    try {
      // Check if LP tokens were sent to a burn address
      const postBalances = tx.meta?.postTokenBalances || [];
      
      for (const balance of postBalances) {
        if (balance.mint === lpMint.toString()) {
          // Common burn addresses
          const burnAddresses = [
            '1111111111111111111111111111111111111111111',
            '11111111111111111111111111111111'
          ];
          
          if (burnAddresses.includes(balance.owner)) {
            return true;
          }
        }
      }
      
      // Alternative: Check if authority was revoked
      const mintInfo = await this.connection.getParsedAccountInfo(lpMint);
      if (mintInfo.value?.data && 'parsed' in mintInfo.value.data) {
        const parsed = mintInfo.value.data.parsed;
        if (parsed.info.mintAuthority === null && parsed.info.freezeAuthority === null) {
          return true;
        }
      }
      
    } catch (error) {
      logger.error('Failed to check LP burn status', { error });
    }
    
    return false;
  }

  /**
   * Analyze token distribution
   */
  private async analyzeTokenDistribution(tokenMint: PublicKey): Promise<{
    devWalletPercent: number;
    topHolderPercent: number;
    uniqueHolders: number;
  }> {
    try {
      // Get token supply
      const mintInfo = await this.connection.getParsedAccountInfo(tokenMint);
      if (!mintInfo.value?.data || !('parsed' in mintInfo.value.data)) {
        throw new Error('Failed to get mint info');
      }
      
      const totalSupply = new BN(mintInfo.value.data.parsed.info.supply);
      
      // Get largest accounts
      const largestAccounts = await this.connection.getTokenLargestAccounts(tokenMint);
      
      if (largestAccounts.value.length === 0) {
        return {
          devWalletPercent: 0,
          topHolderPercent: 0,
          uniqueHolders: 0
        };
      }
      
      // Calculate percentages
      const topHolder = largestAccounts.value[0];
      const topHolderPercent = topHolder.amount.mul(new BN(100)).div(totalSupply).toNumber();
      
      // Estimate dev wallet (usually one of the top holders)
      // This is a heuristic - could be improved
      let devWalletPercent = 0;
      for (const account of largestAccounts.value.slice(0, 5)) {
        const percent = account.amount.mul(new BN(100)).div(totalSupply).toNumber();
        if (percent > 5 && percent < 30) {
          devWalletPercent = Math.max(devWalletPercent, percent);
        }
      }
      
      return {
        devWalletPercent,
        topHolderPercent,
        uniqueHolders: largestAccounts.value.length
      };
      
    } catch (error) {
      logger.error('Failed to analyze token distribution', { error });
      return {
        devWalletPercent: 100,
        topHolderPercent: 100,
        uniqueHolders: 0
      };
    }
  }

  /**
   * Calculate token price
   */
  private calculateTokenPrice(poolInfo: ParsedPoolInfo): number {
    if (poolInfo.poolType === 'SOL') {
      // Price in SOL per token
      return poolInfo.baseReserve.toNumber() / poolInfo.quoteReserve.toNumber();
    } else {
      // Price in USDC per token
      return poolInfo.quoteReserve.toNumber() / poolInfo.baseReserve.toNumber();
    }
  }

  /**
   * Calculate fully diluted valuation
   */
  private async calculateFDV(poolInfo: ParsedPoolInfo, pricePerToken: number): Promise<number> {
    try {
      const tokenMint = poolInfo.poolType === 'SOL' ? poolInfo.quoteMint : poolInfo.baseMint;
      
      const mintInfo = await this.connection.getParsedAccountInfo(tokenMint);
      if (!mintInfo.value?.data || !('parsed' in mintInfo.value.data)) {
        return 0;
      }
      
      const totalSupply = new BN(mintInfo.value.data.parsed.info.supply);
      const decimals = mintInfo.value.data.parsed.info.decimals;
      
      const supplyNumber = totalSupply.toNumber() / Math.pow(10, decimals);
      
      // Convert to USD
      let priceUSD = pricePerToken;
      if (poolInfo.poolType === 'SOL') {
        priceUSD *= this.tokenPriceCache.get(SOL_MINT.toString()) || 100;
      }
      
      return supplyNumber * priceUSD;
      
    } catch (error) {
      logger.error('Failed to calculate FDV', { error });
      return 0;
    }
  }

  /**
   * Find Raydium instruction in transaction
   */
  private findRaydiumInstruction(instructions: (ParsedInstruction | PartiallyDecodedInstruction)[]): 
    ParsedInstruction | PartiallyDecodedInstruction | null {
    
    for (const ix of instructions) {
      if ('programId' in ix) {
        if (ix.programId.toString() === '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8') {
          return ix;
        }
      }
    }
    
    return null;
  }

  /**
   * Get pool keys for swap
   */
  async getPoolKeys(poolInfo: ParsedPoolInfo): Promise<LiquidityPoolKeys | null> {
    try {
      // Fetch market info
      const marketInfo = await this.connection.getAccountInfo(poolInfo.marketId);
      if (!marketInfo) return null;
      
      const market = MARKET_STATE_LAYOUT_V3.decode(marketInfo.data);
      
      // Construct pool keys
      const poolKeys: LiquidityPoolKeys = {
        id: poolInfo.poolId,
        baseMint: poolInfo.baseMint,
        quoteMint: poolInfo.quoteMint,
        lpMint: poolInfo.lpMint,
        baseDecimals: poolInfo.poolType === 'SOL' ? 9 : 6, // TODO: Fetch actual
        quoteDecimals: poolInfo.poolType === 'SOL' ? 6 : 9, // TODO: Fetch actual
        lpDecimals: 9,
        version: 4,
        programId: new PublicKey('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'),
        authority: RAYDIUM_AUTHORITY_V4,
        openOrders: new PublicKey(market.openOrders),
        targetOrders: new PublicKey(market.targetOrders),
        baseVault: poolInfo.baseVault,
        quoteVault: poolInfo.quoteVault,
        withdrawQueue: PublicKey.default, // Not used in V4
        lpVault: PublicKey.default, // Not used in V4
        marketVersion: 3,
        marketProgramId: new PublicKey('srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX'),
        marketId: poolInfo.marketId,
        marketAuthority: new PublicKey(market.vaultSignerNonce),
        marketBaseVault: new PublicKey(market.baseVault),
        marketQuoteVault: new PublicKey(market.quoteVault),
        marketBids: new PublicKey(market.bids),
        marketAsks: new PublicKey(market.asks),
        marketEventQueue: new PublicKey(market.eventQueue),
        lookupTableAccount: PublicKey.default
      };
      
      return poolKeys;
      
    } catch (error) {
      logger.error('Failed to get pool keys', { error });
      return null;
    }
  }
}

// ===== MARKET STATE LAYOUT =====
const MARKET_STATE_LAYOUT_V3 = struct([
  // Serum DEX V3 market layout
  // This is a simplified version - full layout is complex
  u64('accountFlags'),
  publicKeyLayout('ownAddress'),
  u64('vaultSignerNonce'),
  publicKeyLayout('baseMint'),
  publicKeyLayout('quoteMint'),
  publicKeyLayout('baseVault'),
  publicKeyLayout('quoteVault'),
  publicKeyLayout('bids'),
  publicKeyLayout('asks'),
  publicKeyLayout('eventQueue'),
  publicKeyLayout('requestQueue'),
  u64('baseReserve'),
  u64('quoteReserve'),
  // ... more fields
]);