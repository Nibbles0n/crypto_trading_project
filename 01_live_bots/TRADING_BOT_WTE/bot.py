"""
Bananas Trading Bot - Production Trading Engine

Fetches data from Binance, executes on Kraken, sends Telegram alerts.
Integrates with strategy.py (Dual Range Filter Pro V5.0) for signals.

LONG ONLY MODE - Short signals are ignored (Ontario restrictions).
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any
from pathlib import Path

import ccxt
import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Import strategy components
from strategy import SignalGenerator, ExitManager

# =============================================================================
# CONFIGURATION
# =============================================================================

load_dotenv()

# Logging setup
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    """Configuration matching strategy.py SignalGenerator and ExitManager."""
    # Range Filter 1
    rf1_range_scale: str = "ATR"
    rf1_range_size: float = 2.618
    rf1_range_period: int = 14
    rf1_filter_type: str = "Type 1"
    rf1_movement_source: str = "Wicks"
    rf1_smooth_range: bool = True
    rf1_smoothing_period: int = 27
    rf1_avg_filter_changes: bool = True
    rf1_changes_to_avg: int = 2
    
    # Range Filter 2
    rf2_range_scale: str = "ATR"
    rf2_range_size: float = 5.0
    rf2_range_period: int = 27
    rf2_filter_type: str = "Type 1"
    rf2_movement_source: str = "Wicks"
    rf2_smooth_range: bool = True
    rf2_smoothing_period: int = 55
    rf2_avg_filter_changes: bool = False
    rf2_changes_to_avg: int = 2
    
    # Signal Settings
    show_all_signals: bool = False
    min_signal_rating: int = 3
    use_cooldown: bool = True
    cooldown_bars: int = 3
    enable_price_distance_filter: bool = False
    min_price_distance_pct: float = 1.0
    use_alternate_signals: bool = False
    enable_signal_sizing: bool = True
    enable_profit_potential: bool = True
    min_profit_potential: float = 3.5
    enable_quality_filter: bool = True
    min_quality_score: float = 50.0
    
    # Exit Settings
    exit_mode: str = "Signal + Peak Protection"
    max_profit_cap: float = 25.0
    max_loss_cap: float = 8.0
    peak_profit_trigger: float = 12.0
    peak_drawdown_pct_input: float = 35.0
    peak_lookback_bars: int = 2
    min_profit_threshold: float = 5.0
    enable_same_direction_autoclose: bool = True
    use_profit_cap: bool = True
    use_loss_cap: bool = True
    use_regime_adaptive_exits: bool = True
    
    # Regime Adaptive
    adx_period: int = 14
    ranging_max_profit: float = 15.0
    ranging_peak_dd: float = 25.0
    explosive_min_profit: float = 30.0
    explosive_peak_dd: float = 45.0


@dataclass
class Position:
    """Track an open position."""
    token: str
    entry_price: float
    amount: float
    entry_time: str
    bars_held: int = 0
    signal_rating: int = 0
    position_size_mult: float = 1.0
    order_id: Optional[str] = None
    order_placed_time: Optional[str] = None


@dataclass
class BotState:
    """Bot state for persistence."""
    running: bool = False
    positions: Dict[str, dict] = field(default_factory=dict)
    capital: float = 0.0
    last_update: str = ""
    pending_orders: Dict[str, dict] = field(default_factory=dict)
    pnl_history: List[dict] = field(default_factory=list)


# =============================================================================
# TELEGRAM NOTIFICATIONS
# =============================================================================

class TelegramNotifier:
    """Send alerts to Telegram."""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id and bot_token != "")
        self.last_error = None
        self.connected = False
        
        if self.enabled:
            self._test_connection()
        else:
            logger.warning("Telegram notifications disabled - missing credentials")
    
    def _test_connection(self) -> bool:
        """Test Telegram connection."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            response = requests.get(url, timeout=10)
            if response.ok:
                self.connected = True
                logger.info("Telegram connection verified")
                return True
            else:
                self.last_error = response.text
                self.connected = False
                return False
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            return False
    
    def send(self, message: str) -> bool:
        """Send a message to Telegram."""
        if not self.enabled:
            logger.info(f"[TELEGRAM DISABLED] {message}")
            return False
        
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=payload, timeout=10)
            if response.ok:
                self.connected = True
                return True
            else:
                self.last_error = response.text
                logger.error(f"Telegram send failed: {response.text}")
                return False
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Telegram error: {e}")
            return False
    
    def get_status(self) -> dict:
        """Get Telegram connection status."""
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "last_error": self.last_error
        }


# =============================================================================
# SYMBOL MAPPING
# =============================================================================

BINANCE_TO_KRAKEN = {
    "BTC": "XBT",
}

def binance_symbol(token: str) -> str:
    """Get Binance symbol for token (USDT pair)."""
    return f"{token}/USDT"

def kraken_symbol(token: str) -> str:
    """Get Kraken symbol for token (USD pair)."""
    mapped = BINANCE_TO_KRAKEN.get(token, token)
    return f"{mapped}/USD"

def normalize_token(binance_token: str) -> str:
    """Normalize token name from Binance format."""
    return binance_token.replace("/USDT", "").replace("USDT", "")


# =============================================================================
# EXCHANGE CLIENTS
# =============================================================================

class BinanceDataClient:
    """Fetch OHLCV data from Binance."""
    
    def __init__(self):
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        self.connected = False
        self.last_error = None
    
    def test_connection(self) -> bool:
        """Test Binance connection."""
        try:
            self.exchange.fetch_ticker('BTC/USDT')
            self.connected = True
            return True
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            return False
    
    def fetch_ohlcv(self, token: str, timeframe: str = '5m', limit: int = 300) -> List[dict]:
        """Fetch OHLCV bars."""
        symbol = binance_symbol(token)
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            self.connected = True
            bars = []
            for candle in ohlcv:
                bars.append({
                    'timestamp': candle[0],
                    'open': candle[1],
                    'high': candle[2],
                    'low': candle[3],
                    'close': candle[4],
                    'volume': candle[5]
                })
            return bars
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Binance fetch error for {symbol}: {e}")
            raise
    
    def fetch_ticker(self, token: str) -> dict:
        """Fetch current ticker for token."""
        symbol = binance_symbol(token)
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            self.connected = True
            logger.debug(f"Binance ticker SUCCESS for {token} ({symbol}): ${ticker.get('last', 0)}")
            return {
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'last': ticker['last'],
                'change_pct': ticker.get('percentage', 0)
            }
        except Exception as e:
            logger.error(f"Binance ticker fetch FAILED for {token} ({symbol}): {str(e)}")
            logger.error(f"Binance last_error was: {self.last_error}")
            self.connected = False
            self.last_error = str(e)
            return {'bid': 0, 'ask': 0, 'last': 0, 'change_pct': 0}
    
    def get_status(self) -> dict:
        """Get Binance connection status."""
        return {
            "connected": self.connected,
            "last_error": self.last_error
        }


class KrakenExecutionClient:
    """Execute trades on Kraken."""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.exchange = None
        self.connected = False
        self.last_error = None
        self.live_trading = False
        
        if self.is_configured():
            self._init_exchange()
    
    def _init_exchange(self):
        """Initialize exchange connection."""
        try:
            self.exchange = ccxt.kraken({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'enableRateLimit': True,
            })
            self.live_trading = True
        except Exception as e:
            self.last_error = str(e)
            self.live_trading = False
    
    def is_configured(self) -> bool:
        """Check if API credentials are set."""
        return bool(self.api_key and self.api_secret and self.api_key != "")
    
    def test_connection(self) -> bool:
        """Test Kraken connection with API keys."""
        if not self.is_configured():
            self.connected = False
            self.live_trading = False
            return False
        
        try:
            self.exchange.fetch_balance()
            self.connected = True
            self.live_trading = True
            logger.info("Kraken API connection verified - LIVE TRADING ENABLED")
            return True
        except Exception as e:
            self.last_error = str(e)
            self.connected = False
            self.live_trading = False
            logger.error(f"Kraken connection failed: {e}")
            return False
    
    def fetch_balance(self) -> Dict[str, float]:
        """Fetch account balances."""
        if not self.is_configured():
            logger.warning("Kraken not configured - returning empty balance")
            return {}
        
        try:
            balance = self.exchange.fetch_balance()
            self.connected = True
            return {k: v for k, v in balance['free'].items() if v > 0}
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Kraken balance fetch error: {e}")
            raise
    
    def get_ticker(self, token: str) -> Dict[str, float]:
        """Get current bid/ask for token."""
        symbol = kraken_symbol(token)
        try:
            if self.exchange:
                ticker = self.exchange.fetch_ticker(symbol)
                self.connected = True
                return {
                    'bid': ticker['bid'],
                    'ask': ticker['ask'],
                    'last': ticker['last']
                }
            else:
                return {'bid': 0, 'ask': 0, 'last': 0}
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Kraken ticker fetch error for {symbol}: {e}")
            return {'bid': 0, 'ask': 0, 'last': 0}
    
    def place_limit_buy(self, token: str, amount: float, price: float) -> str:
        """Place a limit buy order at ask price."""
        if not self.is_configured():
            logger.warning(f"Kraken not configured - simulating buy order for {token}")
            return f"SIM-BUY-{token}-{int(time.time())}"
        
        symbol = kraken_symbol(token)
        try:
            order = self.exchange.create_limit_buy_order(symbol, amount, price)
            logger.info(f"Placed limit buy: {symbol} {amount} @ {price} - Order ID: {order['id']}")
            return order['id']
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Kraken buy order error: {e}")
            raise
    
    def place_limit_sell(self, token: str, amount: float, price: float) -> str:
        """Place a limit sell order at bid price."""
        if not self.is_configured():
            logger.warning(f"Kraken not configured - simulating sell order for {token}")
            return f"SIM-SELL-{token}-{int(time.time())}"
        
        symbol = kraken_symbol(token)
        try:
            order = self.exchange.create_limit_sell_order(symbol, amount, price)
            logger.info(f"Placed limit sell: {symbol} {amount} @ {price} - Order ID: {order['id']}")
            return order['id']
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Kraken sell order error: {e}")
            raise
    
    def get_order_status(self, order_id: str, token: str) -> Dict[str, Any]:
        """Get order status."""
        if order_id.startswith("SIM-"):
            return {'status': 'closed', 'filled': 1.0}
        
        symbol = kraken_symbol(token)
        try:
            order = self.exchange.fetch_order(order_id, symbol)
            return {
                'status': order['status'],
                'filled': order['filled'],
                'remaining': order['remaining'],
                'price': order['price']
            }
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Kraken order status error: {e}")
            raise
    
    def cancel_order(self, order_id: str, token: str) -> bool:
        """Cancel an order."""
        if order_id.startswith("SIM-"):
            return True
        
        symbol = kraken_symbol(token)
        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Cancelled order: {order_id}")
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Kraken cancel order error: {e}")
            return False
    
    def get_status(self) -> dict:
        """Get Kraken connection status."""
        return {
            "configured": self.is_configured(),
            "connected": self.connected,
            "live_trading": self.live_trading,
            "last_error": self.last_error
        }


# =============================================================================
# TRADING BOT
# =============================================================================

class TradingBot:
    """Main trading bot."""
    
    STATE_FILE = Path(__file__).parent / "state.json"
    POSITIONS_FILE = Path(__file__).parent / "positions.json"
    ORDER_TIMEOUT_MINUTES = 25
    
    def __init__(self):
        # Load configuration from environment
        self.tokens = [t.strip() for t in os.getenv("TRADING_TOKENS", "BTC,ETH").split(",")]
        self.max_holdings = int(os.getenv("MAX_HOLDINGS", "5"))
        self.capital = float(os.getenv("TOTAL_CAPITAL", "10000"))
        
        # Initialize clients
        self.binance = BinanceDataClient()
        self.kraken = KrakenExecutionClient(
            api_key=os.getenv("KRAKEN_API_KEY", ""),
            api_secret=os.getenv("KRAKEN_API_SECRET", "")
        )
        self.telegram = TelegramNotifier(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", "")
        )
        
        # Load strategy config from environment
        self.strategy_config = self._load_strategy_config()
        
        # Initialize per-token strategy instances
        self.signal_generators: Dict[str, SignalGenerator] = {}
        self.exit_managers: Dict[str, ExitManager] = {}
        self._init_strategy_instances()
        
        # Bot state
        self.state = BotState()
        self.positions: Dict[str, Position] = {}
        self.pending_orders: Dict[str, dict] = {}
        self.running = False
        self.loop_thread: Optional[threading.Thread] = None
        self.log_buffer: List[str] = []
        self.pnl_history: List[dict] = []
        self.token_prices: Dict[str, dict] = {}
        self.bar_index = 0
        
        # Load saved state
        self._load_state()
        
        # Test connections
        self._test_connections()
    
    def _test_connections(self):
        """Test all API connections."""
        self.binance.test_connection()
        if self.kraken.is_configured():
            self.kraken.test_connection()
    
    def _load_strategy_config(self) -> StrategyConfig:
        """Load strategy configuration from environment."""
        def get_env(key: str, default: Any) -> Any:
            val = os.getenv(key, str(default))
            if isinstance(default, bool):
                return val.lower() in ('true', '1', 'yes')
            elif isinstance(default, int):
                return int(val)
            elif isinstance(default, float):
                return float(val)
            return val
        
        return StrategyConfig(
            rf1_range_scale=get_env("RF1_RANGE_SCALE", "ATR"),
            rf1_range_size=get_env("RF1_RANGE_SIZE", 2.618),
            rf1_range_period=get_env("RF1_RANGE_PERIOD", 14),
            rf1_filter_type=get_env("RF1_FILTER_TYPE", "Type 1"),
            rf1_movement_source=get_env("RF1_MOVEMENT_SOURCE", "Wicks"),
            rf1_smooth_range=get_env("RF1_SMOOTH_RANGE", True),
            rf1_smoothing_period=get_env("RF1_SMOOTHING_PERIOD", 27),
            rf1_avg_filter_changes=get_env("RF1_AVG_FILTER_CHANGES", True),
            rf1_changes_to_avg=get_env("RF1_CHANGES_TO_AVG", 2),
            
            rf2_range_scale=get_env("RF2_RANGE_SCALE", "ATR"),
            rf2_range_size=get_env("RF2_RANGE_SIZE", 5.0),
            rf2_range_period=get_env("RF2_RANGE_PERIOD", 27),
            rf2_filter_type=get_env("RF2_FILTER_TYPE", "Type 1"),
            rf2_movement_source=get_env("RF2_MOVEMENT_SOURCE", "Wicks"),
            rf2_smooth_range=get_env("RF2_SMOOTH_RANGE", True),
            rf2_smoothing_period=get_env("RF2_SMOOTHING_PERIOD", 55),
            rf2_avg_filter_changes=get_env("RF2_AVG_FILTER_CHANGES", False),
            rf2_changes_to_avg=get_env("RF2_CHANGES_TO_AVG", 2),
            
            exit_mode=get_env("EXIT_MODE", "Signal + Peak Protection"),
            enable_profit_potential=get_env("ENABLE_PROFIT_POTENTIAL", True),
            min_profit_potential=get_env("MIN_PROFIT_POTENTIAL", 3.5),
            enable_quality_filter=get_env("ENABLE_QUALITY_FILTER", True),
            min_quality_score=get_env("MIN_QUALITY_SCORE", 50.0),
            min_signal_rating=get_env("MIN_SIGNAL_RATING", 3),
            show_all_signals=get_env("SHOW_ALL_SIGNALS", False),
            use_cooldown=get_env("USE_COOLDOWN", True),
            cooldown_bars=get_env("COOLDOWN_BARS", 3),
            enable_price_distance_filter=get_env("ENABLE_PRICE_DISTANCE_FILTER", False),
            min_price_distance_pct=get_env("MIN_PRICE_DISTANCE_PCT", 1.0),
            use_alternate_signals=get_env("USE_ALTERNATE_SIGNALS", False),
            enable_signal_sizing=get_env("ENABLE_SIGNAL_SIZING", True),
            
            max_profit_cap=get_env("MAX_PROFIT_CAP", 25.0),
            max_loss_cap=get_env("MAX_LOSS_CAP", 8.0),
            peak_profit_trigger=get_env("PEAK_PROFIT_TRIGGER", 12.0),
            peak_drawdown_pct_input=get_env("PEAK_DRAWDOWN_PCT_INPUT", 35.0),
            peak_lookback_bars=get_env("PEAK_LOOKBACK_BARS", 2),
            min_profit_threshold=get_env("MIN_PROFIT_THRESHOLD", 5.0),
            enable_same_direction_autoclose=get_env("ENABLE_SAME_DIRECTION_AUTOCLOSE", True),
            use_profit_cap=get_env("USE_PROFIT_CAP", True),
            use_loss_cap=get_env("USE_LOSS_CAP", True),
            use_regime_adaptive_exits=get_env("USE_REGIME_ADAPTIVE_EXITS", True),
            
            adx_period=get_env("ADX_PERIOD", 14),
            ranging_max_profit=get_env("RANGING_MAX_PROFIT", 15.0),
            ranging_peak_dd=get_env("RANGING_PEAK_DD", 25.0),
            explosive_min_profit=get_env("EXPLOSIVE_MIN_PROFIT", 30.0),
            explosive_peak_dd=get_env("EXPLOSIVE_PEAK_DD", 45.0),
        )
    
    def _init_strategy_instances(self):
        """Initialize signal generators and exit managers for each token."""
        for token in self.tokens:
            self.signal_generators[token] = SignalGenerator(self.strategy_config)
            self.exit_managers[token] = ExitManager(self.strategy_config)
    
    def _load_state(self):
        """Load saved state from disk."""
        if self.STATE_FILE.exists():
            try:
                with open(self.STATE_FILE, 'r') as f:
                    data = json.load(f)
                    # Reconstruct positions
                    if 'positions' in data:
                        for token, pos_data in data['positions'].items():
                            self.positions[token] = Position(**pos_data)
                    if 'pnl_history' in data:
                        self.pnl_history = data['pnl_history']
                    logger.info(f"Loaded state: {len(self.positions)} positions")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
    
    def _save_state(self):
        """Save state to disk."""
        try:
            state_data = {
                'positions': {t: asdict(p) for t, p in self.positions.items()},
                'capital': self.capital,
                'last_update': datetime.now(timezone.utc).isoformat(),
                'running': self.running,
                'pnl_history': self.pnl_history[-100:]  # Keep last 100 entries
            }
            
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state_data, f, indent=2)
            
            # Also save positions backup
            with open(self.POSITIONS_FILE, 'w') as f:
                json.dump(state_data['positions'], f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def _log(self, message: str):
        """Log message and add to buffer."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        logger.info(message)
        self.log_buffer.append(full_msg)
        # Keep last 100 logs
        if len(self.log_buffer) > 100:
            self.log_buffer = self.log_buffer[-100:]
    
    def _update_pnl_history(self):
        """Update P&L history for graphing."""
        total_pnl = 0.0
        for token, position in self.positions.items():
            if token in self.token_prices:
                current_price = self.token_prices[token].get('last', position.entry_price)
                pnl = (current_price - position.entry_price) / position.entry_price * 100
                total_pnl += pnl * position.amount * position.entry_price / self.capital * 100
        
        self.pnl_history.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'pnl_pct': round(total_pnl, 2),
            'position_count': len(self.positions)
        })
        
        # Keep last 100 entries
        if len(self.pnl_history) > 100:
            self.pnl_history = self.pnl_history[-100:]
    
    def calculate_position_size(self, token: str) -> float:
        """Calculate position size: total_capital / (max_holdings / current_holdings)."""
        current_holdings = len(self.positions)
        if current_holdings >= self.max_holdings:
            return 0.0
        
        position_capital = self.capital / self.max_holdings
        return position_capital
    
    def _prepare_bar_data(self, bars: List[dict]) -> tuple:
        """Prepare auxiliary data for signal generation."""
        closes = np.array([b['close'] for b in bars])
        volumes = np.array([b['volume'] for b in bars])
        highs = np.array([b['high'] for b in bars])
        lows = np.array([b['low'] for b in bars])
        
        # Volume MA (20 period)
        vol_ma = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        
        # Trend EMA (50 period)
        if len(closes) >= 50:
            alpha = 2.0 / 51
            ema = closes[0]
            for c in closes[1:]:
                ema = alpha * c + (1 - alpha) * ema
            trend_ema = ema
        else:
            trend_ema = np.mean(closes)
        
        # ATR (14 period)
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
        atr_val = np.mean(trs[-14:]) if len(trs) >= 14 else np.mean(trs) if trs else 0
        
        # Recent high/low (20 bars)
        recent_high = np.max(highs[-20:]) if len(highs) >= 20 else np.max(highs)
        recent_low = np.min(lows[-20:]) if len(lows) >= 20 else np.min(lows)
        
        # Close 3 and 10 bars ago
        close_3 = closes[-4] if len(closes) >= 4 else closes[0]
        close_10 = closes[-11] if len(closes) >= 11 else closes[0]
        
        # Local volume MA (5 period)
        local_vol_ma = np.mean(volumes[-5:]) if len(volumes) >= 5 else np.mean(volumes)
        
        # Average separation (placeholder - will be calculated in strategy)
        avg_sep = 0.01
        
        return vol_ma, trend_ema, atr_val, recent_high, recent_low, close_3, close_10, local_vol_ma, avg_sep
    
    def _process_token(self, token: str, bars: List[dict], bar_index: int) -> tuple:
        """Process a single token's bar data through the strategy."""
        if len(bars) < 50:
            return None, None
        
        signal_gen = self.signal_generators[token]
        exit_mgr = self.exit_managers[token]
        
        current_bar = bars[-1]
        vol_ma, trend_ema, atr_val, recent_high, recent_low, close_3, close_10, local_vol_ma, avg_sep = self._prepare_bar_data(bars)
        
        # Process bar through signal generator
        signal, filt1, filt2 = signal_gen.process_bar(
            bar_index=bar_index,
            open_price=current_bar['open'],
            high=current_bar['high'],
            low=current_bar['low'],
            close=current_bar['close'],
            volume=current_bar['volume'],
            vol_ma=vol_ma,
            trend_ema=trend_ema,
            atr_val=atr_val,
            recent_high=recent_high,
            recent_low=recent_low,
            close_3=close_3,
            close_10=close_10,
            local_vol_ma=local_vol_ma,
            avg_sep=avg_sep
        )
        
        # Check for exit if in position
        exit_result = None
        if token in self.positions:
            exit_result = exit_mgr.check_exit(
                bar_index=bar_index,
                open_price=current_bar['open'],
                high=current_bar['high'],
                low=current_bar['low'],
                close=current_bar['close'],
                long_signal=bool(signal and signal.is_long),
                short_signal=False  # LONG ONLY - always False
            )
        
        return signal, exit_result
    
    def _execute_entry(self, token: str, signal, current_price: float):
        """Execute a long entry."""
        # LONG ONLY - ignore short signals
        if not signal.is_long:
            return
        
        if token in self.positions:
            self._log(f"Already in position for {token}, skipping entry")
            return
        
        if len(self.positions) >= self.max_holdings:
            self._log(f"Max holdings ({self.max_holdings}) reached, skipping {token}")
            return
        
        position_capital = self.calculate_position_size(token)
        if position_capital <= 0:
            return
        
        try:
            if self.kraken.live_trading:
                ticker = self.kraken.get_ticker(token)
                ask_price = ticker['ask']
            else:
                ticker = self.binance.fetch_ticker(token)
                ask_price = ticker['ask']
            
            amount = position_capital / ask_price
            
            # Place limit buy at ask
            order_id = self.kraken.place_limit_buy(token, amount, ask_price)
            
            # Create position
            self.positions[token] = Position(
                token=token,
                entry_price=ask_price,
                amount=amount,
                entry_time=datetime.now(timezone.utc).isoformat(),
                signal_rating=signal.rating,
                position_size_mult=signal.position_size_mult,
                order_id=order_id,
                order_placed_time=datetime.now(timezone.utc).isoformat()
            )
            
            # Open trade in exit manager
            self.exit_managers[token].open_trade(
                is_long=True,
                entry_price=ask_price,
                bar_index=signal.bar_index,
                position_size_mult=signal.position_size_mult,
                signal_rating=signal.rating
            )
            
            mode = "LIVE" if self.kraken.live_trading else "SIM"
            self._log(f"[{mode}] LONG {token} @ ${ask_price:,.2f} (Rating: {signal.rating}, Size: {signal.position_size_mult}x)")
            self.telegram.send(f"LONG {token} @ ${ask_price:,.2f} (Rating: {signal.rating}, Size: {signal.position_size_mult}x)")
            
            self._save_state()
            
        except Exception as e:
            self._log(f"Entry error for {token}: {e}")
            self.telegram.send(f"CRITICAL: Entry failed for {token}: {e}")
    
    def _execute_exit(self, token: str, exit_result, current_price: float):
        """Execute an exit."""
        if token not in self.positions:
            return
        
        position = self.positions[token]
        
        try:
            if self.kraken.live_trading:
                ticker = self.kraken.get_ticker(token)
                bid_price = ticker['bid']
            else:
                ticker = self.binance.fetch_ticker(token)
                bid_price = ticker['bid']
            
            # Place limit sell at bid
            order_id = self.kraken.place_limit_sell(token, position.amount, bid_price)
            
            pnl_pct = (bid_price - position.entry_price) / position.entry_price * 100
            pnl_dollar = (bid_price - position.entry_price) * position.amount
            
            mode = "LIVE" if self.kraken.live_trading else "SIM"
            self._log(f"[{mode}] EXIT {token} @ ${bid_price:,.2f} | {pnl_pct:+.2f}% (${pnl_dollar:+,.2f}) | {exit_result.exit_reason}")
            self.telegram.send(f"EXIT {token} @ ${bid_price:,.2f} | {pnl_pct:+.2f}% | {exit_result.exit_reason}")
            
            # Remove position
            del self.positions[token]
            
            # Reset exit manager
            self.exit_managers[token].reset()
            
            self._save_state()
            
        except Exception as e:
            self._log(f"Exit error for {token}: {e}")
            self.telegram.send(f"CRITICAL: Exit failed for {token}: {e}")
    
    def _check_pending_orders(self):
        """Check status of pending orders and cancel if timed out."""
        for token, position in list(self.positions.items()):
            if not position.order_id or not position.order_placed_time:
                continue
            
            try:
                order_status = self.kraken.get_order_status(position.order_id, token)
                
                if order_status['status'] == 'closed':
                    self._log(f"Order filled: {token}")
                    position.order_id = None
                    position.order_placed_time = None
                    self._save_state()
                    continue
                
                # Check timeout
                order_time = datetime.fromisoformat(position.order_placed_time.replace('Z', '+00:00'))
                elapsed = (datetime.now(timezone.utc) - order_time).total_seconds() / 60
                
                if elapsed > self.ORDER_TIMEOUT_MINUTES:
                    self._log(f"Order timeout for {token}, cancelling")
                    self.kraken.cancel_order(position.order_id, token)
                    del self.positions[token]
                    self._save_state()
                    
            except Exception as e:
                logger.error(f"Order check error for {token}: {e}")
    
    def _update_token_prices(self):
        """Update current prices for all tokens."""
        for token in self.tokens:
            try:
                ticker = self.binance.fetch_ticker(token)
                self.token_prices[token] = ticker
            except Exception as e:
                logger.error(f"Price update error for {token}: {e}")
    
    def _trading_iteration(self):
        """Run one iteration of the trading loop."""
        self._update_token_prices()
        
        for token in self.tokens:
            try:
                # Fetch latest bars
                bars = self.binance.fetch_ohlcv(token, '5m', 300)
                
                if len(bars) < 50:
                    self._log(f"Insufficient bars for {token}: {len(bars)}")
                    continue
                
                current_bar = bars[-1]
                current_price = current_bar['close']
                
                # Process through strategy
                signal, exit_result = self._process_token(token, bars, self.bar_index)
                
                # Handle exit first
                if exit_result:
                    self._execute_exit(token, exit_result, current_price)
                
                # Then handle entry (LONG ONLY)
                if signal and signal.is_long:
                    self._execute_entry(token, signal, current_price)
                
                # Update bars held for existing positions
                if token in self.positions:
                    self.positions[token].bars_held += 1
                    
            except Exception as e:
                self._log(f"Error processing {token}: {e}")
                self.telegram.send(f"Error processing {token}: {e}")
        
        # Check pending orders
        self._check_pending_orders()
        
        # Update P&L history
        self._update_pnl_history()
        
        # Save state
        self._save_state()
        
        self.bar_index += 1
        self._log(f"Processed {len(self.tokens)} tokens, {len(self.positions)} open positions")
    
    def _trading_loop(self):
        """Main trading loop - runs every 5 minutes."""
        mode = "LIVE" if self.kraken.live_trading else "SIMULATION"
        self._log(f"Trading loop started - {mode} MODE - {len(self.tokens)} tokens")
        self.telegram.send(f"Bot started | {mode} MODE | {len(self.positions)} positions | {len(self.tokens)} tokens")
        
        while self.running:
            try:
                # Wait for next 5-minute candle close
                now = datetime.now()
                seconds_until_next = (5 - (now.minute % 5)) * 60 - now.second
                if seconds_until_next > 0 and seconds_until_next < 300:
                    time.sleep(seconds_until_next + 2)  # Add 2 seconds buffer
                
                if not self.running:
                    break
                
                self._trading_iteration()
                
                # Small delay before next check
                time.sleep(30)
                
            except Exception as e:
                self._log(f"Trading loop error: {e}")
                self.telegram.send(f"CRITICAL: Trading loop error: {e}")
                time.sleep(60)  # Wait before retrying
    
    def start(self):
        """Start the trading bot."""
        if self.running:
            return False
        
        self.running = True
        self.loop_thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.loop_thread.start()
        self._log("Bot started")
        return True
    
    def stop(self):
        """Stop the trading bot."""
        if not self.running:
            return False
        
        self.running = False
        self._log("Bot stopped")
        self.telegram.send("Bot stopped")
        self._save_state()
        return True
    
    def exit_all(self):
        """Exit all positions immediately."""
        results = []
        for token in list(self.positions.keys()):
            try:
                if self.kraken.live_trading:
                    ticker = self.kraken.get_ticker(token)
                else:
                    ticker = self.binance.fetch_ticker(token)
                
                position = self.positions[token]
                bid_price = ticker['bid']
                
                order_id = self.kraken.place_limit_sell(token, position.amount, bid_price)
                
                pnl_pct = (bid_price - position.entry_price) / position.entry_price * 100
                results.append({
                    'token': token,
                    'pnl_pct': pnl_pct,
                    'exit_price': bid_price
                })
                
                del self.positions[token]
                self.exit_managers[token].reset()
                
                self._log(f"EMERGENCY EXIT {token} @ ${bid_price:,.2f} | {pnl_pct:+.2f}%")
                
            except Exception as e:
                self._log(f"Emergency exit error for {token}: {e}")
        
        self._save_state()
        self.telegram.send(f"EMERGENCY EXIT ALL | {len(results)} positions closed")
        return results
    
    def get_status(self) -> dict:
        """Get current bot status."""
        return {
            'running': self.running,
            'capital': self.capital,
            'position_count': len(self.positions),
            'max_holdings': self.max_holdings,
            'tokens': self.tokens,
            'last_update': datetime.now(timezone.utc).isoformat(),
            'connections': {
                'binance': self.binance.get_status(),
                'kraken': self.kraken.get_status(),
                'telegram': self.telegram.get_status()
            },
            'live_trading': self.kraken.live_trading,
            'mode': 'LIVE' if self.kraken.live_trading else 'SIMULATION'
        }
    
    def get_positions(self) -> List[dict]:
        """Get current positions with P&L."""
        result = []
        for token, position in self.positions.items():
            try:
                if token in self.token_prices:
                    current_price = self.token_prices[token].get('last', 0)
                else:
                    ticker = self.binance.fetch_ticker(token)
                    current_price = ticker['last']
                
                pnl_pct = (current_price - position.entry_price) / position.entry_price * 100
                pnl_dollar = (current_price - position.entry_price) * position.amount
                
                result.append({
                    'token': token,
                    'entry_price': position.entry_price,
                    'current_price': current_price,
                    'amount': position.amount,
                    'pnl_pct': pnl_pct,
                    'pnl_dollar': pnl_dollar,
                    'bars_held': position.bars_held,
                    'signal_rating': position.signal_rating,
                    'entry_time': position.entry_time
                })
            except Exception as e:
                result.append({
                    'token': token,
                    'entry_price': position.entry_price,
                    'current_price': 0,
                    'amount': position.amount,
                    'pnl_pct': 0,
                    'pnl_dollar': 0,
                    'bars_held': position.bars_held,
                    'signal_rating': position.signal_rating,
                    'entry_time': position.entry_time,
                    'error': str(e)
                })
        return result
    
    def get_tokens(self) -> List[dict]:
        """Get token list with current prices."""
        result = []
        for token in self.tokens:
            # Fetch fresh prices to see which ones work
            ticker = self.binance.fetch_ticker(token)
            data = {
                'token': token,
                'price': ticker.get('last', 0),
                'change_pct': ticker.get('change_pct', 0),
                'in_position': token in self.positions
            }
            result.append(data)
        return result
    
    def get_pnl_history(self) -> List[dict]:
        """Get P&L history for graphing."""
        return self.pnl_history
    
    def get_logs(self) -> List[str]:
        """Get recent log entries."""
        return self.log_buffer[-100:]


# =============================================================================
# FLASK API
# =============================================================================

app = Flask(__name__)
CORS(app)
bot: Optional[TradingBot] = None


def get_bot() -> TradingBot:
    """Get or create bot instance."""
    global bot
    if bot is None:
        bot = TradingBot()
    return bot


@app.route('/api/status', methods=['GET'])
def api_status():
    """Get bot status including connection info."""
    try:
        return jsonify(get_bot().get_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/positions', methods=['GET'])
def api_positions():
    """Get current positions."""
    try:
        return jsonify(get_bot().get_positions())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tokens', methods=['GET'])
def api_tokens():
    """Get token list with prices."""
    try:
        return jsonify(get_bot().get_tokens())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pnl_history', methods=['GET'])
def api_pnl_history():
    """Get P&L history for graphing."""
    try:
        return jsonify(get_bot().get_pnl_history())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs', methods=['GET'])
def api_logs():
    """Get recent logs."""
    try:
        return jsonify({'logs': get_bot().get_logs()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/start', methods=['POST'])
def api_start():
    """Start trading loop."""
    try:
        success = get_bot().start()
        return jsonify({'success': success, 'message': 'Bot started' if success else 'Bot already running'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stop', methods=['POST'])
def api_stop():
    """Stop trading loop."""
    try:
        success = get_bot().stop()
        return jsonify({'success': success, 'message': 'Bot stopped' if success else 'Bot not running'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/exit_all', methods=['POST'])
def api_exit_all():
    """Exit all positions."""
    try:
        results = get_bot().exit_all()
        return jsonify({'success': True, 'exits': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def api_health():
    """Health check endpoint."""
    return jsonify({'status': 'ok', 'timestamp': datetime.now(timezone.utc).isoformat()})


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    port = int(os.getenv('BOT_API_PORT', 5001))
    logger.info(f"Starting bot API on port {port}")
    
    # Initialize bot on startup
    get_bot()
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
