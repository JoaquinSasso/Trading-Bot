"""Módulo de estrategias de trading algorítmico."""

from tbot.strategies.interfaces import (
    Signal,
    Strategy,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)
from tbot.strategies.s2_mean_reversion_rsi2 import MeanReversionRSI2Strategy
from tbot.strategies.s3_trend_pullback import TrendPullbackStrategy
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy
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
from tbot.strategies.s9_turn_of_month_momentum import TurnOfMonthMomentumStrategy
from tbot.strategies.s10_antonacci_dual_momentum import AntonacciDualMomentumStrategy
from tbot.strategies.s11_volatility_squeeze import VolatilitySqueezeStrategy

__all__ = [
    "Signal",
    "Strategy",
    "StrategyContext",
    "StrategyDataRequirements",
    "compute_signal_id",
    "MeanReversionRSI2Strategy",
    "TrendPullbackStrategy",
    "DualMomentumLeaderStrategy",
    "PIDScoreResult",
    "score_universe_pid",
    "S7PIDScorerStrategy",
    "S8ScoreResult",
    "score_universe_s8",
    "S8PIDMultihorizonStrategy",
    "TurnOfMonthMomentumStrategy",
    "AntonacciDualMomentumStrategy",
    "VolatilitySqueezeStrategy",
]
