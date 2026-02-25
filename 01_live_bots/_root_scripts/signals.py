"""
Signal Generation - Lean Version

Byte-for-byte match with lean PineScript.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.strategy import StrategyConfig
from src.strategy.core import (
    RangeSizeCalculator, RangeFilter, CondEMA,
    calculate_sma, calculate_ema, highest, lowest
)


@dataclass
class Signal:
    """Trading signal."""
    bar_index: int
    is_long: bool
    rating: int
    position_size_mult: float
    entry_price: float


class SignalGenerator:
    """
    Signal generation matching lean PineScript exactly.
    
    Order of operations:
    1. Calculate range filters
    2. Detect crossovers (bullish/bearish)
    3. Profit potential filter
    4. Quality filter
    5. Signal rating
    6. Rating filter
    7. Cooldown filter
    8. Price distance filter
    9. Alternating signals filter
    """
    
    def __init__(self, config: StrategyConfig):
        self.cfg = config
        self._reset()
    
    def _reset(self):
        """Reset state."""
        # Range Filter 1
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
        
        # Range Filter 2
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
        
        # State
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
        # Precomputed indicators
        vol_ma: float,
        trend_ema: float,
        atr_val: float,
        # Lookback data for quality filter
        recent_high: float,
        recent_low: float,
        close_3: float,
        close_10: float,
        local_vol_ma: float,
        avg_sep: float
    ) -> Tuple[Optional[Signal], float, float]:
        """
        Process bar and return signal if generated.
        
        Returns: (signal, filt1, filt2)
        """
        cfg = self.cfg
        
        # Get h/l values based on movement source
        h_val1 = high if cfg.rf1_movement_source == "Wicks" else close
        l_val1 = low if cfg.rf1_movement_source == "Wicks" else close
        h_val2 = high if cfg.rf2_movement_source == "Wicks" else close
        l_val2 = low if cfg.rf2_movement_source == "Wicks" else close
        
        # Calculate range sizes
        prev_close = self.prev_close if not np.isnan(self.prev_close) else close
        rng1 = self.rng_size1.update(high, low, close, prev_close)
        rng2 = self.rng_size2.update(high, low, close, prev_close)
        
        # Calculate range filters
        filt1 = self.rng_filt1.update(h_val1, l_val1, rng1)
        filt2 = self.rng_filt2.update(h_val2, l_val2, rng2)
        
        # Detect crossovers
        bullish_cross = False
        bearish_cross = False
        if not np.isnan(self.prev_filt1) and not np.isnan(self.prev_filt2):
            bullish_cross = (filt1 > filt2) and (self.prev_filt1 <= self.prev_filt2)
            bearish_cross = (filt1 < filt2) and (self.prev_filt1 >= self.prev_filt2)
        
        # Store previous values
        prev_filt1 = self.prev_filt1
        prev_filt2 = self.prev_filt2
        self.prev_filt1 = filt1
        self.prev_filt2 = filt2
        self.prev_close = close
        
        base_long_entry = bullish_cross
        base_short_entry = bearish_cross
        
        # =====================================================================
        # PROFIT POTENTIAL FILTER
        # =====================================================================
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
        
        # Apply profit potential filter
        if cfg.enable_profit_potential and (base_long_entry or base_short_entry):
            if estimated_profit_potential < cfg.min_profit_potential:
                base_long_entry = False
                base_short_entry = False
        
        # =====================================================================
        # QUALITY FILTER
        # =====================================================================
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
            
            # Momentum ratio
            fast_momentum = abs(close - close_3) / close_3 * 100 if close_3 > 0 else 0
            slow_momentum = abs(close - close_10) / close_10 * 100 if close_10 > 0 else 0
            ratio = fast_momentum / slow_momentum if slow_momentum > 0 else 0
            if ratio > 2.0:
                quality_score += 30
            elif ratio > 1.5:
                quality_score += 20
            elif ratio > 1.2:
                quality_score += 10
            
            # Volume ratio
            vol_ratio = volume / local_vol_ma if local_vol_ma > 0 else 1
            if vol_ratio > 1.8:
                quality_score += 25
            elif vol_ratio > 1.4:
                quality_score += 15
            elif vol_ratio > 1.1:
                quality_score += 8
            
            # Separation ratio
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
        
        # =====================================================================
        # SIGNAL RATING (LEAN)
        # =====================================================================
        signal_rating = 1
        if base_long_entry or base_short_entry:
            distance = abs(filt1 - filt2)
            price_roc = abs(close - close_3) / close_3 * 100 if close_3 > 0 else 0
            
            vol_score = min(15.0, (atr_val / close * 100) * 30) if close > 0 else 0
            mom_score = min(15.0, price_roc * 3)
            sep_score = min(15.0, (distance / close * 100) * 40) if close > 0 else 0
            vel_score = min(10.0, price_roc * 2)
            
            # Fixed base: 10 + 5 + 2.5 + 10 = 27.5
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
        
        # Apply rating filter
        long_entry = base_long_entry and (cfg.show_all_signals or signal_rating >= cfg.min_signal_rating)
        short_entry = base_short_entry and (cfg.show_all_signals or signal_rating >= cfg.min_signal_rating)
        
        # =====================================================================
        # COOLDOWN FILTER
        # =====================================================================
        if cfg.use_cooldown and self.bars_since_last_signal < cfg.cooldown_bars:
            long_entry = False
            short_entry = False
        
        # =====================================================================
        # PRICE DISTANCE FILTER
        # =====================================================================
        if cfg.enable_price_distance_filter and (long_entry or short_entry) and not np.isnan(self.last_entry_price):
            dist_pct = abs(open_price - self.last_entry_price) / self.last_entry_price * 100
            if dist_pct < cfg.min_price_distance_pct:
                long_entry = False
                short_entry = False
        
        # =====================================================================
        # ALTERNATING SIGNALS FILTER
        # =====================================================================
        if cfg.use_alternate_signals:
            if long_entry and self.last_signal_type == 1:
                long_entry = False
            if short_entry and self.last_signal_type == -1:
                short_entry = False
        
        # =====================================================================
        # UPDATE STATE
        # =====================================================================
        if long_entry or short_entry:
            self.bars_since_last_signal = 0
            self.last_entry_price = open_price
            self.last_signal_type = 1 if long_entry else -1
            
            # Position size multiplier
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
