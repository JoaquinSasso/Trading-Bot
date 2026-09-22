"""Pruebas unitarias para la estrategia S6: Intraday 5-Minute Multi-Horizon Momentum."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import StrategyContext
from tbot.strategies.s6_intraday_5m_multi_horizon import (
    HORIZON_BARS_MAP,
    Intraday5mMultiHorizonStrategy,
)

EASTERN = ZoneInfo("America/New_York")


def make_mock_5m_bars(prices: list[float], start_dt: datetime) -> pd.DataFrame:
    """Genera un DataFrame mock de barras de 5 minutos."""
    rows = []
    for i, p in enumerate(prices):
        bar_dt = start_dt + pd.Timedelta(minutes=5 * i)
        rows.append({
            "datetime_et": bar_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "date": bar_dt.strftime("%Y-%m-%d"),
            "time": bar_dt.strftime("%H:%M:%S"),
            "timestamp": int(bar_dt.timestamp()),
            "open": p,
            "high": p * 1.002,
            "low": p * 0.998,
            "close": p,
            "volume": 10000,
        })
    return pd.DataFrame(rows)


def test_horizon_bar_mapping():
    """Verifica el mapeo exacto de minutos a barras de 5 minutos."""
    assert HORIZON_BARS_MAP[10] == 2
    assert HORIZON_BARS_MAP[15] == 3
    assert HORIZON_BARS_MAP[30] == 6
    assert HORIZON_BARS_MAP[45] == 9
    assert HORIZON_BARS_MAP[60] == 12


def test_calculate_metrics_for_ticker():
    """Verifica el cálculo de retornos en los 5 horizontes."""
    # 20 barras con tendencia alcista lineal: 100.0 a 119.0
    prices = [100.0 + i * 1.0 for i in range(20)]
    start_dt = datetime(2026, 9, 1, 9, 30, tzinfo=EASTERN)
    df = make_mock_5m_bars(prices, start_dt)

    m = Intraday5mMultiHorizonStrategy.calculate_metrics_for_ticker(df, "NVDA")
    assert m is not None
    assert m.symbol == "NVDA"
    assert m.current_price == 119.0

    # m10 = (119 - 117) / 117
    expected_m10 = (119.0 - 117.0) / 117.0
    assert abs(m.m10 - expected_m10) < 1e-6

    # m15 = (119 - 116) / 116
    expected_m15 = (119.0 - 116.0) / 116.0
    assert abs(m.m15 - expected_m15) < 1e-6

    # m30 = (119 - 113) / 113
    expected_m30 = (119.0 - 113.0) / 113.0
    assert abs(m.m30 - expected_m30) < 1e-6

    # m45 = (119 - 110) / 110
    expected_m45 = (119.0 - 110.0) / 110.0
    assert abs(m.m45 - expected_m45) < 1e-6

    # m60 = (119 - 107) / 107
    expected_m60 = (119.0 - 107.0) / 107.0
    assert abs(m.m60 - expected_m60) < 1e-6

    # Tendencia alcista -> pasa gate
    assert m.passes_gate is True


def test_rank_universe_ordering():
    """Verifica que el ranking compuesto ordene correctamente por consistencia multi-horizonte."""
    start_dt = datetime(2026, 9, 1, 9, 30, tzinfo=EASTERN)

    # Activo A: Fuerte aceleración reciente
    prices_a = [100.0 + (i * 0.5 if i < 15 else i * 2.0) for i in range(25)]
    # Activo B: Movimiento moderado constante
    prices_b = [100.0 + i * 0.2 for i in range(25)]
    # Activo C: Caída
    prices_c = [100.0 - i * 0.5 for i in range(25)]

    all_bars = {
        "NVDA": make_mock_5m_bars(prices_a, start_dt),
        "AAPL": make_mock_5m_bars(prices_b, start_dt),
        "TSLA": make_mock_5m_bars(prices_c, start_dt),
    }

    ranked = Intraday5mMultiHorizonStrategy.rank_universe(
        all_bars, ["NVDA", "AAPL", "TSLA"]
    )

    assert len(ranked) == 3
    # NVDA debe ser el rank 1 (menor composite_rank)
    assert ranked[0].symbol == "NVDA"
    assert ranked[0].composite_rank < ranked[1].composite_rank
    assert ranked[1].symbol == "AAPL"
    assert ranked[2].symbol == "TSLA"
    assert ranked[2].passes_gate is False  # TSLA cayendo no pasa gate


def test_generate_signals_respects_time_window():
    """Verifica que antes de las 09:45 o después de las 15:30 no se emitan señales de entrada."""
    strat = Intraday5mMultiHorizonStrategy(start_entry_time=time(9, 45), end_entry_time=time(15, 30))

    # Barra a las 09:35 ET
    dt_early = datetime(2026, 9, 1, 9, 35, tzinfo=EASTERN)
    ctx_early = StrategyContext(
        now=dt_early,
        regime=MarketRegime.BULL_CALM,
        current_prices={"NVDA": Decimal("120.0")},
        daily_bars={},
        intraday_bars={"NVDA": make_mock_5m_bars([100 + i for i in range(20)], dt_early)},
        portfolio_positions=set(),
    )
    assert strat.generate(ctx_early) == []

    # Barra a las 15:40 ET (pasada la hora de entrada)
    dt_late = datetime(2026, 9, 1, 15, 40, tzinfo=EASTERN)
    ctx_late = StrategyContext(
        now=dt_late,
        regime=MarketRegime.BULL_CALM,
        current_prices={"NVDA": Decimal("120.0")},
        daily_bars={},
        intraday_bars={"NVDA": make_mock_5m_bars([100 + i for i in range(20)], dt_late)},
        portfolio_positions=set(),
    )
    assert strat.generate(ctx_late) == []


def test_generate_signal_properties():
    """Verifica que la señal generada contenga stop loss, exit_at_close y features completas."""
    strat = Intraday5mMultiHorizonStrategy(
        top_n=2,
        initial_stop_pct=0.008,
        start_entry_time=time(9, 45),
        end_entry_time=time(15, 30),
    )
    strat.universe = ["NVDA", "AAPL"]

    dt_valid = datetime(2026, 9, 1, 10, 30, tzinfo=EASTERN)
    prices_nvda = [100.0 + i * 1.0 for i in range(25)]
    prices_spy = [500.0 + i * 0.5 for i in range(25)]

    ctx = StrategyContext(
        now=dt_valid,
        regime=MarketRegime.BULL_CALM,
        current_prices={"NVDA": Decimal("124.0"), "SPY": Decimal("512.0")},
        daily_bars={},
        intraday_bars={
            "NVDA": make_mock_5m_bars(prices_nvda, dt_valid),
            "SPY": make_mock_5m_bars(prices_spy, dt_valid),
        },
        portfolio_positions=set(),
    )

    signals = strat.generate(ctx)
    assert len(signals) >= 1
    sig = signals[0]
    assert sig.symbol == "NVDA"
    assert sig.side == "buy"
    assert sig.exit_at_close is True
    assert sig.stop_price < sig.entry_price_ref
    assert abs(float(sig.stop_price) - float(sig.entry_price_ref) * 0.992) < 1e-4
    assert "m10" in sig.features
    assert "m60" in sig.features
    assert "composite_rank" in sig.features
