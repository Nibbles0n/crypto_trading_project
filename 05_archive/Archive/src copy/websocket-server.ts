import WebSocket from 'ws';
import { EventEmitter } from 'events';
import express from 'express';
import path from 'path';
import { logger } from './logger';

// ===== INTERFACES =====
export interface DashboardUpdate {
  type: 'status' | 'stats' | 'trade' | 'wallet' | 'risk' | 'alert';
  data: any;
  timestamp: number;
}

export interface BotStats {
  totalTrades: number;
  wins: number;
  losses: number;
  winRate: number;
  dailyPnL: number;
  totalProfit: number;
  avgHoldTime: number;
  activePositions: number;
  consecutiveLosses: number;
  maxDrawdown: number;
}

export interface TradeUpdate {
  timestamp: number;
  poolId: string;
  token: string;
  entryPrice: number;
  exitPrice?: number;
  pnl: number;
  holdTime: number;
  status: 'active' | 'closed' | 'pending';
}

// ===== DASHBOARD SERVER CLASS =====
export class DashboardServer extends EventEmitter {
  private wss: WebSocket.Server;
  private app: express.Application;
  private clients: Set<WebSocket> = new Set();
  private stats: BotStats = {
    totalTrades: 0,
    wins: 0,
    losses: 0,
    winRate: 0,
    dailyPnL: 0,
    totalProfit: 0,
    avgHoldTime: 0,
    activePositions: 0,
    consecutiveLosses: 0,
    maxDrawdown: 0
  };
  private tradeHistory: TradeUpdate[] = [];
  private isRunning: boolean = false;
  
  constructor(port: number = 8080) {
    super();
    
    // Create Express app for serving dashboard
    this.app = express();
    this.app.use(express.static(path.join(__dirname, '../dashboard')));
    
    // Create WebSocket server
    const server = this.app.listen(port, () => {
      logger.info(`Dashboard server running on http://localhost:${port}`);
    });
    
    this.wss = new WebSocket.Server({ server });
    
    this.setupWebSocketHandlers();
  }
  
  /**
   * Setup WebSocket connection handlers
   */
  private setupWebSocketHandlers() {
    this.wss.on('connection', (ws: WebSocket) => {
      logger.info('Dashboard client connected');
      this.clients.add(ws);
      
      // Send initial state
      this.sendInitialState(ws);
      
      // Handle messages from dashboard
      ws.on('message', (message: string) => {
        try {
          const data = JSON.parse(message);
          this.handleClientMessage(data, ws);
        } catch (error) {
          logger.error('Invalid message from client', { error });
        }
      });
      
      // Handle disconnect
      ws.on('close', () => {
        logger.info('Dashboard client disconnected');
        this.clients.delete(ws);
      });
      
      // Handle errors
      ws.on('error', (error) => {
        logger.error('WebSocket error', { error });
        this.clients.delete(ws);
      });
    });
  }
  
  /**
   * Send initial state to new client
   */
  private sendInitialState(ws: WebSocket) {
    // Send current status
    this.sendToClient(ws, {
      type: 'status',
      data: {
        status: this.isRunning ? 'RUNNING' : 'STOPPED',
        uptime: this.isRunning ? Date.now() : 0
      },
      timestamp: Date.now()
    });
    
    // Send current stats
    this.sendToClient(ws, {
      type: 'stats',
      data: {
        stats: this.stats
      },
      timestamp: Date.now()
    });
    
    // Send recent trades
    this.tradeHistory.slice(-50).forEach(trade => {
      this.sendToClient(ws, {
        type: 'trade',
        data: { trade },
        timestamp: Date.now()
      });
    });
  }
  
  /**
   * Handle messages from dashboard client
   */
  private handleClientMessage(data: any, ws: WebSocket) {
    logger.debug('Received message from client', { action: data.action });
    
    switch (data.action) {
      case 'start':
        this.emit('start');
        break;
        
      case 'stop':
        this.emit('stop');
        break;
        
      case 'emergency_stop':
        this.emit('emergency_stop');
        break;
        
      case 'clear_stats':
        this.clearStats();
        break;
        
      case 'export_data':
        this.exportData(ws);
        break;
        
      default:
        logger.warn('Unknown action', { action: data.action });
    }
  }
  
  /**
   * Send update to specific client
   */
  private sendToClient(ws: WebSocket, update: DashboardUpdate) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(update));
    }
  }
  
  /**
   * Broadcast update to all clients
   */
  private broadcast(update: DashboardUpdate) {
    const message = JSON.stringify(update);
    this.clients.forEach(client => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(message);
      }
    });
  }
  
  /**
   * Update bot status
   */
  updateStatus(isRunning: boolean) {
    this.isRunning = isRunning;
    this.broadcast({
      type: 'status',
      data: {
        status: isRunning ? 'RUNNING' : 'STOPPED'
      },
      timestamp: Date.now()
    });
  }
  
  /**
   * Update statistics
   */
  updateStats(stats: Partial<BotStats>) {
    this.stats = { ...this.stats, ...stats };
    
    // Calculate win rate
    if (this.stats.totalTrades > 0) {
      this.stats.winRate = (this.stats.wins / this.stats.totalTrades) * 100;
    }
    
    this.broadcast({
      type: 'stats',
      data: { stats: this.stats },
      timestamp: Date.now()
    });
  }
  
  /**
   * Add new trade
   */
  addTrade(trade: TradeUpdate) {
    this.tradeHistory.push(trade);
    
    // Keep only last 1000 trades
    if (this.tradeHistory.length > 1000) {
      this.tradeHistory.shift();
    }
    
    // Update stats
    if (trade.status === 'closed') {
      this.stats.totalTrades++;
      if (trade.pnl > 0) {
        this.stats.wins++;
        this.stats.consecutiveLosses = 0;
      } else {
        this.stats.losses++;
        this.stats.consecutiveLosses++;
      }
      this.stats.dailyPnL += trade.pnl;
      this.stats.totalProfit += trade.pnl;
      
      // Update average hold time
      this.stats.avgHoldTime = 
        (this.stats.avgHoldTime * (this.stats.totalTrades - 1) + trade.holdTime) / 
        this.stats.totalTrades;
      
      // Update max drawdown
      if (this.stats.dailyPnL < this.stats.maxDrawdown) {
        this.stats.maxDrawdown = this.stats.dailyPnL;
      }
    }
    
    // Broadcast trade update
    this.broadcast({
      type: 'trade',
      data: { trade },
      timestamp: Date.now()
    });
    
    // Also broadcast updated stats
    this.updateStats(this.stats);
  }
  
  /**
   * Update wallet balance
   */
  updateWalletBalance(balance: number) {
    this.broadcast({
      type: 'wallet',
      data: { walletBalance: balance },
      timestamp: Date.now()
    });
  }
  
  /**
   * Update risk score
   */
  updateRiskScore(score: 'LOW' | 'MEDIUM' | 'HIGH') {
    this.broadcast({
      type: 'risk',
      data: { riskScore: score },
      timestamp: Date.now()
    });
  }
  
  /**
   * Send alert
   */
  sendAlert(message: string, severity: 'info' | 'warning' | 'error') {
    this.broadcast({
      type: 'alert',
      data: {
        message,
        severity
      },
      timestamp: Date.now()
    });
  }
  
  /**
   * Clear statistics
   */
  private clearStats() {
    this.stats = {
      totalTrades: 0,
      wins: 0,
      losses: 0,
      winRate: 0,
      dailyPnL: 0,
      totalProfit: 0,
      avgHoldTime: 0,
      activePositions: 0,
      consecutiveLosses: 0,
      maxDrawdown: 0
    };
    
    this.tradeHistory = [];
    
    this.updateStats(this.stats);
    logger.info('Statistics cleared');
  }
  
  /**
   * Export data
   */
  private exportData(ws: WebSocket) {
    const data = {
      stats: this.stats,
      trades: this.tradeHistory,
      exportTime: new Date().toISOString()
    };
    
    this.sendToClient(ws, {
      type: 'export',
      data: {
        filename: `sniper-bot-data-${Date.now()}.json`,
        content: JSON.stringify(data, null, 2)
      },
      timestamp: Date.now()
    });
    
    logger.info('Data exported');
  }
  
  /**
   * Reset daily stats
   */
  resetDailyStats() {
    this.stats.dailyPnL = 0;
    this.stats.maxDrawdown = 0;
    this.updateStats(this.stats);
    logger.info('Daily stats reset');
  }
  
  /**
   * Get current statistics
   */
  getStats(): BotStats {
    return { ...this.stats };
  }
  
  /**
   * Shutdown server
   */
  shutdown() {
    logger.info('Shutting down dashboard server');
    
    // Close all connections
    this.clients.forEach(client => {
      client.close();
    });
    
    // Close server
    this.wss.close();
  }
}

// ===== INTEGRATION WITH BOT =====
export class BotDashboardIntegration {
  private dashboard: DashboardServer;
  private bot: any; // Reference to main bot instance
  
  constructor(bot: any, port: number = 8080) {
    this.bot = bot;
    this.dashboard = new DashboardServer(port);
    
    this.setupEventHandlers();
  }
  
  /**
   * Setup event handlers
   */
  private setupEventHandlers() {
    // Dashboard control events
    this.dashboard.on('start', () => {
      logger.info('Starting bot from dashboard');
      this.bot.start();
    });
    
    this.dashboard.on('stop', () => {
      logger.info('Stopping bot from dashboard');
      this.bot.stop();
    });
    
    this.dashboard.on('emergency_stop', () => {
      logger.warn('Emergency stop triggered from dashboard');
      this.bot.emergencyStop();
    });
    
    // Bot events
    this.bot.on('started', () => {
      this.dashboard.updateStatus(true);
    });
    
    this.bot.on('stopped', () => {
      this.dashboard.updateStatus(false);
    });
    
    this.bot.on('trade', (trade: TradeUpdate) => {
      this.dashboard.addTrade(trade);
    });
    
    this.bot.on('stats_update', (stats: Partial<BotStats>) => {
      this.dashboard.updateStats(stats);
    });
    
    this.bot.on('wallet_update', (balance: number) => {
      this.dashboard.updateWalletBalance(balance);
    });
    
    this.bot.on('risk_update', (score: 'LOW' | 'MEDIUM' | 'HIGH') => {
      this.dashboard.updateRiskScore(score);
    });
    
    this.bot.on('alert', (message: string, severity: 'info' | 'warning' | 'error') => {
      this.dashboard.sendAlert(message, severity);
    });
  }
  
  /**
   * Start integration
   */
  start() {
    logger.info('Dashboard integration started');
  }
  
  /**
   * Stop integration
   */
  stop() {
    this.dashboard.shutdown();
    logger.info('Dashboard integration stopped');
  }
}