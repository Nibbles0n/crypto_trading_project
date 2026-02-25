import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button
from datetime import datetime
import os

class RealisticConsolidationSimulator:
    def __init__(self,
                 fractal_period=3,
                 deviation_threshold=2.0,
                 third_point_multiplier=1.5,
                 min_points_for_line=3,
                 max_skipped_points=3,
                 parallel_angle_threshold=5.0,
                 min_pattern_width=10,
                 lookback_bars=100,
                 forward_buffer=50,
                 atr_period=14,
                 breakout_atr_multiplier=0.5,
                 tp_atr_multiplier=2.0,
                 initial_capital=10000.0,
                 risk_per_trade_pct=0.01,         # risk fraction of equity per trade
                 position_size_pct=None,          # if set, override risk-based sizing and use % of capital
                 maker_fee=0.0005,                # realistic maker fee
                 taker_fee=0.0007,                # realistic taker fee
                 spread_pct=0.0002,               # typical spread as percent of price
                 slippage_atr_multiplier=0.2,     # slippage stdev as fraction of ATR
                 order_latency_bars=1,            # fills occur at next bar open by default
                 cooldown_bars=5,
                 min_ticks=0.0):                  # minimum tick size / price step
        # Pattern detection params (unchanged)
        self.fractal_period = fractal_period
        self.deviation_threshold = deviation_threshold
        self.third_point_multiplier = third_point_multiplier
        self.min_points_for_line = min_points_for_line
        self.max_skipped_points = max_skipped_points
        self.parallel_angle_threshold = parallel_angle_threshold
        self.min_pattern_width = min_pattern_width
        self.lookback_bars = lookback_bars
        self.forward_buffer = forward_buffer

        # ATR / breakout / TP
        self.atr_period = atr_period
        self.breakout_atr_multiplier = breakout_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

        # Execution realism
        self.initial_capital = float(initial_capital)
        self.current_equity = float(initial_capital)
        self.risk_per_trade_pct = risk_per_trade_pct
        self.position_size_pct = position_size_pct
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.spread_pct = spread_pct
        self.slippage_atr_multiplier = slippage_atr_multiplier
        self.order_latency_bars = order_latency_bars
        self.cooldown_bars = cooldown_bars
        self.min_ticks = min_ticks

        # Internal state
        self.df = None
        self.current_bar = 0
        self.fig = None
        self.ax = None

        # Orders and positions
        self.pending_order = None   # dict with order info waiting fill at future bar
        self.position = None        # dict with live position
        self.position_history = []  # closed trades
        self.last_exit_bar = -cooldown_bars
        self.processed_entries = set()

    # ----------------------
    # Data loading & indicators
    # ----------------------
    def load_data(self, filepath, start_date=None, end_date=None):
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)
        df = pd.read_csv(filepath)
        if 'open_time' not in df.columns:
            raise ValueError("CSV must have 'open_time' column")
        # parse datetimes robustly
        try:
            df['datetime'] = pd.to_datetime(df['open_time'])
        except:
            try:
                df['datetime'] = pd.to_datetime(df['open_time'], unit='ms')
            except Exception as e:
                raise ValueError("Could not parse 'open_time' column as datetime") from e

        if df['datetime'].dt.tz is None:
            df['datetime'] = df['datetime'].dt.tz_localize('UTC')

        if start_date:
            start_dt = pd.to_datetime(start_date).tz_localize('UTC')
            df = df[df['datetime'] >= start_dt]
        if end_date:
            end_dt = pd.to_datetime(end_date).tz_localize('UTC')
            df = df[df['datetime'] <= end_dt]

        # numeric columns ensure
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)

        # ATR
        df['tr'] = np.maximum(df['high'] - df['low'],
                              np.maximum(abs(df['high'] - df['close'].shift(1)),
                                         abs(df['low'] - df['close'].shift(1))))
        df['atr'] = df['tr'].rolling(window=self.atr_period, min_periods=1).mean()
        self.df = df
        self.current_bar = max(self.fractal_period * 3, self.atr_period + 10, 50)
        print(f"Loaded {len(df)} rows. Date range: {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}")
        return self.df

    # ----------------------
    # Fractal peak/trough detection
    # ----------------------
    def is_peak(self, index, as_of_bar):
        p = self.fractal_period
        if index < p or index + p > as_of_bar:
            return False
        center_high = self.df.loc[index, 'high']
        for i in range(1, p + 1):
            if self.df.loc[index - i, 'high'] > center_high or self.df.loc[index + i, 'high'] >= center_high:
                return False
        return True

    def is_trough(self, index, as_of_bar):
        p = self.fractal_period
        if index < p or index + p > as_of_bar:
            return False
        center_low = self.df.loc[index, 'low']
        for i in range(1, p + 1):
            if self.df.loc[index - i, 'low'] < center_low or self.df.loc[index + i, 'low'] <= center_low:
                return False
        return True

    def get_peaks_up_to_bar(self, bar_index):
        peaks = []
        start = max(0, bar_index - self.lookback_bars)
        end = min(bar_index - self.fractal_period + 1, len(self.df))
        for i in range(start, end):
            if self.is_peak(i, bar_index):
                peaks.append({'index': i, 'price': self.df.loc[i, 'high']})
        return peaks

    def get_troughs_up_to_bar(self, bar_index):
        troughs = []
        start = max(0, bar_index - self.lookback_bars)
        end = min(bar_index - self.fractal_period + 1, len(self.df))
        for i in range(start, end):
            if self.is_trough(i, bar_index):
                troughs.append({'index': i, 'price': self.df.loc[i, 'low']})
        return troughs

    # ----------------------
    # Line fitting utilities
    # ----------------------
    def calculate_best_fit(self, x_values, y_values):
        n = len(x_values)
        if n < 2:
            return 0.0, 0.0
        x = np.array(x_values)
        y = np.array(y_values)
        A = np.vstack([x, np.ones_like(x)]).T
        slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
        return float(slope), float(intercept)

    def get_deviation_percent(self, x, y, slope, intercept, ref_price):
        predicted = slope * x + intercept
        deviation = abs(y - predicted)
        return (deviation / ref_price) * 100 if ref_price != 0 else np.inf

    def build_most_recent_line(self, pivots):
        # Require pivots sorted by index. Use the most recent pivots first so the line reflects
        # the latest structure (helps visual alignment and breakout projection).
        if len(pivots) < self.min_points_for_line:
            return None
        pivots = sorted(pivots, key=lambda p: p['index'])
        # start from the most recent two pivots
        gx = [pivots[-2]['index'], pivots[-1]['index']]
        gy = [pivots[-2]['price'], pivots[-1]['price']]
        ref_price = (gy[0] + gy[1]) / 2.0
        skips = 0
        # attach earlier pivots going backwards (so line is anchored to recent structure)
        for i in range(len(pivots)-3, -1, -1):
            if skips >= self.max_skipped_points:
                break
            tx = pivots[i]['index']
            ty = pivots[i]['price']
            slope, intercept = self.calculate_best_fit(gx, gy)
            deviation = self.get_deviation_percent(tx, ty, slope, intercept, ref_price)
            threshold = self.deviation_threshold * (self.third_point_multiplier if len(gx) >= 2 else 1.0)
            if deviation <= threshold:
                gx.append(tx); gy.append(ty)
                skips = 0
            else:
                skips += 1
        if len(gx) < self.min_points_for_line:
            return None
        s, b = self.calculate_best_fit(gx, gy)
        # store points sorted (old->new) for plotting consistency
        points = sorted(list(zip(gx, gy)), key=lambda t: t[0])
        return {'slope': float(s), 'intercept': float(b), 'points': points}


    # ----------------------
    # Angle helpers
    # ----------------------
    def slope_to_angle(self, slope, avg_price):
        # convert slope (price per bar) into degrees considering price scale
        angle_rad = np.arctan((slope / avg_price) * 100) if avg_price != 0 else 0.0
        return np.degrees(angle_rad)

    def are_parallel(self, slope1, slope2, avg_price):
        return abs(self.slope_to_angle(slope1, avg_price) - self.slope_to_angle(slope2, avg_price)) < self.parallel_angle_threshold

    def are_converging(self, peak_slope, trough_slope):
        return peak_slope < trough_slope

    # ----------------------
    # Execution & simulation helpers
    # ----------------------
    def price_with_spread(self, price, side):
        # side = 'buy' or 'sell'. buy pays ask (price*(1+spread/2)), sell receives bid (price*(1-spread/2))
        if side == 'buy':
            return price * (1.0 + self.spread_pct / 2.0)
        else:
            return price * (1.0 - self.spread_pct / 2.0)

    def apply_slippage(self, price, atr):
        # Slippage drawn from normal with scale proportional to ATR
        if atr <= 0 or np.isnan(atr):
            return price
        slippage = np.random.normal(0, self.slippage_atr_multiplier * atr)
        return max(price + slippage, self.min_ticks + 0.0)

    def round_tick(self, price):
        if self.min_ticks <= 0:
            return price
        return np.round(price / self.min_ticks) * self.min_ticks

    def compute_position_size(self, entry_price, stop_loss_price):
        # If user set absolute position_size_pct, use that fraction of equity.
        if self.position_size_pct is not None:
            notional = self.current_equity * self.position_size_pct
            qty = notional / entry_price
            return qty
        # Otherwise size by risk_per_trade_pct using dollar risk to stop
        risk_dollars = self.current_equity * self.risk_per_trade_pct
        per_unit_risk = abs(entry_price - stop_loss_price)
        if per_unit_risk <= 0:
            return 0.0
        qty = risk_dollars / per_unit_risk
        return qty

    # ----------------------
    # Order lifecycle
    # ----------------------
    def place_long_order(self, trigger_price, stop_price, tp_price, reason='breakout'):
        if self.pending_order or (self.position and self.position.get('open')):
            return False
        fill_bar = self.current_bar + max(1, self.order_latency_bars)
        self.pending_order = {
            'side': 'long',
            'trigger_price': float(trigger_price),
            'stop_price': float(stop_price),
            'tp_price': float(tp_price),
            'placed_bar': self.current_bar,
            'fill_bar': fill_bar,
            'reason': reason
        }
        # avoid placing multiple orders for the same triggering bar
        self.processed_entries.add(self.current_bar)
        return True


    def try_fill_pending(self):
        if not self.pending_order:
            return
        if self.current_bar < self.pending_order['fill_bar']:
            return
        # simulate fill at this bar's open with spread and slippage
        row = self.df.loc[self.current_bar]
        open_price = float(row['open'])
        atr = float(row['atr']) if not pd.isna(row['atr']) else 0.0
        raw_fill = max(open_price, self.pending_order['trigger_price'])  # conservative: price won't be better than open
        price_after_spread = self.price_with_spread(raw_fill, 'buy')
        fill_price = self.apply_slippage(price_after_spread, atr)
        fill_price = self.round_tick(fill_price)

        stop_price = self.pending_order['stop_price']
        tp_price = self.pending_order['tp_price']
        qty = self.compute_position_size(fill_price, stop_price)
        if qty <= 0:
            self.pending_order = None
            return

        # calculate fees and initial cash impact
        notional = qty * fill_price
        entry_fee = notional * self.taker_fee  # market order -> taker
        # set up live position
        self.position = {
            'open': True,
            'side': 'long',
            'qty': qty,
            'entry_price': fill_price,
            'entry_bar': self.current_bar,
            'stop_price': stop_price,
            'tp_price': tp_price,
            'entry_fee': entry_fee,
            'notional': notional,
            'unrealized_pnl': 0.0
        }
        # reserve capital implicitly; we will update equity when closing
        self.pending_order = None
        self.processed_entries.add(self.current_bar)
        # log
        print(f"\nFILLED LONG at bar {self.current_bar}: price {fill_price:.6f} qty {qty:.6f} notional {notional:.2f} fee {entry_fee:.4f}")

    def manage_position(self):
        if not self.position or not self.position.get('open'):
            return
        row = self.df.loc[self.current_bar]
        low = float(row['low'])
        high = float(row['high'])
        atr = float(row['atr']) if not pd.isna(row['atr']) else 0.0

        exit_reason = None
        exit_price = None

        # Stop-loss intrabar check (use stop_price)
        if low <= self.position['stop_price'] <= high:
            # assume stop hit, fill at stop price minus spread (we're selling)
            raw_exit = self.position['stop_price']
            exit_price = self.price_with_spread(raw_exit, 'sell')
            exit_price = self.apply_slippage(exit_price, atr)
            exit_price = self.round_tick(exit_price)
            exit_reason = 'Stop Loss'
        # Take-profit intrabar check
        elif low <= self.position['tp_price'] <= high:
            raw_exit = self.position['tp_price']
            exit_price = self.price_with_spread(raw_exit, 'sell')
            exit_price = self.apply_slippage(exit_price, atr)
            exit_price = self.round_tick(exit_price)
            exit_reason = 'Take Profit'
        else:
            # no exit this bar; update unrealized pnl
            cur_close = float(row['close'])
            self.position['unrealized_pnl'] = (cur_close - self.position['entry_price']) * self.position['qty']
            return

        # Compute realized PnL and fees
        qty = self.position['qty']
        exit_notional = qty * exit_price
        exit_fee = exit_notional * (self.maker_fee if exit_reason == 'Take Profit' else self.taker_fee)
        entry_notional = qty * self.position['entry_price']
        entry_fee = self.position.get('entry_fee', 0.0)
        pnl = exit_notional - exit_fee - entry_notional - entry_fee
        pnl_percent = (pnl / entry_notional) * 100 if entry_notional != 0 else 0.0

        trade = {
            'entry_bar': self.position['entry_bar'],
            'exit_bar': self.current_bar,
            'entry_price': self.position['entry_price'],
            'exit_price': exit_price,
            'qty': qty,
            'entry_fee': entry_fee,
            'exit_fee': exit_fee,
            'pnl': pnl,
            'pnl_pct': pnl_percent,
            'exit_reason': exit_reason
        }
        self.position_history.append(trade)
        self.current_equity += pnl
        self.last_exit_bar = self.current_bar

        print(f"\nEXIT [{exit_reason}] at bar {self.current_bar}: price {exit_price:.6f} P&L {pnl:.2f} ({pnl_percent:+.2f}%) New equity {self.current_equity:.2f}")

        # clear position
        self.position = None

    # ----------------------
    # Trading logic & checks
    # ----------------------
    def check_trade_logic(self, peak_line, trough_line):
        if peak_line is None or trough_line is None:
            return

        # Pattern width
        peak_points = peak_line['points']
        trough_points = trough_line['points']
        all_x = [x for x, _ in peak_points + trough_points]
        pattern_width = max(all_x) - min(all_x) if len(all_x) > 1 else 0
        if pattern_width < self.min_pattern_width:
            return

        avg_price = ((peak_line['slope'] * self.current_bar + peak_line['intercept']) +
                     (trough_line['slope'] * self.current_bar + trough_line['intercept'])) / 2.0
        is_parallel = self.are_parallel(peak_line['slope'], trough_line['slope'], avg_price)
        is_converging = self.are_converging(peak_line['slope'], trough_line['slope'])
        if not (is_parallel or is_converging):
            return

        # skip if in cooldown
        if self.current_bar < self.last_exit_bar + self.cooldown_bars:
            return
        # skip duplicate processing
        if self.current_bar in self.processed_entries:
            return

        # compute trough and peak projected prices at current bar
        trough_price = trough_line['slope'] * self.current_bar + trough_line['intercept']
        peak_price = peak_line['slope'] * self.current_bar + peak_line['intercept']
        atr = float(self.df.loc[self.current_bar, 'atr'])
        if atr <= 0 or np.isnan(atr):
            return
        breakout_line = trough_price + (atr * self.breakout_atr_multiplier)
        current_high = float(self.df.loc[self.current_bar, 'high'])
        current_close = float(self.df.loc[self.current_bar, 'close'])
        # long-only breakout rule: require a true crossing this bar (previous bar did not break)
        prev_high = float(self.df.loc[self.current_bar - 1, 'high']) if self.current_bar - 1 >= 0 else current_high
        prev_close = float(self.df.loc[self.current_bar - 1, 'close']) if self.current_bar - 1 >= 0 else current_close

        # consider breakout if intrabar high crosses breakout_line or close crosses breakout_line,
        # but only if previous bar did not already exceed breakout_line.
        crossed_high = (current_high > breakout_line) and (prev_high <= breakout_line)
        crossed_close = (current_close > breakout_line) and (prev_close <= breakout_line)

        if (not self.position) and (crossed_high or crossed_close):
            # ensure pattern supports long (peak > trough)
            if peak_price <= trough_price:
                return
            stop = trough_price
            entry_price = breakout_line
            pattern_height = max(peak_price - trough_price, 0.0)
            if pattern_height <= 0:
                return
            tp = entry_price + pattern_height * self.tp_atr_multiplier

            qty = self.compute_position_size(entry_price, stop)
            if qty <= 0:
                return
            notional = qty * entry_price
            estimated_entry_fee = notional * self.taker_fee
            gross_profit = (tp - entry_price) * qty
            estimated_exit_fee = tp * qty * self.maker_fee
            if gross_profit <= (estimated_entry_fee + estimated_exit_fee):
                return

            # place pending order to be filled at next bar open
            placed = self.place_long_order(trigger_price=entry_price, stop_price=stop, tp_price=tp, reason='breakout')
            if placed:
                print(f"Bar {self.current_bar}: PLACED pending entry -> trigger {entry_price:.6f} stop {stop:.6f} tp {tp:.6f}")

    # ----------------------
    # Plotting & UI
    # ----------------------
    def plot_current_state(self):
        self.ax.clear()
        start_idx = max(0, self.current_bar - self.lookback_bars)
        end_idx = min(len(self.df) - 1, self.current_bar + self.forward_buffer)
        visible_df = self.df.iloc[start_idx:end_idx+1]

        # Candles
        for i, (_, row) in enumerate(visible_df.iterrows()):
            idx = start_idx + i
            o, h, l, c = row['open'], row['high'], row['low'], row['close']
            color = 'green' if c >= o else 'red'
            alpha = 1.0 if idx <= self.current_bar else 0.2
            self.ax.plot([idx, idx], [l, h], color=color, linewidth=0.7, alpha=alpha)
            self.ax.add_patch(plt.Rectangle((idx-0.3, min(o,c)), 0.6, abs(c-o), facecolor=color, edgecolor=color, alpha=alpha))

        # Peaks/troughs & lines
        peaks = self.get_peaks_up_to_bar(self.current_bar)
        troughs = self.get_troughs_up_to_bar(self.current_bar)
        for p in peaks:
            self.ax.plot(p['index'], p['price'], 'ro', markersize=4, alpha=0.7)
        for t in troughs:
            self.ax.plot(t['index'], t['price'], 'go', markersize=4, alpha=0.7)

        peak_line = self.build_most_recent_line(peaks) if len(peaks) >= 2 else None
        trough_line = self.build_most_recent_line(troughs) if len(troughs) >= 2 else None

        # draw lines (use higher zorder and a dense x vector so lines render smoothly)
        if peak_line:
            xs = np.linspace(start_idx, end_idx, 200)
            ys = peak_line['slope'] * xs + peak_line['intercept']
            self.ax.plot(xs, ys, 'r-', linewidth=1.8, alpha=0.9, zorder=5)
            for x, y in peak_line['points']:
                if start_idx <= x <= end_idx:
                    self.ax.plot(x, y, 'r^', markersize=6, zorder=6)
        if trough_line:
            xs = np.linspace(start_idx, end_idx, 200)
            ys = trough_line['slope'] * xs + trough_line['intercept']
            self.ax.plot(xs, ys, 'g-', linewidth=1.8, alpha=0.9, zorder=5)
            for x, y in trough_line['points']:
                if start_idx <= x <= end_idx:
                    self.ax.plot(x, y, 'gv', markersize=6, zorder=6)


        # breakout line
        if trough_line:
            curr_atr = float(self.df.loc[self.current_bar, 'atr'])
            xs = [start_idx, end_idx]
            ys = [trough_line['slope'] * x + trough_line['intercept'] + curr_atr * self.breakout_atr_multiplier for x in xs]
            self.ax.plot(xs, ys, linestyle='--', linewidth=1.2, color='orange', alpha=0.8)

        # check trade logic (places orders)
        self.check_trade_logic(peak_line, trough_line)
        # try filling pending order (fills at current bar open if scheduled)
        self.try_fill_pending()
        # manage open position (intrabar check)
        self.manage_position()

        # Draw open position lines
        if self.position:
            self.ax.axhline(self.position['entry_price'], color='blue', linestyle=':', linewidth=1.5, label=f"Entry {self.position['entry_price']:.4f}")
            self.ax.axhline(self.position['stop_price'], color='red', linestyle='--', linewidth=1.2, label=f"Stop {self.position['stop_price']:.4f}")
            self.ax.axhline(self.position['tp_price'], color='green', linestyle='--', linewidth=1.2, label=f"TP {self.position['tp_price']:.4f}")

        # Plot closed trades markers
        for i, t in enumerate(self.position_history):
            if start_idx <= t['entry_bar'] <= end_idx:
                self.ax.plot(t['entry_bar'], t['entry_price'], marker='^', color='blue', markersize=8, zorder=6)
            if start_idx <= t['exit_bar'] <= end_idx:
                col = 'lime' if t['pnl'] > 0 else 'red'
                marker = 'v' if t['exit_reason'] == 'Stop Loss' else '^'
                self.ax.plot(t['exit_bar'], t['exit_price'], marker=marker, color=col, markersize=8, zorder=6)

        # Title & stats
        cur = self.df.loc[self.current_bar]
        title = f"Bar {self.current_bar}/{len(self.df)-1} | {cur['datetime']} | Equity: ${self.current_equity:.2f}"
        self.ax.set_title(title)
        # y-limits
        visible_prices = np.concatenate([visible_df['low'].values, visible_df['high'].values])
        ymin, ymax = visible_prices.min(), visible_prices.max()
        pad = (ymax - ymin) * 0.12 if ymax > ymin else ymin * 0.01
        self.ax.set_ylim(ymin - pad, ymax + pad)
        self.ax.set_xlim(start_idx, end_idx)

        # info box
        info = f"Pending: {'Yes' if self.pending_order else 'No'} | Position: {'Open' if self.position else 'Flat'}\nTrades: {len(self.position_history)} | Equity: ${self.current_equity:.2f}"
        dd = self.max_drawdown()
        info += f"\nMax DD: {dd:.2f}%"
        self.ax.text(0.02, 0.98, info, transform=self.ax.transAxes, fontsize=9, va='top',
                     bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))

        self.ax.legend(loc='upper left', fontsize=8)
        self.fig.canvas.draw_idle()

    # ----------------------
    # Navigation callbacks
    # ----------------------
    def next_bar(self, event=None):
        if self.current_bar < len(self.df) - self.forward_buffer - 1:
            self.current_bar += 1
            self.plot_current_state()

    def prev_bar(self, event=None):
        min_bar = max(self.fractal_period * 3, 50)
        if self.current_bar > min_bar:
            self.current_bar -= 1
            # rollback state: recompute position history up to this bar
            self.rebuild_state_up_to(self.current_bar)
            self.plot_current_state()

    def jump_forward(self, event=None):
        self.current_bar = min(len(self.df) - self.forward_buffer - 1, self.current_bar + 10)
        self.plot_current_state()

    def jump_back(self, event=None):
        self.current_bar = max(max(self.fractal_period * 3, 50), self.current_bar - 10)
        self.rebuild_state_up_to(self.current_bar)
        self.plot_current_state()

    # ----------------------
    # State rebuild (for stepping back in time)
    # ----------------------
    def rebuild_state_up_to(self, bar_index):
        # Reset everything then replay bars up to bar_index to reconstruct history
        saved_equity = self.initial_capital
        self.current_equity = float(self.initial_capital)
        self.pending_order = None
        self.position = None
        self.position_history = []
        self.processed_entries = set()
        self.last_exit_bar = -self.cooldown_bars

        for b in range(max(0, max(self.fractal_period * 3, 50)), bar_index + 1):
            self.current_bar = b
            peaks = self.get_peaks_up_to_bar(b)
            troughs = self.get_troughs_up_to_bar(b)
            peak_line = self.build_most_recent_line(peaks) if len(peaks) >= 2 else None
            trough_line = self.build_most_recent_line(troughs) if len(troughs) >= 2 else None
            # run checks and manage fills/exits
            self.check_trade_logic(peak_line, trough_line)
            self.try_fill_pending()
            self.manage_position()
        # restore current_bar to caller's value; caller will set it again if needed
        self.current_bar = bar_index

    # ----------------------
    # Metrics
    # ----------------------
    def equity_series(self):
        # produce equity series by replaying trades in order
        series = [self.initial_capital]
        eq = float(self.initial_capital)
        # sort trades by exit_bar
        for t in sorted(self.position_history, key=lambda x: x['exit_bar']):
            eq += t['pnl']
            series.append(eq)
        return np.array(series)

    def max_drawdown(self):
        eq = self.equity_series()
        if len(eq) == 0:
            return 0.0
        peak = np.maximum.accumulate(eq)
        drawdown = (peak - eq) / peak
        return (drawdown.max() * 100) if peak.max() > 0 else 0.0

    def summary(self):
        trades = len(self.position_history)
        wins = len([t for t in self.position_history if t['pnl'] > 0])
        losses = len([t for t in self.position_history if t['pnl'] <= 0])
        total_pnl = sum([t['pnl'] for t in self.position_history])
        avg_r = np.mean([ (t['pnl'] / (abs(t['entry_price'] - (t['entry_price'] - 1)) if False else 1)) for t in self.position_history ]) if trades else 0
        return {
            'trades': trades,
            'wins': wins,
            'losses': losses,
            'win_rate': (wins / trades * 100) if trades else 0.0,
            'total_pnl': total_pnl,
            'equity': self.current_equity,
            'max_dd_pct': self.max_drawdown()
        }

    # ----------------------
    # UI run
    # ----------------------
    def run_interactive(self):
        self.fig, self.ax = plt.subplots(figsize=(14, 9))
        plt.subplots_adjust(bottom=0.15)
        ax_prev = plt.axes([0.15, 0.03, 0.12, 0.05])
        ax_next = plt.axes([0.28, 0.03, 0.12, 0.05])
        ax_back10 = plt.axes([0.44, 0.03, 0.12, 0.05])
        ax_fwd10 = plt.axes([0.57, 0.03, 0.12, 0.05])

        btn_prev = Button(ax_prev, 'Previous')
        btn_next = Button(ax_next, 'Next')
        btn_back10 = Button(ax_back10, 'Back 10')
        btn_fwd10 = Button(ax_fwd10, 'Forward 10')

        btn_prev.on_clicked(self.prev_bar)
        btn_next.on_clicked(self.next_bar)
        btn_back10.on_clicked(self.jump_back)
        btn_fwd10.on_clicked(self.jump_forward)

        self.fig.canvas.mpl_connect('key_press_event', self.on_key)
        self.plot_current_state()

        print("Interactive mode ready. Keys: right/space next, left prev, up jump fwd, down jump back.")
        plt.show()

    def on_key(self, event):
        if event.key == 'right' or event.key == ' ':
            self.next_bar()
        elif event.key == 'left':
            self.prev_bar()
        elif event.key == 'up':
            self.jump_forward()
        elif event.key == 'down':
            self.jump_back()

# ----------------------
# Example usage
# ----------------------
if __name__ == "__main__":
    sim = RealisticConsolidationSimulator(
        fractal_period=3,
        deviation_threshold=2.0,
        third_point_multiplier=1.5,
        min_points_for_line=3,
        max_skipped_points=2,
        parallel_angle_threshold=5.0,
        min_pattern_width=10,
        lookback_bars=80,
        forward_buffer=50,
        atr_period=14,
        breakout_atr_multiplier=1.0,
        tp_atr_multiplier=1.0,
        initial_capital=10000,
        risk_per_trade_pct=0.01,
        position_size_pct=None,   # None -> use risk-based sizing
        maker_fee=0.0025,
        taker_fee=0.004,
        spread_pct=0.0003,
        slippage_atr_multiplier=0.15,
        order_latency_bars=1,
        cooldown_bars=5,
        min_ticks=0.0
    )
    filepath = "other data/ARBUSDT.csv"   # change path as needed
    print(f"Loading {filepath}")
    sim.load_data(filepath)
    sim.run_interactive()
