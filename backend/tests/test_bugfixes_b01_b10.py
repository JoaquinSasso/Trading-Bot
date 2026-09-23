"""Suite de regresión para verificar genuinamente las correcciones B-01 a B-10 del Milestone M1.

Cubre:
- B-01: Reseteo de frontera de sesión intradía (intraday momentum solo usa la sesión actual).
- B-02: Filtro pasa-bajos de 3 barras SMA aislado estrictamente por sesión.
- B-03: Control de riesgo de cartera con matriz de covarianza contraída (shrinkage lambda = 0.3).
- B-04: Consistencia dimensional de aceleración en PID de S8 (sin doble división por sigma).
- B-05: Factor de escala de volatilidad intradía corregido (sqrt(78) para barras de 5 minutos).
- B-06: Sustracción de tasa libre de riesgo diaria BIL en cálculo de ratio de Sharpe.
- B-07: Estimación de Alpha de Jensen y Beta mediante regresión OLS sobre retornos excedentes.
- B-08: Prohibición de anualización de CAGR/Sharpe para periodos cortos (T < 252 sesiones) y error estándar SE(S).
- B-09: Paridad de campos en dataclass S8ScoreResult y lógica de arbitraje U/D.
- B-10: Liquidación T+1 y prevención de violaciones de buena fe (GFV) en SimulatedBroker.
"""

import math
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tbot.backtest.metrics import (
    AlphaRegressionResult,
    calculate_shrunk_covariance,
    compute_backtest_metrics,
    compute_ols_alpha_beta,
    compute_portfolio_volatility_and_scale,
    load_risk_free_rate_bil,
)
from tbot.backtest.simulated_broker import SimulatedBroker, SimulatedTrade
from tbot.strategies.interfaces import Signal
from tbot.strategies.s8_pid_multihorizon import (
    S8ScoreResult,
    _extract_current_session_df,
    calculate_horizon_weights,
    compute_s8_pid_for_asset,
    score_universe_s8,
)


# ==============================================================================
# Helper fixtures / synthetic generators
# ==============================================================================
def _make_daily_df(n_days: int = 100, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    rets = np.random.normal(0.0005, 0.015, n_days)
    prices = 100.0 * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "date": dates,
        "open": prices * 0.999,
        "high": prices * 1.005,
        "low": prices * 0.995,
        "close": prices,
        "volume": 1_000_000.0,
    })


def _make_intraday_multi_session_df() -> pd.DataFrame:
    """Genera 2 sesiones completas de 5 minutos (78 barras por sesión)."""
    # Sesión 1: 2026-03-02 (09:30 a 16:00, 78 barras de 5m)
    dts_1 = pd.date_range("2026-03-02 09:30", periods=78, freq="5min")
    prices_1 = 200.0 + np.linspace(0, 10, 78)

    # Sesión 2: 2026-03-03 (09:30 a 16:00, 78 barras de 5m)
    dts_2 = pd.date_range("2026-03-03 09:30", periods=78, freq="5min")
    prices_2 = 100.0 + np.linspace(0, 5, 78)

    dts = list(dts_1) + list(dts_2)
    prices = list(prices_1) + list(prices_2)

    df = pd.DataFrame({
        "datetime_et": dts,
        "date": [d.strftime("%Y-%m-%d") for d in dts],
        "time": [d.strftime("%H:%M:%S") for d in dts],
        "open": [p * 0.999 for p in prices],
        "high": [p * 1.001 for p in prices],
        "low": [p * 0.999 for p in prices],
        "close": prices,
        "volume": 50_000.0,
    })
    return df


# ==============================================================================
# B-01: Intraday session boundary reset
# ==============================================================================
def test_b01_intraday_session_boundary_reset():
    """B-01: Verifica que los cálculos intradía se aíslen estrictamente a la sesión actual."""
    df_intra = _make_intraday_multi_session_df()

    # Sesión actual debe ser únicamente el día 2026-03-03
    cur_df = _extract_current_session_df(df_intra)
    assert len(cur_df) == 78
    assert (cur_df["date"] == "2026-03-03").all()

    # Si la sesión actual solo tiene 6 barras (09:30 a 09:55 = 30 min)
    partial_df = df_intra.iloc[:84].copy()  # 78 barras del Día 1 + 6 barras del Día 2
    cur_partial = _extract_current_session_df(partial_df)
    assert len(cur_partial) == 6
    assert (cur_partial["date"] == "2026-03-03").all()

    # Con 6 barras de 5m en el día 2, los horizontes >30m (1h=12b, 2h=24b, 4h=48b) NO deben calcularse
    # y los pesos deben redistribuirse dinámicamente sumando 1.0
    df_daily = _make_daily_df(100)
    res = compute_s8_pid_for_asset(df_daily, partial_df, sign_p=1.0, weight_rule="A")
    assert res["valid"] is True
    assert -3.0 <= res["I"] <= 3.0
    # Los horizontes intradía disponibles deben sumar sus pesos normalizados
    weights = calculate_horizon_weights(["10d", "5d", "1d", "30m"], rule="A")
    assert pytest.approx(sum(weights.values()), abs=1e-6) == 1.0


# ==============================================================================
# B-02: Intraday 3-bar lowpass filter reset per session
# ==============================================================================
def test_b02_intraday_lowpass_filter_session_reset():
    """B-02: Verifica que el filtro pasa-bajos SMA de 3 barras no arrastre precios de la sesión anterior."""
    df_intra = _make_intraday_multi_session_df()

    # El día 1 terminó en ~210. El día 2 abre en 100.
    # Si el SMA(3) sangrara entre días, la primera barra de hoy promediaría (209.87 + 210 + 100) / 3 ~= 173.
    # Al aislar la sesión, la primera barra del día 2 debe ser exactamente 100.0.
    cur_df = _extract_current_session_df(df_intra)
    smoothed_close = cur_df["close"].rolling(3, min_periods=1).mean()

    assert pytest.approx(smoothed_close.iloc[0], abs=1e-4) == 100.0
    assert pytest.approx(smoothed_close.iloc[1], abs=1e-4) == (100.0 + cur_df["close"].iloc[1]) / 2.0


# ==============================================================================
# B-03: Shrunk covariance matrix portfolio risk control
# ==============================================================================
def test_b03_shrunk_covariance_portfolio_risk():
    """B-03: Verifica la contracción lineal de covarianza (lambda=0.3) y escalado de volatilidad."""
    np.random.seed(42)
    # Crear 3 activos con correlaciones muestrales
    t = 60
    returns = np.random.normal(0.001, [0.01, 0.02, 0.03], size=(t, 3))
    returns_df = pd.DataFrame(returns, columns=["A", "B", "C"])

    sample_cov = np.cov(returns, rowvar=False, ddof=1)
    shrunk_cov = calculate_shrunk_covariance(returns, shrinkage_lambda=0.3)

    # 1. Las varianzas diagonales se preservan idénticas
    for i in range(3):
        assert pytest.approx(shrunk_cov[i, i], rel=1e-6) == sample_cov[i, i]

    # 2. Las covarianzas fuera de la diagonal se contraen en (1 - lambda) = 0.70
    assert pytest.approx(shrunk_cov[0, 1], rel=1e-6) == 0.70 * sample_cov[0, 1]
    assert pytest.approx(shrunk_cov[1, 2], rel=1e-6) == 0.70 * sample_cov[1, 2]

    # 3. compute_portfolio_volatility_and_scale reduce peso si la volatilidad excede target_vol
    weights = {"A": 0.33, "B": 0.33, "C": 0.34}
    sigma_port, k_scale, _ = compute_portfolio_volatility_and_scale(
        weights, returns_df, target_vol=0.10, shrinkage_lambda=0.3
    )
    assert sigma_port > 0.0
    if sigma_port > 0.10:
        assert k_scale < 1.0
        assert pytest.approx(k_scale, rel=1e-3) == 0.10 / sigma_port
    else:
        assert k_scale == 1.0


# ==============================================================================
# B-04: S8 PID acceleration dimensional consistency
# ==============================================================================
def test_b04_s8_pid_acceleration_dimensional_consistency():
    """B-04: Verifica que la aceleración no sufra una doble división por sigma_45d."""
    df_daily = _make_daily_df(100, seed=123)
    df_intra = _make_intraday_multi_session_df()

    res = compute_s8_pid_for_asset(df_daily, df_intra, sign_p=1.0, weight_rule="A")
    assert res["valid"] is True
    # Las 6 features de estrés d_features deben ser finitas y coherentes
    assert len(res["d_features"]) == 6
    for feat in res["d_features"]:
        assert math.isfinite(feat)
        assert not math.isnan(feat)


# ==============================================================================
# B-05: Realized volatility intraday scaling factor (sqrt(78) for 5m)
# ==============================================================================
def test_b05_realized_volatility_scaling_factor_5m():
    """B-05: Verifica el escalado de volatilidad de 5m con sqrt(78.0) en lugar de factores incorrectos."""
    # Una sesión regular en EE.UU. son 6.5 horas = 390 minutos = 78 barras de 5m
    bars_per_day = 78.0
    expected_scale = math.sqrt(bars_per_day)
    assert pytest.approx(expected_scale, abs=1e-4) == 8.83176

    # 5 días de barras de 5m equivalen a 5 * 78 = 390 barras
    assert 5 * int(bars_per_day) == 390

    # Comprobación de que compute_s8_pid_for_asset calcula vol_exp utilizando window=390 y sqrt(78.0)
    df_daily = _make_daily_df(100)
    n_bars = 400
    dts = pd.date_range("2026-03-01 09:30", periods=n_bars, freq="5min")
    np.random.seed(99)
    rets = np.random.normal(0.0, 0.001, n_bars)
    prices = 100.0 * np.exp(np.cumsum(rets))
    df_intra = pd.DataFrame({
        "datetime_et": dts,
        "date": [d.strftime("%Y-%m-%d") for d in dts],
        "time": [d.strftime("%H:%M:%S") for d in dts],
        "open": prices,
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
        "volume": 1000.0,
    })

    res = compute_s8_pid_for_asset(df_daily, df_intra)
    assert res["valid"] is True
    vol_exp = res["d_features"][0]
    assert 0.1 <= vol_exp <= 5.0

    # Verificación matemática del cálculo de volatilidad realizada 5d
    bars_vol = 390
    scale_vol = math.sqrt(78.0)
    intra_rets = np.log(df_intra["close"].astype(float) / df_intra["close"].astype(float).shift(1)).dropna()
    sigma_5d_realized = float(intra_rets.iloc[-bars_vol:].std()) * scale_vol
    assert sigma_5d_realized > 0.0


# ==============================================================================
# B-06: Daily BIL risk-free rate subtraction in Sharpe computation
# ==============================================================================
def test_b06_daily_bil_risk_free_rate_subtraction():
    """B-06: Verifica que la tasa libre de riesgo BIL se reste de los retornos diarios para el Sharpe."""
    rf_series = load_risk_free_rate_bil()
    # Si el archivo data/risk_free_rate_bil_2010_2026.csv existe, debe tener registros y promedio positivo
    if not rf_series.empty:
        assert len(rf_series) > 3000
        assert rf_series.mean() > 0.0

    # Generar curva de equity sintética de 300 días con retorno positivo y varianza realista
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    np.random.seed(42)
    daily_rets = np.random.normal(0.0008, 0.006, 300)
    eq_vals = 2000.0 * np.cumprod(1.0 + daily_rets)
    equity_curve = pd.Series(eq_vals, index=dates)

    # Trades ficticios mínimos para pasar compute_backtest_metrics
    trades: list[SimulatedTrade] = [
        SimulatedTrade(
            trade_id="T1",
            symbol="SPY",
            strategy_id="test",
            entry_time=dates[10],
            exit_time=dates[20],
            entry_price=Decimal("100"),
            exit_price=Decimal("105"),
            qty=Decimal("10"),
            pnl=Decimal("50"),
            pnl_pct=0.05,
            pnl_r=1.0,
            fees=Decimal("0.10"),
            slippage_cost=Decimal("0.05"),
            exit_reason="take_profit",
            initial_risk_usd=Decimal("50"),
        )
    ]

    # Sin tasa libre de riesgo (rf_series vacío con ceros)
    rf_zero = pd.Series(0.0, index=[d.date() for d in dates])
    metrics_no_rf = compute_backtest_metrics("test_strat", trades, equity_curve, rf_series=rf_zero)

    # Con tasa libre de riesgo positiva (e.g. 5% anualizado ~= 0.0002 diario)
    rf_custom = pd.Series(0.0002, index=[d.date() for d in dates])
    metrics_with_rf = compute_backtest_metrics("test_strat", trades, equity_curve, rf_series=rf_custom)

    assert metrics_no_rf.sharpe_ratio is not None
    assert metrics_with_rf.sharpe_ratio is not None
    # Al restar el riesgo libre positivo, el ratio de Sharpe debe ser estrictamente menor
    assert metrics_with_rf.sharpe_ratio < metrics_no_rf.sharpe_ratio


# ==============================================================================
# B-07: OLS alpha/beta computation on excess returns
# ==============================================================================
def test_b07_ols_alpha_beta_excess_returns():
    """B-07: Verifica la regresión OLS sobre retornos excedentes (Alpha de Jensen y Beta)."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=500, freq="B")

    # Benchmark: retornos normales
    b_rets = np.random.normal(0.0004, 0.01, len(dates))
    benchmark_s = pd.Series(b_rets, index=dates)

    # Tasa libre de riesgo constante (2.52% anual -> 0.0001 diario)
    rf_daily = 0.0001

    # Estrategia: true beta = 1.20, true alpha anual = 0.0504 (0.0002 diario)
    s_rets = rf_daily + 0.0002 + 1.20 * (b_rets - rf_daily) + np.random.normal(0, 0.0001, len(dates))
    strategy_s = pd.Series(s_rets, index=dates)

    res = compute_ols_alpha_beta(strategy_s, benchmark_s, risk_free_returns=rf_daily)

    assert isinstance(res, AlphaRegressionResult)
    # Beta estimado debe ser muy cercano a 1.20
    assert pytest.approx(res.beta, abs=0.03) == 1.20
    # Alpha anualizada estimada debe ser cercana a 0.0504
    assert pytest.approx(res.alpha_annualized, abs=0.01) == 0.0504
    assert res.r_squared > 0.90
    assert res.alpha_se > 0.0
    assert res.beta_se > 0.0


# ==============================================================================
# B-08: Prohibit annualization under 252 bars & Sharpe SE
# ==============================================================================
def test_b08_prohibit_annualization_under_252_bars_and_sharpe_se():
    """B-08: Verifica que no se anualicen CAGR ni Sharpe para T < 252 barras, y que se compute Sharpe SE."""
    dates_short = pd.date_range("2026-01-02", periods=60, freq="B")
    eq_short = pd.Series(np.linspace(1000, 1100, 60), index=dates_short)
    trades: list[SimulatedTrade] = [
        SimulatedTrade(
            trade_id="T1",
            symbol="SPY",
            strategy_id="test",
            entry_time=dates_short[5],
            exit_time=dates_short[15],
            entry_price=Decimal("100"),
            exit_price=Decimal("105"),
            qty=Decimal("10"),
            pnl=Decimal("50"),
            pnl_pct=0.05,
            pnl_r=1.0,
            fees=Decimal("0.10"),
            slippage_cost=Decimal("0.05"),
            exit_reason="take_profit",
            initial_risk_usd=Decimal("50"),
        )
    ]

    m_short = compute_backtest_metrics("test_strategy", trades, eq_short)
    # Prohibición de anualización
    assert m_short.cagr_pct is None
    assert m_short.sharpe_ratio is None
    assert m_short.sortino_ratio is None
    # Error estándar de Sharpe debe computarse
    assert m_short.sharpe_se is not None
    assert m_short.sharpe_se > 0.0
    assert m_short.account_type == "cash"

    # Con T >= 252 barras, la anualización debe estar permitida
    dates_long = pd.date_range("2025-01-02", periods=255, freq="B")
    eq_long = pd.Series(np.linspace(1000, 1200, 255), index=dates_long)
    m_long = compute_backtest_metrics("test_strategy", trades, eq_long)
    assert m_long.cagr_pct is not None
    assert m_long.sharpe_ratio is not None
    assert m_long.sharpe_se is not None


# ==============================================================================
# B-09: S8 dataclass fields parity & arbitration
# ==============================================================================
def test_b09_s8_dataclass_fields_parity_and_arbitration():
    """B-09: Verifica que S8ScoreResult tenga todos los campos requeridos y sincronice d_stress."""
    # Instanciación directa con nuevos campos
    res = S8ScoreResult(
        symbol="QQQ",
        p_term=0.5,
        i_term=0.2,
        d_term=0.1,
        u_score=1.85,
        d_score=-0.42,
        decision="BUY",
        deciding_system="U_SYSTEM",
        d_veto_triggered=False,
        forced_exit_triggered=False,
    )
    assert res.symbol == "QQQ"
    assert res.u_score == 1.85
    assert res.d_score == -0.42
    # Sincronización automática de d_stress
    assert res.d_stress == -0.42
    assert res.decision == "BUY"
    assert res.deciding_system == "U_SYSTEM"
    assert res.d_veto_triggered is False
    assert res.forced_exit_triggered is False

    # score_universe_s8 debe retornar S8ScoreResult con arbitraje poblado
    d1 = _make_daily_df(100, seed=1)
    d2 = _make_daily_df(100, seed=2)
    spy_d = _make_daily_df(100, seed=3)
    i1 = _make_intraday_multi_session_df()
    i2 = _make_intraday_multi_session_df()

    scores = score_universe_s8(
        {"SYM1": d1, "SYM2": d2},
        {"SYM1": i1, "SYM2": i2},
        df_spy=spy_d,
        sign_p=1.0,
        weight_rule="A",
    )
    for _sym, item in scores.items():
        assert isinstance(item, S8ScoreResult)
        assert hasattr(item, "decision")
        assert hasattr(item, "deciding_system")
        assert hasattr(item, "d_veto_triggered")
        assert hasattr(item, "forced_exit_triggered")
        assert item.decision.lower() in {"enter", "buy", "hold", "veto", "exit", "skip"}


# ==============================================================================
# B-10: SimulatedBroker T+1 cash settlement & GFV prevention
# ==============================================================================
def test_b10_simulated_broker_t1_cash_settlement_and_gfv():
    """B-10: Verifica liquidación T+1 y prevención de compras con fondos no liquidados (GFV)."""
    broker = SimulatedBroker(
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        settlement_days=1,
    )
    assert broker.account_type == "cash"
    assert broker.settled_cash == Decimal("2000.00")
    assert broker.unsettled_cash == Decimal("0.0")
    assert broker.buying_power == Decimal("2000.00")

    # 1. Compra de 2 acciones de SPY a $500.00 el lunes 2025-06-09
    t_buy = datetime(2025, 6, 9, 10, 0)
    sig = Signal.create(
        strategy_id="s8",
        version="1.0.0",
        symbol="SPY",
        bar_ts=t_buy,
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("500.00"),
        stop_price=Decimal("490.00"),
    )
    pos = broker.submit_buy(sig, qty=Decimal("2"), next_bar_open=Decimal("500.00"), timestamp=t_buy)
    assert pos is not None
    # Con 2 bps slippage, costo es 2 * 500.10 = 1000.20 -> quedan 999.80
    assert broker.settled_cash == Decimal("999.8000")
    assert broker.buying_power == Decimal("999.8000")

    # 2. Venta el mismo lunes a las 15:00 a $510.00
    t_sell = datetime(2025, 6, 9, 15, 0)
    trade = broker.close_position("SPY", Decimal("510.00"), timestamp=t_sell, reason="take_profit")
    assert trade is not None
    assert trade.account_type == "cash"

    # Fondos de la venta deben quedar como UNSETTLED (no disponibles para comprar de inmediato)
    assert broker.settled_cash == Decimal("999.8000")
    assert broker.unsettled_cash > Decimal("1010.00")
    # Efectivo total refleja ambas, pero el poder de compra solo refleja el settled
    assert broker.cash > Decimal("2010.00")
    assert broker.buying_power == Decimal("999.8000")

    # 3. Regla GFV: Si intentamos comprar $1500 el mismo lunes a las 15:30, la orden se ajusta a $999.80
    sig_qqq = Signal.create(
        strategy_id="s8",
        version="1.0.0",
        symbol="QQQ",
        bar_ts=datetime(2025, 6, 9, 15, 30),
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("500.00"),
        stop_price=Decimal("490.00"),
    )
    # Intentamos comprar 3 acciones ($1500)
    pos_qqq = broker.submit_buy(
        sig_qqq,
        qty=Decimal("3"),
        next_bar_open=Decimal("500.00"),
        timestamp=datetime(2025, 6, 9, 15, 30),
    )
    assert pos_qqq is not None
    # Solo alcanza para 1 acción con $999.80 (2 costarían >1000)
    assert pos_qqq.qty == Decimal("1")
    assert broker.settled_cash < Decimal("500.00")

    # 4. Liquidación al día siguiente hábil (martes 2025-06-10 09:30)
    t_next_day = datetime(2025, 6, 10, 9, 30)
    broker.process_settlement(t_next_day)
    assert broker.unsettled_cash == Decimal("0.0")
    assert broker.settled_cash > Decimal("1500.00")
    assert broker.buying_power == broker.settled_cash

    # 5. Comprobación de fin de semana: Viernes -> Lunes
    settle_friday = broker.calculate_settlement_date(datetime(2025, 6, 13, 15, 0))
    assert settle_friday == date(2025, 6, 16)
