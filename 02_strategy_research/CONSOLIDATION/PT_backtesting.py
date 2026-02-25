"""
CONSOLIDATION BREAKOUT BACKTESTER v2
- Quote-fee only version (cleaned and fixed)
- Fixes to capital accounting (deduct entry principal+entry fee on entry, add net exit on exit)
- Simplified fee model: fees are always charged in the quote currency
- Fixed equity curve indexing and process_single_file bugs
- Kept parallel processing and progress bars
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from concurrent.futures import ProcessPoolExecutor, as_completed
from threading import Thread
from tqdm import tqdm
import os
import glob
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')


# ------------------------------
# Consolidation Backtester Class
# ------------------------------
class ConsolidationBacktester:
    def __init__(self, 
                 fractal_period=3,
                 deviation_threshold=2.0,
                 third_point_multiplier=1.5,
                 min_points_for_line=3,
                 max_skipped_points=3,
                 parallel_angle_threshold=5.0,
                 lookback_bars=100,
                 atr_period=14,
                 breakout_atr_multiplier=0.5,
                 tp_atr_multiplier=2.0,
                 initial_capital=10000,
                 position_size_pct=0.1,
                 maker_fee=0.001,
                 taker_fee=0.001,
                 min_angle_down=-10.0):
        
        # parameters
        self.fractal_period = fractal_period
        self.deviation_threshold = deviation_threshold
        self.third_point_multiplier = third_point_multiplier
        self.min_points_for_line = min_points_for_line
        self.max_skipped_points = max_skipped_points
        self.parallel_angle_threshold = parallel_angle_threshold
        self.lookback_bars = lookback_bars
        self.atr_period = atr_period
        self.breakout_atr_multiplier = breakout_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.min_angle_down = min_angle_down
        
        # runtime state
        self.reset_state()

    def reset_state(self):
        self.current_capital = float(self.initial_capital)
        self.in_position = False
        self.entry_bar = None
        self.entry_price = None
        self.position_size = 0.0  # in base units
        self.stop_loss = None
        self.take_profit = None
        self.position_history = []
        # bookkeeping for open trade
        self.entry_cash = None   # quote currency spent including entry fee
        self.entry_fee = None
        self.entry_principal = None  # quote currency principal (units * price)

    # ------------------
    # Data Loading
    # ------------------
    def load_data(self, filepath):
        df = pd.read_csv(filepath)
        if 'open_time' in df.columns:
            try:
                df['datetime'] = pd.to_datetime(df['open_time'])
            except:
                try:
                    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')
                except:
                    raise ValueError("Could not parse datetime column 'open_time'")
        elif 'datetime' in df.columns:
            try:
                df['datetime'] = pd.to_datetime(df['datetime'])
            except:
                raise ValueError("Could not parse datetime column 'datetime'")
        else:
            raise ValueError("No datetime column found in CSV (expected 'open_time' or 'datetime')")

        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize('UTC')
        df = df.reset_index(drop=True)

        # ATR calculation (simple moving average of TR)
        df['tr'] = np.maximum(df['high'] - df['low'],
                              np.maximum(abs(df['high'] - df['close'].shift(1)),
                                         abs(df['low'] - df['close'].shift(1))))
        df['atr'] = df['tr'].rolling(window=self.atr_period).mean()
        return df

    # ------------------
    # Fractal Functions
    # ------------------
    def is_peak(self, df, index):
        if index < self.fractal_period or index + self.fractal_period > len(df) - 1:
            return False
        center_high = df.loc[index, 'high']
        for i in range(1, self.fractal_period + 1):
            if df.loc[index - i, 'high'] > center_high or df.loc[index + i, 'high'] >= center_high:
                return False
        return True
    
    def is_trough(self, df, index):
        if index < self.fractal_period or index + self.fractal_period > len(df) - 1:
            return False
        center_low = df.loc[index, 'low']
        for i in range(1, self.fractal_period + 1):
            if df.loc[index - i, 'low'] < center_low or df.loc[index + i, 'low'] <= center_low:
                return False
        return True
    
    def get_pivots_up_to_bar(self, df, bar_index, is_peak_type):
        pivots = []
        start = max(0, bar_index - self.lookback_bars)
        end = min(bar_index - self.fractal_period, len(df))
        for i in range(start, end):
            if is_peak_type and self.is_peak(df, i):
                pivots.append({'index': i, 'price': df.loc[i, 'high']})
            elif not is_peak_type and self.is_trough(df, i):
                pivots.append({'index': i, 'price': df.loc[i, 'low']})
        return pivots

    # ------------------
    # Line Drawing
    # ------------------
    def calculate_best_fit(self, x_values, y_values):
        n = len(x_values)
        if n < 2:
            return 0.0, 0.0
        x_arr = np.array(x_values, dtype=float)
        y_arr = np.array(y_values, dtype=float)
        avg_x, avg_y = np.mean(x_arr), np.mean(y_arr)
        denominator = np.sum(x_arr ** 2) - n * avg_x ** 2
        slope = (np.sum(x_arr * y_arr) - n * avg_x * avg_y) / denominator if denominator != 0 else 0.0
        intercept = avg_y - slope * avg_x
        return slope, intercept
    
    def get_deviation_percent(self, x, y, slope, intercept, ref_price):
        predicted = slope * x + intercept
        deviation = abs(y - predicted)
        return (deviation / ref_price) * 100 if ref_price != 0 else 0.0

    def build_most_recent_line(self, pivots):
        if len(pivots) < self.min_points_for_line:
            return None
        group_x = [pivots[0]['index'], pivots[1]['index']]
        group_y = [pivots[0]['price'], pivots[1]['price']]
        ref_price = np.mean(group_y)
        skips = 0
        for i in range(2, len(pivots)):
            if skips >= self.max_skipped_points:
                break
            test_x, test_y = pivots[i]['index'], pivots[i]['price']
            slope, intercept = self.calculate_best_fit(group_x, group_y)
            deviation = self.get_deviation_percent(test_x, test_y, slope, intercept, ref_price)
            threshold = self.deviation_threshold * self.third_point_multiplier if len(group_x) >= 2 else self.deviation_threshold
            # decide whether to accept this pivot
            if deviation > threshold:
                skips += 1
            else:
                group_x.append(test_x)
                group_y.append(test_y)
                skips = 0
        if len(group_x) >= self.min_points_for_line:
            slope, intercept = self.calculate_best_fit(group_x, group_y)
            return {'slope': slope, 'intercept': intercept, 'points': list(zip(group_x, group_y))}
        return None

    def slope_to_angle(self, slope, avg_price):
        # convert slope to a rough angle (percent-per-bar scaled)
        if avg_price == 0:
            return 0.0
        angle_rad = np.arctan((slope / avg_price) * 100)
        return np.degrees(angle_rad)

    # ------------------
    # Trade Logic (quote-fee only)
    # ------------------
    def check_trade_logic(self, df, current_bar, peak_line, trough_line):
        if peak_line is None or trough_line is None:
            return

        current_high = df.loc[current_bar, 'high']
        current_low = df.loc[current_bar, 'low']
        atr = df.loc[current_bar, 'atr']
        if pd.isna(atr):
            return

        trough_price = trough_line['slope'] * current_bar + trough_line['intercept']
        peak_price = peak_line['slope'] * current_bar + peak_line['intercept']
        avg_price = (peak_price + trough_price) / 2
        breakout_line = trough_price + (atr * self.breakout_atr_multiplier)

        # ENTRY (taker fee in quote currency)
        if not self.in_position:
            if current_high > breakout_line:
                # reject very steep downtrends
                if self.slope_to_angle(peak_line['slope'], avg_price) < self.min_angle_down and \
                   self.slope_to_angle(trough_line['slope'], avg_price) < self.min_angle_down:
                    return

                allocated_cash = min(self.current_capital * self.position_size_pct, self.current_capital)
                if allocated_cash <= 0:
                    return

                # Solve units accounting for quote fee: units = allocated_cash / (price * (1 + taker_fee))
                units = allocated_cash / (breakout_line * (1.0 + self.taker_fee))
                entry_principal = units * breakout_line
                entry_fee = entry_principal * self.taker_fee
                entry_cash = entry_principal + entry_fee

                # safety
                if units <= 0 or entry_cash > self.current_capital:
                    return

                # set position and stops/targets
                self.position_size = units
                self.entry_principal = entry_principal
                self.entry_fee = entry_fee
                self.entry_cash = entry_cash
                self.stop_loss = trough_price
                pattern_height = peak_price - trough_price
                self.take_profit = breakout_line + (pattern_height * self.tp_atr_multiplier)

                # profit filter (ensure potential gross > fees)
                potential_gross = (self.take_profit - breakout_line) * self.position_size
                exit_fee_est = (self.take_profit * self.position_size) * self.maker_fee
                if potential_gross <= (entry_fee + exit_fee_est):
                    # don't enter if fees would eat profits
                    self.entry_principal = None
                    self.entry_fee = None
                    self.entry_cash = None
                    self.position_size = 0.0
                    return

                # deduct entry cash (principal + entry fee) from capital
                self.current_capital -= entry_cash

                # bookkeeping
                self.in_position = True
                self.entry_bar = current_bar
                self.entry_price = breakout_line

        # EXIT
        else:
            exit_reason = None
            exit_price = None
            if current_low <= self.stop_loss:
                exit_reason, exit_price = "Stop Loss", self.stop_loss
            elif current_high >= self.take_profit:
                exit_reason, exit_price = "Take Profit", self.take_profit

            if exit_reason:
                gross_exit = self.position_size * exit_price
                fee_rate = self.maker_fee if exit_reason == "Take Profit" else self.taker_fee
                exit_fee = gross_exit * fee_rate
                net_exit = gross_exit - exit_fee

                # clamp
                net_exit = max(0.0, net_exit)
                exit_fee = min(exit_fee, gross_exit)

                # pnl relative to the cash spent at entry (entry_cash includes entry_fee)
                pnl = net_exit - (self.entry_cash if self.entry_cash is not None else 0.0)
                pnl_pct = (pnl / (self.entry_cash if (self.entry_cash is not None and self.entry_cash > 0) else 1.0)) * 100.0

                # update capital: add back net exit proceeds
                self.current_capital += net_exit

                # risk dollars
                risk_per_unit = max(self.entry_price - self.stop_loss, 1e-12)
                risk_dollars = risk_per_unit * self.position_size
                r_multiple = pnl / risk_dollars if risk_dollars != 0 else np.nan

                # record trade
                self.position_history.append({
                    'entry_bar': self.entry_bar,
                    'exit_bar': current_bar,
                    'entry_price': self.entry_price,
                    'exit_price': exit_price,
                    'position_size': self.position_size,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'risk': risk_dollars,
                    'r_multiple': r_multiple,
                    'exit_reason': exit_reason,
                    'entry_principal': self.entry_principal,
                    'entry_fee': self.entry_fee,
                    'exit_fee': exit_fee,
                    'capital_after': self.current_capital
                })

                # reset position state
                self.in_position = False
                self.entry_bar = None
                self.entry_price = None
                self.position_size = 0.0
                self.stop_loss = None
                self.take_profit = None
                self.entry_cash = None
                self.entry_fee = None
                self.entry_principal = None

    # ------------------
    # Backtest Runner
    # ------------------
    def run_backtest(self, df, progress_queue=None):
        self.reset_state()
        start_bar = max(self.fractal_period * 3, self.atr_period + 10, 50)
        total_bars = len(df) - start_bar
        for idx, current_bar in enumerate(range(start_bar, len(df))):
            peaks = self.get_pivots_up_to_bar(df, current_bar, True)
            troughs = self.get_pivots_up_to_bar(df, current_bar, False)
            peak_line = self.build_most_recent_line(peaks) if len(peaks) >= 2 else None
            trough_line = self.build_most_recent_line(troughs) if len(troughs) >= 2 else None
            self.check_trade_logic(df, current_bar, peak_line, trough_line)
            if progress_queue is not None:
                progress_queue.put((idx + 1, total_bars))
        return self.get_summary(df)

    # ------------------
    # Summary
    # ------------------
    def get_summary(self, df=None):
        if not self.position_history:
            return None
        df_positions = pd.DataFrame(self.position_history)
        summary = {
            'total_trades': int(len(df_positions)),
            'win_rate': float(np.mean(df_positions['pnl'] > 0)),
            'avg_pnl': float(np.mean(df_positions['pnl'])),
            'avg_r_multiple': float(np.nanmean(df_positions['r_multiple'])),
            'final_capital': float(self.current_capital),
        }

        if df is not None:
            equity_vals = [self.initial_capital]
            equity_idx = []
            if len(df) > 0:
                equity_idx.append(df.loc[0, 'datetime'])
            else:
                equity_idx.append(pd.Timestamp.now(tz='UTC'))

            for trade in self.position_history:
                equity_vals.append(trade['capital_after'])
                try:
                    equity_idx.append(df.loc[trade['exit_bar'], 'datetime'])
                except Exception:
                    equity_idx.append(df['datetime'].iloc[-1] if 'datetime' in df.columns else pd.Timestamp.now(tz='UTC'))

            summary['equity_curve'] = equity_vals
            summary['equity_index'] = equity_idx

        return summary

# ------------------------------
# Multiprocessing Utilities
# ------------------------------
def progress_listener(progress_queue, symbol):
    pbar = None
    while True:
        item = progress_queue.get()
        if item == "DONE":
            if pbar:
                pbar.close()
            break
        current, total = item
        if pbar is None:
            pbar = tqdm(total=total, desc=f"{symbol}", ncols=80, leave=False)
        pbar.n = current
        pbar.refresh()


def process_single_file(args):
    filepath, params = args
    manager = None
    try:
        from multiprocessing import Manager
        manager = Manager()
        progress_queue = manager.Queue()

        backtester = ConsolidationBacktester(**params)
        df = backtester.load_data(filepath)
        symbol = os.path.basename(filepath).replace('.csv','')
        listener_thread = Thread(target=progress_listener, args=(progress_queue, symbol))
        listener_thread.start()
        summary = backtester.run_backtest(df, progress_queue)
        progress_queue.put("DONE")
        listener_thread.join()
        if summary is None:
            return None
        summary['symbol'] = symbol
        summary['filepath'] = filepath
        # Create equity_curve_df from equity values and corresponding datetimes if available
        if 'equity_curve' in summary and 'equity_index' in summary:
            try:
                idx = pd.to_datetime(summary['equity_index'])
                summary['equity_curve_df'] = pd.Series(index=idx, data=summary['equity_curve'])
            except Exception:
                summary['equity_curve_df'] = pd.Series(summary['equity_curve'])
        else:
            summary['equity_curve_df'] = pd.Series([backtester.initial_capital])
        return summary

    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return None
    finally:
        try:
            if manager is not None:
                manager.shutdown()
        except Exception:
            pass


def run_parallel_backtest(data_folder, params, max_workers=None):
    # Find CSV files
    csv_files = glob.glob(os.path.join(data_folder, '*.csv'))
    if len(csv_files) == 0:
        print(f"No CSV files found in {data_folder}")
        return []

    if max_workers is None:
        max_workers = os.cpu_count()

    print(f"\n{'='*60}")
    print(f"CONSOLIDATION BREAKOUT BACKTEST")
    print(f"{'='*60}")
    print(f"Data Folder: {data_folder}")
    print(f"Symbols Found: {len(csv_files)}")
    print(f"CPU Cores: {max_workers}")
    print(f"{'='*60}\n")

    results = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_file, (fp, params)): fp for fp in csv_files}
        for f in tqdm(as_completed(futures), total=len(futures), desc="Processing symbols", ncols=80):
            try:
                res = f.result()
                if res is not None:
                    results.append(res)
            except Exception as e:
                print(f"Error processing {futures[f]}: {e}")

    return results



# ------------------------------
# Reporting Utilities
# ------------------------------
def save_operator_report(results, output_csv="operator_report.csv"):
    rows = []
    for res in results:
        summary_row = {
            'symbol': res['symbol'],
            'total_trades': res['total_trades'],
            'win_rate': res['win_rate'],
            'avg_pnl': res['avg_pnl'],
            'avg_r_multiple': res['avg_r_multiple'],
            'final_capital': res['final_capital']
        }
        rows.append(summary_row)
    df_report = pd.DataFrame(rows)
    df_report.to_csv(output_csv, index=False)
    print(f"Operator report saved to {output_csv}")


def plot_equity_curves(results, folder="equity_curves"):
    os.makedirs(folder, exist_ok=True)
    for res in results:
        df_eq = res['equity_curve_df']
        plt.figure(figsize=(12,6))
        plt.plot(df_eq.index, df_eq.values, label=res['symbol'])
        plt.xlabel("Date")
        plt.ylabel("Equity")
        plt.title(f"Equity Curve for {res['symbol']}")
        plt.legend()
        plt.tight_layout()
        plt.savefig(os.path.join(folder, f"{res['symbol']}_equity.png"))
        plt.close()
    print(f"Equity curves saved to {folder}/")


# ------------------------------
# Example Run
# ------------------------------
if __name__ == "__main__":
    DATA_FOLDER = "/Users/malcolm/CONSOLIDATION/input_data"
    params = {
        "fractal_period": 3,
        "deviation_threshold": 2.0,
        "third_point_multiplier": 1.5,
        "min_points_for_line": 3,
        "max_skipped_points": 3,
        "parallel_angle_threshold": 5.0,
        "lookback_bars": 100,
        "atr_period": 14,
        "breakout_atr_multiplier": 0.5,
        "tp_atr_multiplier": 2.0,
        "initial_capital": 10000,
        "position_size_pct": 1.0,
        "maker_fee": 0.0025,
        "taker_fee": 0.0025,
        "min_angle_down": -20.0
    }

    results = run_parallel_backtest(DATA_FOLDER, params)
    save_operator_report(results)
    plot_equity_curves(results)
