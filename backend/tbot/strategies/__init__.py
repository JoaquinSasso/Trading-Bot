"""Módulo de estrategias de trading algorítmico."""

from tbot.strategies.interfaces import (
    Signal,
    Strategy,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)
from tbot.strategies.s1_intraday_momentum import IntradayMomentumStrategy
from tbot.strategies.s2_mean_reversion_rsi2 import MeanReversionRSI2Strategy
from tbot.strategies.s3_trend_pullback import TrendPullbackStrategy
from tbot.strategies.s4_opening_range_breakout import OpeningRangeBreakoutStrategy
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy

__all__ = [
    "Signal",
    "Strategy",
    "StrategyContext",
    "StrategyDataRequirements",
    "compute_signal_id",
    "IntradayMomentumStrategy",
    "MeanReversionRSI2Strategy",
    "TrendPullbackStrategy",
    "OpeningRangeBreakoutStrategy",
    "DualMomentumLeaderStrategy",
]
