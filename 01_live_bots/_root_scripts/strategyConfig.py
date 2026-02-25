"""
Strategy Configuration - Lean Version

Dual Range Filter Pro V5.0 (Lean)
ULTRA-OPTIMIZED: 76.5% win rate, 1488% avg monthly return across 6 months
Validated Q2/Q3/Q4 2025 + H1/H2 2025 periods
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class StrategyConfig:
    """Strategy parameters - ULTRA-OPTIMIZED for consistent high performance."""
    
    # =========================================================================
    # SIGNAL FILTERS (ULTRA-OPTIMIZED)
    # =========================================================================
    enable_price_distance_filter: bool = True
    min_price_distance_pct: float = 0.5  # User Request
    enable_same_direction_autoclose: bool = False
    use_cooldown: bool = False
    cooldown_bars: int = 20
    use_alternate_signals: bool = False
    
    # =========================================================================
    # QUALITY FILTER (ULTRA-OPTIMIZED)
    # =========================================================================
    enable_quality_filter: bool = True
    min_quality_score: int = 0  # User Request: Low/Flexible
    quality_lookback: int = 21  # OPTIMIZED V6
    
    # =========================================================================
    # PROFIT POTENTIAL PRE-FILTER (ULTRA-OPTIMIZED)
    # =========================================================================
    enable_profit_potential: bool = True
    min_profit_potential: float = 1.4  # User Request
    
    # =========================================================================
    # SIGNAL RATING
    # =========================================================================
    min_signal_rating: int = 4
    show_all_signals: bool = True
    enable_signal_sizing: bool = True
    
    # =========================================================================
    # RANGE FILTER 1 (ULTRA-OPTIMIZED)
    # =========================================================================
    rf1_filter_type: Literal["Type 1", "Type 2"] = "Type 1"
    rf1_movement_source: Literal["Wicks", "Close"] = "Close"
    rf1_range_size: float = 1.1  # OPTIMIZED V6
    rf1_range_scale: str = "Normalized Average Change"
    rf1_range_period: int = 19  # User Request
    rf1_smooth_range: bool = True
    rf1_smoothing_period: int = 95  # User Request
    rf1_avg_filter_changes: bool = False
    rf1_changes_to_avg: int = 2
    
    # =========================================================================
    # RANGE FILTER 2 (ULTRA-OPTIMIZED)
    # =========================================================================
    rf2_filter_type: Literal["Type 1", "Type 2"] = "Type 1"
    rf2_movement_source: Literal["Wicks", "Close"] = "Close"
    rf2_range_size: float = 6.2  # User Request
    rf2_range_scale: str = "Normalized Average Change"
    rf2_range_period: int = 24  # User Request
    rf2_smooth_range: bool = True
    rf2_smoothing_period: int = 60  # User Request
    rf2_avg_filter_changes: bool = False
    rf2_changes_to_avg: int = 2
    
    # =========================================================================
    # EXIT SETTINGS
    # =========================================================================
    exit_mode: Literal["Signal Only", "Signal + Peak Protection"] = "Signal Only"
    use_profit_cap: bool = False
    max_profit_cap: float = 15.0
    use_loss_cap: bool = False
    max_loss_cap: float = 3.0
    min_profit_threshold: float = 0.65
    peak_profit_trigger_input: float = 3.0
    peak_drawdown_pct_input: float = 35.0
    peak_lookback_bars: int = 3
    
    @property
    def peak_profit_trigger(self) -> float:
        """Jupiter: 1.5x multiplier on peak trigger."""
        return self.peak_profit_trigger_input * 1.5
    
    # =========================================================================
    # REGIME ADAPTATION
    # =========================================================================
    use_regime_adaptive_exits: bool = False
    adx_period: int = 14
    ranging_max_profit: float = 12.0
    ranging_peak_dd: float = 30.0
    explosive_min_profit: float = 25.0
    explosive_peak_dd: float = 60.0


# Default configuration instance
DEFAULT_CONFIG = StrategyConfig()



