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
from tbot.strategies.s6_intraday_5m_multi_horizon import (
    HourlyIntradayMultiHorizonStrategy,
    Intraday5mMultiHorizonStrategy,
)
from tbot.strategies.s7_pid_scorer import (
    PIDScoreResult,
    S7PIDScorerStrategy,
    score_universe_pid,
)
from tbot.strategies.s8_pid_multihorizon import (
    S8PIDMultihorizonStrategy,
    S8ScoreResult,
    score_universe_s8,
)

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
    "Intraday5mMultiHorizonStrategy",
    "HourlyIntradayMultiHorizonStrategy",
    "PIDScoreResult",
    "score_universe_pid",
    "S7PIDScorerStrategy",
    "S8ScoreResult",
    "score_universe_s8",
    "S8PIDMultihorizonStrategy",
]
