"""
Improved Heikin-Ashi ATR Trailing Stop Trading System
Matches the exact logic used in the live Alpaca trading strategy

Key improvements:
- Consistent signal generation matching live strategy
- Proper position management (no overlapping positions)
- Configurable end-of-day position closing
- Enhanced logging and debugging
- Same mathematical calculations as live strategy

Requirements: pip install pandas numpy matplotlib yfinance

Usage:
    system = HeikinAshiTradingSystem(
        initial_capital=100000,
        trade_size_pct=0.10,
        atr_period=14,
        atr_sensitivity=1.0,
        commission_fixed=0.0,
        commission_pct=0.000035,
        enable_shorting=True,
        close_eod=True,
        verbose=True
    )
    results = system.run("AAPL", period="1y")
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import yfinance as yf
from typing import Dict, Optional
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

class HeikinAshiTradingSystem:
    def __init__(
        self,
        initial_capital: float = 100000,
        trade_size_pct: float = 0.10,
        atr_period: int = 14,
        atr_sensitivity: float = 1.0,
        commission_fixed: float = 0.0,
        commission_pct: float = 0.000035,
        enable_shorting: bool = True,
        close_eod: bool = True,
        verbose: bool = True,
        trailing_stop_smooth_period: int = 0
    ):
        """
        Initialize trading system with parameters matching live strategy
        
        Args:
            initial_capital: Starting capital
            trade_size_pct: Position size as % of capital (0.10 = 10%)
            atr_period: ATR calculation period
            atr_sensitivity: ATR multiplier for trailing stop
            commission_fixed: Fixed commission per trade (e.g., $1.00)
            commission_pct: Commission as percentage of trade value (e.g., 0.000035 = 0.0035%)
            enable_shorting: Whether to allow short positions
            close_eod: Close positions at end of day
            verbose: Print detailed trade information
        """
        self.initial_capital = initial_capital
        self.trade_size_pct = trade_size_pct
        self.atr_period = atr_period
        self.atr_sensitivity = atr_sensitivity
        self.commission_fixed = commission_fixed
        self.commission_pct = commission_pct
        self.enable_shorting = enable_shorting
        self.trailing_stop_smooth_period = trailing_stop_smooth_period
        self.close_eod = close_eod
        self.verbose = verbose
        self.reset()
    
    def reset(self):
        """Reset system state"""
        self.capital = self.initial_capital
        self.position = 0  # 0 = no position, 1 = long, -1 = short
        self.position_size = 0  # Number of shares
        self.entry_price = 0
        self.entry_time = 0
        self.trades = []
        self.equity_curve = []
        self.total_commissions = 0.0
        self.last_signal = None
    
    def calculate_commission(self, trade_value: float) -> float:
        """Calculate total commission for a trade"""
        commission = self.commission_fixed + (abs(trade_value) * self.commission_pct)
        return commission
    
    def interpolate_series(self, series, factor=2):
        """Linearly interpolate a series by the given factor (2 = add midpoint between each pair)"""
        x = np.arange(len(series))
        x_new = np.linspace(0, len(series) - 1, len(series) * factor - (factor - 1))
        return np.interp(x_new, x, series)
    
    def get_data(self, ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """Get data from Yahoo Finance"""
        try:
            data = yf.download(ticker, period=period, interval=interval, progress=False)
            if data.empty:
                raise ValueError(f"No data for {ticker}")
            
            data = data.reset_index()
            
            # Handle MultiIndex columns
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.droplevel(1)
            
            # Standardize column names
            data.columns = [col.lower().replace(' ', '_') for col in data.columns]
            
            # Handle datetime column
            date_cols = [col for col in data.columns if 'date' in col or 'time' in col]
            if date_cols:
                data = data.rename(columns={date_cols[0]: 'date'})
            
            # Remove adj close if present
            if 'adj_close' in data.columns:
                data = data.drop('adj_close', axis=1)
            
            # Ensure required columns exist
            required = ['open', 'high', 'low', 'close', 'volume']
            if not all(col in data.columns for col in required):
                raise ValueError(f"Missing required columns. Found: {list(data.columns)}")
            
            return data.dropna().reset_index(drop=True)
            
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            return pd.DataFrame()
    
    def calculate_heikin_ashi(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate Heikin-Ashi candles - EXACT same as live strategy"""
        data = df.copy()
        
        # HA Close = (O + H + L + C) / 4
        ha_close = (data['open'] + data['high'] + data['low'] + data['close']) / 4
        
        ha_open = np.zeros(len(data))
        ha_open[0] = data['open'].iloc[0]
        for i in range(1, len(data)):
            ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2
        
        # HA High = max(H, HA_O, HA_C)
        ha_high = np.maximum(data['high'], np.maximum(ha_open, ha_close))
        
        # HA Low = min(L, HA_O, HA_C)
        ha_low = np.minimum(data['low'], np.minimum(ha_open, ha_close))
        
        data['ha_open'] = ha_open
        data['ha_high'] = ha_high
        data['ha_low'] = ha_low
        data['ha_close'] = ha_close
        
        return data
    
    def calculate_atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
        """Calculate ATR using Wilder's method - EXACT same as live strategy"""
        tr = np.zeros(len(high))
        tr[0] = high[0] - low[0]
        
        for i in range(1, len(high)):
            tr[i] = max(high[i] - low[i], 
                       abs(high[i] - close[i-1]), 
                       abs(low[i] - close[i-1]))
        
        atr = np.zeros(len(high))
        atr[0] = tr[0]
        
        for i in range(1, len(high)):
            if i < self.atr_period:
                atr[i] = np.mean(tr[:i+1])
            else:
                atr[i] = (tr[i] + (self.atr_period - 1) * atr[i-1]) / self.atr_period
        
        return atr
    
    def calculate_trailing_stop(self, close: np.ndarray, atr: np.ndarray) -> np.ndarray:
        """Calculate ATR trailing stop - EXACT same as live strategy"""
        n_loss = self.atr_sensitivity * atr
        trailing_stop = np.zeros(len(close))
        trailing_stop[0] = close[0] - n_loss[0]
        
        for i in range(1, len(close)):
            prev_stop = trailing_stop[i-1]
            current = close[i]
            prev = close[i-1]
            
            if current > prev_stop and prev > prev_stop:
                trailing_stop[i] = max(prev_stop, current - n_loss[i])
            elif current < prev_stop and prev < prev_stop:
                trailing_stop[i] = min(prev_stop, current + n_loss[i])
            elif current > prev_stop:
                trailing_stop[i] = current - n_loss[i]
            else:
                trailing_stop[i] = current + n_loss[i]
        
        return trailing_stop
    
    def calculate_ema(self, values: np.ndarray, period: int) -> np.ndarray:
        """Calculate EMA - EXACT same as live strategy"""
        alpha = 2.0 / (period + 1.0)
        ema = np.zeros_like(values)
        ema[0] = values[0]
        
        for i in range(1, len(values)):
            ema[i] = alpha * values[i] + (1 - alpha) * ema[i-1]
        
        return ema
    
    def generate_signal(self, current_ema: float, current_stop: float, 
                       prev_ema: float, prev_stop: float) -> Optional[str]:
        """Generate signals - EXACT same logic as live strategy"""
        # Buy signal: EMA(1) crosses above trailing stop
        if current_ema > current_stop and prev_ema <= prev_stop:
            return 'buy'
        
        # Sell signal: EMA(1) crosses below trailing stop
        elif current_ema < current_stop and prev_ema >= prev_stop:
            if self.enable_shorting:
                return 'sell'
            else:
                return 'close'  # Close long positions only if shorting disabled
        
        return None
    
    def close_position(self, price: float, timestamp: int, reason: str = ""):
        """Close current position and record trade"""
        if self.position == 0:
            return
        
        trade_value = abs(self.position_size) * price
        commission = self.calculate_commission(trade_value)
        self.total_commissions += commission
        
        if self.position == 1:  # Close long
            pnl = (price - self.entry_price) * self.position_size - commission
            side = 'LONG'
        else:  # Close short
            pnl = (self.entry_price - price) * abs(self.position_size) - commission
            side = 'SHORT'
        
        self.trades.append({
            'side': side.lower(),
            'entry_time': self.entry_time,
            'exit_time': timestamp,
            'entry_price': self.entry_price,
            'exit_price': price,
            'quantity': abs(self.position_size),
            'pnl': pnl,
            'commission': commission
        })
        
        self.capital += pnl
        
        if self.verbose:
            print(f"  ✅ Closed {side} {reason}: ${pnl:.0f} (${self.entry_price:.2f} -> ${price:.2f}) [Commission: ${commission:.2f}]")
        
        # Reset position
        self.position = 0
        self.position_size = 0
        self.entry_price = 0
    
    def execute_signal(self, signal: str, price: float, timestamp: int):
        """Execute trading signal - EXACT same logic as live strategy"""
        if signal == 'buy':
            # Close any short position first
            if self.position == -1:
                self.close_position(price, timestamp, "for BUY signal")
            
            # Open long position if not already long
            if self.position != 1:
                trade_value = self.capital * self.trade_size_pct
                entry_commission = self.calculate_commission(trade_value)
                self.total_commissions += entry_commission
                
                # Adjust position size to account for entry commission
                effective_trade_value = trade_value - entry_commission
                self.position_size = effective_trade_value / price
                self.position = 1
                self.entry_price = price
                self.entry_time = timestamp
                
                if self.verbose:
                    print(f"  🟢 Opened LONG at ${price:.2f}, size: {self.position_size:.0f} shares [Entry Commission: ${entry_commission:.2f}]")
        
        elif signal == 'sell' and self.enable_shorting:
            # Close any long position first
            if self.position == 1:
                self.close_position(price, timestamp, "for SELL signal")
            
            # Open short position if not already short
            if self.position != -1:
                trade_value = self.capital * self.trade_size_pct
                entry_commission = self.calculate_commission(trade_value)
                self.total_commissions += entry_commission
                
                # Adjust position size to account for entry commission
                effective_trade_value = trade_value - entry_commission
                self.position_size = -(effective_trade_value / price)  # Negative for short
                self.position = -1
                self.entry_price = price
                self.entry_time = timestamp
                
                if self.verbose:
                    print(f"  🔴 Opened SHORT at ${price:.2f}, size: {abs(self.position_size):.0f} shares [Entry Commission: ${entry_commission:.2f}]")
        
        elif signal == 'close':
            # Close any existing position (used when shorting is disabled)
            if self.position != 0:
                self.close_position(price, timestamp, "due to CLOSE signal")
                if self.verbose:
                    print(f"  ⏹️ Going NEUTRAL - shorting disabled")
    
    def is_end_of_day(self, df: pd.DataFrame, current_index: int) -> bool:
        """Check if this is end of trading day"""
        if not self.close_eod:
            return False
            
        # Last bar of data
        if current_index == len(df) - 1:
            return True
        
        # Check if next bar is different day
        if 'date' in df.columns and current_index < len(df) - 1:
            current_date = df['date'].iloc[current_index]
            next_date = df['date'].iloc[current_index + 1]
            
            if hasattr(current_date, 'date'):
                current_date = current_date.date()
                next_date = next_date.date()
                return current_date != next_date
        
        return False
    
    def backtest(self, df: pd.DataFrame) -> Dict:
        """Run backtest with EXACT same logic as live strategy"""
        if self.verbose:
            shorting_status = "Enabled" if self.enable_shorting else "Disabled"
            print(f"\n🚀 Running Heikin-Ashi ATR Strategy Backtest")
            print(f"📊 ATR Period: {self.atr_period}, Sensitivity: {self.atr_sensitivity}")
            print(f"💰 Position Size: {self.trade_size_pct:.1%} of capital")
            print(f"📈 Shorting: {shorting_status}")
            print(f"🌅 Close EOD: {'Yes' if self.close_eod else 'No'}")
            print(f"💸 Commission: ${self.commission_fixed:.2f} fixed + {self.commission_pct:.5%} of trade value")
        
        # Calculate Heikin-Ashi candles
        df = self.calculate_heikin_ashi(df)
        
        # Calculate ATR using HA data
        atr = self.calculate_atr(df['ha_high'].values, df['ha_low'].values, df['ha_close'].values)
        
        # Calculate trailing stop using HA close
        trailing_stop = self.calculate_trailing_stop(df['ha_close'].values, atr)

        #smooth the trailing stop
        if self.trailing_stop_smooth_period > 1:
            trailing_stop_smoothed = self.calculate_ema(trailing_stop, self.trailing_stop_smooth_period)
        else:
            trailing_stop_smoothed = trailing_stop
        
        # Calculate EMA(1) on HA open
        ema1 = self.calculate_ema(df['ha_open'].values, 1)

        # Interpolate indicators
        ema_interp = self.interpolate_series(ema1, factor=2)
        stop_interp = self.interpolate_series(trailing_stop_smoothed, factor=2)
        price_interp = self.interpolate_series(df['close'].values, factor=2)

        # Add indicators to dataframe (for plotting)
        df['atr'] = atr
        df['trailing_stop'] = trailing_stop_smoothed
        df['ema1'] = ema1

        # --- Interpolated crossover logic ---
        last_signal = None
        last_position = 0
        last_entry_idx = None

        for i in range(1, len(ema_interp)):
            # Detect crossovers
            signal = None
            if ema_interp[i-1] <= stop_interp[i-1] and ema_interp[i] > stop_interp[i]:
                signal = 'buy'
            elif ema_interp[i-1] >= stop_interp[i-1] and ema_interp[i] < stop_interp[i]:
                signal = 'sell' if self.enable_shorting else 'close'

            # Only act on new signals
            if signal and signal != last_signal:
                # Map interpolated index to nearest real bar for timestamp
                real_idx = int(i // 2)
                price = price_interp[i]

                if self.verbose:
                    print(f"\n📈 Interpolated {signal.upper()} at interp idx {i} (real idx {real_idx})")
                    print(f"   Interp Price: ${price:.2f}, EMA: ${ema_interp[i]:.2f}, Stop: ${stop_interp[i]:.2f}")

                self.execute_signal(signal, price, real_idx)
                last_signal = signal
                last_entry_idx = i

            # End-of-day close (use real bars only)
            if self.position != 0 and (i % 2 == 0):  # Only check on real bars
                real_idx = int(i // 2)
                if self.is_end_of_day(df, real_idx):
                    if self.verbose:
                        print(f"\n🌅 End of day - closing position at ${df['close'].iloc[real_idx]:.2f}")
                    self.close_position(df['close'].iloc[real_idx], real_idx, "EOD")
                    last_signal = None

            # Record equity curve (on real bars only)
            if i % 2 == 0:
                self.equity_curve.append(self.capital)

        # Close final position if exists
        if self.position != 0:
            final_price = df['close'].iloc[-1]
            if self.verbose:
                print(f"\n🏁 Final position close at ${final_price:.2f}")
            self.close_position(final_price, len(df)-1, "End of backtest")

        # --- Performance metrics (unchanged) ---
        if self.trades:
            trades_df = pd.DataFrame(self.trades)
            total_pnl = trades_df['pnl'].sum()
            total_commissions = trades_df['commission'].sum()
            win_rate = len(trades_df[trades_df['pnl'] > 0]) / len(trades_df)
            roi = (self.capital - self.initial_capital) / self.initial_capital
            avg_trade = total_pnl / len(trades_df)
            avg_commission = total_commissions / len(trades_df)

            # Additional metrics
            winning_trades = trades_df[trades_df['pnl'] > 0]
            losing_trades = trades_df[trades_df['pnl'] <= 0]

            avg_win = winning_trades['pnl'].mean() if len(winning_trades) > 0 else 0
            avg_loss = losing_trades['pnl'].mean() if len(losing_trades) > 0 else 0
            profit_factor = abs(winning_trades['pnl'].sum() / losing_trades['pnl'].sum()) if len(losing_trades) > 0 and losing_trades['pnl'].sum() != 0 else float('inf')

            max_win = trades_df['pnl'].max()
            max_loss = trades_df['pnl'].min()

        else:
            total_pnl = total_commissions = win_rate = roi = avg_trade = avg_commission = 0
            avg_win = avg_loss = profit_factor = max_win = max_loss = 0

        return {
            'df': df,
            'total_trades': len(self.trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'total_commissions': total_commissions,
            'avg_trade': avg_trade,
            'avg_commission': avg_commission,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_win': max_win,
            'max_loss': max_loss,
            'profit_factor': profit_factor,
            'roi': roi,
            'final_capital': self.capital,
            'equity_curve': self.equity_curve
        }
    
    def plot_results(self, df: pd.DataFrame):
        """Plot trading results with Heikin-Ashi candles"""
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 10), height_ratios=[3, 1])
        
        # Heikin-Ashi candlestick chart
        for i in range(len(df)):
            color = 'green' if df['ha_close'].iloc[i] > df['ha_open'].iloc[i] else 'red'
            body_height = abs(df['ha_close'].iloc[i] - df['ha_open'].iloc[i])
            body_bottom = min(df['ha_close'].iloc[i], df['ha_open'].iloc[i])
            
            ax1.bar(i, body_height, bottom=body_bottom, color=color, alpha=0.7, width=0.8)
            ax1.plot([i, i], [df['ha_low'].iloc[i], df['ha_high'].iloc[i]], 
                    color='black', linewidth=1, alpha=0.7)
        
        # Plot indicators
        ax1.plot(df['ha_close'], label='HA Close', color='blue', linewidth=1, alpha=0.8)
        ax1.plot(df['ema1'], label='EMA(1) on HA', color='cyan', linewidth=1.5)
        ax1.plot(df['trailing_stop'], label='ATR Trailing Stop', color='orange', linewidth=2)
        
        # Plot trade entry/exit points
        if self.trades:
            trades_df = pd.DataFrame(self.trades)
            
            # Entry points
            long_entries = trades_df[trades_df['side'] == 'long']
            short_entries = trades_df[trades_df['side'] == 'short']
            
            if len(long_entries) > 0:
                ax1.scatter(long_entries['entry_time'], long_entries['entry_price'], 
                           color='green', marker='^', s=100, label='Long Entry', zorder=5)
            
            if len(short_entries) > 0:
                ax1.scatter(short_entries['entry_time'], short_entries['entry_price'], 
                           color='red', marker='v', s=100, label='Short Entry', zorder=5)
            
            # Exit points
            ax1.scatter(trades_df['exit_time'], trades_df['exit_price'], 
                       color='black', marker='x', s=50, label='Exit', zorder=5)
        
        shorting_status = "Enabled" if self.enable_shorting else "Disabled"
        ax1.set_title(f'Heikin-Ashi ATR Trailing Stop System (Shorting: {shorting_status})')
        ax1.set_ylabel('Price')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Equity curve
        if self.equity_curve:
            ax2.plot(self.equity_curve, label='Equity', color='blue', linewidth=2)
            ax2.axhline(y=self.initial_capital, color='gray', linestyle='--', alpha=0.7, label='Initial Capital')
        
        ax2.set_ylabel('Capital ($)')
        ax2.set_xlabel('Time')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.show()
    
    def run(self, ticker: str, period: str = "1y", interval: str = "1d", plot: bool = True) -> Dict:
        """Complete run: fetch data, backtest, and optionally plot"""
        self.reset()
        
        if self.verbose:
            print(f"🎯 Backtesting {ticker} ({period}, {interval})")
        
        df = self.get_data(ticker, period, interval)
        
        if df.empty:
            print("❌ No data available")
            return {}
        
        results = self.backtest(df)
        
        # Print summary
        print(f"\n" + "="*50)
        print(f"📈 BACKTEST RESULTS for {ticker}")
        print(f"="*50)
        print(f"💰 Initial Capital: ${self.initial_capital:,.0f}")
        print(f"💰 Final Capital: ${results['final_capital']:,.0f}")
        print(f"📊 Total Trades: {results['total_trades']}")
        print(f"🎯 Win Rate: {results['win_rate']:.1%}")
        print(f"💵 Total P&L: ${results['total_pnl']:,.0f}")
        print(f"💸 Total Commissions: ${results['total_commissions']:,.2f}")
        print(f"📈 ROI: {results['roi']:.1%}")
        print(f"📊 Average Trade: ${results['avg_trade']:,.2f}")
        print(f"💸 Average Commission: ${results['avg_commission']:,.2f}")
        
        if results['total_trades'] > 0:
            print(f"🟢 Average Win: ${results['avg_win']:,.2f}")
            print(f"🔴 Average Loss: ${results['avg_loss']:,.2f}")
            print(f"🏆 Max Win: ${results['max_win']:,.2f}")
            print(f"💥 Max Loss: ${results['max_loss']:,.2f}")
            print(f"⚖️ Profit Factor: {results['profit_factor']:.2f}")
        
        print(f"="*50)
        
        if plot and not df.empty:
            self.plot_results(results['df'])
        
        return results


# =============================================================================
# USAGE EXAMPLES - MATCHING LIVE STRATEGY CONFIGURATIONS
# =============================================================================

def example_default_strategy():
    """Default strategy matching live trading config"""
    system = HeikinAshiTradingSystem(
        initial_capital=100000,
        trade_size_pct=1.00,        # 100% position size
        atr_period=30,              # 10-period ATR
        atr_sensitivity=1.5,        # 1x ATR sensitivity
        commission_fixed=0.0,       # No fixed commission
        commission_pct=0.0005,    # 0.05% commission (realistic)
        enable_shorting=False,       # Allow shorting
        close_eod=False,             # Close positions at EOD
        verbose=True,           # Detailed logging
        trailing_stop_smooth_period = 0
    )
    return system.run("SOL-USD", period="60d", interval="30m")


if __name__ == "__main__":
    # Run default example
    print("=== Running Default Strategy Example ===")
    results = example_default_strategy()
