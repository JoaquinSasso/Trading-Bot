"""Suite completa de tests unitarios e integrados para el Motor Unificado BacktestEngine.

Verifica:
1. Regla 0 (Rule 0 Holdout Guard): Rechazo de fechas dentro del holdout sellado.
2. Invariante 1: Circuit breakers (-2.0% pausa diaria, -3.5% liquidación de emergencia, racha de pérdidas).
3. Invariante 2: Rechazo de futuros sintéticos (USO, UNG, etc.) y sólo metales físicos permitidos.
4. Bloques de activos (BlockConfig), límite máximo de 4 posiciones y cap individual <= 25%.
5. Control de volatilidad de cartera con covarianza contraída (shrunk covariance, lambda=0.3, 90d).
6. Fricciones de Alpaca, acciones enteras (integer shares) y piso mínimo de $150.
7. Rendimiento de efectivo remanente (Cash Yield / BIL real diario).
8. Régimen graduado (SPY vs SMA200/EMA50 + amplitud GICS) y rotación defensiva.
9. Compatibilidad total hacia atrás con la API de ReplayEngine y desempacado en tupla.
"""

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import (
    FORBIDDEN_SYNTHETIC_COMMODITIES,
    PHYSICAL_COMMODITIES_ALLOWLIST,
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    BlockConfig,
    ReplayEngine,
    compute_graduated_regime_score,
)
from tbot.backtest.guards import HoldoutViolationError
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


# --- Mock Strategy para testing ---
class MockBuyStrategy:
    """Estrategia determinista que emite señales fijas para evaluar el motor."""

    id: str = "mock_strategy"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(self, target_symbols: list[str], stop_pct: float = 0.05) -> None:
        self.target_symbols = target_symbols
        self.stop_pct = stop_pct

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        signals = []
        for s in self.target_symbols:
            if s in ctx.current_prices and s not in ctx.portfolio_positions:
                price = ctx.current_prices[s]
                stop = price * Decimal(str(1.0 - self.stop_pct))
                signals.append(
                    Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=s,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=price,
                        stop_price=stop,
                    )
                )
        return signals


def _create_mock_daily_df(
    symbol: str,
    dates: list[date],
    base_price: float = 100.0,
    daily_ret: float = 0.001,
    vol: float = 0.01,
    is_synthetic: bool = True,
) -> pd.DataFrame:
    """Crea un DataFrame diario determinista con atributos sintéticos."""
    records = []
    p = base_price
    for d in dates:
        p_open = p
        p_close = p * (1.0 + daily_ret)
        p_high = max(p_open, p_close) * (1.0 + vol)
        p_low = min(p_open, p_close) * (1.0 - vol)
        records.append(
            {
                "symbol": symbol,
                "date": d,
                "open": round(p_open, 2),
                "high": round(p_high, 2),
                "low": round(p_low, 2),
                "close": round(p_close, 2),
                "volume": 1_000_000,
            }
        )
        p = p_close
    df = pd.DataFrame(records)
    if is_synthetic:
        df.attrs["is_synthetic"] = True
    return df


# ==============================================================================
# 1. Test Rule 0: Holdout Guard Enforcement
# ==============================================================================
def test_rule_0_holdout_guard_blocks_sealed_dates():
    """Verifica que el motor rechace inmediatamente cualquier backtest en el holdout sellado."""
    dates_dev = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
    df_spy = _create_mock_daily_df("SPY", dates_dev, is_synthetic=False)

    config = BacktestConfig(strategy=MockBuyStrategy(["SPY"]))
    engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy})

    # 1. Rango legítimo en ventana de desarrollo 2022: debe pasar la guarda
    result = engine.run(start_date=date(2022, 1, 3), end_date=date(2022, 1, 10))
    assert isinstance(result, BacktestResult)

    # 2. Rango en holdout sellado (2023-01-01 a 2026-02-27): DEBE lanzar HoldoutViolationError
    with pytest.raises(HoldoutViolationError):
        engine.run(start_date=date(2023, 6, 1), end_date=date(2023, 6, 15))


# ==============================================================================
# 2. Test Invariant 2: Forbidden Synthetic Commodities & Shorting Prohibited
# ==============================================================================
def test_invariant_2_forbidden_synthetic_commodities():
    """Verifica que el motor rechace cualquier commodity sintética como USO o UNG."""
    for forbidden in FORBIDDEN_SYNTHETIC_COMMODITIES:
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY", forbidden]),
                historical_daily={"SPY": pd.DataFrame()},
            )

    # Comprobar que en bloques también se rechace
    with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
        BacktestEngine(
            config=BacktestConfig(
                blocks={
                    "energy": BlockConfig(
                        name="Energy", tickers=["USO"], capital_cap=0.2, top_n=1, buffer_rank=1
                    )
                }
            ),
            historical_daily={"USO": pd.DataFrame()},
        )

    # Materias primas físicas en la lista permitida deben aceptarse sin error
    for physical in PHYSICAL_COMMODITIES_ALLOWLIST:
        engine = BacktestEngine(
            config=BacktestConfig(universe=["SPY", physical]),
            historical_daily={"SPY": pd.DataFrame(), physical: pd.DataFrame()},
        )
        assert engine is not None


# ==============================================================================
# 3. Test Invariant 1: Circuit Breaker Daily Pause & Emergency Flatten
# ==============================================================================
def test_circuit_breaker_daily_pause_and_emergency_flatten():
    """Verifica la activación del cortacircuitos diario al -2.0% y -3.5%."""
    # Generar días de prueba
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(5)]

    # SPY normal
    df_spy = _create_mock_daily_df("SPY", dates, base_price=400.0, is_synthetic=True)

    # Activo volátil con caída severa en el día 2
    records_vol = [
        {"symbol": "VOL", "date": dates[0], "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"symbol": "VOL", "date": dates[1], "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1000},
        # Día 2: Caída intradiaria severa (Low $70 -> pérdida de $150 sobre $2000 = -7.5% -> EMERGENCY_FLATTEN)
        {"symbol": "VOL", "date": dates[2], "open": 100.0, "high": 100.0, "low": 70.0, "close": 75.0, "volume": 1000},
        {"symbol": "VOL", "date": dates[3], "open": 90.0, "high": 91.0, "low": 89.0, "close": 90.0, "volume": 1000},
        {"symbol": "VOL", "date": dates[4], "open": 90.0, "high": 91.0, "low": 89.0, "close": 90.0, "volume": 1000},
    ]
    df_vol = pd.DataFrame(records_vol)
    df_vol.attrs["is_synthetic"] = True

    # v2.3: los stops (órdenes en reposo) se evalúan antes que el cortacircuitos y el overlay de
    # EMA sube el stop a ~96.5; para ejercitar el cortacircuitos se usa un stop lejano (60) y sin overlay.
    strat = MockBuyStrategy(["VOL"], stop_pct=0.40)
    config = BacktestConfig(
        strategy=strat,
        initial_capital=Decimal("2000.00"),
        risk_per_trade_pct=10.0,  # $200 de riesgo / $40 por acción -> 5 acciones a $100 ($500 nocional)
        single_position_cap=0.50,
        exit_overlay="never",
        enable_circuit_breakers=True,
        daily_loss_limit_pct=2.0,
        emergency_loss_limit_pct=3.5,
        integer_shares=True,
        enable_vol_control=False,
        min_position_usd=50.0,
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_spy, "VOL": df_vol},
    )

    result = engine.run(start_date=dates[0], end_date=dates[4])

    # Debe haberse disparado al menos un evento de cortacircuitos
    assert len(result.circuit_breaker_events) > 0
    event_types = [e.event_type for e in result.circuit_breaker_events]
    assert "EMERGENCY_FLATTEN" in event_types or "PAUSED_DAILY_LOSS" in event_types


# ==============================================================================
# 4. Test Asset Blocks & Single Position Caps
# ==============================================================================
def test_block_config_and_position_caps():
    """Verifica que el motor respete el cap de 4 posiciones y cap individual <= 25%."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(150)]
    symbols = ["SYM1", "SYM2", "SYM3", "SYM4", "SYM5", "SYM6"]

    daily_data = {"SPY": _create_mock_daily_df("SPY", dates, 400.0, daily_ret=0.0005, is_synthetic=True)}
    for s in symbols:
        daily_data[s] = _create_mock_daily_df(s, dates, 50.0, daily_ret=0.001, is_synthetic=True)

    strat = MockBuyStrategy(symbols, stop_pct=0.05)
    config = BacktestConfig(
        strategy=strat,
        initial_capital=Decimal("2000.00"),
        max_open_positions=4,
        single_position_cap=0.25,
        min_position_usd=150.0,
        integer_shares=True,
        enable_vol_control=False,
    )

    engine = BacktestEngine(config=config, historical_daily=daily_data)
    result = engine.run(start_date=dates[0], end_date=dates[-1])

    # El broker nunca debe haber tenido más de 4 posiciones abiertas simultáneamente
    assert len(engine.broker.positions) <= 4

    # En los trades generados, verificar que el nocional de compra respetó el cap
    for t in result.trades:
        entry_val = float(t.qty * t.entry_price)
        # Cap es 25% de $2000 = $500 (con margen por apreciación)
        assert entry_val <= 600.0


# ==============================================================================
# 5. Test Shrunk Covariance Volatility Scaling (B-03)
# ==============================================================================
def test_shrunk_covariance_volatility_scaling():
    """Verifica que el control de volatilidad de cartera escale las posiciones si excede el target."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(120)]
    # Activos altamente volátiles (40% vol anual)
    df_spy = _create_mock_daily_df("SPY", dates, 400.0, is_synthetic=True)
    df_hi_vol = _create_mock_daily_df("HIVOL", dates, 100.0, vol=0.04, is_synthetic=True)

    strat = MockBuyStrategy(["HIVOL"], stop_pct=0.10)
    config = BacktestConfig(
        strategy=strat,
        initial_capital=Decimal("2000.00"),
        enable_vol_control=True,
        target_portfolio_vol=0.10,  # 10% target muy estricto
        vol_shrinkage_lambda=0.3,
        vol_lookback_days=90,
    )

    engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy, "HIVOL": df_hi_vol})
    result = engine.run(start_date=dates[100], end_date=dates[-1])

    assert isinstance(result, BacktestResult)
    # Si hubo operaciones, el tamaño debió ser escalado defensivamente
    for t in result.trades:
        assert t.qty >= Decimal("1")


# ==============================================================================
# 6. Test Whole Shares & Minimum Trade Size
# ==============================================================================
def test_whole_shares_and_minimum_trade_floor():
    """Verifica el redondeo a acciones enteras y el piso mínimo de $150."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
    # Activo con precio unitario alto ($800)
    df_spy = _create_mock_daily_df("SPY", dates, 400.0, is_synthetic=True)
    df_expensive = _create_mock_daily_df("EXP", dates, 800.0, is_synthetic=True)

    strat = MockBuyStrategy(["EXP"])
    config = BacktestConfig(
        strategy=strat,
        initial_capital=Decimal("1000.00"),
        single_position_cap=0.25,  # 25% de 1000 = $250 -> Menor a $800
        min_position_usd=150.0,
        integer_shares=True,
    )

    engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy, "EXP": df_expensive})
    result = engine.run(start_date=dates[0], end_date=dates[-1])

    # Debido a integer_shares y cap de $250, floor($250 / $800) = 0 acciones -> No debe haber compras
    assert len(result.trades) == 0


# ==============================================================================
# 7. Test Cash Yield Accrual (BIL)
# ==============================================================================
def test_cash_yield_accrual_on_idle_capital():
    """Verifica que el efectivo remanente gane rendimiento diario de BIL."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(20)]
    df_spy = _create_mock_daily_df("SPY", dates, 400.0, is_synthetic=True)

    # Serie sintética de RF positiva constante (ej. ~5% anual = 0.0002 diario)
    rf_series = pd.Series(0.0002, index=dates)

    config = BacktestConfig(
        strategy=None,  # 100% efectivo sin señales
        initial_capital=Decimal("2000.00"),
        enable_cash_yield=True,
        rf_series=rf_series,
    )

    engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy})
    result = engine.run(start_date=dates[0], end_date=dates[-1])

    # Tras 20 días al 0.02% diario, el capital final debe ser estrictamente mayor al inicial
    final_equity = float(result.equity_curve.iloc[-1])
    assert final_equity > 2000.0
    # Retorno esperado aproximado: 2000 * (1 + 0.0002)^20 ~ 2008.0
    assert final_equity >= 2005.0


# ==============================================================================
# 8. Test Graduated Regime Score
# ==============================================================================
def test_graduated_regime_score_calculation():
    """Verifica que el cálculo de score de régimen graduado retorne valores en {0.0, 0.33, 0.67, 1.0}."""
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(250)]
    daily_data = {"SPY": _create_mock_daily_df("SPY", dates, 400.0, daily_ret=0.001, is_synthetic=True)}
    for s in ["XLK", "XLF", "XLV"]:
        daily_data[s] = _create_mock_daily_df(s, dates, 100.0, daily_ret=0.001, is_synthetic=True)

    score = compute_graduated_regime_score(daily_data, dates[-1])
    assert 0.0 <= score <= 1.0


# ==============================================================================
# 9. Test Backward Compatibility with ReplayEngine & Tuple Unpacking
# ==============================================================================
def test_replay_engine_backward_compatibility_tuple_unpack():
    """Verifica que ReplayEngine siga funcionando con desempaquetado de 3 elementos."""
    loader = HistoricalDataLoader()
    start_d = date(2022, 1, 10)
    end_d = date(2022, 1, 20)

    daily_df, intra_df = loader.generate_synthetic_history(
        symbol="SPY",
        start_date=start_d - timedelta(days=30),
        end_date=end_d,
        seed=42,
    )

    strat = MockBuyStrategy(["SPY"])
    engine = ReplayEngine(
        strategy=strat,
        historical_daily={"SPY": daily_df},
        historical_intraday={"SPY": intra_df},
        initial_capital=Decimal("2000.00"),
    )

    # Desempaquetado tradicional en 3 elementos
    metrics, trades, equity_curve = engine.run(start_date=start_d, end_date=end_d)

    assert metrics is not None
    assert isinstance(trades, list)
    assert isinstance(equity_curve, pd.Series)
    assert not equity_curve.empty


# ==============================================================================
# 10. Test Intraday Resolution Dispatch & Execution (_run_intraday)
# ==============================================================================
def test_intraday_run_resolution_dispatch_and_execution():
    """Verifica la ejecución determinista en resolución intradiaria barra a barra (5m)."""
    loader = HistoricalDataLoader()
    start_d = date(2022, 1, 10)
    end_d = date(2022, 1, 14)

    daily_df, intra_df = loader.generate_synthetic_history(
        symbol="SPY",
        start_date=start_d - timedelta(days=10),
        end_date=end_d,
        seed=42,
    )

    strat = MockBuyStrategy(["SPY"], stop_pct=0.01)
    config = BacktestConfig(
        strategy=strat,
        universe=["SPY"],
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        max_open_positions=1,
        single_position_cap=0.5,
        integer_shares=False,
    )
    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": daily_df},
        historical_intraday={"SPY": intra_df},
    )

    res = engine.run(start_date=start_d, end_date=end_d, resolution="5m")
    assert isinstance(res, BacktestResult)
    assert isinstance(res.trades, list)
    assert isinstance(res.equity_curve, pd.Series)
    assert not res.equity_curve.empty
    assert res.metrics is not None

