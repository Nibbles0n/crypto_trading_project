import os
import json
import time
import logging
import re
from datetime import datetime
from dotenv import load_dotenv
import discum

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingSignal:
    def __init__(self):
        self.symbol = None
        self.signal_type = None  # 'BUY' or 'SELL'
        self.entry_price = None
        self.stop_loss_1 = None
        self.stop_loss_2 = None
        self.take_profits = []  # List of TP levels
        self.trend_strength = None
        self.volatility = None
        self.timestamp = None
        self.status = 'PENDING'  # PENDING, OPEN, CLOSED, CANCELLED
        self.order_ids = []
    
    def is_valid(self):
        """Validate that the signal has all required fields"""
        required_fields = [
            self.symbol,
            self.signal_type,
            self.entry_price is not None,
            self.stop_loss_1 is not None,
            len(self.take_profits) > 0
        ]
        return all(required_fields)
    
    def get_risk_reward_ratio(self):
        """Calculate risk/reward ratio based on first take profit and first stop loss"""
        if not self.take_profits or self.stop_loss_1 is None or self.entry_price is None:
            return None
            
        risk = abs(float(self.entry_price) - float(self.stop_loss_1))
        reward = abs(float(self.take_profits[0]) - float(self.entry_price))
        
        if risk == 0:
            return float('inf')
            
        return round(reward / risk, 2)
    
    def get_position_size(self, account_balance, risk_percent=1.0):
        """Calculate position size based on account balance and risk percentage"""
        if not self.is_valid() or self.entry_price is None or self.stop_loss_1 is None:
            return None
            
        risk_amount = account_balance * (risk_percent / 100)
        price_diff = abs(float(self.entry_price) - float(self.stop_loss_1))
        
        if price_diff == 0:
            return 0
            
        return risk_amount / price_diff
    
    def to_dict(self):
        return {
            'symbol': self.symbol,
            'signal_type': self.signal_type,
            'entry_price': str(self.entry_price) if self.entry_price is not None else None,
            'stop_loss_1': str(self.stop_loss_1) if self.stop_loss_1 is not None else None,
            'stop_loss_2': str(self.stop_loss_2) if self.stop_loss_2 is not None else None,
            'take_profits': [str(tp) for tp in self.take_profits],
            'trend_strength': self.trend_strength,
            'volatility': self.volatility,
            'timestamp': self.timestamp,
            'status': self.status,
            'order_ids': self.order_ids,
            'risk_reward_ratio': self.get_risk_reward_ratio()
        }
    
    def __str__(self):
        return json.dumps(self.to_dict(), indent=2)
import ccxt.async_support as ccxt
import asyncio
from decimal import Decimal, ROUND_DOWN

class KrakenTradingClient:
    async def initialize(self):
        """Initialize the exchange and load markets"""
        await self.exchange.load_markets()
        self.logger.info(f"Loaded {len(self.exchange.markets)} markets")
        return self

    def __init__(self, api_key=None, api_secret=None):
        self.api_key = api_key or os.getenv('KRAKEN_API_KEY')
        self.api_secret = api_secret or os.getenv('KRAKEN_SECRET')
        self.exchange = ccxt.kraken({
            'apiKey': self.api_key,
            'secret': self.api_secret,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True,
                'createMarketBuyOrderRequiresPrice': False,
            },
            'timeout': 30000,
        })
        self.logger = logging.getLogger('KrakenClient')
        # Initialize markets in the background
        asyncio.create_task(self.initialize())
    
    async def get_balance(self, currency='USD'):
        """Get available balance for a specific currency"""
        try:
            balance = await self.exchange.fetch_balance()
            if currency in balance['free']:
                return float(balance['free'][currency])
            return 0.0
        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
            return 0.0
    
    async def get_market_info(self, symbol):
        """Get market information including precision and limits"""
        try:
            market = self.exchange.market(symbol)
            return {
                'symbol': market['symbol'],
                'base': market['base'],
                'quote': market['quote'],
                'precision': {
                    'amount': market['precision']['amount'],
                    'price': market['precision']['price'],
                },
                'limits': {
                    'amount': {
                        'min': float(market['limits']['amount']['min']) if market['limits']['amount']['min'] else 0.0,
                        'max': float(market['limits']['amount']['max']) if market['limits']['amount']['max'] else None,
                    },
                    'price': {
                        'min': float(market['limits']['price']['min']) if market['limits']['price']['min'] else None,
                        'max': float(market['limits']['price']['max']) if market['limits']['price']['max'] else None,
                    },
                    'cost': {
                        'min': float(market['limits']['cost']['min']) if market['limits']['cost']['min'] else None,
                        'max': float(market['limits']['cost']['max']) if market['limits']['cost']['max'] else None,
                    },
                },
            }
        except Exception as e:
            self.logger.error(f"Error getting market info for {symbol}: {e}")
            return None
    
    def format_quantity(self, qty, precision):
        """Format quantity according to market precision"""
        return float(Decimal(str(qty)).quantize(
            Decimal(str(pow(10, -precision))),
            rounding=ROUND_DOWN
        ))
    
    async def create_market_order(self, symbol, side, amount, params=None):
        """Create a market order"""
        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=side.lower(),
                amount=amount,
                params=params or {}
            )
            self.logger.info(f"Market order created: {order}")
            return order
        except Exception as e:
            self.logger.error(f"Error creating market order: {e}")
            return None
    
    async def create_limit_order(self, symbol, side, amount, price, params=None):
        """Create a limit order"""
        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type='limit',
                side=side.lower(),
                amount=amount,
                price=price,
                params=params or {}
            )
            self.logger.info(f"Limit order created: {order}")
            return order
        except Exception as e:
            self.logger.error(f"Error creating limit order: {e}")
            return None
    
    async def create_stop_loss_order(self, symbol, side, amount, stop_price, params=None):
        """Create a stop loss order"""
        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type='stop-loss',
                side=side.lower(),
                amount=amount,
                price=stop_price,
                params={
                    'stopPrice': stop_price,
                    **(params or {})
                }
            )
            self.logger.info(f"Stop loss order created: {order}")
            return order
        except Exception as e:
            self.logger.error(f"Error creating stop loss order: {e}")
            return None
    
    async def create_take_profit_order(self, symbol, side, amount, price, stop_price, params=None):
        """Create a take profit order"""
        try:
            order = await self.exchange.create_order(
                symbol=symbol,
                type='take-profit',
                side=side.lower(),
                amount=amount,
                price=price,
                params={
                    'stopPrice': stop_price,
                    **(params or {})
                }
            )
            self.logger.info(f"Take profit order created: {order}")
            return order
        except Exception as e:
            self.logger.error(f"Error creating take profit order: {e}")
            return None
    
    async def cancel_order(self, order_id, symbol=None):
        """Cancel an order"""
        try:
            result = await self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Order {order_id} cancelled: {result}")
            return result
        except Exception as e:
            self.logger.error(f"Error cancelling order {order_id}: {e}")
            return None
    
    async def close(self):
        """Close the exchange connection"""
        await self.exchange.close()

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_USER_TOKEN')
CHANNEL_IDS = [int(id.strip()) for id in os.getenv('DISCORD_CHANNEL_IDS', '').split(',') if id.strip()]

class SimpleDiscordBot:
    def __init__(self):
            self.bot = discum.Client(token=TOKEN, log=False)
            self.channel_ids = CHANNEL_IDS
            self.active_signals = {}
            self.trading_client = KrakenTradingClient()
            self.risk_percent = float(os.getenv('RISK_PERCENT', '1.0'))  # Default 1% risk per trade
            self.max_trades = int(os.getenv('MAX_TRADES', '5'))  # Default max 5 concurrent trades
            self.leverage = int(os.getenv('LEVERAGE', '1'))  # Default no leverage
            
            logger.info(f"Initialized bot monitoring channels: {self.channel_ids}")
            logger.info(f"Trading settings - Risk: {self.risk_percent}%, Max Trades: {self.max_trades}, Leverage: {self.leverage}x")
            
            # Set up global message handler
            @self.bot.gateway.command
            def handle_messages(resp):
                self.handle_discord_message(resp)

    def parse_signal(self, content):
        """Parse trading signal from message content"""
        signal = TradingSignal()
        signal.timestamp = datetime.utcnow().isoformat()
        
        try:
            # Extract symbol (e.g., GRTUSDT.P)
            symbol_match = re.search(r'Symbol: (\w+\.?\w+)', content)
            if symbol_match:
                signal.symbol = symbol_match.group(1).replace('.P', '').replace('USDT', '/USDT')
            
            # Extract signal type (Buy/Sell)
            if 'Strong Buy' in content:
                signal.signal_type = 'BUY'
            elif 'Strong Sell' in content:
                signal.signal_type = 'SELL'
            
            # Extract entry price - look for the price after "Signal Detected :"
            entry_match = re.search(r'Signal Detected\s*:\s*(\d+(?:\.\d+)?)', content)
            if entry_match:
                signal.entry_price = float(entry_match.group(1))
                logger.info(f"Parsed entry price: {signal.entry_price}")
            else:
                logger.warning("Could not parse entry price from signal")
            
            # Extract stop losses - more flexible pattern to match different formats
            sl_matches = re.findall(r'Stop-Loss\s*\d+\s*:\s*(\d+(?:\.\d+)?)', content)
            if len(sl_matches) >= 1:
                signal.stop_loss_1 = float(sl_matches[0].strip())
                logger.info(f"Parsed stop loss 1: {signal.stop_loss_1}")
            if len(sl_matches) >= 2:
                signal.stop_loss_2 = float(sl_matches[1].strip())
                logger.info(f"Parsed stop loss 2: {signal.stop_loss_2}")
            
            # Extract take profits - more flexible pattern
            tp_matches = re.findall(r'Take-Profit\s*\d+\s*:\s*(\d+(?:\.\d+)?)', content)
            if tp_matches:
                signal.take_profits = [float(tp.strip()) for tp in tp_matches]
                logger.info(f"Parsed take profits: {signal.take_profits}")
            else:
                logger.warning("No take profit levels found in signal")
            
            # Extract trend strength
            trend_match = re.search(r'Trend Strength\s*:\s*(\d+\.?\d*)%', content)
            if trend_match:
                signal.trend_strength = float(trend_match.group(1))
            
            # Extract volatility
            vol_match = re.search(r'Volatility:\s*(\d+)', content)
            if vol_match:
                signal.volatility = int(vol_match.group(1))
            
            return signal
            
        except Exception as e:
            logger.error(f"Error parsing signal: {e}\nContent: {content}")
            return None
    
    async def process_signal_async(self, content, author, channel_id):
        """Process and log trading signal asynchronously"""
        logger.info(f"Processing new signal from {author}")
        
        try:
            # Parse the signal
            signal = self.parse_signal(content)
            if not signal or not signal.symbol:
                logger.warning("Failed to parse signal")
                return
            
            # Validate the signal
            if not signal.is_valid():
                logger.warning(f"Invalid signal received: {signal}")
                return
            
            # Log the parsed signal
            logger.info(f"Parsed signal: {signal}")
            
            # Check if we already have an active signal for this symbol
            if signal.symbol in self.active_signals:
                existing_signal = self.active_signals[signal.symbol]
                if existing_signal.status in ['OPEN', 'PENDING']:
                    logger.info(f"Active signal already exists for {signal.symbol}. Checking if we should update...")
                    # If the new signal is in the same direction, update the existing one
                    if existing_signal.signal_type == signal.signal_type:
                        logger.info(f"Updating existing {signal.signal_type} signal for {signal.symbol}")
                        self.active_signals[signal.symbol] = signal
                        await self.update_position(signal)
                        return
                    else:
                        # If opposite direction, close the existing position first
                        logger.info(f"Opposite signal received for {signal.symbol}. Closing existing position...")
                        await self.close_position(existing_symbol=signal.symbol)
            
            # Check max concurrent trades
            active_trades = sum(1 for s in self.active_signals.values() if s.status in ['OPEN', 'PENDING'])
            if active_trades >= self.max_trades:
                logger.warning(f"Maximum number of trades ({self.max_trades}) reached. Ignoring signal for {signal.symbol}")
                return
            
            # Store the signal
            self.active_signals[signal.symbol] = signal
            
            # Log to file for record keeping
            self.log_signal_to_file(signal)
            
            # Execute the trading strategy
            await self.execute_trading_strategy(signal)
            
        except Exception as e:
            logger.error(f"Error processing signal: {e}", exc_info=True)
    
    def log_signal_to_file(self, signal):
        """Log signal to a JSON file for record keeping"""
        try:
            log_entry = {
                'timestamp': signal.timestamp,
                'signal': signal.to_dict()
            }
            
            with open('signals_log.json', 'a') as f:
                f.write(json.dumps(log_entry) + '\n')
                
        except Exception as e:
            logger.error(f"Error logging signal to file: {e}")
    
    async def execute_trading_strategy(self, signal):
        """Execute trading strategy based on the signal"""
        try:
            logger.info(f"Executing strategy for {signal.symbol} - {signal.signal_type}")
            
            # Get account balance for position sizing
            quote_currency = signal.symbol.split('/')[-1]  # Get quote currency (e.g., USDT)
            balance = await self.trading_client.get_balance(quote_currency)
            
            if balance <= 0:
                logger.error(f"Insufficient {quote_currency} balance")
                return
            
            # Get market info for the symbol
            market_info = await self.trading_client.get_market_info(signal.symbol)
            if not market_info:
                logger.error(f"Could not get market info for {signal.symbol}")
                return
            
            # Calculate position size based on risk percentage
            position_size = signal.get_position_size(balance, self.risk_percent)
            
            # Apply leverage if needed
            if self.leverage > 1:
                position_size *= self.leverage
                logger.info(f"Applying {self.leverage}x leverage. New position size: {position_size}")
            
            # Format position size according to market precision
            position_size = self.trading_client.format_quantity(
                position_size, 
                market_info['precision']['amount']
            )
            
            # Check if position size is within exchange limits
            min_amount = market_info['limits']['amount']['min']
            max_amount = market_info['limits']['amount']['max']
            
            if position_size < min_amount:
                logger.warning(f"Position size {position_size} is below minimum {min_amount} for {signal.symbol}")
                return
                
            if max_amount and position_size > max_amount:
                logger.warning(f"Position size {position_size} exceeds maximum {max_amount} for {signal.symbol}")
                position_size = max_amount
            
            # Place the market order
            side = 'buy' if signal.signal_type == 'BUY' else 'sell'
            order = await self.trading_client.create_market_order(
                symbol=signal.symbol,
                side=side,
                amount=position_size
            )
            
            if not order:
                logger.error(f"Failed to place {side} order for {signal.symbol}")
                return
            
            # Store order ID
            signal.order_ids.append(order['id'])
            signal.status = 'OPEN'
            
            # Place stop loss order
            stop_side = 'sell' if signal.signal_type == 'BUY' else 'buy'
            stop_price = signal.stop_loss_1
            
            stop_order = await self.trading_client.create_stop_loss_order(
                symbol=signal.symbol,
                side=stop_side,
                amount=position_size,
                stop_price=stop_price
            )
            
            if stop_order:
                signal.order_ids.append(stop_order['id'])
            
            # Place take profit orders
            for i, tp_price in enumerate(signal.take_profits, 1):
                # For take profit, we use a limit order
                tp_order = await self.trading_client.create_limit_order(
                    symbol=signal.symbol,
                    side=stop_side,  # Opposite of entry
                    amount=position_size / len(signal.take_profits),  # Split position across TPs
                    price=tp_price
                )
                
                if tp_order:
                    signal.order_ids.append(tp_order['id'])
            
            logger.info(f"Successfully executed {signal.signal_type} strategy for {signal.symbol}")
            logger.info(f"Position size: {position_size} {signal.symbol.split('/')[0]}")
            logger.info(f"Stop loss: {signal.stop_loss_1}")
            logger.info(f"Take profits: {signal.take_profits}")
            
        except Exception as e:
            logger.error(f"Error executing trading strategy: {e}", exc_info=True)
    
    async def update_position(self, signal):
        """Update an existing position with new signal"""
        try:
            logger.info(f"Updating position for {signal.symbol}")
            
            # Here you would implement logic to update stop losses and take profits
            # based on the new signal. This is a simplified version.
            
            # For now, we'll just log the update
            logger.info(f"Updated signal for {signal.symbol}: {signal}")
            
        except Exception as e:
            logger.error(f"Error updating position: {e}", exc_info=True)
    
    async def close_position(self, symbol=None, order_id=None, existing_signal=None):
        """Close an existing position"""
        try:
            if not existing_signal and symbol:
                existing_signal = self.active_signals.get(symbol)
            
            if not existing_signal:
                logger.warning(f"No active position found for symbol: {symbol}")
                return
            
            logger.info(f"Closing position for {existing_signal.symbol}")
            
            # Cancel all open orders
            for oid in existing_signal.order_ids:
                await self.trading_client.cancel_order(oid, existing_signal.symbol)
            
            # If we still have an open position, close it with a market order
            # This is a simplified version - in a real scenario, you'd check the actual position size
            if existing_signal.status == 'OPEN':
                # Get current position size (simplified)
                position_size = existing_signal.get_position_size(1000, self.risk_percent)  # Using 1000 as placeholder balance
                
                if position_size > 0:
                    side = 'sell' if existing_signal.signal_type == 'BUY' else 'buy'
                    await self.trading_client.create_market_order(
                        symbol=existing_signal.symbol,
                        side=side,
                        amount=position_size
                    )
            
            # Update signal status
            existing_signal.status = 'CLOSED'
            logger.info(f"Successfully closed position for {existing_signal.symbol}")
            
        except Exception as e:
            logger.error(f"Error closing position: {e}", exc_info=True)
    
    def run(self):
        """Start the Discord bot"""
        logger.info("Starting Discord bot...")
        logger.info(f"Monitoring channels: {self.channel_ids}")
        
        # Configure discum logging
        self.bot.gateway.log = {
            'console': False,  # We're using our own logging
            'file': False,     # We're using our own file logging
            'level': 0         # Disable discum's internal logging
        }
        
        # Add connection event handler
        @self.bot.gateway.command
        def handle_ready(resp):
            if resp.event.ready_supplemental:
                logger.info("Discord gateway connection established")
                try:
                    user_id = self.bot.gateway.session.user.get('id', 'Unknown')
                    logger.info(f"Bot user ID: {user_id}")
                    
                    # Wait a bit to ensure gateway is fully ready
                    time.sleep(2)
                    
                    # Subscribe to message events for our channels
                    for channel_id in self.channel_ids:
                        try:
                            # First check if we can fetch the channel
                            self.bot.getChannel(channel_id).addCallback(
                                lambda result, cid=channel_id: self._on_channel_check_success(result, cid),
                                lambda failure, cid=channel_id: self._on_channel_check_failure(failure, cid)
                            )
                        except Exception as e:
                            logger.error(f"Error checking channel {channel_id}: {e}")
                    
                except Exception as e:
                    logger.error(f"Error in ready handler: {e}", exc_info=True)
                    
    def handle_discord_message(self, resp):
        """Handle incoming Discord messages"""
        try:
            if resp.event.message:
                message = resp.parsed.auto()
                channel_id = message.get('channel_id')
                
                if not channel_id:
                    logger.warning(f"No channel_id in message: {message}")
                    return
                    
                # Log all channel messages for debugging
                logger.debug(f"Message in channel {channel_id}: {json.dumps(message, indent=2)}")
                
                # Only process messages from the specified channels
                if int(channel_id) in self.channel_ids:
                    author_info = message.get('author', {})
                    author = author_info.get('username', 'Unknown')
                    author_id = author_info.get('id', 'Unknown')
                    content = message.get('content', '')
                    
                    logger.info(f"[Channel: {channel_id}] {author} ({author_id}): {content}")
                    
                    # Process signal if it's from Pine Bot and contains signal information
                    if "Pine Bot" in author and "New signal from EliteAlgo" in content:
                        logger.info("Processing signal from Pine Bot...")
                        asyncio.create_task(self.process_signal_async(content, author, channel_id))
                    else:
                        logger.debug("Message doesn't match signal criteria")
                else:
                    logger.debug(f"Ignoring message from non-monitored channel: {channel_id}")
        except Exception as e:
            logger.error(f"Error in message handler: {e}", exc_info=True)
    
    def _on_channel_check_success(self, result, channel_id):
        """Callback when channel check is successful"""
        logger.info(f"Successfully accessed channel: {channel_id}")
        try:
            # Subscribe to the channel using the correct method
            self.bot.gateway.subscriptions.addChannel(channel_id)
            logger.info(f"Successfully subscribed to channel: {channel_id}")
        except Exception as e:
            logger.error(f"Failed to subscribe to channel {channel_id}: {e}")
            logger.error("Please ensure the bot has been added to the server and has the correct permissions.")
    
    def _on_channel_check_failure(self, failure, channel_id):
        """Callback when channel check fails"""
        logger.error(f"Failed to access channel {channel_id}: {str(failure) if hasattr(failure, '__str__') else 'Unknown error'}")
        logger.info("Please ensure:")
        logger.info(f"1. The bot has been added to the server containing channel {channel_id}")
        logger.info(f"2. The channel ID {channel_id} is correct")
        logger.info("3. The bot has the necessary permissions in the channel")
        
        try:
            logger.info("Connecting to Discord gateway...")
            # Run with auto-reconnect enabled
            self.bot.gateway.run(auto_reconnect=True)
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Error in bot execution: {e}", exc_info=True)
        finally:
            try:
                logger.info("Shutting down Discord connection...")
                self.bot.gateway.close()
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")
            finally:
                logger.info("Bot shutdown complete")

async def main():
    if not TOKEN:
        logger.error("Error: DISCORD_USER_TOKEN not found in .env file")
        return 1
    
    if not CHANNEL_IDS:
        logger.error("Error: No valid channel IDs found in DISCORD_CHANNEL_IDS")
        return 1
    
    bot = None
    try:
        logger.info("=== Starting EliteAlgo Trading Bot ===")
        logger.info(f"Monitoring {len(CHANNEL_IDS)} channel(s)")
        
        # Initialize and run the bot
        bot = SimpleDiscordBot()
        
        # Create a task for the bot's run method
        bot_task = asyncio.create_task(bot.run())
        
        # Wait for keyboard interrupt or other exceptions
        try:
            await bot_task
        except asyncio.CancelledError:
            logger.info("Shutting down gracefully...")
        except Exception as e:
            logger.critical(f"Fatal error in bot: {e}", exc_info=True)
            return 1
            
    except Exception as e:
        logger.critical(f"Fatal error during initialization: {e}", exc_info=True)
        return 1
    finally:
        # Clean up resources
        if bot and hasattr(bot, 'trading_client'):
            await bot.trading_client.close()
        logger.info("Bot shutdown complete")
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        exit(0)
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
        exit(1)
