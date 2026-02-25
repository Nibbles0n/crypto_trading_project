import WebSocket from 'ws';
import axios from 'axios';
import { Logger } from './logger';
import { TradingConfig, TokenData } from './types';

export class MarketMonitor {
  private ws: WebSocket | null = null;
  private logger: Logger;
  private config: TradingConfig;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private pingInterval: NodeJS.Timeout | null = null;
  private lastTxTime: number = 0;

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

      this.ws.on('open', () => {
        this.logger.info('WebSocket connected to Helius');
        this.reconnectAttempts = 0;
        this.subscribeToLogs();
        this.startPing();
      });

      this.ws.on('message', (data) => {
        this.handleMessage(data, onNewToken);
      });

      this.ws.on('close', () => {
        this.logger.warn('WebSocket disconnected');
        this.cleanup();
        this.scheduleReconnect(onNewToken);
      });

      this.ws.on('error', (error) => {
        this.logger.error('WebSocket error', { error });
      });

    } catch (error) {
      this.logger.error('Failed to create WebSocket connection', { error });
      this.scheduleReconnect(onNewToken);
    }
  }

  private subscribeToLogs(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.logger.warn('Cannot subscribe: WebSocket not open');
      return;
    }

    const request = {
      jsonrpc: '2.0',
      id: 420,
      method: 'programSubscribe',
      params: [
        this.PUMP_FUN_PROGRAM_ID,
        {
          commitment: this.config.commitmentLevel,
          encoding: 'jsonParsed'
        }
      ]
    };

    this.ws.send(JSON.stringify(request));
    this.logger.info('Subscribed to pump.fun program logs');
  }

  private handleMessage(data: WebSocket.Data, onNewToken: (tokenData: TokenData) => void): void {
    try {
      this.logger.debug('Received WebSocket message', { data: data.toString() });
      const message = JSON.parse(data.toString());

      if (!message || !message.params || !message.params.result) {
        this.logger.warn('Invalid message format', { message });
        return;
      }

      const result = message.params.result;
      if (!result.transaction || !result.transaction.transaction) {
        this.logger.warn('Missing transaction data', { result });
        return;
      }

      const signature = result.signature;
      const instructions = result.transaction.transaction.message.instructions || [];

      this.logger.debug('Processing transaction', { signature, instructionCount: instructions.length });
      this.lastTxTime = Date.now();

      for (const instruction of instructions) {
        if (instruction.programId === this.PUMP_FUN_PROGRAM_ID) {
          this.logger.debug('Found pump.fun instruction', { signature, instructionData: instruction.data });
          const instructionData = Buffer.from(instruction.data, 'base64');
          const createDiscriminator = Buffer.from([248, 198, 177, 38, 245, 192, 231, 108]);
          if (instructionData.length >= 8 && instructionData.subarray(0, 8).equals(createDiscriminator)) {
            const tokenMint = instruction.accounts[0];
            this.logger.info('Found create instruction', { signature, tokenMint });
            this.processNewToken(result, signature, onNewToken, tokenMint);
            break;
          }
        }
      }
    } catch (error) {
      this.logger.error('Error processing WebSocket message', { error });
    }
  }

  private async processNewToken(
    result: any,
    signature: string,
    onNewToken: (tokenData: TokenData) => void,
    tokenMint: string
  ): Promise<void> {
    try {
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
        poolId: '',
        baseMint: mint,
        quoteMint: this.config.quoteMint,
        baseDecimals: asset?.token_info?.decimals || 9,
        quoteDecimals: 9,
        liquidity: 0,
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
      } else {
        this.logger.warn('WebSocket not open for ping');
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

  public isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  public getLastTxTime(): number {
    return this.lastTxTime;
  }

  stop(): void {
    this.cleanup();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.logger.info('Market monitor stopped');
  }
}