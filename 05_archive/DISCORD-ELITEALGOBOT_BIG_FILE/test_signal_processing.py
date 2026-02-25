import asyncio
import json
import logging
import time
from simple_bot import SimpleDiscordBot, TradingSignal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Test signal content
TEST_SIGNAL = """
New signal from EliteAlgo v31 @everyone

 Symbol: SOLUSDT | TF: 15 | 
 Strong Sell Signal Detected : 205
 Stop-Loss 01  : 207.9
 Stop-Loss 02  : 210.8
 Take-Profit 01: 202.1
 Take-Profit 02: 199.2
 Take-Profit 03: 196.3
 Take-Profit 04: 193.4
 Trend Strength : 50.00%
 Volatility: 50
NEW
"""

async def test_signal_processing():
    # Initialize the bot
    bot = SimpleDiscordBot()
    
    # Wait for the Kraken client to initialize and load markets
    if hasattr(bot, 'trading_client'):
        logger.info("Initializing Kraken client and loading markets...")
        try:
            # Ensure markets are loaded before proceeding
            if not hasattr(bot.trading_client.exchange, 'markets') or not bot.trading_client.exchange.markets:
                await bot.trading_client.exchange.load_markets()
                logger.info(f"Loaded {len(bot.trading_client.exchange.markets)} markets")
        except Exception as e:
            logger.error(f"Error loading markets: {e}")
            # Continue with the test even if markets fail to load
    
    # Create a test signal
    signal = bot.parse_signal(TEST_SIGNAL)
    if not signal or not signal.is_valid():
        logger.error("Failed to parse test signal")
        logger.error(f"Signal: {signal}")
        return
        
    # Add signal to active_signals for testing
    bot.active_signals[signal.symbol] = signal
    
    logger.info(f"Parsed test signal: {signal}")
    
    # Test position sizing
    balance = 10000  # Test with $10,000 balance
    position_size = signal.get_position_size(balance, 1.0)  # 1% risk
    logger.info(f"Position size for ${balance} balance at 1% risk: {position_size} {signal.symbol.split('/')[0]}")
    
    # Test risk/reward ratio
    rr_ratio = signal.get_risk_reward_ratio()
    logger.info(f"Risk/Reward ratio: {rr_ratio}")
    
    # Test trading strategy (in paper trading mode)
    logger.info("Testing trading strategy execution...")
    
    # Save original methods
    original_methods = {
        'get_balance': bot.trading_client.get_balance,
        'get_market_info': bot.trading_client.get_market_info,
        'create_market_order': bot.trading_client.create_market_order,
        'create_stop_loss_order': bot.trading_client.create_stop_loss_order,
        'create_take_profit_order': bot.trading_client.create_take_profit_order
    }
    
    async def mock_get_balance(currency='USDT'):
        if currency == 'USDT':
            return 1000  # Return a test balance of 1000 USDT
        return 0.0
    
    async def mock_get_market_info(symbol):
        # Return mock market info for SOL/USDT
        if symbol == 'SOL/USDT':
            return {
                'precision': {'amount': 8, 'price': 8},
                'limits': {
                    'amount': {'min': 0.01, 'max': 1000000},
                    'price': {'min': 0.0001, 'max': 1000000},
                    'cost': {'min': 10, 'max': None}
                },
                'active': True
            }
        return None
        
    async def mock_create_market_order(symbol, side, amount, params=None):
        logger.info(f"[MOCK] Would create {side} market order for {amount} {symbol}")
        return {'id': f'mock_{int(time.time())}', 'status': 'closed', 'symbol': symbol, 'side': side, 'amount': amount}
        
    async def mock_create_stop_loss_order(symbol, side, amount, stop_price, params=None):
        logger.info(f"[MOCK] Would create {side} stop loss order for {amount} {symbol} at {stop_price}")
        return {'id': f'sl_{int(time.time())}', 'status': 'open', 'symbol': symbol, 'side': side, 'amount': amount, 'stopPrice': stop_price}
        
    async def mock_create_take_profit_order(symbol, side, amount, price, stop_price, params=None):
        logger.info(f"[MOCK] Would create {side} take profit order for {amount} {symbol} at {price}")
        return {'id': f'tp_{int(time.time())}', 'status': 'open', 'symbol': symbol, 'side': side, 'amount': amount, 'price': price}
    
    try:
        # Replace the methods with our mocks
        bot.trading_client.get_balance = mock_get_balance
        bot.trading_client.get_market_info = mock_get_market_info
        bot.trading_client.create_market_order = mock_create_market_order
        bot.trading_client.create_stop_loss_order = mock_create_stop_loss_order
        bot.trading_client.create_take_profit_order = mock_create_take_profit_order
        
        # Execute the strategy
        await bot.execute_trading_strategy(signal)
        
        # Verify the signal was processed correctly
        if signal.symbol in bot.active_signals and bot.active_signals[signal.symbol].status == 'OPEN':
            logger.info(f"Signal for {signal.symbol} was processed successfully")
            logger.info(f"Signal details: {bot.active_signals[signal.symbol]}")
        else:
            logger.warning(f"Signal was not properly processed. Status: {getattr(signal, 'status', 'UNKNOWN')}")
            if signal.symbol in bot.active_signals:
                logger.warning(f"Active signal status: {bot.active_signals[signal.symbol].status}")
    except Exception as e:
        logger.error(f"Error executing trading strategy: {e}", exc_info=True)
    finally:
        # Restore the original methods
        for method_name, method in original_methods.items():
            setattr(bot.trading_client, method_name, method)
    
    # Clean up
    await bot.trading_client.close()

if __name__ == "__main__":
    asyncio.run(test_signal_processing())
