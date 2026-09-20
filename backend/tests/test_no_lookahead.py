"""Test estricto de garantía de No-Lookahead Bias en ReplayEngine."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import ReplayEngine
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


class LookaheadDetectorStrategy:
    """Estrategia centinela que registra violaciones de causalidad temporal."""

    id: str = "lookahead_detector"
    version: str = "1.0.0"
    schedule: list[str] = ["15:30 America/New_York"]
    allowed_regimes: set[MarketRegime] = {MarketRegime.BULL_CALM}
    allows_open_window: bool = False
    universe: list[str] | None = ["SPY"]
    data_requirements = StrategyDataRequirements()

    def __init__(self) -> None:
        self.future_violations: list[tuple[datetime, datetime]] = []

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        current_now = ctx.now
        current_date = current_now.date()

        # Verificar barras diarias
        for _sym, df in ctx.daily_bars.items():
            d_col = "date" if "date" in df.columns else "timestamp"
            max_d = pd.to_datetime(df[d_col]).dt.date.max()
            if max_d > current_date:
                self.future_violations.append((current_now, max_d))

        # Verificar barras intradía
        for _sym, df in ctx.intraday_bars.items():
            t_col = "timestamp" if "timestamp" in df.columns else "date"
            max_ts = pd.to_datetime(df[t_col]).max()
            if max_ts > current_now:
                self.future_violations.append((current_now, max_ts))

        return []


def test_replay_engine_strictly_enforces_no_lookahead():
    loader = HistoricalDataLoader()
    start_d = date(2025, 1, 10)
    end_d = date(2025, 1, 20)

    daily_df, intra_df = loader.generate_synthetic_history(
        symbol="SPY",
        start_date=start_d - timedelta(days=30),
        end_date=end_d,
        seed=123,
    )

    sentinel_strategy = LookaheadDetectorStrategy()

    engine = ReplayEngine(
        strategy=sentinel_strategy,
        historical_daily={"SPY": daily_df},
        historical_intraday={"SPY": intra_df},
        initial_capital=Decimal("2000.00"),
    )

    metrics, trades, equity_curve = engine.run(start_date=start_d, end_date=end_d)

    # Garantía absoluta: Cero accesos a datos futuros
    assert len(sentinel_strategy.future_violations) == 0
