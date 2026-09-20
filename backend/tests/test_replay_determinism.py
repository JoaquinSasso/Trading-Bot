"""Test de determinismo del motor de Replay (dos corridas idénticas producen exactamente el mismo resultado)."""

from datetime import date, timedelta
from decimal import Decimal

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import ReplayEngine
from tbot.strategies.s1_intraday_momentum import IntradayMomentumStrategy


def test_replay_is_100_percent_deterministic():
    loader = HistoricalDataLoader()
    start_d = date(2025, 2, 1)
    end_d = date(2025, 3, 1)

    daily_df, intra_df = loader.generate_synthetic_history(
        symbol="SPY",
        start_date=start_d - timedelta(days=60),
        end_date=end_d,
        seed=999,
    )

    strat1 = IntradayMomentumStrategy()
    engine1 = ReplayEngine(
        strategy=strat1,
        historical_daily={"SPY": daily_df},
        historical_intraday={"SPY": intra_df},
        initial_capital=Decimal("2000.00"),
    )
    metrics1, trades1, equity1 = engine1.run(start_date=start_d, end_date=end_d)

    strat2 = IntradayMomentumStrategy()
    engine2 = ReplayEngine(
        strategy=strat2,
        historical_daily={"SPY": daily_df},
        historical_intraday={"SPY": intra_df},
        initial_capital=Decimal("2000.00"),
    )
    metrics2, trades2, equity2 = engine2.run(start_date=start_d, end_date=end_d)

    # Verificación de identidad estricta
    assert metrics1.total_net_pnl == metrics2.total_net_pnl
    assert metrics1.total_trades == metrics2.total_trades
    assert metrics1.sharpe_ratio == metrics2.sharpe_ratio
    assert metrics1.deflated_sharpe_ratio == metrics2.deflated_sharpe_ratio
    assert len(trades1) == len(trades2)

    for t1, t2 in zip(trades1, trades2, strict=True):
        assert t1.trade_id == t2.trade_id
        assert t1.entry_price == t2.entry_price
        assert t1.exit_price == t2.exit_price
        assert t1.pnl == t2.pnl
        assert t1.fees == t2.fees

    assert equity1.equals(equity2)
