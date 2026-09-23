#!/usr/bin/env python3
"""Informe Exhaustivo de Estrategias — Backtest y Comparación con S&P 500.

Ejecuta TODAS las estrategias (S1–S8) en la ventana temporal más larga disponible
según los datos que requiere cada una, compara contra SPY Buy & Hold y genera:
  1. Gráficos de rendimiento acumulado (matplotlib → PNG).
  2. Informe Markdown didáctico con tablas comparativas.
  3. Análisis de sobreajuste y recomendaciones.

Uso:
    python scripts/run_comprehensive_strategy_report.py
"""

from __future__ import annotations

import math
import os
import sys
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Backend sin GUI para generar PNGs
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

# Agregar backend al path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from tbot.backtest.guards import (
    DEV_DAILY_END,
    DEV_DAILY_START,
    DEV_HOURLY_END,
    DEV_HOURLY_START,
    SAMPLE_5M_END,
    SAMPLE_5M_START,
)
from tbot.backtest.metrics import BacktestMetrics, load_risk_free_rate_bil

from tbot.strategies import (
    DualMomentumLeaderStrategy,
    HourlyIntradayMultiHorizonStrategy,
    Intraday5mMultiHorizonStrategy,
    IntradayMomentumStrategy,
    MeanReversionRSI2Strategy,
    OpeningRangeBreakoutStrategy,
    S7PIDScorerStrategy,
    S8PIDMultihorizonStrategy,
    TrendPullbackStrategy,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuración
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INITIAL_CAPITAL = Decimal("2000.00")
REPORTS_DIR = PROJECT_ROOT / "reports"
CHARTS_DIR = REPORTS_DIR / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

# Universos
UNIVERSE_A_SYMBOLS = [
    "BIL", "GLDM", "IEFA", "IEF", "IEMG", "SLV", "SPY", "TIP",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
    "XLU", "XLV", "XLY",
]

# Universo 14 original de S5 (para datos daily de historical_2010_2026)
UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "JPM", "LLY", "XOM", "COST", "GLD", "SLV",
]

# Para S6/S8 con datos hourly de intraday_1h
UNIVERSE_HOURLY = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "IWM", "TSLA", "GLD", "AMD",
]

# Para S1/S4/S6 con datos 5min
UNIVERSE_5MIN = [
    "SPY", "AAPL", "MSFT", "AMZN", "META", "GLD", "SLV",
    "IWM", "TSLA", "XOM",
]

# Directorios de datos
DAILY_DATA_DIR = PROJECT_ROOT / "data" / "universe_a"
DAILY_2010_DIR = PROJECT_ROOT / "data" / "historical_2010_2026"
HOURLY_DATA_DIR = PROJECT_ROOT / "data" / "intraday_1h"
FIVEMIN_DATA_DIR = PROJECT_ROOT / "data" / "intraday_5min"
RF_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil_2010_2026.csv"
RF_FILE_ALT = PROJECT_ROOT / "data" / "risk_free_rate_bil.csv"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resultado por estrategia
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class StrategyReport:
    """Datos de un backtest completo para el informe."""
    name: str
    short_name: str
    description: str
    timeframe: str       # "daily", "hourly", "5min"
    start_date: date
    end_date: date
    metrics: BacktestMetrics | None = None
    equity_curve: pd.Series = field(default_factory=pd.Series)
    spy_equity: pd.Series = field(default_factory=pd.Series)
    chart_path: str = ""
    total_trades: int = 0
    error: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Funciones de carga de datos
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_daily_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Carga barras diarias para una lista de símbolos."""
    loader = HistoricalDataLoader(data_dir)
    data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        csv_path = data_dir / f"{sym}_daily.csv"
        if csv_path.exists():
            try:
                df = loader.load_from_csv(csv_path, symbol=sym, resolution="daily")
                data[sym] = df
            except Exception as e:
                print(f"  ⚠ Error cargando {sym} desde {csv_path}: {e}")
        else:
            print(f"  ⚠ Archivo no encontrado: {csv_path}")
    return data


def load_hourly_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Carga barras de 1 hora para una lista de símbolos."""
    loader = HistoricalDataLoader(data_dir)
    data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        csv_path = data_dir / f"{sym}_1h.csv"
        if csv_path.exists():
            try:
                df = loader.load_from_csv(csv_path, symbol=sym, resolution="1h")
                data[sym] = df
            except Exception as e:
                print(f"  ⚠ Error cargando {sym} desde {csv_path}: {e}")
        else:
            print(f"  ⚠ Archivo no encontrado: {csv_path}")
    return data


def load_5min_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Carga barras de 5 minutos para una lista de símbolos."""
    loader = HistoricalDataLoader(data_dir)
    data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        # Intentar CONSOLIDATED primero, luego yfinance
        for suffix in ["_5min_CONSOLIDATED.csv", "_5min_yfinance.csv", "_5m_CONSOLIDATED.csv", "_5m_yfinance.csv"]:
            csv_path = data_dir / f"{sym}{suffix}"
            if csv_path.exists():
                try:
                    df = loader.load_from_csv(csv_path, symbol=sym, resolution="5m")
                    data[sym] = df
                    break
                except Exception as e:
                    print(f"  ⚠ Error cargando {sym} desde {csv_path}: {e}")
    return data


def aggregate_daily_from_intraday(intraday: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Agrega barras intradía a barras diarias para alimentar el contexto diario del motor."""
    daily: dict[str, pd.DataFrame] = {}
    for sym, df in intraday.items():
        if df.empty:
            continue
        dfcopy = df.copy()
        ts_col = None
        for c in ["_parsed_ts", "timestamp", "datetime_et", "date"]:
            if c in dfcopy.columns:
                ts_col = c
                break
        if ts_col is None:
            continue
        dfcopy["_agg_date"] = pd.to_datetime(dfcopy[ts_col]).dt.date
        agg = (
            dfcopy.groupby("_agg_date")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .reset_index()
            .rename(columns={"_agg_date": "date"})
        )
        agg["date"] = pd.to_datetime(agg["date"])
        agg["symbol"] = sym
        daily[sym] = agg
    return daily


def compute_spy_buy_hold(spy_df: pd.DataFrame, start: date, end: date, capital: float = 2000.0) -> pd.Series:
    """Calcula la equity curve de SPY Buy & Hold en el periodo dado."""
    df = spy_df.copy()
    d_col = "date" if "date" in df.columns else "timestamp"
    df["_d"] = pd.to_datetime(df[d_col]).dt.date
    mask = (df["_d"] >= start) & (df["_d"] <= end)
    df = df[mask].sort_values("_d")
    if df.empty:
        return pd.Series(dtype=float)
    closes = df["close"].astype(float).values
    base = closes[0]
    equity = capital * (closes / base)
    return pd.Series(equity, index=pd.to_datetime(df["_d"].values))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gráfico comparativo
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def plot_equity_comparison(
    strat_eq: pd.Series,
    spy_eq: pd.Series,
    strat_name: str,
    save_path: Path,
    capital: float = 2000.0,
) -> None:
    """Genera un gráfico comparativo entre la estrategia y SPY Buy & Hold."""
    fig, ax = plt.subplots(figsize=(12, 5.5))

    # Normalizar ambas curvas para que empiecen en capital inicial
    if not strat_eq.empty:
        ax.plot(strat_eq.index, strat_eq.values, linewidth=1.6,
                label=strat_name, color="#2196F3")
    if not spy_eq.empty:
        ax.plot(spy_eq.index, spy_eq.values, linewidth=1.2,
                label="S&P 500 (SPY)", color="#888888", linestyle="--")

    ax.axhline(y=capital, color="#e0e0e0", linestyle=":", linewidth=0.8)
    ax.set_title(f"{strat_name}  vs  S&P 500 (SPY Buy & Hold)", fontsize=13, fontweight="bold")
    ax.set_ylabel("Valor del Portafolio (USD)", fontsize=10)
    ax.set_xlabel("Fecha", fontsize=10)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.25)

    # Formatear eje X
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=12))
    fig.autofmt_xdate(rotation=30)

    fig.tight_layout()
    fig.savefig(save_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  📊 Gráfico guardado: {save_path}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Ejecución de estrategias
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_strategy_backtest(
    strategy,
    daily_data: dict[str, pd.DataFrame],
    intraday_data: dict[str, pd.DataFrame] | None,
    universe: list[str],
    start: date,
    end: date,
    resolution: str = "daily",
    rf_series: pd.Series | None = None,
    extra_config: dict[str, Any] | None = None,
) -> BacktestResult | None:
    """Ejecuta un backtest con parámetros dados y retorna el resultado."""
    cfg_kwargs = dict(
        strategy=strategy,
        universe=universe,
        initial_capital=INITIAL_CAPITAL,
        account_type="cash",
        settlement_days=1,
        max_open_positions=4,
        single_position_cap=0.25,
        risk_per_trade_pct=0.5,
        enable_circuit_breakers=True,
        daily_loss_limit_pct=2.0,
        emergency_loss_limit_pct=3.5,
        integer_shares=True,
        apply_retail_costs=True,
        enable_cash_yield=True,
        rf_series=rf_series,
        enable_graduated_regime=True,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
    )
    if extra_config:
        cfg_kwargs.update(extra_config)

    config = BacktestConfig(**cfg_kwargs)

    engine = BacktestEngine(
        config=config,
        historical_daily=daily_data,
        historical_intraday=intraday_data or {},
    )

    result = engine.run(start_date=start, end_date=end, resolution=resolution)
    return result


def load_rf_series() -> pd.Series | None:
    """Carga la serie de tasa libre de riesgo BIL."""
    rf_path = RF_FILE if RF_FILE.exists() else (RF_FILE_ALT if RF_FILE_ALT.exists() else None)
    if rf_path is None:
        return None
    try:
        return load_risk_free_rate_bil(rf_path)
    except Exception:
        return load_risk_free_rate_bil()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN — Ejecutar todas las estrategias
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    print("=" * 80)
    print("  INFORME EXHAUSTIVO DE ESTRATEGIAS — TRADING BOT v2.0")
    print("=" * 80)

    reports: list[StrategyReport] = []
    rf_series = load_rf_series()

    # ─────────────────────────────────────────────────────────────────────
    # GRUPO 1: ESTRATEGIAS DIARIAS (ventana 2018-06-01 a 2022-12-31)
    # Universo A: 19 ETFs (datos desde 2018-06-01)
    # ─────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("GRUPO 1: Estrategias Diarias — Universo A (2018-06 a 2022-12)")
    print("─" * 70)

    # Fechas del dev window con datos de Universe A
    DAILY_START = date(2018, 9, 1)  # Dar margen para lookback de 250 días (SMA200)
    DAILY_END = DEV_DAILY_END  # 2022-12-31

    print(f"\n📅 Periodo: {DAILY_START} → {DAILY_END}")
    print(f"🏛️ Universo: {len(UNIVERSE_A_SYMBOLS)} ETFs")

    print("\n📂 Cargando datos diarios del Universo A...")
    daily_data_ua = load_daily_data(DAILY_DATA_DIR, UNIVERSE_A_SYMBOLS)
    print(f"   Cargados: {len(daily_data_ua)} tickers")

    # Adicional: cargar datos diarios del Universo 14 (para S5 con su universo nativo)
    print("📂 Cargando datos diarios del Universo 14 (2010-2026)...")
    daily_data_14 = load_daily_data(DAILY_2010_DIR, UNIVERSE_14)
    print(f"   Cargados: {len(daily_data_14)} tickers")

    # SPY para benchmark en ambos datasets
    spy_ua = daily_data_ua.get("SPY")
    spy_14 = daily_data_14.get("SPY")

    # Universo A tickers que no son BIL (BIL es cash, no se opera)
    UA_TRADEABLE = [s for s in UNIVERSE_A_SYMBOLS if s != "BIL"]

    # ── S2: Mean Reversion RSI(2) ────────────────────────────────────
    print("\n▶ S2 — Mean Reversion RSI(2) [Universo A, Diario]")
    try:
        strat_s2 = MeanReversionRSI2Strategy()
        result_s2 = run_strategy_backtest(
            strategy=strat_s2,
            daily_data=daily_data_ua,
            intraday_data=None,
            universe=UA_TRADEABLE,
            start=DAILY_START, end=DAILY_END,
            resolution="daily", rf_series=rf_series,
        )
        spy_eq_s2 = compute_spy_buy_hold(spy_ua, DAILY_START, DAILY_END, float(INITIAL_CAPITAL))
        chart_path_s2 = CHARTS_DIR / "s2_mean_reversion_rsi2.png"
        plot_equity_comparison(result_s2.equity_curve, spy_eq_s2, "S2 — Mean Reversion RSI(2)", chart_path_s2)
        reports.append(StrategyReport(
            name="S2 — Mean Reversion RSI(2)",
            short_name="S2",
            description=(
                "Compra cuando una acción está muy sobrevendida (RSI de 2 periodos < 10) "
                "pero sigue en tendencia alcista general (precio arriba de su media de 200 días). "
                "Vende cuando rebota a la media corta de 5 días o tras 3 días máximo."
            ),
            timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END,
            metrics=result_s2.metrics,
            equity_curve=result_s2.equity_curve,
            spy_equity=spy_eq_s2,
            chart_path=str(chart_path_s2.relative_to(REPORTS_DIR)),
            total_trades=result_s2.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s2.metrics.total_trades} trades, "
              f"Retorno: {result_s2.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S2 — Mean Reversion RSI(2)", short_name="S2",
            description="Error durante ejecución.", timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END, error=str(e),
        ))

    # ── S3: Trend Pullback ──────────────────────────────────────────
    print("\n▶ S3 — Trend Pullback [Universo A, Diario]")
    try:
        strat_s3 = TrendPullbackStrategy()
        result_s3 = run_strategy_backtest(
            strategy=strat_s3,
            daily_data=daily_data_ua,
            intraday_data=None,
            universe=UA_TRADEABLE,
            start=DAILY_START, end=DAILY_END,
            resolution="daily", rf_series=rf_series,
        )
        spy_eq_s3 = compute_spy_buy_hold(spy_ua, DAILY_START, DAILY_END, float(INITIAL_CAPITAL))
        chart_path_s3 = CHARTS_DIR / "s3_trend_pullback.png"
        plot_equity_comparison(result_s3.equity_curve, spy_eq_s3, "S3 — Trend Pullback", chart_path_s3)
        reports.append(StrategyReport(
            name="S3 — Trend Pullback",
            short_name="S3",
            description=(
                "Busca retrocesos ordenados hacia la media móvil de 20 días dentro de tendencias "
                "alcistas establecidas (EMA20 > EMA50). Vende con objetivo de ganancia a 2x el riesgo "
                "o después de 5 días."
            ),
            timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END,
            metrics=result_s3.metrics,
            equity_curve=result_s3.equity_curve,
            spy_equity=spy_eq_s3,
            chart_path=str(chart_path_s3.relative_to(REPORTS_DIR)),
            total_trades=result_s3.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s3.metrics.total_trades} trades, "
              f"Retorno: {result_s3.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S3 — Trend Pullback", short_name="S3",
            description="Error durante ejecución.", timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END, error=str(e),
        ))

    # ── S5: Dual Momentum Leader (Universo A) ────────────────────────
    print("\n▶ S5 — Dual Momentum Leader [Universo A, Diario]")
    try:
        strat_s5_ua = DualMomentumLeaderStrategy(
            universe=UA_TRADEABLE,
            top_n_leaders=4,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            max_holding_sessions=30,
        )
        result_s5_ua = run_strategy_backtest(
            strategy=strat_s5_ua,
            daily_data=daily_data_ua,
            intraday_data=None,
            universe=UA_TRADEABLE,
            start=DAILY_START, end=DAILY_END,
            resolution="daily", rf_series=rf_series,
        )
        spy_eq_s5 = compute_spy_buy_hold(spy_ua, DAILY_START, DAILY_END, float(INITIAL_CAPITAL))
        chart_path_s5 = CHARTS_DIR / "s5_dual_momentum_ua.png"
        plot_equity_comparison(result_s5_ua.equity_curve, spy_eq_s5, "S5 — Dual Momentum (Universo A)", chart_path_s5)
        reports.append(StrategyReport(
            name="S5 — Dual Momentum Leader (Universo A)",
            short_name="S5-UA",
            description=(
                "Selecciona los 4 ETFs con mayor impulso (momentum) a 45 días dentro de los 18 ETFs "
                "sectoriales, internacionales y metales del Universo A, siempre que estén sobre su media "
                "móvil de 25 días. En mercados bajistas, rota 100% a efectivo remunerado (~4.5% anual)."
            ),
            timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END,
            metrics=result_s5_ua.metrics,
            equity_curve=result_s5_ua.equity_curve,
            spy_equity=spy_eq_s5,
            chart_path=str(chart_path_s5.relative_to(REPORTS_DIR)),
            total_trades=result_s5_ua.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s5_ua.metrics.total_trades} trades, "
              f"Retorno: {result_s5_ua.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S5 — Dual Momentum Leader (Universo A)", short_name="S5-UA",
            description="Error durante ejecución.", timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END, error=str(e),
        ))

    # ── S5: Dual Momentum Leader (Universo 14 Original, 2010-2022) ───
    print("\n▶ S5 — Dual Momentum Leader [Universo 14, 2010-2022]")
    DAILY_14_START = date(2011, 1, 3)  # Margen para 250d lookback
    try:
        strat_s5_14 = DualMomentumLeaderStrategy(
            universe=UNIVERSE_14,
            top_n_leaders=4,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            max_holding_sessions=30,
        )
        result_s5_14 = run_strategy_backtest(
            strategy=strat_s5_14,
            daily_data=daily_data_14,
            intraday_data=None,
            universe=UNIVERSE_14,
            start=DAILY_14_START, end=DAILY_END,
            resolution="daily", rf_series=rf_series,
        )
        spy_eq_s5_14 = compute_spy_buy_hold(spy_14, DAILY_14_START, DAILY_END, float(INITIAL_CAPITAL))
        chart_path_s5_14 = CHARTS_DIR / "s5_dual_momentum_u14.png"
        plot_equity_comparison(result_s5_14.equity_curve, spy_eq_s5_14, "S5 — Dual Momentum (U14, 2011-2022)", chart_path_s5_14)
        reports.append(StrategyReport(
            name="S5 — Dual Momentum Leader (Universo 14, 2011-2022)",
            short_name="S5-U14",
            description=(
                "Selecciona los 4 activos líderes en momentum a 45 días dentro del universo de 14 activos "
                "(Tech+Finanzas+Salud+Energía+Oro+Plata). En mercados bajistas, se refugia en efectivo."
            ),
            timeframe="daily",
            start_date=DAILY_14_START, end_date=DAILY_END,
            metrics=result_s5_14.metrics,
            equity_curve=result_s5_14.equity_curve,
            spy_equity=spy_eq_s5_14,
            chart_path=str(chart_path_s5_14.relative_to(REPORTS_DIR)),
            total_trades=result_s5_14.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s5_14.metrics.total_trades} trades, "
              f"Retorno: {result_s5_14.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S5 — Dual Momentum Leader (Universo 14, 2011-2022)", short_name="S5-U14",
            description="Error durante ejecución.", timeframe="daily",
            start_date=DAILY_14_START, end_date=DAILY_END, error=str(e),
        ))

    # ── S5: Dual Momentum Leader (Universo A, 30d) ────────────────────────
    print("\n▶ S5 — Dual Momentum Leader [Universo A, 30d, Diario]")
    try:
        strat_s5_ua_30 = DualMomentumLeaderStrategy(
            universe=UA_TRADEABLE,
            top_n_leaders=4,
            momentum_lookback_days=30,
            trailing_ema_period=25,
            max_holding_sessions=30,
        )
        result_s5_ua_30 = run_strategy_backtest(
            strategy=strat_s5_ua_30,
            daily_data=daily_data_ua,
            intraday_data=None,
            universe=UA_TRADEABLE,
            start=DAILY_START, end=DAILY_END,
            resolution="daily", rf_series=rf_series,
        )
        spy_eq_s5_30 = compute_spy_buy_hold(spy_ua, DAILY_START, DAILY_END, float(INITIAL_CAPITAL))
        chart_path_s5_30 = CHARTS_DIR / "s5_dual_momentum_ua_30d.png"
        plot_equity_comparison(result_s5_ua_30.equity_curve, spy_eq_s5_30, "S5 — Dual Momentum 30d (Universo A)", chart_path_s5_30)
        reports.append(StrategyReport(
            name="S5 — Dual Momentum Leader 30d (Universo A)",
            short_name="S5-UA-30d",
            description=(
                "Igual a S5 original, pero usa un periodo de momentum más corto de 30 días para "
                "adaptarse más rápidamente a los cambios de tendencia y evaluar su sensibilidad "
                "(robustez) ante cambios de parámetros."
            ),
            timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END,
            metrics=result_s5_ua_30.metrics,
            equity_curve=result_s5_ua_30.equity_curve,
            spy_equity=spy_eq_s5_30,
            chart_path=str(chart_path_s5_30.relative_to(REPORTS_DIR)),
            total_trades=result_s5_ua_30.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s5_ua_30.metrics.total_trades} trades, "
              f"Retorno: {result_s5_ua_30.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S5 — Dual Momentum Leader 30d (Universo A)", short_name="S5-UA-30d",
            description="Error durante ejecución.", timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END, error=str(e),
        ))

    # ── S7: PID Scorer ──────────────────────────────────────────────
    print("\n▶ S7 — PID Scorer [Universo A, Diario]")
    try:
        strat_s7 = S7PIDScorerStrategy(top_n=4)
        result_s7 = run_strategy_backtest(
            strategy=strat_s7,
            daily_data=daily_data_ua,
            intraday_data=None,
            universe=UA_TRADEABLE,
            start=DAILY_START, end=DAILY_END,
            resolution="daily", rf_series=rf_series,
        )
        spy_eq_s7 = compute_spy_buy_hold(spy_ua, DAILY_START, DAILY_END, float(INITIAL_CAPITAL))
        chart_path_s7 = CHARTS_DIR / "s7_pid_scorer.png"
        plot_equity_comparison(result_s7.equity_curve, spy_eq_s7, "S7 — PID Scorer", chart_path_s7)
        reports.append(StrategyReport(
            name="S7 — PID Scorer",
            short_name="S7",
            description=(
                "Sistema de control dual inspirado en ingeniería (PID: Proporcional-Integral-Derivativo). "
                "Mide la fuerza de tendencia (Sistema U) y el nivel de estrés/deterioro (Sistema D) de cada "
                "activo. Solo compra activos con tendencia fuerte y bajo estrés. Sale forzadamente si "
                "el estrés se dispara."
            ),
            timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END,
            metrics=result_s7.metrics,
            equity_curve=result_s7.equity_curve,
            spy_equity=spy_eq_s7,
            chart_path=str(chart_path_s7.relative_to(REPORTS_DIR)),
            total_trades=result_s7.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s7.metrics.total_trades} trades, "
              f"Retorno: {result_s7.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S7 — PID Scorer", short_name="S7",
            description="Error durante ejecución.", timeframe="daily",
            start_date=DAILY_START, end_date=DAILY_END, error=str(e),
        ))

    # ─────────────────────────────────────────────────────────────────────
    # GRUPO 2: ESTRATEGIAS DE 1 HORA (ventana 2023-10-23 a 2025-09-21)
    # ─────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("GRUPO 2: Estrategias de 1 Hora (2023-10 a 2025-09)")
    print("─" * 70)

    HOURLY_START = DEV_HOURLY_START  # 2023-10-23
    HOURLY_END = DEV_HOURLY_END     # 2025-09-21

    print(f"\n📅 Periodo: {HOURLY_START} → {HOURLY_END}")
    print(f"🏛️ Universo disponible: {len(UNIVERSE_HOURLY)} tickers")

    print("\n📂 Cargando datos de 1 hora...")
    hourly_data = load_hourly_data(HOURLY_DATA_DIR, UNIVERSE_HOURLY)
    print(f"   Cargados: {len(hourly_data)} tickers")

    # Agregar barras diarias desde hourly
    print("📂 Generando barras diarias agregadas desde datos horarios...")
    daily_from_hourly = aggregate_daily_from_intraday(hourly_data)
    print(f"   Generadas para: {len(daily_from_hourly)} tickers")

    # SPY benchmark para hourly
    spy_h = daily_from_hourly.get("SPY")

    # ── S6 Hourly: Intraday Multi-Horizon (1h) ──────────────────────
    print("\n▶ S6 — Hourly Multi-Horizon Momentum [1h]")
    try:
        strat_s6h = HourlyIntradayMultiHorizonStrategy()
        result_s6h = run_strategy_backtest(
            strategy=strat_s6h,
            daily_data=daily_from_hourly,
            intraday_data=hourly_data,
            universe=UNIVERSE_HOURLY,
            start=HOURLY_START, end=HOURLY_END,
            resolution="1h", rf_series=rf_series,
            extra_config={
                "intraday_start_time": time(9, 30),
                "intraday_end_time": time(14, 30),
                "flatten_time": time(15, 55),
            },
        )
        spy_eq_s6h = compute_spy_buy_hold(spy_h, HOURLY_START, HOURLY_END, float(INITIAL_CAPITAL)) if spy_h is not None else pd.Series(dtype=float)
        chart_path_s6h = CHARTS_DIR / "s6_hourly_momentum.png"
        plot_equity_comparison(result_s6h.equity_curve, spy_eq_s6h, "S6 — Hourly Multi-Horizon", chart_path_s6h)
        reports.append(StrategyReport(
            name="S6 — Hourly Multi-Horizon Momentum",
            short_name="S6-1h",
            description=(
                "Cada hora analiza el impulso de los últimos 1h, 2h, 3h, 4h y 7h (sesión completa) "
                "de cada activo, los ordena por fuerza combinada y compra los 3 mejores. "
                "Cierra todas las posiciones antes del cierre de cada día para evitar riesgo nocturno."
            ),
            timeframe="hourly",
            start_date=HOURLY_START, end_date=HOURLY_END,
            metrics=result_s6h.metrics,
            equity_curve=result_s6h.equity_curve,
            spy_equity=spy_eq_s6h,
            chart_path=str(chart_path_s6h.relative_to(REPORTS_DIR)),
            total_trades=result_s6h.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s6h.metrics.total_trades} trades, "
              f"Retorno: {result_s6h.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S6 — Hourly Multi-Horizon Momentum", short_name="S6-1h",
            description="Error durante ejecución.", timeframe="hourly",
            start_date=HOURLY_START, end_date=HOURLY_END, error=str(e),
        ))

    # ── S8: PID Multi-Horizon (Hourly + Daily) ───────────────────────
    print("\n▶ S8 — PID Multi-Horizon [Variante A, 1h]")
    try:
        strat_s8a = S8PIDMultihorizonStrategy(weight_rule="A", top_n=4)
        result_s8a = run_strategy_backtest(
            strategy=strat_s8a,
            daily_data=daily_from_hourly,
            intraday_data=hourly_data,
            universe=UNIVERSE_HOURLY,
            start=HOURLY_START, end=HOURLY_END,
            resolution="1h", rf_series=rf_series,
            extra_config={
                "intraday_start_time": time(9, 30),
                "intraday_end_time": time(15, 30),
            },
        )
        spy_eq_s8a = compute_spy_buy_hold(spy_h, HOURLY_START, HOURLY_END, float(INITIAL_CAPITAL)) if spy_h is not None else pd.Series(dtype=float)
        chart_path_s8a = CHARTS_DIR / "s8_pid_multihorizon_A.png"
        plot_equity_comparison(result_s8a.equity_curve, spy_eq_s8a, "S8 — PID Multi-Horizon (Var. A)", chart_path_s8a)
        reports.append(StrategyReport(
            name="S8 — PID Multi-Horizon (Variante A)",
            short_name="S8-A",
            description=(
                "Extensión del PID Scorer que combina 8 horizontes temporales (desde 5 minutos hasta "
                "10 días) para medir tendencia y estrés. La Variante A da más peso a los horizontes "
                "largos (visión macro), combinando datos diarios y de 1 hora."
            ),
            timeframe="hourly",
            start_date=HOURLY_START, end_date=HOURLY_END,
            metrics=result_s8a.metrics,
            equity_curve=result_s8a.equity_curve,
            spy_equity=spy_eq_s8a,
            chart_path=str(chart_path_s8a.relative_to(REPORTS_DIR)),
            total_trades=result_s8a.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s8a.metrics.total_trades} trades, "
              f"Retorno: {result_s8a.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S8 — PID Multi-Horizon (Variante A)", short_name="S8-A",
            description="Error durante ejecución.", timeframe="hourly",
            start_date=HOURLY_START, end_date=HOURLY_END, error=str(e),
        ))

    print("\n▶ S8 — PID Multi-Horizon [Variante B, 1h]")
    try:
        strat_s8b = S8PIDMultihorizonStrategy(weight_rule="B", top_n=4)
        result_s8b = run_strategy_backtest(
            strategy=strat_s8b,
            daily_data=daily_from_hourly,
            intraday_data=hourly_data,
            universe=UNIVERSE_HOURLY,
            start=HOURLY_START, end=HOURLY_END,
            resolution="1h", rf_series=rf_series,
            extra_config={
                "intraday_start_time": time(9, 30),
                "intraday_end_time": time(15, 30),
            },
        )
        spy_eq_s8b = compute_spy_buy_hold(spy_h, HOURLY_START, HOURLY_END, float(INITIAL_CAPITAL)) if spy_h is not None else pd.Series(dtype=float)
        chart_path_s8b = CHARTS_DIR / "s8_pid_multihorizon_B.png"
        plot_equity_comparison(result_s8b.equity_curve, spy_eq_s8b, "S8 — PID Multi-Horizon (Var. B)", chart_path_s8b)
        reports.append(StrategyReport(
            name="S8 — PID Multi-Horizon (Variante B)",
            short_name="S8-B",
            description=(
                "Igual que Variante A, pero usa pesos logarítmicos que dan más importancia relativa "
                "a los horizontes intermedios (30min-2h). Busca capturar movimientos intradía con "
                "más peso en la acción de precio reciente."
            ),
            timeframe="hourly",
            start_date=HOURLY_START, end_date=HOURLY_END,
            metrics=result_s8b.metrics,
            equity_curve=result_s8b.equity_curve,
            spy_equity=spy_eq_s8b,
            chart_path=str(chart_path_s8b.relative_to(REPORTS_DIR)),
            total_trades=result_s8b.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s8b.metrics.total_trades} trades, "
              f"Retorno: {result_s8b.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S8 — PID Multi-Horizon (Variante B)", short_name="S8-B",
            description="Error durante ejecución.", timeframe="hourly",
            start_date=HOURLY_START, end_date=HOURLY_END, error=str(e),
        ))

    # ─────────────────────────────────────────────────────────────────────
    # GRUPO 3: ESTRATEGIAS DE 5 MINUTOS (ventana diagnóstica 2026-06-26 a 2026-09-21)
    # Nota: Ventana diagnóstica corta (~60 sesiones), no apta para selección de modelos
    # ─────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("GRUPO 3: Estrategias de 5 Minutos (ventana diagnóstica, ~60 sesiones)")
    print("─" * 70)

    FIVEMIN_START = SAMPLE_5M_START  # 2026-06-26
    FIVEMIN_END = SAMPLE_5M_END     # 2026-09-21

    print(f"\n📅 Periodo: {FIVEMIN_START} → {FIVEMIN_END}")
    print(f"⚠️  Nota: Ventana diagnóstica corta. Resultados no aptos para selección de modelos.")

    print("\n📂 Cargando datos de 5 minutos...")
    fivemin_data = load_5min_data(FIVEMIN_DATA_DIR, UNIVERSE_5MIN)
    print(f"   Cargados: {len(fivemin_data)} tickers")

    daily_from_5m = aggregate_daily_from_intraday(fivemin_data)
    spy_5m = daily_from_5m.get("SPY")

    # ── S1: Intraday Momentum ───────────────────────────────────────
    print("\n▶ S1 — Intraday Momentum [5min]")
    try:
        strat_s1 = IntradayMomentumStrategy()
        # S1 opera solo SPY y QQQ; usar lo que haya disponible
        s1_universe = [s for s in ["SPY", "QQQ"] if s in fivemin_data]
        result_s1 = run_strategy_backtest(
            strategy=strat_s1,
            daily_data=daily_from_5m,
            intraday_data=fivemin_data,
            universe=s1_universe,
            start=FIVEMIN_START, end=FIVEMIN_END,
            resolution="5m", rf_series=rf_series,
            extra_config={
                "intraday_start_time": time(9, 30),
                "intraday_end_time": time(15, 58),
                "flatten_time": time(15, 58),
            },
        )
        spy_eq_s1 = compute_spy_buy_hold(spy_5m, FIVEMIN_START, FIVEMIN_END, float(INITIAL_CAPITAL)) if spy_5m is not None else pd.Series(dtype=float)
        chart_path_s1 = CHARTS_DIR / "s1_intraday_momentum.png"
        plot_equity_comparison(result_s1.equity_curve, spy_eq_s1, "S1 — Intraday Momentum (5min)", chart_path_s1)
        reports.append(StrategyReport(
            name="S1 — Intraday Momentum",
            short_name="S1",
            description=(
                "Apuesta a que el impulso de la primera media hora del día se repite en la última "
                "media hora. Si el mercado subió entre 9:30 y 10:00, compra a las 15:30 y vende "
                "a las 15:58. Operaciones de solo 28 minutos de duración."
            ),
            timeframe="5min",
            start_date=FIVEMIN_START, end_date=FIVEMIN_END,
            metrics=result_s1.metrics,
            equity_curve=result_s1.equity_curve,
            spy_equity=spy_eq_s1,
            chart_path=str(chart_path_s1.relative_to(REPORTS_DIR)),
            total_trades=result_s1.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s1.metrics.total_trades} trades, "
              f"Retorno: {result_s1.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S1 — Intraday Momentum", short_name="S1",
            description="Error durante ejecución.", timeframe="5min",
            start_date=FIVEMIN_START, end_date=FIVEMIN_END, error=str(e),
        ))

    # ── S4: Opening Range Breakout ─────────────────────────────────
    print("\n▶ S4 — Opening Range Breakout [5min]")
    try:
        strat_s4 = OpeningRangeBreakoutStrategy()
        s4_universe = [s for s in ["SPY", "QQQ"] if s in fivemin_data]
        result_s4 = run_strategy_backtest(
            strategy=strat_s4,
            daily_data=daily_from_5m,
            intraday_data=fivemin_data,
            universe=s4_universe,
            start=FIVEMIN_START, end=FIVEMIN_END,
            resolution="5m", rf_series=rf_series,
            extra_config={
                "intraday_start_time": time(9, 30),
                "intraday_end_time": time(15, 55),
                "flatten_time": time(15, 55),
            },
        )
        spy_eq_s4 = compute_spy_buy_hold(spy_5m, FIVEMIN_START, FIVEMIN_END, float(INITIAL_CAPITAL)) if spy_5m is not None else pd.Series(dtype=float)
        chart_path_s4 = CHARTS_DIR / "s4_orb.png"
        plot_equity_comparison(result_s4.equity_curve, spy_eq_s4, "S4 — Opening Range Breakout (5min)", chart_path_s4)
        reports.append(StrategyReport(
            name="S4 — Opening Range Breakout (ORB)",
            short_name="S4",
            description=(
                "Identifica la vela de los primeros 5 minutos de la sesión (9:30-9:35). "
                "Si el precio supera el máximo de esa vela con volumen elevado (>1.5x normal), "
                "compra. Objetivo de ganancia a 2x el riesgo o cierre al final del día."
            ),
            timeframe="5min",
            start_date=FIVEMIN_START, end_date=FIVEMIN_END,
            metrics=result_s4.metrics,
            equity_curve=result_s4.equity_curve,
            spy_equity=spy_eq_s4,
            chart_path=str(chart_path_s4.relative_to(REPORTS_DIR)),
            total_trades=result_s4.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s4.metrics.total_trades} trades, "
              f"Retorno: {result_s4.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S4 — Opening Range Breakout (ORB)", short_name="S4",
            description="Error durante ejecución.", timeframe="5min",
            start_date=FIVEMIN_START, end_date=FIVEMIN_END, error=str(e),
        ))

    # ── S6 5min: Intraday Multi-Horizon (5min) ──────────────────────
    print("\n▶ S6 — 5min Multi-Horizon Momentum [5min]")
    try:
        strat_s6_5m = Intraday5mMultiHorizonStrategy()
        s6_5m_universe = [s for s in UNIVERSE_5MIN if s in fivemin_data]
        result_s6_5m = run_strategy_backtest(
            strategy=strat_s6_5m,
            daily_data=daily_from_5m,
            intraday_data=fivemin_data,
            universe=s6_5m_universe,
            start=FIVEMIN_START, end=FIVEMIN_END,
            resolution="5m", rf_series=rf_series,
            extra_config={
                "intraday_start_time": time(9, 45),
                "intraday_end_time": time(15, 30),
                "flatten_time": time(15, 55),
            },
        )
        spy_eq_s6_5m = compute_spy_buy_hold(spy_5m, FIVEMIN_START, FIVEMIN_END, float(INITIAL_CAPITAL)) if spy_5m is not None else pd.Series(dtype=float)
        chart_path_s6_5m = CHARTS_DIR / "s6_5min_momentum.png"
        plot_equity_comparison(result_s6_5m.equity_curve, spy_eq_s6_5m, "S6 — 5min Multi-Horizon", chart_path_s6_5m)
        reports.append(StrategyReport(
            name="S6 — 5min Multi-Horizon Momentum",
            short_name="S6-5m",
            description=(
                "Cada 5 minutos evalúa el impulso a 10min, 15min, 30min, 45min y 60min de cada activo, "
                "los clasifica y compra los 3 con mejor ranking combinado. Cierra todo antes del cierre."
            ),
            timeframe="5min",
            start_date=FIVEMIN_START, end_date=FIVEMIN_END,
            metrics=result_s6_5m.metrics,
            equity_curve=result_s6_5m.equity_curve,
            spy_equity=spy_eq_s6_5m,
            chart_path=str(chart_path_s6_5m.relative_to(REPORTS_DIR)),
            total_trades=result_s6_5m.metrics.total_trades,
        ))
        print(f"   ✅ Completado: {result_s6_5m.metrics.total_trades} trades, "
              f"Retorno: {result_s6_5m.metrics.total_return_pct:.2f}%")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        reports.append(StrategyReport(
            name="S6 — 5min Multi-Horizon Momentum", short_name="S6-5m",
            description="Error durante ejecución.", timeframe="5min",
            start_date=FIVEMIN_START, end_date=FIVEMIN_END, error=str(e),
        ))

    # ─────────────────────────────────────────────────────────────────────
    # Generar gráfico comparativo consolidado por grupo
    # ─────────────────────────────────────────────────────────────────────
    for group_name, tf in [("Diarias", "daily"), ("Horarias", "hourly"), ("5 Minutos", "5min")]:
        group = [r for r in reports if r.timeframe == tf and not r.error]
        if len(group) < 2:
            continue
        fig, ax = plt.subplots(figsize=(14, 6))
        colors = ["#2196F3", "#4CAF50", "#FF9800", "#E91E63", "#9C27B0", "#00BCD4", "#795548"]
        for i, r in enumerate(group):
            if not r.equity_curve.empty:
                # Normalizar a retorno %
                base = float(r.equity_curve.iloc[0]) if r.equity_curve.iloc[0] != 0 else 1
                pct = (r.equity_curve / base - 1) * 100
                ax.plot(pct.index, pct.values, linewidth=1.4,
                        label=r.short_name, color=colors[i % len(colors)])
        # SPY benchmark
        spy_ref = group[0].spy_equity
        if not spy_ref.empty:
            base_spy = float(spy_ref.iloc[0]) if spy_ref.iloc[0] != 0 else 1
            pct_spy = (spy_ref / base_spy - 1) * 100
            ax.plot(pct_spy.index, pct_spy.values, linewidth=1.2,
                    label="SPY B&H", color="#888888", linestyle="--")
        ax.axhline(y=0, color="#e0e0e0", linestyle=":", linewidth=0.8)
        ax.set_title(f"Comparativa — Estrategias {group_name}", fontsize=13, fontweight="bold")
        ax.set_ylabel("Rendimiento (%)", fontsize=10)
        ax.legend(loc="best", fontsize=8)
        ax.grid(alpha=0.25)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()
        save_p = CHARTS_DIR / f"comparativa_{tf}.png"
        fig.savefig(save_p, dpi=140, bbox_inches="tight")
        plt.close(fig)
        print(f"\n📊 Gráfico comparativo '{group_name}' guardado: {save_p}")

    # ─────────────────────────────────────────────────────────────────────
    # Generar informe Markdown
    # ─────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  GENERANDO INFORME MARKDOWN...")
    print("=" * 70)

    md = generate_markdown_report(reports)
    report_path = REPORTS_DIR / "informe_exhaustivo_estrategias.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"\n✅ Informe guardado: {report_path}")
    print(f"📊 Gráficos en: {CHARTS_DIR}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Generador de Markdown
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:+.2f}%"


def _fmt_ratio(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:.2f}"


def generate_markdown_report(reports: list[StrategyReport]) -> str:
    """Genera el informe Markdown completo."""
    lines: list[str] = []

    lines.append("# 📊 Informe Exhaustivo de Estrategias — Trading Bot v2.0\n")
    lines.append(f"**Fecha de generación:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"**Capital inicial:** \\$2,000.00 USD\n")
    lines.append("")
    lines.append("---\n")
    lines.append("## 📋 Resumen Ejecutivo\n")
    lines.append("Este informe compara **todas las estrategias del Trading Bot** contra el rendimiento ")
    lines.append("del S&P 500 (SPY) en el mayor periodo de datos disponible para cada una. ")
    lines.append("Se divide en tres grupos según el tipo de datos que necesita cada estrategia:\n")
    lines.append("1. **Estrategias Diarias** — Analizan precios de cierre diarios (periodo más largo: 2018-2022 o 2011-2022)")
    lines.append("2. **Estrategias Horarias** — Analizan precios cada hora (2023-2025)")
    lines.append("3. **Estrategias de 5 Minutos** — Analizan precios cada 5 minutos (ventana diagnóstica corta de ~60 sesiones)\n")
    lines.append("> **⚠️ Nota importante:** Los resultados de estrategias de 5 minutos son de una ventana")
    lines.append("> diagnóstica muy corta (~3 meses) y **no deben usarse para tomar decisiones** sobre qué")
    lines.append("> estrategia es \"mejor\". Sirven solo para verificar que el motor funciona correctamente.\n")
    lines.append("---\n")

    # ────────────────────────────────────────────────────────────
    # Sección por cada estrategia
    # ────────────────────────────────────────────────────────────
    for tf_name, tf_key in [("Diarias", "daily"), ("Horarias (1h)", "hourly"), ("5 Minutos", "5min")]:
        group = [r for r in reports if r.timeframe == tf_key]
        if not group:
            continue

        lines.append(f"## 📈 Grupo: Estrategias {tf_name}\n")

        for r in group:
            lines.append(f"### {r.name}\n")
            lines.append(f"**📅 Periodo:** {r.start_date} → {r.end_date}\n")
            lines.append(f"**📝 ¿Qué hace?** {r.description}\n")

            if r.error:
                lines.append(f"> **❌ Error durante la ejecución:** `{r.error}`\n")
                lines.append("---\n")
                continue

            m = r.metrics
            if m is None:
                lines.append("> No se obtuvieron métricas.\n")
                lines.append("---\n")
                continue

            # Calcular retorno del SPY en el mismo periodo
            spy_ret = 0.0
            if not r.spy_equity.empty and len(r.spy_equity) > 1:
                spy_ret = (float(r.spy_equity.iloc[-1]) / float(r.spy_equity.iloc[0]) - 1) * 100

            alpha = m.total_return_pct - spy_ret

            lines.append("| Métrica | Valor |")
            lines.append("|---|---|")
            lines.append(f"| Capital Final | \\${m.final_capital:,.2f} |")
            lines.append(f"| Rendimiento Total | {_fmt_pct(m.total_return_pct)} |")
            lines.append(f"| S&P 500 (mismo periodo) | {_fmt_pct(spy_ret)} |")
            lines.append(f"| **Alpha vs S&P 500** | **{_fmt_pct(alpha)}** |")
            if m.cagr_pct is not None:
                lines.append(f"| Rendimiento Anualizado (CAGR) | {_fmt_pct(m.cagr_pct)} |")
            lines.append(f"| Ratio Sharpe | {_fmt_ratio(m.sharpe_ratio)} |")
            lines.append(f"| Ratio Sortino | {_fmt_ratio(m.sortino_ratio)} |")
            lines.append(f"| Máxima Caída (Drawdown) | {m.max_drawdown_pct:.2f}% |")
            lines.append(f"| Total de Operaciones | {m.total_trades} |")
            lines.append(f"| Tasa de Aciertos | {m.win_rate_pct:.1f}% |")
            lines.append(f"| Factor de Beneficio | {_fmt_ratio(m.profit_factor)} |")
            lines.append(f"| Comisiones Pagadas | \\${m.total_fees_paid:.2f} |")
            lines.append("")

            # Gráfico
            if r.chart_path:
                lines.append(f"![{r.name}]({r.chart_path})\n")

            # Análisis
            lines.append("**🔍 Análisis del resultado:**\n")
            if m.total_trades == 0:
                lines.append("Esta estrategia no generó operaciones en el periodo evaluado. ")
                lines.append("Esto puede deberse a que sus condiciones de entrada son muy restrictivas ")
                lines.append("o a que el régimen de mercado bloqueó las señales durante todo el periodo.\n")
            elif m.total_return_pct > spy_ret and m.total_return_pct > 0:
                lines.append(f"✅ **Resultado positivo.** La estrategia superó al S&P 500 por {alpha:.2f} puntos ")
                lines.append(f"porcentuales. Generó {m.total_trades} operaciones con una tasa de aciertos del ")
                lines.append(f"{m.win_rate_pct:.1f}%.\n")
            elif m.total_return_pct > 0:
                lines.append(f"⚖️ **Resultado mixto.** La estrategia fue rentable ({m.total_return_pct:.2f}%) ")
                lines.append(f"pero quedó por debajo del S&P 500 ({spy_ret:.2f}%). ")
                lines.append(f"Esto significa que habría sido más rentable simplemente comprar y mantener SPY.\n")
            else:
                lines.append(f"❌ **Resultado negativo.** La estrategia perdió {abs(m.total_return_pct):.2f}% ")
                lines.append(f"del capital. Con {m.total_trades} operaciones y una tasa de aciertos del ")
                lines.append(f"{m.win_rate_pct:.1f}%, los costos de comisiones (\\${m.total_fees_paid:.2f}) y el ")
                lines.append(f"deslizamiento de precios contribuyeron a la pérdida.\n")

            # Sobreajuste
            lines.append("**🎯 ¿Tiene sobreajuste?**\n")
            if tf_key == "5min":
                lines.append("⚠️ **No se puede evaluar.** Con solo ~60 sesiones de datos, la ventana es demasiado ")
                lines.append("corta para sacar conclusiones sobre sobreajuste. Estos resultados son puramente diagnósticos.\n")
            elif m.total_trades < 30:
                lines.append(f"⚠️ **Riesgo alto de sobreajuste.** Con solo {m.total_trades} operaciones, ")
                lines.append("la muestra es demasiado pequeña para tener confianza estadística en los resultados. ")
                lines.append("Un resultado aparentemente bueno podría deberse al azar.\n")
            elif m.sharpe_ratio is not None and m.sharpe_ratio > 3.0:
                lines.append(f"🚩 **Posible sobreajuste.** Un Sharpe de {m.sharpe_ratio:.2f} es inusualmente alto y ")
                lines.append("sugiere que la estrategia podría estar \"memorizando\" patrones históricos que no ")
                lines.append("se repetirán en el futuro. Resultado sospechoso.\n")
            elif m.deflated_sharpe_ratio < 0.5:
                lines.append(f"⚠️ **Posible sobreajuste.** El Deflated Sharpe Ratio ({m.deflated_sharpe_ratio:.2f}) ")
                lines.append("es bajo, lo que sugiere que el resultado podría ser producto del azar o del sesgo ")
                lines.append("de selección entre múltiples pruebas.\n")
            else:
                lines.append("✅ **Sin señales evidentes de sobreajuste.** Los parámetros de la estrategia son ")
                lines.append("razonables y no hay indicios claros de memorización de datos históricos. ")
                lines.append("Sin embargo, solo la validación forward (paper trading) puede confirmarlo.\n")

            lines.append("---\n")

        # Tabla comparativa del grupo
        valid_group = [r for r in group if not r.error and r.metrics is not None]
        if len(valid_group) >= 2:
            lines.append(f"### 📊 Tabla Comparativa — Estrategias {tf_name}\n")

            # SPY benchmark
            spy_ret = 0.0
            ref = valid_group[0]
            if not ref.spy_equity.empty and len(ref.spy_equity) > 1:
                spy_ret = (float(ref.spy_equity.iloc[-1]) / float(ref.spy_equity.iloc[0]) - 1) * 100

            headers = ["Estrategia", "Retorno", "Alpha vs SPY", "Sharpe", "Sortino", "Max DD", "Trades", "Win Rate", "Profit F."]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

            for r in valid_group:
                m = r.metrics
                spy_r = 0.0
                if not r.spy_equity.empty and len(r.spy_equity) > 1:
                    spy_r = (float(r.spy_equity.iloc[-1]) / float(r.spy_equity.iloc[0]) - 1) * 100
                alpha = m.total_return_pct - spy_r
                row = [
                    f"**{r.short_name}**",
                    _fmt_pct(m.total_return_pct),
                    _fmt_pct(alpha),
                    _fmt_ratio(m.sharpe_ratio),
                    _fmt_ratio(m.sortino_ratio),
                    f"{m.max_drawdown_pct:.1f}%",
                    str(m.total_trades),
                    f"{m.win_rate_pct:.0f}%",
                    _fmt_ratio(m.profit_factor),
                ]
                lines.append("| " + " | ".join(row) + " |")

            # Fila SPY
            row_spy = [
                "**SPY B&H**",
                _fmt_pct(spy_ret),
                "+0.00%",
                "—", "—", "—", "1", "—", "—",
            ]
            lines.append("| " + " | ".join(row_spy) + " |")
            lines.append("")

            # Gráfico comparativo
            comp_chart = CHARTS_DIR / f"comparativa_{tf_key}.png"
            if comp_chart.exists():
                lines.append(f"![Comparativa {tf_name}](charts/comparativa_{tf_key}.png)\n")
            lines.append("---\n")

    # ────────────────────────────────────────────────────────────
    # Sección: Investigación y Recomendaciones
    # ────────────────────────────────────────────────────────────
    lines.append("## 🔬 Análisis de Estrategias y Recomendaciones\n")
    lines.append("### ¿Qué estrategias deberíamos descartar?\n")
    lines.append("""
Basándose en la teoría financiera y los resultados observados:

1. **S1 (Intraday Momentum de 28 minutos):** Esta estrategia explota una anomalía muy estudiada
   (que la primera media hora predice la última). La evidencia académica sugiere que esta anomalía
   ha disminuido significativamente desde que fue publicada. Además, con solo 28 minutos de exposición
   por día, las comisiones y el deslizamiento de precios pueden consumir fácilmente las ganancias
   en una cuenta pequeña (\\$2,000). **Recomendación: Descartar o usar solo como complemento
   con capital mayor.**

2. **S4 (Opening Range Breakout):** Las rupturas de rango de apertura son una de las estrategias
   más antiguas y más «abarrotadas» del mercado. Demasiados traders las usan, lo que reduce su
   efectividad. Con capital de \\$2,000, las comisiones hacen muy difícil ser rentable.
   **Recomendación: Mantener en monitoreo, pero no priorizar.**

### ¿Qué estrategias tienen potencial de mejora?\n

1. **S5 (Dual Momentum Leader):** Es la estrategia principal y la más robusta teóricamente.
   El momentum es una de las anomalías más documentadas y persistentes en las finanzas.
   **Mejoras posibles sin sobreajuste:**
   - *Para el usuario:* Probar con diferentes periodos de momentum (30, 45, 60 días) y verificar
     que los resultados no sean sensibles a cambios pequeños. Si lo son, hay riesgo de sobreajuste.
   - *Técnico:* Implementar volatility scaling (ajustar tamaño de posición según la volatilidad
     reciente de cada activo) usando la inversa de la volatilidad a 60 días como ponderador.

2. **S7 (PID Scorer):** El concepto de sistema de control dual es innovador, pero su complejidad
   puede esconder sobreajuste.
   - *Para el usuario:* Simplificar puede ser mejor que complicar. Si S7 no supera a S5 de forma
     consistente, la complejidad extra no se justifica.
   - *Técnico:* Reducir el número de features del Sistema D de 5 a 3 (los más independientes) para
     reducir dimensionalidad y riesgo de sobreajuste.

3. **S2 (Mean Reversion RSI-2):** Estrategia clásica con fuerte base teórica (Connors Research).
   - *Para el usuario:* Funciona mejor como complemento de S5, ya que opera en condiciones de
     mercado opuestas (compra caídas vs. compra ganadores).
   - *Técnico:* Agregar un filtro de volumen (comprar solo si el volumen del día de la caída
     es > 1.5x el promedio de 20 días) para mejorar la calidad de las señales.

### ¿Qué estrategias nuevas podríamos probar?\n

#### Para el usuario (explicación simple):

1. **Momentum + Estacionalidad (Turn-of-Month):** Históricamente, los últimos 3 días del mes y
   los primeros 3 del siguiente tienden a ser positivos. Combinar esto con nuestro filtro de momentum
   podría mejorar el timing de entradas.

2. **Momentum Relativo entre Activos y Bonos (Dual Momentum de Antonacci):** En lugar de solo
   comparar acciones entre sí, comparar el retorno de las mejores acciones vs. bonos del Tesoro.
   Solo comprar acciones si superan a los bonos; si no, quedarse en bonos. Ya tenemos datos de IEF
   y TIP en Universo A para esto.

3. **Carry + Momentum:** Combinar el momentum de precios con el «carry» (rendimiento que genera
   mantener el activo). Para ETFs de bonos como TIP o IEF, el carry es el cupón; para acciones,
   el dividend yield.

#### Parte técnica (para implementación):

1. **Turn-of-Month Effect (Lakonishok & Smidt, 1988; Ariel, 1990):**
   ```
   Regla: Solo abrir posiciones de S5 en los últimos 3 + primeros 3 días de cada mes.
   Lógica: if (day_of_month >= last_biz_day - 2) or (day_of_month <= 3): allow_entry = True
   Validación: Comparar Sharpe con y sin este filtro en la ventana 2018-2022.
   ```

2. **Gary Antonacci's Dual Momentum (2014):**
   ```
   Paso 1: Calcular retorno 12 meses de cada ETF del Universo A.
   Paso 2: Calcular retorno 12 meses de BIL (benchmark libre de riesgo).
   Paso 3: Si el mejor ETF supera a BIL → comprar ese ETF.
            Si no → quedarse 100% en BIL/IEF.
   Diferencia con S5: Usa lookback de 12 meses (252d) vs. 45d de S5.
   ```

3. **Risk Parity Simplificado (All-Weather de Dalio simplificado):**
   ```
   Asignación: Ponderar cada activo del Universo A por 1/volatilidad(60d).
   Rebalanceo: Mensual.
   Ya tenemos: vol_control en BacktestEngine (target_portfolio_vol=0.12).
   Mejora: En lugar de ranking de momentum, simplemente invertir σ.
   ```

4. **Carry Factor (Koijen et al., 2018):**
   ```
   Para bonos (IEF, TIP): carry = yield_actual - yield_hace_3_meses
   Para acciones/ETFs: carry = dividend_yield
   Combinar: score_final = 0.5 * z_momentum + 0.5 * z_carry
   Datos necesarios: Dividendos históricos (disponibles via yfinance).
   ```

5. **Breakout de Volatilidad (Keltner Channel / Squeeze):**
   ```
   Detectar "squeeze" cuando Bollinger Bands están dentro de Keltner Channels.
   Comprar al primer breakout alcista post-squeeze.
   Indicadores: Ya tenemos ATR; solo falta añadir Bollinger Bands.
   Ventaja: No correlacionado con momentum puro.
   ```
""")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
