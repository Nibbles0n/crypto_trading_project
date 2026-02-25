"""
Strategy Core Functions - Lean Version

Byte-for-byte replication of PineScript functions.
"""

import numpy as np
from typing import List


class CondEMA:
    """
    Conditional EMA - updates only when condition is True.
    
    PineScript:
    Cond_EMA(x, cond, n) =>
        var float[] val = array.new_float()
        var float[] ema_val = array.new_float(1, na)
        if cond
            array.push(val, x)
            if array.size(val) > 1
                array.remove(val, 0)
            if na(array.get(ema_val, 0))
                array.fill(ema_val, array.get(val, 0))
            array.set(ema_val, 0, (array.get(val, 0) - array.get(ema_val, 0)) * (2 / (n + 1)) + array.get(ema_val, 0))
        array.get(ema_val, 0)
    """
    
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
    """
    Conditional SMA - updates only when condition is True.
    
    PineScript:
    Cond_SMA(x, cond, n) =>
        var float[] vals = array.new_float()
        if cond
            array.push(vals, x)
            if array.size(vals) > n
                array.remove(vals, 0)
        array.avg(vals)
    """
    
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
    """
    Standard deviation: sqrt(E[X²] - E[X]²)
    
    PineScript:
    Stdev(x, n) => math.sqrt(Cond_SMA(math.pow(x, 2), true, n) - math.pow(Cond_SMA(x, true, n), 2))
    """
    
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
    """
    Range size calculation based on scale type.
    
    PineScript rng_size function.
    """
    
    def __init__(self, scale: str, qty: float, period: int):
        self.scale = scale
        self.qty = qty
        self.atr_ema = CondEMA(period)
        self.ac_ema = CondEMA(period)
        self.stdev = Stdev(period)
        self.prev_mid = np.nan
        
        # For Normalized Average Change: use long period for price normalization
        # This preserves short-term patterns while normalizing across price levels
        self.price_ema = CondEMA(200)  # Long period to not smooth out patterns
        self.ac_pct_ema = CondEMA(period)  # Same period as regular AC
    
    def update(self, high: float, low: float, close: float, prev_close: float) -> float:
        mid = (high + low) / 2
        
        # True Range
        if np.isnan(prev_close):
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        
        # Average Change (of mid price) - absolute
        if np.isnan(self.prev_mid):
            ac = 0.0
            ac_pct = 0.0
        else:
            ac = abs(mid - self.prev_mid)
            # Normalized: change as percentage of price
            ac_pct = (ac / self.prev_mid * 100) if self.prev_mid > 0 else 0.0
        self.prev_mid = mid
        
        # Update indicators
        atr = self.atr_ema.update(tr, True)
        avg_change = self.ac_ema.update(ac, True)
        sd = self.stdev.update(mid)
        
        # Update normalized indicators
        avg_price = self.price_ema.update(close, True)
        avg_change_pct = self.ac_pct_ema.update(ac_pct, True)
        
        # Calculate range based on scale
        if self.scale == "Pips":
            return self.qty * 0.0001
        elif self.scale == "Points":
            return self.qty * 1.0  # syminfo.pointvalue
        elif self.scale == "% of Price":
            return close * self.qty / 100
        elif self.scale == "ATR":
            return self.qty * atr if not np.isnan(atr) else 0.0
        elif self.scale == "Average Change":
            return self.qty * avg_change if not np.isnan(avg_change) else 0.0
        elif self.scale == "Normalized Average Change":
            # Normalized: qty × avg_change_pct × current_price / 100
            # This keeps volatility adaptation but normalizes across price levels
            if np.isnan(avg_change_pct) or close <= 0:
                return 0.0
            return self.qty * avg_change_pct * close / 100
        elif self.scale == "Standard Deviation":
            return self.qty * sd if not np.isnan(sd) else 0.0
        elif self.scale == "Ticks":
            return self.qty * 0.01  # syminfo.mintick
        else:  # "Absolute"
            return self.qty
    
    def reset(self):
        self.atr_ema.reset()
        self.ac_ema.reset()
        self.stdev.reset()
        self.prev_mid = np.nan
        self.price_ema.reset()
        self.ac_pct_ema.reset()


class RangeFilter:
    """
    Range Filter (Type 1 and Type 2).
    
    PineScript rng_filt function.
    """
    
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
        # Smooth range if enabled
        if self.smooth:
            r = self.smooth_ema.update(rng, True)
        else:
            r = rng
        
        if np.isnan(r):
            r = rng
        
        # Initialize
        if not self.initialized:
            self.rfilt = (high + low) / 2
            self.rfilt_prev = self.rfilt
            self.initialized = True
            return self.rfilt
        
        # Store previous
        self.rfilt_prev = self.rfilt
        
        # Filter logic
        if self.filter_type == "Type 1":
            if high - r > self.rfilt_prev:
                self.rfilt = high - r
            elif low + r < self.rfilt_prev:
                self.rfilt = low + r
        else:  # Type 2
            if high >= self.rfilt_prev + r:
                offset = int(abs(high - self.rfilt_prev) / r)
                self.rfilt = self.rfilt_prev + offset * r
            elif low <= self.rfilt_prev - r:
                offset = int(abs(low - self.rfilt_prev) / r)
                self.rfilt = self.rfilt_prev - offset * r
        
        # Average filter changes if enabled
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
    """
    ADX calculation for regime detection.
    
    PineScript calc_adx function.
    """
    
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
        # True Range
        if np.isnan(self.prev_close):
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        
        # Directional Movement
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
        
        # RMAs
        self.tr_rma = self._rma(self.tr_rma, tr)
        self.plus_dm_rma = self._rma(self.plus_dm_rma, plus_dm)
        self.minus_dm_rma = self._rma(self.minus_dm_rma, minus_dm)
        
        if self.tr_rma == 0 or np.isnan(self.tr_rma):
            return 0.0
        
        # +DI / -DI
        plus_di = 100 * self.plus_dm_rma / self.tr_rma
        minus_di = 100 * self.minus_dm_rma / self.tr_rma
        
        # DX
        di_sum = plus_di + minus_di
        dx = abs(plus_di - minus_di) / di_sum * 100 if di_sum != 0 else 0
        
        # ADX
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
