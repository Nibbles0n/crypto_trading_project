"""
Exit Logic - Lean Version

Byte-for-byte match with lean PineScript.
No parabolic mode, no explosive extension.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional
from enum import Enum

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.strategy import StrategyConfig
from src.strategy.core import ADXCalculator


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
    """
    Exit logic matching lean PineScript.
    
    Exit order (as in PineScript):
    1. Same-direction auto-close
    2. Max profit cap (regime-adaptive)
    3. Max loss cap
    4. Opposite signal
    5. Peak protection
    """
    
    def __init__(self, config: StrategyConfig):
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
        
        # Current P&L
        if state.is_long:
            current_pnl_pct = (close - state.entry_price) / state.entry_price * 100
        else:
            current_pnl_pct = (state.entry_price - close) / state.entry_price * 100
        
        # Update profit peak
        if current_pnl_pct > state.profit_peak:
            state.profit_peak = current_pnl_pct
            state.bars_since_peak = 0
        else:
            state.bars_since_peak += 1
        
        # Activate peak protection
        if cfg.exit_mode == "Signal + Peak Protection":
            if current_pnl_pct >= cfg.peak_profit_trigger and not state.peak_protection_active:
                state.peak_protection_active = True
        
        # Regime detection (without parabolic)
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
        
        # 1. Same-direction auto-close
        if cfg.enable_same_direction_autoclose and not should_exit:
            same_dir = (state.is_long and long_signal) or (not state.is_long and short_signal)
            if same_dir and current_pnl_pct < 0:
                should_exit = True
                exit_price = close
                exit_reason = "Auto-Close (Same Dir)"
        
        # 2. Max profit cap
        if not should_exit and cfg.use_profit_cap and current_pnl_pct >= regime_max_profit:
            should_exit = True
            exit_price = close
            exit_reason = f"{regime.value} Max Profit"
        
        # 3. Max loss cap
        if not should_exit and cfg.use_loss_cap and current_pnl_pct <= -cfg.max_loss_cap:
            should_exit = True
            exit_price = close
            exit_reason = "Max Loss"
        
        # 4. Opposite signal
        if not should_exit:
            opposite = (state.is_long and short_signal) or (not state.is_long and long_signal)
            if opposite:
                should_exit = True
                exit_price = open_price  # Execute at open
                exit_reason = "Opposite Signal"
        
        # 5. Peak protection
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
            # Calculate execution P&L
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
            
            # Reset state
            self.state = TradeState()
            
            return result
        
        return None
    
    @property
    def is_in_trade(self) -> bool:
        return self.state.active
    
    @property
    def current_position(self) -> Optional[bool]:
        return self.state.is_long if self.state.active else None
