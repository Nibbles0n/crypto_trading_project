import os
import re
import asyncio
import logging
import sys
import time
from typing import Optional
from datetime import datetime, timedelta

import discord
import discord.gateway
import discord.http
import discord.state
import aiohttp

# Disable voice-related functionality
discord.opus = None
discord.ffmpeg = None
if hasattr(discord, 'voice_client'):
    discord.voice_client.VoiceClient = None

# Patch discord.py to work with user tokens
class DiscordClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trader = None
        self.last_message_time = 0
        self.message_delay = 1  # seconds between processing messages
        
    async def on_ready(self):
        logger.info(f'Successfully logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')
        for guild in self.guilds:
            logger.info(f'Connected to guild: {guild.name} (ID: {guild.id})')
            for channel in guild.text_channels:
                logger.info(f'  - Channel: #{channel.name} (ID: {channel.id})')
        
        # Initialize the trader
        self.trader = KrakenTrader()
        logger.info('Kraken trader initialized and ready')
    
    async def on_message(self, message):
        # Ignore messages from ourselves
        if message.author == self.user:
            return
            
        # Only process messages from specified channels
        if message.channel.id not in DISCORD_CHANNEL_IDS:
            return
            
        # Rate limiting
        current_time = time.time()
        if current_time - self.last_message_time < self.message_delay:
            return
        self.last_message_time = current_time
        
        logger.info(f"Message from {message.author}: {message.content}")
        
        # Parse the message for trading signals
        signal = SignalParser.parse_message(message.content)
        if signal:
            logger.info(f"Signal detected: {signal}")
            try:
                await self.trader.place_order(signal)
                # Send a confirmation message
                await message.add_reaction('✅')
            except Exception as e:
                logger.error(f"Error placing order: {e}")
                await message.add_reaction('❌')

# Import other dependencies
import ccxt.async_support as ccxt
from dotenv import load_dotenv

# Configure logging before any other imports
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Load environment variables
load_dotenv()

# Get logger instance
logger = logging.getLogger('TradingBot')
logger.setLevel(logging.INFO)

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_USER_TOKEN')
DISCORD_CHANNEL_IDS = [int(id.strip()) for id in os.getenv('DISCORD_CHANNEL_IDS', '').split(',') if id.strip()]
KRAKEN_API_KEY = os.getenv('KRAKEN_API_KEY')
KRAKEN_SECRET = os.getenv('KRAKEN_SECRET')

# Initialize Discord client with intents
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True

# Initialize Discord client
client = DiscordClient(intents=intents)

class TradingSignal:
    def __init__(self, symbol: str, side: str, entry: float, stop_loss: float, take_profit: float):
        self.symbol = symbol.upper()
        self.side = side.upper()
        self.entry = float(entry)
        self.stop_loss = float(stop_loss)
        self.take_profit = float(take_profit)
    
    def __str__(self):
        return f"{self.symbol} {self.side} Entry: {self.entry} SL: {self.stop_loss} TP: {self.take_profit}"

class SignalParser:
    @staticmethod
    def parse_message(message: str) -> Optional[TradingSignal]:
        try:
            # Remove any markdown formatting and extra spaces
            clean_message = ' '.join(message.replace('*', '').replace('`', '').split())
            
            # Extract symbol (e.g., "BTC/USD" from "BTC/USDT LONG")
            symbol_match = re.search(r'([A-Z]+/[A-Z]+)', clean_message)
            if not symbol_match:
                return None
                
            symbol = symbol_match.group(1)
            
            # Determine direction (LONG/SHORT)
            if 'LONG' in clean_message.upper():
                side = 'BUY'
            elif 'SHORT' in clean_message.upper():
                side = 'SELL'
            else:
                return None
            
            # Extract entry price
            entry_match = re.search(r'ENTRY[^\d]*([\d.]+)', clean_message.upper())
            if not entry_match:
                return None
            entry = entry_match.group(1)
            
            # Extract stop loss
            sl_match = re.search(r'SL[^\d]*([\d.]+)', clean_message.upper())
            if not sl_match:
                return None
            stop_loss = sl_match.group(1)
            
            # Extract take profit
            tp_match = re.search(r'TP[^\d]*([\d.]+)', clean_message.upper())
            if not tp_match:
                return None
            take_profit = tp_match.group(1)
            
            return TradingSignal(symbol, side, entry, stop_loss, take_profit)
            
        except Exception as e:
            logger.error(f"Error parsing message: {e}")
            return None

class KrakenTrader:
    def __init__(self):
        self.exchange = ccxt.kraken({
            'apiKey': KRAKEN_API_KEY,
            'secret': KRAKEN_SECRET,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True,
                'recvWindow': 60000,
            }
        })
        self.positions = {}
    
    async def place_order(self, signal: TradingSignal):
        try:
            # Convert symbol to Kraken format if needed
            symbol = signal.symbol
            if not symbol.endswith('USD'):
                symbol = f"{symbol.split('/')[0]}USD"
            
            # Get market info and check if symbol is available
            markets = await self.exchange.load_markets()
            if symbol not in markets:
                logger.error(f"Symbol {symbol} not found on Kraken")
                return None
            
            market = markets[symbol]
            
            # Get current price and calculate position size (1% of balance for example)
            balance = await self.exchange.fetch_balance()
            usd_balance = balance.get('USD', {}).get('free', 0)
            
            if usd_balance < 10:  # Minimum order size check
                logger.error("Insufficient balance")
                return None
                
            # Calculate position size (1% of balance)
            position_size = (usd_balance * 0.01) / float(signal.entry)
            
            # Round to appropriate precision
            precision = market['precision']['amount']
            position_size = round(position_size, precision)
            
            # Ensure minimum order size
            min_amount = market['limits']['amount']['min']
            if position_size < min_amount:
                position_size = min_amount
            
            logger.info(f"Placing {signal.side} order for {position_size} {symbol} at {signal.entry}")
            
            # Place the order
            order = await self.exchange.create_order(
                symbol=symbol,
                type='limit',
                side=signal.side.lower(),
                amount=position_size,
                price=float(signal.entry)
            )
            
            # If order is successful, place stop loss and take profit
            if order:
                logger.info(f"Order placed: {order}")
                
                # Place stop loss
                sl_order = await self.exchange.create_order(
                    symbol=symbol,
                    type='stop_loss',
                    side='sell' if signal.side.upper() == 'BUY' else 'buy',
                    amount=position_size,
                    price=float(signal.stop_loss),
                    params={'stopPrice': float(signal.stop_loss)}
                )
                logger.info(f"Stop loss placed: {sl_order}")
                
                # Place take profit
                tp_order = await self.exchange.create_order(
                    symbol=symbol,
                    type='take_profit',
                    side='sell' if signal.side.upper() == 'BUY' else 'buy',
                    amount=position_size,
                    price=float(signal.take_profit),
                    params={'stopPrice': float(signal.take_profit)}
                )
                logger.info(f"Take profit placed: {tp_order}")
                
                return {
                    'entry': order,
                    'stop_loss': sl_order,
                    'take_profit': tp_order
                }
            
            return None
            
        except ccxt.InsufficientFunds as e:
            logger.error(f"Insufficient funds: {e}")
            raise
        except ccxt.InvalidOrder as e:
            logger.error(f"Invalid order: {e}")
            raise
        except Exception as e:
            logger.error(f"Error in place_order: {e}")
            raise
        await self.exchange.close()

class DiscordClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trader = KrakenTrader()
        self.last_message_time = 0
        self.message_delay = 1  # Minimum delay between processing messages (in seconds)
    
    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')
        # List the servers the client is connected to
        for guild in self.guilds:
            logger.info(f'Connected to {guild.name} (ID: {guild.id})')
            # List all channels in the server
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    logger.info(f'  - #{channel.name} (ID: {channel.id})')

    async def on_message(self, message):
        try:
            # Ignore messages from ourselves
            if message.author == self.user:
                return
                
            # Only process messages from specified channels
            if message.channel.id not in DISCORD_CHANNEL_IDS:
                return
                
            # Rate limiting
            current_time = time.time()
            if current_time - self.last_message_time < self.message_delay:
                return
            self.last_message_time = current_time
            
            logger.info(f"Message from {message.author}: {message.content}")
            
            # Parse the message for trading signals
            signal = SignalParser.parse_message(message.content)
            if signal:
                logger.info(f"Signal detected: {signal}")
                await self.trader.place_order(signal)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

# Initialize Discord client
client = DiscordClient(intents=intents)

# Rest of your existing TradingSignal, SignalParser, and KrakenTrader classes remain the same
# [Previous TradingSignal, SignalParser, and KrakenTrader classes go here]

def validate_token(token: str) -> bool:
    """Basic token validation"""
    if not token or not isinstance(token, str):
        return False
    # User tokens typically have 3 parts separated by dots
    parts = token.split('.')
    return len(parts) == 3 and all(parts)

async def main_async():
    # Load environment variables
    load_dotenv()
    
    # Validate environment variables
    required_vars = ['DISCORD_USER_TOKEN', 'KRAKEN_API_KEY', 'KRAKEN_SECRET', 'DISCORD_CHANNEL_IDS']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.info("Please update your .env file with the following variables:")
        logger.info("DISCORD_USER_TOKEN=your_discord_user_token")
        logger.info("DISCORD_CHANNEL_IDS=channel_id1,channel_id2,...")
        logger.info("KRAKEN_API_KEY=your_kraken_api_key")
        logger.info("KRAKEN_SECRET=your_kraken_secret")
        return 1
    
    # Get channel IDs
    channel_ids = [int(id.strip()) for id in os.getenv('DISCORD_CHANNEL_IDS', '').split(',') if id.strip()]
    
    if not channel_ids:
        logger.error("No valid channel IDs found in DISCORD_CHANNEL_IDS")
        return 1
    
    # Initialize Discord client
    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True
    intents.guilds = True
    
    client = DiscordClient(intents=intents)
    
    try:
        # Start the client
        logger.info("Starting Discord client...")
        await client.start(os.getenv('DISCORD_USER_TOKEN'))
        
    except discord.LoginFailure as e:
        logger.error(f"Failed to log in to Discord: {e}")
        return 1
    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        return 1
    finally:
        # Clean up
        if not client.is_closed():
            await client.close()
            
        # Close the Kraken exchange connection if it exists
        if hasattr(client, 'trader') and hasattr(client.trader, 'exchange'):
            await client.trader.exchange.close()
    
    return 0

async def main():
    return await main_async()

if __name__ == "__main__":
    import sys
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)