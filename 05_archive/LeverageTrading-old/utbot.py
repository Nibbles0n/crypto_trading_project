#!/usr/bin/env python3
"""
Alpaca UT Bot Trading System (Updated for Alpaca-py SDK)
Modified for 1-Hour timeframe trading to work better with delayed data
"""

import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime, time, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from dataclasses import dataclass
from collections import deque
import json
import signal

# Third-party imports
try:
    # Updated imports for alpaca-py SDK
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, 
        GetOrdersRequest, 
        ClosePositionRequest,
        GetAssetsRequest
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
    from alpaca.data import StockHistoricalDataClient
    from alpaca.data.requests import (
        StockBarsRequest, 
        StockLatestQuoteRequest,
        StockLatestTradeRequest,
        StockSnapshotRequest
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.models import BarSet, Quote, Trade
    
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.animation import FuncAnimation
    from dotenv import load_dotenv
except ImportError as e:
    print(f"Missing required package: {e}")
    print("Install with: pip install alpaca-py matplotlib python-dotenv pandas numpy")
    sys.exit(1)

# Load environment variables
load_dotenv()

@dataclass
class TradingConfig:
    """Configuration class for trading parameters"""
    # Alpaca Config
    alpaca_public_key: str
    alpaca_private_key: str
    data_feed: str  # 'iex' or 'sip'
    paper_trading: bool
    
    # Trading Settings
    trading_symbol: str
    timeframe: str
    data_fetch_interval: int
    strategy_interval: int
    position_size: float
    position_sizing_method: str
    
    # Strategy Parameters
    ut_key_value: float
    ut_atr_period: int
    lookback_length: int
    
    # Plotting Parameters
    enable_plotting: bool
    plot_interval: int
    plot_width: int
    plot_height: int
    buy_signal_size: int
    sell_signal_size: int
    price_line_width: int
    stop_line_width: int
    zoom_bars: int
    zoom_margin: float
    zoom_percent: float
    zoom_atr_mult: float
    zoom_mode: str

    @classmethod
    def from_env(cls):
        """Create config from environment variables"""
        return cls(
            alpaca_public_key=os.getenv('ALPACA_PUBLIC_KEY', ''),
            alpaca_private_key=os.getenv('ALPACA_PRIVATE_KEY', ''),
            data_feed=os.getenv('DATA_FEED', 'iex').lower(),
            paper_trading=os.getenv('PAPER_TRADING', 'true').lower() == 'true',
            
            # Updated defaults for hourly timeframe
            trading_symbol=os.getenv('TRADING_SYMBOL', 'SPY'),
            timeframe=os.getenv('TIMEFRAME', '1Hour'),  # Changed default to 1Hour
            data_fetch_interval=int(os.getenv('DATA_FETCH_INTERVAL', '300')),  # Changed to 5 minutes
            strategy_interval=int(os.getenv('STRATEGY_INTERVAL', '300')),  # Changed to 5 minutes
            position_size=float(os.getenv('POSITION_SIZE', '1000')),
            position_sizing_method=os.getenv('POSITION_SIZING_METHOD', 'fixed'),
            
            # Adjusted strategy parameters for hourly timeframe
            ut_key_value=float(os.getenv('UT_KEY_VALUE', '1.5')),  # Increased from 1.0
            ut_atr_period=int(os.getenv('UT_ATR_PERIOD', '7')),  # Reduced from 10
            lookback_length=int(os.getenv('LOOKBACK_LENGTH', '100')),
            
            enable_plotting=os.getenv('ENABLE_PLOTTING', 'true').lower() == 'true',
            plot_interval=int(os.getenv('PLOT_INTERVAL', '60')),  # Increased to 1 minute
            plot_width=int(os.getenv('PLOT_WIDTH', '1600')),
            plot_height=int(os.getenv('PLOT_HEIGHT', '800')),
            buy_signal_size=int(os.getenv('BUY_SIGNAL_SIZE', '150')),
            sell_signal_size=int(os.getenv('SELL_SIGNAL_SIZE', '150')),
            price_line_width=int(os.getenv('PRICE_LINE_WIDTH', '2')),
            stop_line_width=int(os.getenv('STOP_LINE_WIDTH', '2')),
            zoom_bars=int(os.getenv('ZOOM_BARS', '50')),
            zoom_margin=float(os.getenv('ZOOM_MARGIN', '0.02')),
            zoom_percent=float(os.getenv('ZOOM_PERCENT', '0.01')),
            zoom_atr_mult=float(os.getenv('ZOOM_ATR_MULT', '4')),
            zoom_mode=os.getenv('ZOOM_MODE', 'rolling')
        )

@dataclass
class MarketData:
    """Container for market data point"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

@dataclass
class Signal:
    """Trading signal container"""
    timestamp: datetime
    signal_type: str  # 'BUY' or 'SELL'
    price: float
    stop_loss: float
    reason: str

class MarketHoursChecker:
    """Utility class to check market hours"""
    
    @staticmethod
    def is_market_open() -> bool:
        """Check if market is currently open - MODIFIED FOR 24/7 TRADING"""
        # QUICK HACK: Always return True for 24/7 trading
        # WARNING: This bypasses normal market hours safety checks
        return True
        
        # Original code (commented out):
        # now = datetime.now(timezone.utc).astimezone()
        # market_open = time(9, 30)  # 9:30 AM
        # market_close = time(16, 0)  # 4:00 PM
        # current_time = now.time()
        # weekday = now.weekday()
        # if weekday >= 5:
        #     return False
        # return market_open <= current_time <= market_close

class DataManager:
    """Manages data fetching and real-time price updates - Modified for hourly bars"""
    
    def __init__(self, config: TradingConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        
        # Initialize new alpaca-py clients
        self.data_client = StockHistoricalDataClient(
            api_key=config.alpaca_public_key,
            secret_key=config.alpaca_private_key
        )
        
        # Data storage
        self.price_history: deque = deque(maxlen=config.lookback_length)
        self.last_historical_time: Optional[datetime] = None
        self.current_hour_data: Optional[MarketData] = None
        self.current_hour: Optional[datetime] = None
    
    def _align_to_hourly_timeframe(self, timestamp: datetime) -> datetime:
        """Align timestamp to hourly timeframe"""
        return timestamp.replace(minute=0, second=0, microsecond=0)
        
    async def initialize_historical_data(self) -> bool:
        """Fetch initial historical data for hourly timeframe"""
        try:
            self.logger.info(f"Fetching historical hourly data for {self.config.trading_symbol}")
            
            # Convert timeframe string to Alpaca TimeFrame
            timeframe_map = {
                '1Min': TimeFrame.Minute,
                '5Min': TimeFrame(5, TimeFrame.Minute),
                '15Min': TimeFrame(15, TimeFrame.Minute),
                '1Hour': TimeFrame.Hour,
                '1Day': TimeFrame.Day
            }
            
            timeframe = timeframe_map.get(self.config.timeframe, TimeFrame.Hour)
            
            # Create request for historical bars - increased to 60 days for hourly data
            request = StockBarsRequest(
                symbol_or_symbols=self.config.trading_symbol,
                timeframe=timeframe,
                start=datetime.now() - timedelta(days=60),  # Increased from 30 to 60 days
                limit=self.config.lookback_length
            )
            
            # Get historical bars using new SDK
            bars_response = self.data_client.get_stock_bars(request)
            
            # Extract bars from response
            if hasattr(bars_response, 'data') and self.config.trading_symbol in bars_response.data:
                bars = bars_response.data[self.config.trading_symbol]
            elif isinstance(bars_response, dict) and self.config.trading_symbol in bars_response:
                bars = bars_response[self.config.trading_symbol]
            else:
                self.logger.error("No bars data found in response")
                self.logger.debug(f"Response content: {bars_response}")
                return False
            
            for bar in bars:
                market_data = MarketData(
                    timestamp=bar.timestamp,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=int(bar.volume)
                )
                self.price_history.append(market_data)
                
            self.last_historical_time = self.price_history[-1].timestamp if self.price_history else None
            
            self.logger.info(f"Loaded {len(self.price_history)} historical hourly bars")
            self.logger.info(f"Last historical timestamp: {self.last_historical_time}")
            
            # Set current hour for tracking bar transitions
            self.current_hour = self._align_to_hourly_timeframe(datetime.now(timezone.utc))
            
            return len(self.price_history) > 0
            
        except Exception as e:
            self.logger.error(f"Failed to fetch historical data: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def update_prices(self) -> bool:
        """Update prices based on data feed configuration - Modified for hourly bars"""
        try:
            self.logger.debug("Starting price update process")
            
            # Get the current hour
            now = datetime.now(timezone.utc)
            current_hour = self._align_to_hourly_timeframe(now)
            
            # Check if we've moved to a new hour
            new_hour = False
            if self.current_hour is None or current_hour > self.current_hour:
                self.logger.info(f"New hour started: {current_hour}")
                
                # If we have accumulated data for the previous hour, add it to price history
                if self.current_hour_data is not None and self.current_hour is not None:
                    self.price_history.append(self.current_hour_data)
                    self.last_historical_time = self.current_hour_data.timestamp
                    self.logger.info(f"Added completed hourly bar: {self.current_hour_data.timestamp} "
                                    f"OHLC: {self.current_hour_data.open:.2f}/"
                                    f"{self.current_hour_data.high:.2f}/"
                                    f"{self.current_hour_data.low:.2f}/"
                                    f"{self.current_hour_data.close:.2f}")
                    self.current_hour_data = None
                
                self.current_hour = current_hour
                new_hour = True
                
            # Based on data feed, get the latest data
            if self.config.data_feed == 'sip':
                return await self._update_hourly_sip_data(new_hour)
            else:
                return await self._update_hourly_iex_data(new_hour)
                
        except Exception as e:
            self.logger.error(f"Failed to update prices: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def _update_hourly_sip_data(self, new_hour: bool) -> bool:
        """Update using Alpaca SIP data for hourly bars"""
        try:
            self.logger.debug("Updating hourly SIP data")
            
            # For hourly data, we can get the latest completed hour
            timeframe = TimeFrame.Hour
            
            # Calculate start time to get the most recent hourly bars
            start_time = datetime.now(timezone.utc) - timedelta(hours=3)
            
            # Get latest bars
            request = StockBarsRequest(
                symbol_or_symbols=self.config.trading_symbol,
                timeframe=timeframe,
                start=start_time,
                limit=3  # Get the last few hours
            )
            
            bars_response = self.data_client.get_stock_bars(request)
            
            # Extract bars from response
            if hasattr(bars_response, 'data') and self.config.trading_symbol in bars_response.data:
                bars = bars_response.data[self.config.trading_symbol]
            elif isinstance(bars_response, dict) and self.config.trading_symbol in bars_response:
                bars = bars_response[self.config.trading_symbol]
            else:
                self.logger.warning("No SIP bars found in response")
                self.logger.debug(f"Response content: {bars_response}")
                return False
            
            # Process new bars
            new_data_added = False
            for bar in bars:
                bar_time = self._align_to_hourly_timeframe(bar.timestamp)
                
                # Only add bars we don't already have
                if not self.last_historical_time or bar_time > self.last_historical_time:
                    market_data = MarketData(
                        timestamp=bar_time,
                        open=float(bar.open),
                        high=float(bar.high),
                        low=float(bar.low),
                        close=float(bar.close),
                        volume=int(bar.volume)
                    )
                    
                    # Don't add the current hour's bar to history yet
                    if bar_time < self.current_hour:
                        self.price_history.append(market_data)
                        self.last_historical_time = bar_time
                        self.logger.info(f"Added hourly bar: {bar_time} OHLC: {market_data.open:.2f}/{market_data.high:.2f}/{market_data.low:.2f}/{market_data.close:.2f}")
                        new_data_added = True
                    else:
                        # Update current hour data
                        self.current_hour_data = market_data
                        self.logger.debug(f"Updated current hour data: {bar_time} Close: {market_data.close:.2f}")
            
            return new_data_added
            
        except Exception as e:
            self.logger.error(f"SIP hourly data update failed: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def _update_hourly_iex_data(self, new_hour: bool) -> bool:
        """Update using IEX real-time quotes for hourly timeframe"""
        try:
            self.logger.debug("Updating hourly IEX data")
            
            # Get latest quote
            quote_request = StockLatestQuoteRequest(
                symbol_or_symbols=self.config.trading_symbol
            )
            
            quote_response = self.data_client.get_stock_latest_quote(quote_request)
            
            # Extract quote from response
            if hasattr(quote_response, 'data') and self.config.trading_symbol in quote_response.data:
                quote = quote_response.data[self.config.trading_symbol]
            elif isinstance(quote_response, dict) and self.config.trading_symbol in quote_response:
                quote = quote_response[self.config.trading_symbol]
            else:
                self.logger.warning("No IEX quote found in response")
                return False
            
            # Handle missing price data
            if not quote.ask_price and not quote.bid_price:
                self.logger.warning("Quote has no price data")
                return False
                
            current_price = float(quote.ask_price if quote.ask_price else quote.bid_price)
            current_time = datetime.now(timezone.utc)
            
            self.logger.debug(f"IEX quote: {current_time} - Price: {current_price:.2f}")
            
            # Update or initialize the current hour data
            if self.current_hour_data is None:
                # Initialize a new hour
                self.current_hour_data = MarketData(
                    timestamp=self.current_hour,
                    open=current_price,
                    high=current_price,
                    low=current_price,
                    close=current_price,
                    volume=1
                )
                self.logger.info(f"Initialized data for new hour {self.current_hour}")
                return True
            else:
                # Update existing hour data
                updated = False
                
                if current_price > self.current_hour_data.high:
                    self.current_hour_data.high = current_price
                    updated = True
                    
                if current_price < self.current_hour_data.low:
                    self.current_hour_data.low = current_price
                    updated = True
                
                # Always update close price
                self.current_hour_data.close = current_price
                self.current_hour_data.volume += 1
                
                if updated:
                    self.logger.debug(f"Updated current hour OHLC: {self.current_hour_data.open:.2f}/{self.current_hour_data.high:.2f}/{self.current_hour_data.low:.2f}/{self.current_hour_data.close:.2f}")
                
                return updated
            
        except Exception as e:
            self.logger.error(f"IEX hourly data update failed: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def get_latest_price(self) -> Optional[float]:
        """Get the most recent price"""
        if self.current_hour_data:
            return self.current_hour_data.close
        elif self.price_history:
            return self.price_history[-1].close
        return None
    
    def get_price_dataframe(self) -> pd.DataFrame:
        """Convert price history to pandas DataFrame for strategy calculations"""
        if not self.price_history:
            return pd.DataFrame()
        
        data = []
        # Include all historical data
        for bar in self.price_history:
            data.append({
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume
            })
        
        # Include current hour data if available
        if self.current_hour_data:
            data.append({
                'timestamp': self.current_hour_data.timestamp,
                'open': self.current_hour_data.open,
                'high': self.current_hour_data.high,
                'low': self.current_hour_data.low,
                'close': self.current_hour_data.close,
                'volume': self.current_hour_data.volume
            })
        
        df = pd.DataFrame(data)
        df.set_index('timestamp', inplace=True)
        return df

class UTBotStrategy:
    """UT Bot Alerts strategy implementation - Adjusted for hourly timeframe"""
    
    def __init__(self, config: TradingConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.current_position = None  # 'LONG', 'SHORT', or None
        self.trailing_stop = None
        self.last_signal = None
        
    def calculate_atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        """Calculate Average True Range"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()
        
        return atr
    
    def calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Calculate Exponential Moving Average"""
        return series.ewm(span=period).mean()
    
    def implement_strategy(self, df: pd.DataFrame) -> Optional[Signal]:
        """
        Implement UT Bot strategy logic - Adjusted for hourly timeframe
        Returns signal if there's a position change needed
        """
        try:
            min_bars = max(self.config.ut_atr_period, 7)
            if len(df) < min_bars:
                self.logger.debug(f"Insufficient data for strategy calculation: {len(df)} bars")
                return None
            
            # Calculate ATR - adjusted for hourly volatility
            atr = self.calculate_atr(df, self.config.ut_atr_period)
            
            # Calculate loss threshold - using adjusted key value for hourly timeframe
            n_loss = self.config.ut_key_value * atr
            
            # Use close price as source (not using Heikin Ashi for now)
            src = df['close']
            
            # Calculate trailing stop
            trailing_stop = self._calculate_trailing_stop(src, n_loss)
            
            if trailing_stop.empty:
                self.logger.debug("Trailing stop calculation returned empty series")
                return None
            
            # Get current values
            current_price = src.iloc[-1]
            current_stop = trailing_stop.iloc[-1]
            ema1 = self.calculate_ema(src, 1).iloc[-1]  # EMA(1) is essentially the close price
            
            # Determine position based on UT Bot logic
            if current_price > current_stop and ema1 > current_stop:
                new_position = 'LONG'
                signal_type = 'BUY'
            elif current_price < current_stop and ema1 < current_stop:
                new_position = 'SHORT'
                signal_type = 'SELL'
            else:
                new_position = self.current_position  # No change
                signal_type = None
            
            # Update trailing stop
            self.trailing_stop = current_stop
            
            # Check if position changed
            if new_position != self.current_position:
                self.logger.info(f"Position change: {self.current_position} -> {new_position}")
                self.logger.info(f"Price: {current_price:.2f}, Stop: {current_stop:.2f}, EMA(1): {ema1:.2f}")
                
                old_position = self.current_position
                self.current_position = new_position
                
                signal = Signal(
                    timestamp=df.index[-1],
                    signal_type=signal_type,
                    price=current_price,
                    stop_loss=current_stop,
                    reason=f"UT Bot signal: {old_position} -> {new_position}"
                )
                
                self.last_signal = signal
                return signal
            
            self.logger.debug(f"No position change. Current: {self.current_position}, Price: {current_price:.2f}, Stop: {current_stop:.2f}")
            return None
            
        except Exception as e:
            self.logger.error(f"Strategy calculation failed: {e}")
            self.logger.error(traceback.format_exc())
            return None
    
    def _calculate_trailing_stop(self, src: pd.Series, n_loss: pd.Series) -> pd.Series:
        """Calculate the UT Bot trailing stop"""
        try:
            trailing_stop = pd.Series(index=src.index, dtype=float)
            pos = pd.Series(index=src.index, dtype=int)
            
            # Initialize
            trailing_stop.iloc[0] = src.iloc[0] - n_loss.iloc[0] if not pd.isna(n_loss.iloc[0]) else 0
            pos.iloc[0] = 1
            
            for i in range(1, len(src)):
                if pd.isna(n_loss.iloc[i]) or pd.isna(src.iloc[i]):
                    trailing_stop.iloc[i] = trailing_stop.iloc[i-1]
                    pos.iloc[i] = pos.iloc[i-1]
                    continue
                
                # UT Bot trailing stop logic
                if src.iloc[i] > trailing_stop.iloc[i-1] and src.iloc[i-1] > trailing_stop.iloc[i-1]:
                    new_stop = max(trailing_stop.iloc[i-1], src.iloc[i] - n_loss.iloc[i])
                elif src.iloc[i] < trailing_stop.iloc[i-1] and src.iloc[i-1] < trailing_stop.iloc[i-1]:
                    new_stop = min(trailing_stop.iloc[i-1], src.iloc[i] + n_loss.iloc[i])
                else:
                    if src.iloc[i] > trailing_stop.iloc[i-1]:
                        new_stop = src.iloc[i] - n_loss.iloc[i]
                    else:
                        new_stop = src.iloc[i] + n_loss.iloc[i]
                
                trailing_stop.iloc[i] = new_stop
                
                # Determine position
                if src.iloc[i-1] <= trailing_stop.iloc[i-1] and src.iloc[i] > trailing_stop.iloc[i]:
                    pos.iloc[i] = 1
                elif src.iloc[i-1] >= trailing_stop.iloc[i-1] and src.iloc[i] < trailing_stop.iloc[i]:
                    pos.iloc[i] = -1
                else:
                    pos.iloc[i] = pos.iloc[i-1]
            
            return trailing_stop
            
        except Exception as e:
            self.logger.error(f"Trailing stop calculation failed: {e}")
            self.logger.error(traceback.format_exc())
            return pd.Series()

class OrderExecutor:
    """Handles order execution and position management"""
    
    def __init__(self, config: TradingConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.trading_client = TradingClient(
            api_key=config.alpaca_public_key,
            secret_key=config.alpaca_private_key,
            paper=config.paper_trading
        )
        self.current_position = None
        
    async def execute_signal(self, signal: Signal) -> bool:
        """Execute trading signal"""
        try:
            self.logger.info(f"Executing signal: {signal.signal_type} at {signal.price:.2f}")
            
            # Close existing position first
            if self.current_position:
                await self._close_position()
            
            # Open new position
            success = await self._open_position(signal)
            
            if success:
                self.logger.info(f"Successfully executed {signal.signal_type} signal")
                return True
            else:
                self.logger.error(f"Failed to execute {signal.signal_type} signal")
                return False
                
        except Exception as e:
            self.logger.error(f"Signal execution failed: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def _close_position(self) -> bool:
        """Close current position"""
        try:
            self.logger.debug("Attempting to close current position")
            positions = self.trading_client.get_all_positions()
            
            position_found = False
            for position in positions:
                if position.symbol == self.config.trading_symbol:
                    position_found = True
                    self.logger.info(f"Closing position: {position.qty} shares of {position.symbol}")
                    
                    # Use close_position method from new SDK
                    close_request = ClosePositionRequest(
                        qty=abs(float(position.qty)),
                        percentage=None
                    )
                    
                    order = self.trading_client.close_position(
                        symbol_or_asset_id=position.symbol,
                        close_position_data=close_request
                    )
                    
                    self.logger.info(f"Close order submitted")
                    return True
            
            if not position_found:
                self.logger.debug("No positions found to close")
            
            return True  # No position to close
            
        except Exception as e:
            self.logger.error(f"Failed to close position: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def _open_position(self, signal: Signal) -> bool:
        """Open new position based on signal"""
        try:
            # Calculate position size - adjusted for potential higher volatility in hourly bars
            if self.config.position_sizing_method == 'fixed':
                qty = int(self.config.position_size / signal.price)
            else:  # percent of portfolio
                account = self.trading_client.get_account()
                portfolio_value = float(account.portfolio_value)
                position_value = portfolio_value * (self.config.position_size / 100)
                qty = int(position_value / signal.price)
            
            self.logger.debug(f"Calculated order quantity: {qty} shares")
            
            if qty <= 0:
                self.logger.error("Calculated quantity is 0 or negative")
                return False
            
            # Create market order request using new SDK
            order_side = OrderSide.BUY if signal.signal_type == 'BUY' else OrderSide.SELL
            
            market_order_data = MarketOrderRequest(
                symbol=self.config.trading_symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY
            )
            
            self.logger.info(f"Opening {order_side} position: {qty} shares at ~{signal.price:.2f}")
            
            order = self.trading_client.submit_order(order_data=market_order_data)
            
            self.logger.info(f"Order submitted: {order.id}")
            self.current_position = signal.signal_type
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to open position: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def close_all_positions(self) -> bool:
        """Close all positions on shutdown"""
        try:
            self.logger.info("Closing all positions for shutdown")
            
            # Use close_all_positions method from new SDK
            responses = self.trading_client.close_all_positions(cancel_orders=True)
            
            # Log the responses without assuming specific structure
            for i, response in enumerate(responses):
                self.logger.info(f"Position {i+1} closed: {response}")
            
            self.current_position = None
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to close all positions: {e}")
            self.logger.error(traceback.format_exc())
            return False

class TradingPlotter:
    """Real-time plotting functionality - Adjusted for hourly timeframe"""
    
    def __init__(self, config: TradingConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.fig = None
        self.ax = None
        self.price_line = None
        self.stop_line = None
        self.signals = []
        
        if config.enable_plotting:
            self._setup_plot()
    
    def _setup_plot(self):
        """Initialize matplotlib plot"""
        try:
            plt.style.use('dark_background')
            self.fig, self.ax = plt.subplots(figsize=(self.config.plot_width/100, self.config.plot_height/100))
            self.ax.set_title(f'{self.config.trading_symbol} - UT Bot Strategy (Hourly)', color='white', fontsize=16)
            self.ax.set_xlabel('Time', color='white')
            self.ax.set_ylabel('Price', color='white')
            self.ax.grid(True, alpha=0.3)
            
            # Initialize empty lines
            self.price_line, = self.ax.plot([], [], 'cyan', linewidth=self.config.price_line_width, label='Price')
            self.stop_line, = self.ax.plot([], [], 'orange', linewidth=self.config.stop_line_width, label='Trailing Stop')
            
            self.ax.legend()
            plt.tight_layout()
            
            self.logger.info("Plot initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Plot setup failed: {e}")
            self.logger.error(traceback.format_exc())
            self.config.enable_plotting = False
    
    async def update_plot(self, data_manager: DataManager, strategy: UTBotStrategy):
        """Update the plot with latest data"""
        if not self.config.enable_plotting or not self.fig:
            return
        
        try:
            self.logger.debug("Updating plot")
            df = data_manager.get_price_dataframe()
            
            if df.empty or len(df) < 2:
                self.logger.debug("Not enough data to update plot")
                return
            
            # Get data for plotting
            timestamps = df.index
            prices = df['close'].values
            
            # Update price line
            self.price_line.set_data(timestamps, prices)
            
            # Update trailing stop line if available
            if strategy.trailing_stop is not None:
                try:
                    # Calculate historical trailing stop for visualization
                    atr = strategy.calculate_atr(df, strategy.config.ut_atr_period)
                    n_loss = strategy.config.ut_key_value * atr
                    trailing_stop_series = strategy._calculate_trailing_stop(df['close'], n_loss)
                    
                    # Use the calculated trailing stop series for visualization
                    self.stop_line.set_data(timestamps, trailing_stop_series.values)
                except Exception as e:
                    self.logger.debug(f"Could not calculate trailing stop series for plot: {e}")
                    # Fallback: use current trailing stop value for all points
                    stop_values = [strategy.trailing_stop] * len(timestamps)
                    self.stop_line.set_data(timestamps, stop_values)
            
            # Add buy/sell signals
            if strategy.last_signal:
                signal = strategy.last_signal
                color = 'lime' if signal.signal_type == 'BUY' else 'red'
                marker = '^' if signal.signal_type == 'BUY' else 'v'
                size = self.config.buy_signal_size if signal.signal_type == 'BUY' else self.config.sell_signal_size
                
                self.ax.scatter([signal.timestamp], [signal.price], 
                              c=color, marker=marker, s=size, alpha=0.8,
                              edgecolors='white', linewidth=1, zorder=5)
                
                self.logger.debug(f"Added {signal.signal_type} signal marker to plot")
                
                # Clear the last signal to avoid repeated plotting
                strategy.last_signal = None
            
            # Auto-scale and center the plot
            self._auto_scale_plot(timestamps, prices)
            
            # Refresh the plot
            self.fig.canvas.draw()
            self.fig.canvas.flush_events()
            plt.pause(0.001)  # Added to process GUI events
            self.logger.debug("Plot updated successfully")
            
        except Exception as e:
            self.logger.error(f"Plot update failed: {e}")
            self.logger.error(traceback.format_exc())
    
    def _auto_scale_plot(self, timestamps, prices):
        """Auto-scale plot based on zoom configuration"""
        try:
            if len(timestamps) == 0 or len(prices) == 0:
                return
            
            # Determine visible range - modified for hourly timeframe
            if self.config.zoom_mode == 'rolling' and len(timestamps) > self.config.zoom_bars:
                visible_start = len(timestamps) - self.config.zoom_bars
                visible_timestamps = timestamps[visible_start:]
                visible_prices = prices[visible_start:]
            else:
                visible_timestamps = timestamps
                visible_prices = prices
            
            if len(visible_prices) == 0:
                return
            
            # Set x-axis range
            self.ax.set_xlim(visible_timestamps[0], visible_timestamps[-1])
            
            # Set y-axis range with margin
            price_min = np.min(visible_prices)
            price_max = np.max(visible_prices)
            price_range = price_max - price_min
            
            if price_range == 0:
                price_range = price_max * 0.01  # 1% margin if no range
            
            margin = price_range * self.config.zoom_margin
            self.ax.set_ylim(price_min - margin, price_max + margin)
            
            # Format x-axis for hourly time display
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            self.ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))  # Show every 4 hours
            plt.setp(self.ax.xaxis.get_majorticklabels(), rotation=45)
            
        except Exception as e:
            self.logger.error(f"Auto-scale failed: {e}")
            self.logger.error(traceback.format_exc())
    
    def show_plot(self):
        """Display the plot (non-blocking)"""
        if self.config.enable_plotting and self.fig:
            plt.ion()  # Turn on interactive mode
            plt.show(block=False)
            plt.pause(0.001)  # Process GUI events
            self.logger.debug("Plot displayed (non-blocking)")

class AlpacaTradingBot:
    """Main trading bot orchestrator - Modified for hourly timeframe"""
    
    def __init__(self):
        self.config = TradingConfig.from_env()
        self.logger = self._setup_logging()
        self.running = False
        
        # Components
        self.data_manager = DataManager(self.config, self.logger)
        self.strategy = UTBotStrategy(self.config, self.logger)
        self.order_executor = OrderExecutor(self.config, self.logger) 
        self.plotter = TradingPlotter(self.config, self.logger)
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _setup_logging(self) -> logging.Logger:
        """Setup comprehensive logging"""
        logger = logging.getLogger('AlpacaTradingBot')
        logger.setLevel(logging.DEBUG)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)  # Keep at INFO for clean console output
        
        # File handler
        file_handler = logging.FileHandler('trading_bot.log')
        file_handler.setLevel(logging.DEBUG)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)
        
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        
        return logger
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.running = False
    
    async def initialize(self) -> bool:
        """Initialize all components"""
        try:
            self.logger.info("Initializing Alpaca UT Bot Trading System for Hourly Timeframe")
            self.logger.info(f"Configuration: Symbol={self.config.trading_symbol}, "
                           f"Timeframe={self.config.timeframe}, "
                           f"Data Feed={self.config.data_feed}, "
                           f"Paper Trading={self.config.paper_trading}")
            
            # Validate API credentials using new SDK
            try:
                account = self.order_executor.trading_client.get_account()
                self.logger.info(f"Connected to Alpaca account: {account.id}")
                self.logger.info(f"Buying power: ${float(account.buying_power):,.2f}")
                self.logger.info(f"Portfolio value: ${float(account.portfolio_value):,.2f}")
            except Exception as e:
                self.logger.error(f"Failed to connect to Alpaca API: {e}")
                self.logger.error(traceback.format_exc())
                return False
            
            # Initialize historical data
            if not await self.data_manager.initialize_historical_data():
                self.logger.error("Failed to initialize historical data")
                return False
            
            # Setup plotting
            if self.config.enable_plotting:
                self.plotter.show_plot()
            
            self.logger.info("Bot initialization completed successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    async def run(self):
        """Main bot execution loop - Modified for hourly timeframe"""
        try:
            if not await self.initialize():
                return
            
            self.running = True
            self.logger.info("Starting main trading loop")
            
            # Task scheduling
            data_update_task = None
            strategy_task = None
            plot_task = None
            
            last_data_update = 0
            last_strategy_check = 0
            last_plot_update = 0
            last_status_update = 0
            
            loop_count = 0
            
            while self.running:
                loop_count += 1
                current_time = asyncio.get_event_loop().time()
                
                # Check market hours
                if not MarketHoursChecker.is_market_open():
                    self.logger.info("Market is closed, waiting...")
                    await asyncio.sleep(300)  # Wait 5 minutes
                    continue
                
                # Periodic status update at INFO level for visibility
                if current_time - last_status_update >= 60:  # Every minute
                    last_status_update = current_time
                    current_price = self.data_manager.get_latest_price()
                    
                    if current_price:
                        current_hour_str = self.data_manager.current_hour.strftime("%Y-%m-%d %H:%M:%S") if self.data_manager.current_hour else "None"
                        trailing_stop_str = f"{self.strategy.trailing_stop:.2f}" if self.strategy.trailing_stop is not None else "None"
                        self.logger.info(f"Status: Position={self.strategy.current_position}, "
                                       f"Current Price={current_price:.2f}, "
                                       f"Stop={trailing_stop_str}, "
                                       f"Current Hour={current_hour_str}")
                
                # Update prices - less frequent for hourly strategy
                if current_time - last_data_update >= self.config.data_fetch_interval:
                    self.logger.debug("Scheduling price update task")
                    if data_update_task is None or data_update_task.done():
                        data_update_task = asyncio.create_task(self._update_prices())
                        last_data_update = current_time
                
                # Run strategy
                if current_time - last_strategy_check >= self.config.strategy_interval:
                    self.logger.debug("Scheduling strategy task")
                    if strategy_task is None or strategy_task.done():
                        strategy_task = asyncio.create_task(self._run_strategy())
                        last_strategy_check = current_time
                
                # Update plot
                if (self.config.enable_plotting and 
                    current_time - last_plot_update >= self.config.plot_interval):
                    self.logger.debug("Scheduling plot update task")
                    if plot_task is None or plot_task.done():
                        plot_task = asyncio.create_task(self._update_plot())
                        last_plot_update = current_time
                
                # Check for task exceptions
                for task in [data_update_task, strategy_task, plot_task]:
                    if task and task.done() and not task.cancelled():
                        try:
                            exc = task.exception()
                            if exc:
                                self.logger.error(f"Task raised exception: {exc}")
                                self.logger.error(traceback.format_exc())
                        except asyncio.CancelledError:
                            pass
                
                # Short sleep to prevent CPU spinning
                await asyncio.sleep(1)
            
            # Cleanup
            await self._shutdown()
            
        except Exception as e:
            self.logger.error(f"Main loop error: {e}")
            self.logger.error(traceback.format_exc())
            await self._shutdown()
    
    async def _update_prices(self):
        """Update prices task"""
        try:
            self.logger.debug("Running price update task")
            success = await self.data_manager.update_prices()
            if success:
                price = self.data_manager.get_latest_price()
                self.logger.debug(f"Price update successful: {price:.2f}")
            else:
                self.logger.debug("No new price data available")
        except Exception as e:
            self.logger.error(f"Price update task failed: {e}")
            self.logger.error(traceback.format_exc())
    
    async def _run_strategy(self):
        """Run strategy evaluation task"""
        try:
            self.logger.debug("Running strategy task")
            df = self.data_manager.get_price_dataframe()
            if df.empty:
                self.logger.debug("No data available for strategy")
                return
            
            self.logger.debug(f"Running strategy with {len(df)} data points")
            signal = self.strategy.implement_strategy(df)
            
            if signal:
                self.logger.info(f"Strategy generated signal: {signal.signal_type} at {signal.price:.2f}")
                
                # Execute the signal
                success = await self.order_executor.execute_signal(signal)
                if success:
                    self.logger.info("Signal executed successfully")
                else:
                    self.logger.error("Signal execution failed")
            
        except Exception as e:
            self.logger.error(f"Strategy task failed: {e}")
            self.logger.error(traceback.format_exc())
    
    async def _update_plot(self):
        """Update plot task"""
        try:
            self.logger.debug("Running plot update task")
            await self.plotter.update_plot(self.data_manager, self.strategy)
        except Exception as e:
            self.logger.error(f"Plot update task failed: {e}")
            self.logger.error(traceback.format_exc())
    
    async def _shutdown(self):
        """Graceful shutdown procedure"""
        try:
            self.logger.info("Initiating shutdown sequence")
            
            # Close all positions
            await self.order_executor.close_all_positions()
            
            # Save final state
            self._save_final_state()
            
            # Close matplotlib windows if any
            if self.config.enable_plotting and plt.get_fignums():
                self.logger.debug("Closing matplotlib windows")
                plt.close('all')
            
            self.logger.info("Shutdown completed successfully")
            
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")
            self.logger.error(traceback.format_exc())
    
    def _save_final_state(self):
        """Save final bot state for analysis"""
        try:
            state = {
                'timestamp': datetime.now().isoformat(),
                'final_position': self.strategy.current_position,
                'trailing_stop': self.strategy.trailing_stop,
                'data_points': len(self.data_manager.price_history),
                'last_price': self.data_manager.get_latest_price(),
                'timeframe': self.config.timeframe
            }
            
            with open('final_state.json', 'w') as f:
                json.dump(state, f, indent=2)
            
            self.logger.info("Final state saved to final_state.json")
            
        except Exception as e:
            self.logger.error(f"Failed to save final state: {e}")
            self.logger.error(traceback.format_exc())

def create_env_template():
    """Create a .env template file"""
    template = """# Alpaca Configuration (Updated for alpaca-py SDK)
ALPACA_PUBLIC_KEY=your_public_key_here
ALPACA_PRIVATE_KEY=your_private_key_here
DATA_FEED=iex
PAPER_TRADING=true

# Trading Settings
TRADING_SYMBOL=SPY
TIMEFRAME=1Hour
DATA_FETCH_INTERVAL=300
STRATEGY_INTERVAL=300
POSITION_SIZE=1000
POSITION_SIZING_METHOD=fixed

# Strategy Parameters
UT_KEY_VALUE=1.5
UT_ATR_PERIOD=7
LOOKBACK_LENGTH=100

# Plotting Parameters
ENABLE_PLOTTING=true
PLOT_INTERVAL=60
PLOT_WIDTH=1600
PLOT_HEIGHT=800
BUY_SIGNAL_SIZE=150
SELL_SIGNAL_SIZE=150
PRICE_LINE_WIDTH=2
STOP_LINE_WIDTH=2
ZOOM_BARS=50
ZOOM_MARGIN=0.02
ZOOM_PERCENT=0.01
ZOOM_ATR_MULT=4
ZOOM_MODE=rolling
"""
    
    if not os.path.exists('.env'):
        with open('.env', 'w') as f:
            f.write(template)
        print("Created .env template file. Please update with your credentials.")
        return False
    return True

async def main():
    """Main entry point"""
    print("🤖 Alpaca UT Bot Trading System (Hourly Timeframe)")
    print("=" * 60)
    
    # Check for .env file
    if not create_env_template():
        return
    
    # Create and run bot
    bot = AlpacaTradingBot()
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Bot crashed: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())