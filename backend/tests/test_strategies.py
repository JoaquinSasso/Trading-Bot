"""Tests unitarios para las estrategias de trading S1, S2, S3, S4."""

from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import StrategyContext, compute_signal_id
from tbot.strategies.s1_intraday_momentum import IntradayMomentumStrategy
from tbot.strategies.s2_mean_reversion_rsi2 import MeanReversionRSI2Strategy
from tbot.strategies.s3_trend_pullback import TrendPullbackStrategy


def test_signal_id_determinism():
    dt = datetime(2025, 6, 10, 15, 30)
    sig_id1 = compute_signal_id("intraday_momentum", "1.0.0", "SPY", dt)
    sig_id2 = compute_signal_id("intraday_momentum", "1.0.0", "SPY", dt)
    sig_id_other = compute_signal_id("intraday_momentum", "1.0.0", "QQQ", dt)

    assert sig_id1 == sig_id2
    assert len(sig_id1) == 24
    assert sig_id1 != sig_id_other


def test_s1_intraday_momentum_generates_signal_on_positive_morning():
    strategy = IntradayMomentumStrategy(min_first_half_hour_ret=0.0)

    eval_dt = datetime(2025, 6, 10, 15, 30)

    # 1. Daily bars (prev close = 500.0)
    daily_df = pd.DataFrame(
        [
            {
                "date": datetime(2025, 6, 9).date(),
                "open": 498.0,
                "high": 501.0,
                "low": 497.0,
                "close": 500.0,
                "volume": 1000,
            },
            {
                "date": datetime(2025, 6, 10).date(),
                "open": 501.0,
                "high": 506.0,
                "low": 500.5,
                "close": 505.0,
                "volume": 1200,
            },
        ]
    )

    # 2. Intraday bars: At 10:00 close is 503.0 (> 500.0 -> ret = +0.6%)
    timestamps = [datetime(2025, 6, 10, 9, 30) + timedelta(minutes=i * 5) for i in range(75)]
    intraday_df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [501.0] * 75,
            "high": [506.0] * 75,
            "low": [500.0] * 75,
            "close": [503.0 if ts <= datetime(2025, 6, 10, 10, 0) else 505.0 for ts in timestamps],
            "volume": [10000] * 75,
        }
    )

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"SPY": daily_df},
        intraday_bars={"SPY": intraday_df},
        current_prices={"SPY": Decimal("505.00")},
    )

    signals = strategy.generate(ctx)
    assert len(signals) == 1
    sig = signals[0]
    assert sig.symbol == "SPY"
    assert sig.side == "buy"
    assert sig.entry_type == "market"
    assert sig.entry_price_ref == Decimal("505.00")
    assert sig.stop_price < Decimal("505.00")
    assert sig.exit_at_close is True
    assert sig.features["first_half_hour_ret"] > 0


def test_s1_intraday_momentum_rejects_on_negative_morning_or_unknown_regime():
    strategy = IntradayMomentumStrategy(min_first_half_hour_ret=0.0)
    eval_dt = datetime(2025, 6, 10, 15, 30)

    daily_df = pd.DataFrame(
        [
            {
                "date": datetime(2025, 6, 9).date(),
                "open": 500.0,
                "high": 502.0,
                "low": 498.0,
                "close": 500.0,
                "volume": 1000,
            },
            {
                "date": datetime(2025, 6, 10).date(),
                "open": 498.0,
                "high": 499.0,
                "low": 495.0,
                "close": 496.0,
                "volume": 1200,
            },
        ]
    )

    timestamps = [datetime(2025, 6, 10, 9, 30) + timedelta(minutes=i * 5) for i in range(75)]
    intraday_df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [498.0] * 75,
            "high": [499.0] * 75,
            "low": [495.0] * 75,
            "close": [497.0] * 75,  # 497 < 500 -> negativo
            "volume": [10000] * 75,
        }
    )

    # Caso 1: Mañana negativa
    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"SPY": daily_df},
        intraday_bars={"SPY": intraday_df},
        current_prices={"SPY": Decimal("496.00")},
    )
    assert len(strategy.generate(ctx)) == 0

    # Caso 2: Régimen UNKNOWN (falla cerrada)
    ctx.regime = MarketRegime.UNKNOWN
    assert len(strategy.generate(ctx)) == 0


def test_s2_mean_reversion_rsi2():
    strategy = MeanReversionRSI2Strategy(rsi_threshold=15.0, sma_trend_period=50)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # 60 días con tendencia alcista base pero caída abrupta en los últimos 2 días
    dates = [datetime(2025, 1, 1).date() + timedelta(days=i) for i in range(60)]
    prices = [100.0 + i * 1.0 for i in range(58)] + [150.0, 140.0]  # Drop brusco al final
    daily_df = pd.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": [p + 1.0 for p in prices],
            "low": [p - 1.0 for p in prices],
            "close": prices,
            "volume": [10000] * 60,
        }
    )

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"AAPL": daily_df},
        current_prices={"AAPL": Decimal("140.00")},
    )

    signals = strategy.generate(ctx)
    # Debería generar señal por sobreventa severa (RSI(2) < 15) por encima de la media de 50
    assert len(signals) == 1
    assert signals[0].symbol == "AAPL"
    assert signals[0].features["rsi2"] < 15.0


def test_s3_trend_pullback():
    strategy = TrendPullbackStrategy(ema_fast_period=20, ema_slow_period=50)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    dates = [datetime(2025, 1, 1).date() + timedelta(days=i) for i in range(60)]
    # Tendencia alcista donde EMA20 > EMA50
    prices = [100.0 + i * 0.5 for i in range(58)] + [128.0, 127.0]
    daily_df = pd.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": [p + 2.0 for p in prices],
            "low": [p - 2.0 for p in prices],
            "close": prices,
            "volume": [10000] * 60,
        }
    )

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"NVDA": daily_df},
        current_prices={"NVDA": Decimal("127.00")},
    )

    signals = strategy.generate(ctx)
    # Si cumple pullback, genera señal con Take Profit en 2R
    if signals:
        sig = signals[0]
        assert sig.take_profit_price is not None
        assert sig.take_profit_price > sig.entry_price_ref
