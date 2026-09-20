"""Tests unitarios para la Estrategia S5: Dual Momentum Leader."""

from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import StrategyContext, compute_signal_id
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy


def _create_synthetic_bars(
    start_price: float,
    end_price: float,
    n_days: int = 80,
    end_date: datetime = datetime(2025, 6, 10),
) -> pd.DataFrame:
    """Genera datos diarios sintéticos con tendencia lineal."""
    dates = [end_date.date() - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    step = (end_price - start_price) / (n_days - 1)
    prices = [start_price + i * step for i in range(n_days)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [100000] * n_days,
        }
    )


def test_s5_metadata_and_properties():
    strat = DualMomentumLeaderStrategy()
    assert strat.id == "dual_momentum_leader"
    assert strat.version == "1.2.0"
    assert strat.momentum_lookback_days == 45
    assert strat.trailing_ema_period == 25
    assert strat.top_n_leaders == 2
    assert strat.max_holding_days == 30
    assert MarketRegime.BULL_CALM in strat.allowed_regimes
    assert MarketRegime.BULL_VOLATILE in strat.allowed_regimes
    assert MarketRegime.BEAR not in strat.allowed_regimes
    assert MarketRegime.UNKNOWN not in strat.allowed_regimes
    assert "JPM" in strat.universe
    assert "LLY" in strat.universe
    assert "XOM" in strat.universe
    assert "COST" in strat.universe
    assert "GLD" in strat.universe
    assert "SLV" in strat.universe


def test_s5_regime_gating_preserves_capital_in_bear_market():
    strat = DualMomentumLeaderStrategy()
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # Datos fuertemente alcistas
    df_nvda = _create_synthetic_bars(100.0, 180.0, n_days=80, end_date=eval_dt)
    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BEAR,  # Mercado bajista
        daily_bars={"NVDA": df_nvda},
        current_prices={"NVDA": Decimal("180.00")},
    )

    signals = strat.generate(ctx)
    assert signals == [], "En régimen bajista (BEAR) S5 debe retornar 0 señales (100% efectivo)"


def test_s5_momentum_ranking_and_top_n_selection():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=60, top_n_leaders=2)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # NVDA: +50% a 60 días (de 100 a 150)
    df_nvda = _create_synthetic_bars(100.0, 150.0, n_days=80, end_date=eval_dt)
    # QQQ: +25% a 60 días (de 100 a 125)
    df_qqq = _create_synthetic_bars(100.0, 125.0, n_days=80, end_date=eval_dt)
    # SPY: +5% a 60 días (de 100 a 105)
    df_spy = _create_synthetic_bars(100.0, 105.0, n_days=80, end_date=eval_dt)

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"NVDA": df_nvda, "QQQ": df_qqq, "SPY": df_spy},
        current_prices={
            "NVDA": Decimal("150.00"),
            "QQQ": Decimal("125.00"),
            "SPY": Decimal("105.00"),
        },
    )

    signals = strat.generate(ctx)
    assert len(signals) == 2, "Debe seleccionar únicamente los Top 2 líderes"
    assert signals[0].symbol == "NVDA"
    assert signals[0].features["rank"] == 1
    assert signals[1].symbol == "QQQ"
    assert signals[1].features["rank"] == 2

    # Verificar que SPY quedó fuera por tener menor momentum
    symbols_selected = [s.symbol for s in signals]
    assert "SPY" not in symbols_selected


def test_s5_filters_out_assets_below_ema20():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=60, top_n_leaders=2)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # Activo con buen momentum histórico de 60 días pero fuerte desplome reciente por debajo de EMA20
    dates = [eval_dt.date() - timedelta(days=79 - i) for i in range(80)]
    prices = [100.0 + i * 1.0 for i in range(75)]  # Subiendo de 100 a 174
    # En los últimos 5 días colapsa a 120 (por debajo de la EMA20 que ronda los ~165)
    prices += [150.0, 140.0, 130.0, 125.0, 120.0]
    df_crash = pd.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [10000] * 80,
        }
    )

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"AAPL": df_crash},
        current_prices={"AAPL": Decimal("120.00")},
    )

    signals = strat.generate(ctx)
    assert signals == [], "Activo con ruptura por debajo de EMA20 debe ser descartado"


def test_s5_filters_out_negative_momentum():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=60, top_n_leaders=2)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # Activo en clara tendencia bajista
    df_down = _create_synthetic_bars(150.0, 100.0, n_days=80, end_date=eval_dt)

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"INTC": df_down},
        current_prices={"INTC": Decimal("100.00")},
    )

    signals = strat.generate(ctx)
    assert signals == [], "Activo con momentum negativo no debe generar señales de compra"


def test_s5_portfolio_deduplication():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=60, top_n_leaders=2)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    df_nvda = _create_synthetic_bars(100.0, 150.0, n_days=80, end_date=eval_dt)
    df_qqq = _create_synthetic_bars(100.0, 125.0, n_days=80, end_date=eval_dt)

    # Supongamos que ya poseemos NVDA en el portafolio
    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"NVDA": df_nvda, "QQQ": df_qqq},
        current_prices={"NVDA": Decimal("150.00"), "QQQ": Decimal("125.00")},
        portfolio_positions={"NVDA"},
    )

    signals = strat.generate(ctx)
    # NVDA debe ser omitido porque ya se posee; solo QQQ debe emitirse
    assert len(signals) == 1
    assert signals[0].symbol == "QQQ"


def test_s5_signal_structure_and_asymmetric_take_profit():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=60, top_n_leaders=1)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    df_nvda = _create_synthetic_bars(100.0, 150.0, n_days=80, end_date=eval_dt)
    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"NVDA": df_nvda},
        current_prices={"NVDA": Decimal("150.00")},
    )

    signals = strat.generate(ctx)
    assert len(signals) == 1
    sig = signals[0]

    assert sig.symbol == "NVDA"
    assert sig.side == "buy"
    assert sig.entry_type == "limit"
    assert sig.entry_price_ref == Decimal("150.00")
    assert sig.stop_price < sig.entry_price_ref
    # Regla clave de asimetría: Sin TP fijo para permitir captura de rallies
    assert sig.take_profit_price is None
    assert sig.exit_at_close is False
    assert sig.max_holding == timedelta(days=30)
    assert "momentum_60d" in sig.features
    assert "ema20" in sig.features
    assert sig.features["rank"] == 1


def test_s5_multi_sector_ranking_selects_non_tech_leaders():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=45, top_n_leaders=2)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # LLY (Salud): +40% a 45 días
    df_lly = _create_synthetic_bars(100.0, 140.0, n_days=80, end_date=eval_dt)
    # JPM (Finanzas): +25% a 45 días
    df_jpm = _create_synthetic_bars(100.0, 125.0, n_days=80, end_date=eval_dt)
    # AAPL (Tech): +5% a 45 días
    df_aapl = _create_synthetic_bars(100.0, 105.0, n_days=80, end_date=eval_dt)

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"LLY": df_lly, "JPM": df_jpm, "AAPL": df_aapl},
        current_prices={
            "LLY": Decimal("140.00"),
            "JPM": Decimal("125.00"),
            "AAPL": Decimal("105.00"),
        },
    )

    signals = strat.generate(ctx)
    assert len(signals) == 2
    assert signals[0].symbol == "LLY"
    assert signals[0].features["rank"] == 1
    assert signals[1].symbol == "JPM"
    assert signals[1].features["rank"] == 2


def test_s5_precious_metals_ranking_selects_gold_and_silver():
    strat = DualMomentumLeaderStrategy(momentum_lookback_days=45, top_n_leaders=2)
    eval_dt = datetime(2025, 6, 10, 15, 45)

    # SLV (Plata): +80% a 45 días
    df_slv = _create_synthetic_bars(20.0, 36.0, n_days=80, end_date=eval_dt)
    # GLD (Oro): +50% a 45 días
    df_gld = _create_synthetic_bars(180.0, 270.0, n_days=80, end_date=eval_dt)
    # AAPL (Tech): +10% a 45 días
    df_aapl = _create_synthetic_bars(100.0, 110.0, n_days=80, end_date=eval_dt)

    ctx = StrategyContext(
        now=eval_dt,
        regime=MarketRegime.BULL_CALM,
        daily_bars={"SLV": df_slv, "GLD": df_gld, "AAPL": df_aapl},
        current_prices={
            "SLV": Decimal("36.00"),
            "GLD": Decimal("270.00"),
            "AAPL": Decimal("110.00"),
        },
    )

    signals = strat.generate(ctx)
    assert len(signals) == 2
    assert signals[0].symbol == "SLV"
    assert signals[0].features["rank"] == 1
    assert signals[1].symbol == "GLD"
    assert signals[1].features["rank"] == 2

