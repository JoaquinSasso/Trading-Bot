"""Módulo de backtesting, replay determinista y métricas cuantitativas."""

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import ReplayEngine
from tbot.backtest.metrics import (
    BacktestMetrics,
    calculate_deflated_sharpe_ratio,
    compute_backtest_metrics,
)
from tbot.backtest.simulated_broker import (
    SimulatedBroker,
    SimulatedPosition,
    SimulatedTrade,
)

__all__ = [
    "HistoricalDataLoader",
    "ReplayEngine",
    "BacktestMetrics",
    "calculate_deflated_sharpe_ratio",
    "compute_backtest_metrics",
    "SimulatedBroker",
    "SimulatedPosition",
    "SimulatedTrade",
]
