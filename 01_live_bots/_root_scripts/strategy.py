"""
Dual Range Filter Pro V5.0 - Combined Strategy Module

BYTE-FOR-BYTE match with PineScript logic.
All 3 strategy components (core, signals, exits) in one file.

DO NOT MODIFY THIS LOGIC without verification tests!
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple, List
from enum import Enum


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

class CondEMA:
    """Conditional EMA - updates only when condition is True."""
    
    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.ema_val = np.nan
    
    def update(self, x: float, cond: bool = True) -> float:
        if cond:
            if np.isnan(self.ema_val):
                self.ema_val = x
            else:
                self.ema_val = (x - self.ema_val) * self.alpha + self.ema_val
        return self.ema_val
    
    def reset(self):
        self.ema_val = np.nan


class CondSMA:
    """Conditional SMA - updates only when condition is True."""
    
    def __init__(self, period: int):
        self.period = period
        self.vals: List[float] = []
    
    def update(self, x: float, cond: bool = True) -> float:
        if cond:
            self.vals.append(x)
            if len(self.vals) > self.period:
                self.vals.pop(0)
        return np.mean(self.vals) if self.vals else np.nan
    
    def reset(self):
        self.vals = []


class Stdev:
    """Standard deviation: sqrt(E[X²] - E[X]²)"""
    
    def __init__(self, period: int):
        self.sma_x = CondSMA(period)
        self.sma_x2 = CondSMA(period)
    
    def update(self, x: float) -> float:
        mean = self.sma_x.update(x, True)
        mean_sq = self.sma_x2.update(x * x, True)
        if np.isnan(mean) or np.isnan(mean_sq):
            return np.nan
        variance = mean_sq - mean * mean
        return np.sqrt(max(0, variance))
    
    def reset(self):
        self.sma_x.reset()
        self.sma_x2.reset()


class RangeSizeCalculator:
    """Range size calculation based on scale type."""
    
    def __init__(self, scale: str, qty: float, period: int):
        self.scale = scale
        self.qty = qty
        self.atr_ema = CondEMA(period)
        self.ac_ema = CondEMA(period)
        self.stdev = Stdev(period)
        self.prev_mid = np.nan
        self.price_ema = CondEMA(200)
        self.ac_pct_ema = CondEMA(period)
    
    def update(self, high: float, low: float, close: float, prev_close: float) -> float:
        mid = (high + low) / 2
        
        if np.isnan(prev_close):
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        
        if np.isnan(self.prev_mid):
            ac = 0.0
            ac_pct = 0.0
        else:
            ac = abs(mid - self.prev_mid)
            ac_pct = (ac / self.prev_mid * 100) if self.prev_mid > 0 else 0.0
        self.prev_mid = mid
        
        atr = self.atr_ema.update(tr, True)
        avg_change = self.ac_ema.update(ac, True)
        sd = self.stdev.update(mid)
        avg_price = self.price_ema.update(close, True)
        avg_change_pct = self.ac_pct_ema.update(ac_pct, True)
        
        if self.scale == "Pips":
            return self.qty * 0.0001
        elif self.scale == "Points":
            return self.qty * 1.0
        elif self.scale == "% of Price":
            return close * self.qty / 100
        elif self.scale == "ATR":
            return self.qty * atr if not np.isnan(atr) else 0.0
        elif self.scale == "Average Change":
            return self.qty * avg_change if not np.isnan(avg_change) else 0.0
        elif self.scale == "Normalized Average Change":
            if np.isnan(avg_change_pct) or close <= 0:
                return 0.0
            return self.qty * avg_change_pct * close / 100
        elif self.scale == "Standard Deviation":
            return self.qty * sd if not np.isnan(sd) else 0.0
        elif self.scale == "Ticks":
            return self.qty * 0.01
        else:
            return self.qty
    
    def reset(self):
        self.atr_ema.reset()
        self.ac_ema.reset()
        self.stdev.reset()
        self.prev_mid = np.nan
        self.price_ema.reset()
        self.ac_pct_ema.reset()


class RangeFilter:
    """Range Filter (Type 1 and Type 2)."""
    
    def __init__(self, filter_type: str, smooth: bool, smooth_period: int,
                 avg_changes: bool, avg_samples: int):
        self.filter_type = filter_type
        self.smooth = smooth
        self.avg_changes = avg_changes
        self.smooth_ema = CondEMA(smooth_period)
        self.avg_ema = CondEMA(avg_samples)
        self.rfilt = np.nan
        self.rfilt_prev = np.nan
        self.initialized = False
    
    def update(self, high: float, low: float, rng: float) -> float:
        if self.smooth:
            r = self.smooth_ema.update(rng, True)
        else:
            r = rng
        
        if np.isnan(r):
            r = rng
        
        if not self.initialized:
            self.rfilt = (high + low) / 2
            self.rfilt_prev = self.rfilt
            self.initialized = True
            return self.rfilt
        
        self.rfilt_prev = self.rfilt
        
        if self.filter_type == "Type 1":
            if high - r > self.rfilt_prev:
                self.rfilt = high - r
            elif low + r < self.rfilt_prev:
                self.rfilt = low + r
        else:
            if high >= self.rfilt_prev + r:
                offset = int(abs(high - self.rfilt_prev) / r)
                self.rfilt = self.rfilt_prev + offset * r
            elif low <= self.rfilt_prev - r:
                offset = int(abs(low - self.rfilt_prev) / r)
                self.rfilt = self.rfilt_prev - offset * r
        
        if self.avg_changes:
            changed = self.rfilt != self.rfilt_prev
            avg_filt = self.avg_ema.update(self.rfilt, changed)
            return avg_filt if not np.isnan(avg_filt) else self.rfilt
        
        return self.rfilt
    
    def reset(self):
        self.smooth_ema.reset()
        self.avg_ema.reset()
        self.rfilt = np.nan
        self.rfilt_prev = np.nan
        self.initialized = False


class ADXCalculator:
    """ADX calculation for regime detection."""
    
    def __init__(self, period: int):
        self.period = period
        self.alpha = 1.0 / period
        self.tr_rma = np.nan
        self.plus_dm_rma = np.nan
        self.minus_dm_rma = np.nan
        self.dx_rma = np.nan
        self.prev_high = np.nan
        self.prev_low = np.nan
        self.prev_close = np.nan
    
    def _rma(self, current: float, new_val: float) -> float:
        if np.isnan(current):
            return new_val
        return self.alpha * new_val + (1 - self.alpha) * current
    
    def update(self, high: float, low: float, close: float) -> float:
        if np.isnan(self.prev_close):
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        
        if np.isnan(self.prev_high):
            plus_dm = 0.0
            minus_dm = 0.0
        else:
            up = high - self.prev_high
            down = self.prev_low - low
            plus_dm = up if (up > down and up > 0) else 0.0
            minus_dm = down if (down > up and down > 0) else 0.0
        
        self.prev_high = high
        self.prev_low = low
        self.prev_close = close
        
        self.tr_rma = self._rma(self.tr_rma, tr)
        self.plus_dm_rma = self._rma(self.plus_dm_rma, plus_dm)
        self.minus_dm_rma = self._rma(self.minus_dm_rma, minus_dm)
        
        if self.tr_rma == 0 or np.isnan(self.tr_rma):
            return 0.0
        
        plus_di = 100 * self.plus_dm_rma / self.tr_rma
        minus_di = 100 * self.minus_dm_rma / self.tr_rma
        
        di_sum = plus_di + minus_di
        dx = abs(plus_di - minus_di) / di_sum * 100 if di_sum != 0 else 0
        
        self.dx_rma = self._rma(self.dx_rma, dx)
        return self.dx_rma if not np.isnan(self.dx_rma) else 0.0
    
    def reset(self):
        self.tr_rma = np.nan
        self.plus_dm_rma = np.nan
        self.minus_dm_rma = np.nan
        self.dx_rma = np.nan
        self.prev_high = np.nan
        self.prev_low = np.nan
        self.prev_close = np.nan


def calculate_sma(data: np.ndarray, period: int) -> np.ndarray:
    """Vectorized SMA."""
    result = np.full_like(data, np.nan, dtype=float)
    for i in range(period - 1, len(data)):
        result[i] = np.mean(data[i - period + 1:i + 1])
    return result


def calculate_ema(data: np.ndarray, period: int) -> np.ndarray:
    """Vectorized EMA."""
    alpha = 2.0 / (period + 1)
    result = np.zeros_like(data, dtype=float)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def highest(data: np.ndarray, period: int, idx: int) -> float:
    """Get highest value in lookback period."""
    start = max(0, idx - period + 1)
    return np.max(data[start:idx + 1])


def lowest(data: np.ndarray, period: int, idx: int) -> float:
    """Get lowest value in lookback period."""
    start = max(0, idx - period + 1)
    return np.min(data[start:idx + 1])


# =============================================================================
# SIGNAL GENERATION
# =============================================================================

@dataclass
class Signal:
    """Trading signal."""
    bar_index: int
    is_long: bool
    rating: int
    position_size_mult: float
    entry_price: float


class SignalGenerator:
    """Signal generation matching lean PineScript exactly."""
    
    def __init__(self, config):
        self.cfg = config
        self._reset()
    
    def _reset(self):
        """Reset state."""
        self.rng_size1 = RangeSizeCalculator(
            self.cfg.rf1_range_scale,
            self.cfg.rf1_range_size,
            self.cfg.rf1_range_period
        )
        self.rng_filt1 = RangeFilter(
            self.cfg.rf1_filter_type,
            self.cfg.rf1_smooth_range,
            self.cfg.rf1_smoothing_period,
            self.cfg.rf1_avg_filter_changes,
            self.cfg.rf1_changes_to_avg
        )
        
        self.rng_size2 = RangeSizeCalculator(
            self.cfg.rf2_range_scale,
            self.cfg.rf2_range_size,
            self.cfg.rf2_range_period
        )
        self.rng_filt2 = RangeFilter(
            self.cfg.rf2_filter_type,
            self.cfg.rf2_smooth_range,
            self.cfg.rf2_smoothing_period,
            self.cfg.rf2_avg_filter_changes,
            self.cfg.rf2_changes_to_avg
        )
        
        self.prev_filt1 = np.nan
        self.prev_filt2 = np.nan
        self.prev_close = np.nan
        self.bars_since_last_signal = 999
        self.last_entry_price = np.nan
        self.last_signal_type = 0
    
    def process_bar(
        self,
        bar_index: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        vol_ma: float,
        trend_ema: float,
        atr_val: float,
        recent_high: float,
        recent_low: float,
        close_3: float,
        close_10: float,
        local_vol_ma: float,
        avg_sep: float
    ) -> Tuple[Optional[Signal], float, float]:
        """Process bar and return signal if generated."""
        cfg = self.cfg
        
        h_val1 = high if cfg.rf1_movement_source == "Wicks" else close
        l_val1 = low if cfg.rf1_movement_source == "Wicks" else close
        h_val2 = high if cfg.rf2_movement_source == "Wicks" else close
        l_val2 = low if cfg.rf2_movement_source == "Wicks" else close
        
        prev_close = self.prev_close if not np.isnan(self.prev_close) else close
        rng1 = self.rng_size1.update(high, low, close, prev_close)
        rng2 = self.rng_size2.update(high, low, close, prev_close)
        
        filt1 = self.rng_filt1.update(h_val1, l_val1, rng1)
        filt2 = self.rng_filt2.update(h_val2, l_val2, rng2)
        
        bullish_cross = False
        bearish_cross = False
        if not np.isnan(self.prev_filt1) and not np.isnan(self.prev_filt2):
            bullish_cross = (filt1 > filt2) and (self.prev_filt1 <= self.prev_filt2)
            bearish_cross = (filt1 < filt2) and (self.prev_filt1 >= self.prev_filt2)
        
        prev_filt1 = self.prev_filt1
        prev_filt2 = self.prev_filt2
        self.prev_filt1 = filt1
        self.prev_filt2 = filt2
        self.prev_close = close
        
        base_long_entry = bullish_cross
        base_short_entry = bearish_cross
        
        # PROFIT POTENTIAL FILTER
        estimated_profit_potential = 0.0
        if cfg.enable_profit_potential:
            trend_strength = abs(close - trend_ema) / trend_ema * 100 if trend_ema > 0 else 0
            vol_surge = (volume / vol_ma - 1) * 100 if vol_ma > 0 else 0
            filter_separation = abs(filt1 - filt2) / close * 100 if close > 0 else 0
            price_roc = abs(close - close_3) / close_3 * 100 if close_3 > 0 else 0
            
            score = 0.0
            score += min(30.0, trend_strength * 10)
            score += min(25.0, vol_surge * 5)
            score += min(25.0, filter_separation * 30)
            score += min(20.0, price_roc * 4)
            estimated_profit_potential = score / 20
        
        if cfg.enable_profit_potential and (base_long_entry or base_short_entry):
            if estimated_profit_potential < cfg.min_profit_potential:
                base_long_entry = False
                base_short_entry = False
        
        # QUALITY FILTER
        if cfg.enable_quality_filter and (base_long_entry or base_short_entry):
            quality_score = 0.0
            price_range = recent_high - recent_low
            
            if price_range > 0:
                range_position = (close - recent_low) / price_range
                if base_long_entry and range_position < 0.2:
                    quality_score += 30
                elif base_long_entry and range_position < 0.35:
                    quality_score += 15
                elif base_short_entry and range_position > 0.8:
                    quality_score += 30
                elif base_short_entry and range_position > 0.65:
                    quality_score += 15
            
            fast_momentum = abs(close - close_3) / close_3 * 100 if close_3 > 0 else 0
            slow_momentum = abs(close - close_10) / close_10 * 100 if close_10 > 0 else 0
            ratio = fast_momentum / slow_momentum if slow_momentum > 0 else 0
            if ratio > 2.0:
                quality_score += 30
            elif ratio > 1.5:
                quality_score += 20
            elif ratio > 1.2:
                quality_score += 10
            
            vol_ratio = volume / local_vol_ma if local_vol_ma > 0 else 1
            if vol_ratio > 1.8:
                quality_score += 25
            elif vol_ratio > 1.4:
                quality_score += 15
            elif vol_ratio > 1.1:
                quality_score += 8
            
            current_sep = abs(filt1 - filt2) / close * 100 if close > 0 else 0
            sep_ratio = current_sep / avg_sep if avg_sep > 0 else 1
            if sep_ratio > 1.5:
                quality_score += 15
            elif sep_ratio > 1.2:
                quality_score += 10
            elif sep_ratio > 1.0:
                quality_score += 5
            
            if quality_score < cfg.min_quality_score:
                base_long_entry = False
                base_short_entry = False
        
        # SIGNAL RATING
        signal_rating = 1
        if base_long_entry or base_short_entry:
            distance = abs(filt1 - filt2)
            price_roc = abs(close - close_3) / close_3 * 100 if close_3 > 0 else 0
            
            vol_score = min(15.0, (atr_val / close * 100) * 30) if close > 0 else 0
            mom_score = min(15.0, price_roc * 3)
            sep_score = min(15.0, (distance / close * 100) * 40) if close > 0 else 0
            vel_score = min(10.0, price_roc * 2)
            
            signal_score = vol_score + mom_score + sep_score + vel_score + 27.5
            
            if signal_score >= 80:
                signal_rating = 5
            elif signal_score >= 62:
                signal_rating = 4
            elif signal_score >= 43:
                signal_rating = 3
            elif signal_score >= 27:
                signal_rating = 2
            else:
                signal_rating = 1
        
        long_entry = base_long_entry and (cfg.show_all_signals or signal_rating >= cfg.min_signal_rating)
        short_entry = base_short_entry and (cfg.show_all_signals or signal_rating >= cfg.min_signal_rating)
        
        # COOLDOWN FILTER
        if cfg.use_cooldown and self.bars_since_last_signal < cfg.cooldown_bars:
            long_entry = False
            short_entry = False
        
        # PRICE DISTANCE FILTER
        if cfg.enable_price_distance_filter and (long_entry or short_entry) and not np.isnan(self.last_entry_price):
            dist_pct = abs(open_price - self.last_entry_price) / self.last_entry_price * 100
            if dist_pct < cfg.min_price_distance_pct:
                long_entry = False
                short_entry = False
        
        # ALTERNATING SIGNALS FILTER
        if cfg.use_alternate_signals:
            if long_entry and self.last_signal_type == 1:
                long_entry = False
            if short_entry and self.last_signal_type == -1:
                short_entry = False
        
        # UPDATE STATE
        if long_entry or short_entry:
            self.bars_since_last_signal = 0
            self.last_entry_price = open_price
            self.last_signal_type = 1 if long_entry else -1
            
            if cfg.enable_signal_sizing:
                if signal_rating == 5:
                    size_mult = 1.5
                elif signal_rating == 4:
                    size_mult = 1.2
                elif signal_rating == 3:
                    size_mult = 0.7
                else:
                    size_mult = 0.5
            else:
                size_mult = 1.0
            
            return Signal(
                bar_index=bar_index,
                is_long=long_entry,
                rating=signal_rating,
                position_size_mult=size_mult,
                entry_price=open_price
            ), filt1, filt2
        else:
            self.bars_since_last_signal += 1
            return None, filt1, filt2
    
    def get_filter_values(self) -> Tuple[float, float]:
        return self.prev_filt1, self.prev_filt2


# =============================================================================
# EXIT LOGIC
# =============================================================================

class Regime(Enum):
    RANGING = "RANGING"
    TRENDING = "TRENDING"
    EXPLOSIVE = "EXPLOSIVE"


@dataclass
class TradeState:
    """Current trade state."""
    active: bool = False
    is_long: bool = False
    entry_price: float = 0.0
    entry_bar: int = 0
    position_size_mult: float = 1.0
    signal_rating: int = 0
    profit_peak: float = 0.0
    bars_since_peak: int = 0
    peak_protection_active: bool = False


@dataclass
class ExitResult:
    """Exit information."""
    exit_price: float
    exit_reason: str
    pnl_pct: float
    net_pnl_pct: float
    bars_in_trade: int
    regime: Regime


class ExitManager:
    """Exit logic matching lean PineScript."""
    
    def __init__(self, config):
        self.cfg = config
        self.adx = ADXCalculator(config.adx_period)
        self.state = TradeState()
    
    def reset(self):
        self.adx.reset()
        self.state = TradeState()
    
    def open_trade(self, is_long: bool, entry_price: float, bar_index: int,
                   position_size_mult: float, signal_rating: int):
        self.state = TradeState(
            active=True,
            is_long=is_long,
            entry_price=entry_price,
            entry_bar=bar_index,
            position_size_mult=position_size_mult,
            signal_rating=signal_rating
        )
    
    def check_exit(
        self,
        bar_index: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
        long_signal: bool,
        short_signal: bool
    ) -> Optional[ExitResult]:
        if not self.state.active:
            return None
        
        cfg = self.cfg
        state = self.state
        
        bars_in_trade = bar_index - state.entry_bar
        
        if state.is_long:
            current_pnl_pct = (close - state.entry_price) / state.entry_price * 100
        else:
            current_pnl_pct = (state.entry_price - close) / state.entry_price * 100
        
        if current_pnl_pct > state.profit_peak:
            state.profit_peak = current_pnl_pct
            state.bars_since_peak = 0
        else:
            state.bars_since_peak += 1
        
        if cfg.exit_mode == "Signal + Peak Protection":
            if current_pnl_pct >= cfg.peak_profit_trigger and not state.peak_protection_active:
                state.peak_protection_active = True
        
        adx_value = self.adx.update(high, low, close)
        
        regime = Regime.TRENDING
        regime_max_profit = cfg.max_profit_cap
        regime_peak_dd = cfg.peak_drawdown_pct_input
        
        if cfg.use_regime_adaptive_exits:
            if adx_value < 25:
                regime = Regime.RANGING
                regime_max_profit = cfg.ranging_max_profit
                regime_peak_dd = cfg.ranging_peak_dd
            elif adx_value >= 40:
                regime = Regime.EXPLOSIVE
                regime_max_profit = cfg.explosive_min_profit * 2
                regime_peak_dd = cfg.explosive_peak_dd
        
        should_exit = False
        exit_price = close
        exit_reason = ""
        
        if cfg.enable_same_direction_autoclose and not should_exit:
            same_dir = (state.is_long and long_signal) or (not state.is_long and short_signal)
            if same_dir and current_pnl_pct < 0:
                should_exit = True
                exit_price = close
                exit_reason = "Auto-Close (Same Dir)"
        
        if not should_exit and cfg.use_profit_cap and current_pnl_pct >= regime_max_profit:
            should_exit = True
            exit_price = close
            exit_reason = f"{regime.value} Max Profit"
        
        if not should_exit and cfg.use_loss_cap and current_pnl_pct <= -cfg.max_loss_cap:
            should_exit = True
            exit_price = close
            exit_reason = "Max Loss"
        
        if not should_exit:
            opposite = (state.is_long and short_signal) or (not state.is_long and long_signal)
            if opposite:
                should_exit = True
                exit_price = open_price
                exit_reason = "Opposite Signal"
        
        if cfg.exit_mode == "Signal + Peak Protection" and not should_exit and state.peak_protection_active:
            drawdown = ((state.profit_peak - current_pnl_pct) / state.profit_peak * 100) if state.profit_peak > 0 else 0
            if drawdown >= regime_peak_dd and state.bars_since_peak >= cfg.peak_lookback_bars:
                if current_pnl_pct > cfg.min_profit_threshold:
                    should_exit = True
                    exit_price = close
                    if current_pnl_pct < 8.0:
                        tier = "Small"
                    elif current_pnl_pct < 15.0:
                        tier = "Medium"
                    else:
                        tier = "Large"
                    exit_reason = f"Peak ({tier})"
        
        if should_exit:
            if state.is_long:
                exec_pnl = (exit_price - state.entry_price) / state.entry_price * 100
            else:
                exec_pnl = (state.entry_price - exit_price) / state.entry_price * 100
            
            net_pnl = exec_pnl * state.position_size_mult
            
            result = ExitResult(
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl_pct=exec_pnl,
                net_pnl_pct=net_pnl,
                bars_in_trade=bars_in_trade,
                regime=regime
            )
            
            self.state = TradeState()
            
            return result
        
        return None
    
    @property
    def is_in_trade(self) -> bool:
        return self.state.active
    
    @property
    def current_position(self) -> Optional[bool]:
        return self.state.is_long if self.state.active else None
