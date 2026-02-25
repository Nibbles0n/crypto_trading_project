import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from typing import List, Tuple, Dict
import warnings
warnings.filterwarnings('ignore')

class ConsolidationBreakoutBacktester:
    """
    A realistic backtester for the Consolidation Breakout Strategy.
    
    This backtester properly handles:
    - Realistic position sizing with fees and optional slippage
    - No look-ahead bias (only uses data available at decision time)
    - Proper fee calculation (entry and exit)
    - Comprehensive performance metrics including Sharpe ratio
    - Trade-by-trade accounting
    - Pessimistic exit assumptions (checks stop loss before take profit)
    - Time-based annualization for metrics (handles any bar frequency, e.g., 5-min)
    """
    
    def __init__(self, 
                 fractal_period: int = 3,
                 deviation_threshold: float = 2.0,
                 third_point_multiplier: float = 1.5,
                 min_points_for_line: int = 3,
                 max_skipped_points: int = 3,
                 lookback_bars: int = 100,
                 atr_period: int = 14,
                 breakout_atr_multiplier: float = 0.5,
                 tp_atr_multiplier: float = 2.0,
                 initial_capital: float = 10000,
                 position_size_pct: float = 1.0,
                 maker_fee: float = 0.004,
                 taker_fee: float = 0.0025,
                 slippage_pct: float = 0.0):  # Added slippage for realism
        """
        Initialize the backtester with strategy parameters.
        
        Parameters match the strategy specification exactly.
        """
        self.fractal_period = fractal_period
        self.deviation_threshold = deviation_threshold
        self.third_point_multiplier = third_point_multiplier
        self.min_points_for_line = min_points_for_line
        self.max_skipped_points = max_skipped_points
        self.lookback_bars = lookback_bars
        self.atr_period = atr_period
        self.breakout_atr_multiplier = breakout_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_pct = slippage_pct
        
        # Warning for high risk
        if position_size_pct >= 1.0:
            warnings.warn("Position size >=100% is high risk; one loss can wipe capital.")
        
        # Tracking variables
        self.trades = []
        self.equity_curve = []
        
    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Calculate Average True Range (ATR) for volatility measurement."""
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=self.atr_period).mean()
        
        return atr
    
    def detect_fractals(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """
        Detect fractal peaks (resistance) and troughs (support).
        
        A peak occurs when the current bar's high is higher than 
        fractal_period bars on either side.
        """
        peaks = pd.Series(index=df.index, dtype=bool).fillna(False)
        troughs = pd.Series(index=df.index, dtype=bool).fillna(False)
        
        for i in range(self.fractal_period, len(df) - self.fractal_period):
            # Check for peak
            is_peak = all(df['high'].iloc[i] > df['high'].iloc[i-j] for j in range(1, self.fractal_period + 1)) and \
                      all(df['high'].iloc[i] > df['high'].iloc[i+j] for j in range(1, self.fractal_period + 1))
            peaks.iloc[i] = is_peak
            
            # Check for trough
            is_trough = all(df['low'].iloc[i] < df['low'].iloc[i-j] for j in range(1, self.fractal_period + 1)) and \
                        all(df['low'].iloc[i] < df['low'].iloc[i+j] for j in range(1, self.fractal_period + 1))
            troughs.iloc[i] = is_trough
        
        return peaks, troughs
    
    def fit_trend_line(self, points: List[Tuple[int, float]]) -> Tuple[float, float]:
        """
        Fit a linear trend line through pivot points using least squares.
        
        Returns: (slope, intercept)
        """
        if len(points) < 2:
            return None, None
        
        x = np.array([p[0] for p in points])
        y = np.array([p[1] for p in points])
        
        # Linear regression
        A = np.vstack([x, np.ones(len(x))]).T
        slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
        
        return slope, intercept
    
    def calculate_deviation(self, point: Tuple[int, float], 
                          slope: float, intercept: float) -> float:
        """Calculate percentage deviation of a point from the trend line."""
        x, y = point
        predicted_y = slope * x + intercept
        if predicted_y == 0:
            return float('inf')
        return abs((y - predicted_y) / predicted_y) * 100
    
    def build_trend_line(self, pivots: List[Tuple[int, float]], 
                        max_lookback: int) -> Tuple[float, float, List[Tuple[int, float]]]:
        """
        Build a trend line from pivot points with deviation threshold and recovery after skips.
        
        Starts with most recent 2 pivots, adds backward, skips outliers but continues if later points fit.
        Returns: (slope, intercept, valid_points)
        """
        if len(pivots) < self.min_points_for_line:
            return None, None, []
        
        # Sort pivots by index descending (most recent first)
        sorted_pivots = sorted(pivots, key=lambda p: p[0], reverse=True)
        
        # Start with two most recent
        if len(sorted_pivots) < 2:
            return None, None, []
        valid_points = [sorted_pivots[0], sorted_pivots[1]]
        skipped = 0
        
        # Add older points backward
        for i in range(2, len(sorted_pivots)):
            point = sorted_pivots[i]
            slope, intercept = self.fit_trend_line(valid_points)
            if slope is None:
                continue
            
            deviation = self.calculate_deviation(point, slope, intercept)
            threshold = self.deviation_threshold
            if len(valid_points) > self.min_points_for_line:
                threshold *= self.third_point_multiplier
            
            if deviation <= threshold:
                valid_points.append(point)
                skipped = 0  # Reset skip on good fit (recovery)
                # Refit with new point
                slope, intercept = self.fit_trend_line(valid_points)
            else:
                skipped += 1
                if skipped > self.max_skipped_points:
                    break  # Stop if too many consecutive skips, but prior recovery allowed
        
        if len(valid_points) >= self.min_points_for_line:
            slope, intercept = self.fit_trend_line(valid_points)
            return slope, intercept, valid_points
        
        return None, None, []
    
    def get_line_value(self, slope: float, intercept: float, x: int) -> float:
        """Get the y-value of a line at position x."""
        if slope is None or intercept is None:
            return None
        return slope * x + intercept
    
    def run_backtest(self, df: pd.DataFrame, symbol: str = "UNKNOWN") -> Dict:
        """
        Run the backtest on a single dataframe.
        
        This is the main backtesting loop. It processes data bar-by-bar
        to avoid look-ahead bias.
        """
        # Reset tracking variables
        self.trades = []
        self.equity_curve = [self.initial_capital]
        current_capital = self.initial_capital
        
        # Calculate ATR
        df['atr'] = self.calculate_atr(df)
        
        # Detect fractals
        df['is_peak'], df['is_trough'] = self.detect_fractals(df)
        
        # Store pivot points
        peak_pivots = []
        trough_pivots = []
        
        # Current position
        in_position = False
        entry_price = 0
        position_size = 0
        stop_loss = 0
        take_profit = 0
        entry_time = None
        entry_capital = 0
        
        # Main loop - process bar by bar
        for i in range(max(self.lookback_bars, self.atr_period + 1), len(df)):
            current_bar = df.iloc[i]
            current_time = current_bar['open_time']
            current_open = current_bar['open']
            current_high = current_bar['high']
            current_low = current_bar['low']
            current_close = current_bar['close']
            current_atr = current_bar['atr']
            
            # Update pivot lists (only using past data, with lag for fractal confirmation)
            if i >= 2 * self.fractal_period and df['is_peak'].iloc[i - self.fractal_period]:
                peak_pivots.append((i - self.fractal_period, df['high'].iloc[i - self.fractal_period]))
            if i >= 2 * self.fractal_period and df['is_trough'].iloc[i - self.fractal_period]:
                trough_pivots.append((i - self.fractal_period, df['low'].iloc[i - self.fractal_period]))
            
            # Check if we need to exit an existing position - pessimistic: check SL first
            if in_position:
                exit_price = None
                exit_reason = None
                fee_rate = self.taker_fee  # Assume taker for SL, maker for TP
                
                # Pessimistic for longs: Check SL first (take loss if possible)
                if current_low <= stop_loss:
                    exit_price = max(stop_loss, current_open) * (1 - self.slippage_pct)  # Pessimistic fill
                    exit_reason = "Stop Loss"
                    fee_rate = self.taker_fee
                
                # Then TP
                elif current_high >= take_profit:
                    exit_price = min(take_profit, current_high) * (1 - self.slippage_pct)  # Pessimistic: not best price
                    exit_reason = "Take Profit"
                    fee_rate = self.maker_fee
                
                if exit_price:
                    # Calculate PnL
                    gross_exit_value = position_size * exit_price
                    exit_fee = gross_exit_value * fee_rate
                    net_exit_value = gross_exit_value - exit_fee
                    
                    pnl = net_exit_value - entry_capital
                    pnl_pct = (pnl / entry_capital) * 100 if entry_capital > 0 else 0
                    
                    # Update capital
                    current_capital += pnl
                    
                    # Record trade
                    self.trades.append({
                        'symbol': symbol,
                        'entry_time': entry_time,
                        'exit_time': current_time,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'position_size': position_size,
                        'entry_capital': entry_capital,
                        'exit_value': net_exit_value,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'exit_reason': exit_reason,
                        'capital_after': current_capital
                    })
                    
                    # Reset position
                    in_position = False
            
            # Record equity (fixed: cash + mtm)
            if in_position:
                current_position_value = position_size * current_close
                current_equity = current_capital + (current_position_value - (position_size * entry_price))
                self.equity_curve.append(current_equity)
            else:
                self.equity_curve.append(current_capital)
            
            # Look for entry if not in position
            if not in_position and not pd.isna(current_atr) and current_atr > 0:
                # Build trend lines from recent pivots
                lookback_start = max(0, i - self.lookback_bars)
                
                trough_slope, trough_intercept, trough_points = \
                    self.build_trend_line(trough_pivots, lookback_start)
                peak_slope, peak_intercept, peak_points = \
                    self.build_trend_line(peak_pivots, lookback_start)
                
                # Check if we have valid trend lines
                if (trough_slope is not None and peak_slope is not None and
                    len(trough_points) >= self.min_points_for_line and
                    len(peak_points) >= self.min_points_for_line):
                    
                    # Calculate breakout line
                    trough_line_value = self.get_line_value(trough_slope, trough_intercept, i)
                    peak_line_value = self.get_line_value(peak_slope, peak_intercept, i)
                    
                    if trough_line_value is not None and peak_line_value is not None:
                        breakout_price = trough_line_value + (current_atr * self.breakout_atr_multiplier)
                        
                        # Check if price breaks above breakout line
                        if current_high > breakout_price:
                            # Calculate stop loss and take profit
                            stop_loss = trough_line_value
                            pattern_height = peak_line_value - trough_line_value
                            take_profit = breakout_price + (pattern_height * self.tp_atr_multiplier)
                            
                            # Pessimistic entry price: assume fill at worse price (higher for buy)
                            entry_price = max(breakout_price, current_open) * (1 + self.slippage_pct)
                            
                            # Calculate position size based on available capital
                            risk_capital = current_capital * self.position_size_pct
                            position_size = risk_capital / entry_price
                            
                            # Account for entry fee
                            entry_fee = (position_size * entry_price) * self.taker_fee
                            entry_capital = position_size * entry_price + entry_fee
                            
                            # Check if we can afford
                            if entry_capital <= current_capital:
                                # Profit filter: expected profit > total fees (pessimistic)
                                expected_gross_profit = (take_profit - entry_price) * position_size
                                total_fees = entry_fee + (position_size * take_profit * self.maker_fee)
                                if expected_gross_profit > total_fees * 1.0:  # 10% buffer for pessimism
                                    # Enter position
                                    in_position = True
                                    entry_time = current_time
                                    current_capital -= entry_capital
        
        # Close any remaining position at the end (pessimistic: at close with slippage)
        if in_position:
            exit_price = df.iloc[-1]['close'] * (1 - self.slippage_pct)
            gross_exit_value = position_size * exit_price
            exit_fee = gross_exit_value * self.taker_fee
            net_exit_value = gross_exit_value - exit_fee
            
            pnl = net_exit_value - entry_capital
            pnl_pct = (pnl / entry_capital) * 100 if entry_capital > 0 else 0
            current_capital += pnl
            
            self.trades.append({
                'symbol': symbol,
                'entry_time': entry_time,
                'exit_time': df.iloc[-1]['open_time'],
                'entry_price': entry_price,
                'exit_price': exit_price,
                'position_size': position_size,
                'entry_capital': entry_capital,
                'exit_value': net_exit_value,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'exit_reason': "End of Data",
                'capital_after': current_capital
            })
            
            self.equity_curve.append(current_capital)
        
        return {
            'trades': self.trades,
            'equity_curve': self.equity_curve,
            'final_capital': current_capital,
            'df': df  # For time calculation in metrics
        }
    
    def load_and_backtest_folder(self, folder_path: str, output_dir: str = "."):
        """
        Load all CSV files from a folder and run backtest on each separately.
        
        Generates separate reports and CSV for each symbol, no combination.
        """
        csv_files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
        
        print(f"Found {len(csv_files)} CSV files to process...")
        
        for csv_file in csv_files:
            symbol = csv_file.replace('.csv', '')
            print(f"Processing {symbol}...")
            
            file_path = os.path.join(folder_path, csv_file)
            df = pd.read_csv(file_path)
            
            # Convert time columns
            df['open_time'] = pd.to_datetime(df['open_time'])
            df['close_time'] = pd.to_datetime(df['close_time'])
            
            # Sort by time
            df = df.sort_values('open_time').reset_index(drop=True)
            
            # Run backtest
            results = self.run_backtest(df, symbol)
            
            # Generate report for this symbol
            output_file = os.path.join(output_dir, f"{symbol}_backtest_trades.csv")
            report, metrics = self.generate_report(results, output_file)
            
            print(f"\nReport for {symbol}:")
            print(report)
        
    def calculate_metrics(self, trades: List[Dict], equity_curve: List[float], df: pd.DataFrame) -> Dict:
        """
        Calculate comprehensive performance metrics.
        
        Uses time-based annualization for any bar frequency (e.g., 5-min data).
        """
        if not trades:
            return {
                'error': 'No trades executed',
                'total_trades': 0
            }
        
        trades_df = pd.DataFrame(trades)
        
        # Basic metrics with precision threshold to catch small wins/losses
        total_trades = len(trades_df)
        winning_trades = len(trades_df[trades_df['pnl'] > 1e-6])  # Fix: Avoid rounding tiny PnLs
        losing_trades = len(trades_df[trades_df['pnl'] < -1e-6])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        # PnL metrics
        total_pnl = trades_df['pnl'].sum()
        avg_pnl = trades_df['pnl'].mean() if total_trades > 0 else 0
        avg_win = trades_df[trades_df['pnl'] > 1e-6]['pnl'].mean() if winning_trades > 0 else 0
        avg_loss = trades_df[trades_df['pnl'] < -1e-6]['pnl'].mean() if losing_trades > 0 else 0
        largest_win = trades_df[trades_df['pnl'] > 1e-6]['pnl'].max() if winning_trades > 0 else 0
        largest_loss = trades_df[trades_df['pnl'] < -1e-6]['pnl'].min() if losing_trades > 0 else 0
        
        # Returns calculation
        equity_series = pd.Series(equity_curve)
        returns = equity_series.pct_change().dropna()
        
        # Time-based annualization
        if len(df) > 1 and 'open_time' in df.columns:
            total_time = (df['open_time'].iloc[-1] - df['open_time'].iloc[0]).total_seconds() / (365.25 * 24 * 3600)  # years
            bars_per_year = len(equity_curve) / total_time if total_time > 0 else 0
        else:
            total_time = len(equity_curve) / (365.25 * 288)  # Assume 5-min: 288 bars/day
            bars_per_year = 365.25 * 288  # Approx for 5-min, 24/7
            warnings.warn("No timestamps; assuming 5-min bars for annualization. This may be inaccurate.")
        
        # Cumulative and annualized returns
        total_return = ((equity_curve[-1] - self.initial_capital) / self.initial_capital * 100) if self.initial_capital > 0 else 0
        annualized_return = (((equity_curve[-1] / self.initial_capital) ** (1 / total_time)) - 1) * 100 if total_time > 0 and self.initial_capital > 0 else 0
        
        # Volatility
        if len(returns) > 1:
            per_bar_vol = returns.std()
            annualized_volatility = per_bar_vol * np.sqrt(bars_per_year) * 100 if bars_per_year > 0 else 0
        else:
            per_bar_vol = 0
            annualized_volatility = 0
        
        # Sharpe Ratio (risk-free rate = 0 for simplicity)
        if per_bar_vol > 0:
            sharpe_ratio = (returns.mean() / per_bar_vol) * np.sqrt(bars_per_year)
        else:
            sharpe_ratio = 0
        
        # Sortino Ratio
        negative_returns = returns[returns < 0]
        if len(negative_returns) > 1 and negative_returns.std() > 0:
            downside_dev = negative_returns.std()
            sortino_ratio = (returns.mean() / downside_dev) * np.sqrt(bars_per_year)
        else:
            sortino_ratio = 0
        
        # Maximum Drawdown
        if len(equity_series) > 1:
            peak = equity_series.expanding(min_periods=1).max()
            drawdown = (equity_series / peak - 1) * 100
            max_drawdown = drawdown.min()
        else:
            max_drawdown = 0
        
        # Profit Factor
        gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
        gross_loss = abs(trades_df[trades_df['pnl'] < 0]['pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Average holding period
        trades_df['holding_period'] = (pd.to_datetime(trades_df['exit_time']) - pd.to_datetime(trades_df['entry_time'])).dt.total_seconds() / 3600  # hours
        avg_holding_period_hours = trades_df['holding_period'].mean() if not trades_df.empty else 0
        
        # Exit reason breakdown
        exit_reasons = trades_df['exit_reason'].value_counts().to_dict()
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': round(win_rate, 2),
            'total_pnl': round(total_pnl, 2),
            'average_pnl_per_trade': round(avg_pnl, 2),
            'average_win': round(avg_win, 2),
            'average_loss': round(avg_loss, 2),
            'largest_win': round(largest_win, 2),
            'largest_loss': round(largest_loss, 2),
            'profit_factor': round(profit_factor, 2) if np.isfinite(profit_factor) else 'inf',
            'initial_capital': self.initial_capital,
            'final_capital': round(equity_curve[-1], 2),
            'total_return_pct': round(total_return, 2),
            'annualized_return_pct': round(annualized_return, 2),
            'annualized_volatility_pct': round(annualized_volatility, 2),
            'sharpe_ratio': round(sharpe_ratio, 3),
            'sortino_ratio': round(sortino_ratio, 3),
            'max_drawdown_pct': round(max_drawdown, 2),
            'avg_holding_period_hours': round(avg_holding_period_hours, 2),
            'exit_reasons': exit_reasons
        }
    
    def generate_report(self, results: Dict, output_file: str = None):
        """
        Generate a comprehensive text report and optionally save trades to CSV.
        """
        metrics = self.calculate_metrics(results['trades'], results['equity_curve'], results['df'])
        
        report = f"""
{'='*80}
CONSOLIDATION BREAKOUT STRATEGY - BACKTEST REPORT
{'='*80}

STRATEGY PARAMETERS:
{'-'*80}
Fractal Period: {self.fractal_period}
Deviation Threshold: {self.deviation_threshold}%
Min Points for Line: {self.min_points_for_line}
Lookback Bars: {self.lookback_bars}
ATR Period: {self.atr_period}
Breakout ATR Multiplier: {self.breakout_atr_multiplier}
Take Profit Multiplier: {self.tp_atr_multiplier}
Position Size: {self.position_size_pct * 100}%
Maker Fee: {self.maker_fee * 100}%
Taker Fee: {self.taker_fee * 100}%
Slippage: {self.slippage_pct * 100}%

PERFORMANCE SUMMARY:
{'-'*80}
Initial Capital: ${metrics.get('initial_capital', 0):,.2f}
Final Capital: ${metrics.get('final_capital', 0):,.2f}
Total Return: {metrics.get('total_return_pct', 0):,.2f}%
Annualized Return: {metrics.get('annualized_return_pct', 0):,.2f}%

TRADE STATISTICS:
{'-'*80}
Total Trades: {metrics.get('total_trades', 0)}
Winning Trades: {metrics.get('winning_trades', 0)}
Losing Trades: {metrics.get('losing_trades', 0)}
Win Rate: {metrics.get('win_rate', 0):.2f}%

PNL ANALYSIS:
{'-'*80}
Total PnL: ${metrics.get('total_pnl', 0):,.2f}
Average PnL per Trade: ${metrics.get('average_pnl_per_trade', 0):,.2f}
Average Win: ${metrics.get('average_win', 0):,.2f}
Average Loss: ${metrics.get('average_loss', 0):,.2f}
Largest Win: ${metrics.get('largest_win', 0):,.2f}
Largest Loss: ${metrics.get('largest_loss', 0):,.2f}
Profit Factor: {metrics.get('profit_factor', 0)}

RISK METRICS:
{'-'*80}
Annualized Volatility: {metrics.get('annualized_volatility_pct', 0):.2f}%
Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.3f}
Sortino Ratio: {metrics.get('sortino_ratio', 0):.3f}
Maximum Drawdown: {metrics.get('max_drawdown_pct', 0):.2f}%

OTHER METRICS:
{'-'*80}
Average Holding Period: {metrics.get('avg_holding_period_hours', 0):.2f} hours

EXIT REASON BREAKDOWN:
{'-'*80}
"""
        
        for reason, count in metrics.get('exit_reasons', {}).items():
            report += f"{reason}: {count} trades\n"
        
        report += f"\n{'='*80}\n"
        
        # Save trades to CSV if specified
        if output_file and results['trades']:
            trades_df = pd.DataFrame(results['trades'])
            trades_df.to_csv(output_file, index=False)
            print(f"Trades for this symbol saved to: {output_file}")
        
        return report, metrics


# Example usage
if __name__ == "__main__":
    """
    Example of how to use the backtester.
    
    To run:
    1. Place your CSV files in a folder (e.g., 'input_data/')
    2. Update the folder_path below
    3. Run the script
    """
    
    # Initialize backtester with strategy parameters
    backtester = ConsolidationBreakoutBacktester(
        fractal_period=3,
        deviation_threshold=2.0,
        third_point_multiplier=1.5,
        min_points_for_line=3,
        max_skipped_points=3,
        lookback_bars=100,
        atr_period=14,
        breakout_atr_multiplier=0.5,
        tp_atr_multiplier=2.0,
        initial_capital=10000,
        position_size_pct=0.95,  # High risk - be cautious
        maker_fee=0.0025,       # 0.25%
        taker_fee=0.0025,      # 0.25%
        slippage_pct=0.0001     # 0.01% slippage for pessimism; adjust as needed
    )
    
    folder_path = "input_data/"  # UPDATE THIS PATH
    
    print("Starting backtest...")
    print(f"Looking for CSV files in: {folder_path}")
    
    try:
        # Run backtest on all files in folder, separate reports
        backtester.load_and_backtest_folder(folder_path)
        
        print("\nBacktest complete!")
        
        # Optional: Create equity curve visualization per symbol (manual)
        # Note: Since separate, you'd need to run per-symbol and plot
    except FileNotFoundError:
        print(f"Error: Folder '{folder_path}' not found.")
        print("Please create the folder and add your CSV files, or update the folder_path variable.")
    except Exception as e:
        print(f"Error during backtest: {str(e)}")
        import traceback
        traceback.print_exc()