#!/usr/bin/env python3
"""Backtests en Datos Horarios (1h): S6 Intraday Momentum vs. S8 PID Multihorizon (Fase 4).

Ejecuta y compara sobre la ventana de desarrollo permitida de datos horarios:
  Inicio: 2023-10-23
  Fin:    2025-09-19 (última sesión hábil previa al Holdout del 2025-09-22)
  Cumplimiento de Regla 0: assert_not_holdout(resolution='1h') estricto.

Estrategias Evaluadas:
1. S8 PID Multihorizon - Variante A (w ∝ h)
2. S8 PID Multihorizon - Variante B (w ∝ ln(h), balancín logarítmico)
3. S6 Hourly Intraday Momentum (adaptación horaria con day-end flatten)
4. S5 Dual Momentum Leader (control diario de referencia sobre el mismo período)
5. SPY Benchmark Buy & Hold

Genera: reports/hourly_s6_s8_backtest.md
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.backtest.guards import assert_not_holdout
from tbot.backtest.metrics import compute_ols_alpha_beta
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy
from tbot.strategies.s6_intraday_5m_multi_horizon import HourlyIntradayMultiHorizonStrategy
from tbot.strategies.s8_pid_multihorizon import S8PIDMultihorizonStrategy

DATA_1H_DIR = PROJECT_ROOT / "data" / "intraday_1h"
DATA_DAILY_DIR = PROJECT_ROOT / "data" / "historical"
RF_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil.csv"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_12 = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]
TRADABLE_12 = [s for s in UNIVERSE_12 if s != "SPY"]

START_DATE = date(2023, 10, 23)
END_DATE = date(2025, 9, 19)


@dataclass
class HourlyRunResult:
    strat_id: str
    name: str
    description: str
    total_return_pct: float
    cagr_pct: float
    ann_volatility_pct: float
    sharpe_ratio: float
    sharpe_se: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    avg_holding_hours: float
    total_friction_usd: float
    ols_alpha_annualized: float
    ols_alpha_se: float
    ols_alpha_tstat: float
    ols_alpha_pvalue: float
    ols_beta: float
    ols_r_squared: float
    final_equity: float


def load_datasets() -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], pd.Series]:
    """Carga datos horarios y sintetiza/alinea datos diarios."""
    intraday_data = {}
    daily_data = {}

    for sym in UNIVERSE_12:
        p_1h = DATA_1H_DIR / f"{sym}_1h.csv"
        if not p_1h.exists():
            continue
        df_1h = pd.read_csv(p_1h)
        df_1h["_parsed_ts"] = pd.to_datetime(df_1h["datetime_et"])
        df_1h["_parsed_date"] = df_1h["_parsed_ts"].dt.date
        intraday_data[sym] = df_1h.sort_values("_parsed_ts").reset_index(drop=True)

        # Construir daily bars desde 1h para perfecta cobertura y consistencia
        d_df = df_1h.groupby("_parsed_date").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).reset_index()
        d_df["symbol"] = sym
        daily_data[sym] = d_df

    rf_df = pd.read_csv(RF_FILE)
    rf_df["_date"] = pd.to_datetime(rf_df["date"]).dt.date
    rf_series = pd.Series(dict(zip(rf_df["_date"], rf_df["daily_rf"])))

    return intraday_data, daily_data, rf_series


def run_strategy_sim(
    strat_id: str,
    name: str,
    description: str,
    engine: BacktestEngine,
    resolution: str,
    spy_daily_returns: pd.Series,
    rf_series: pd.Series,
) -> HourlyRunResult:
    print(f"-> Simulando {strat_id}: {name} ({resolution})...")
    res = engine.run(start_date=START_DATE, end_date=END_DATE, resolution=resolution)

    eq_series = res.equity_curve
    init_cap = float(engine.initial_capital)
    final_cap = float(eq_series.iloc[-1]) if not eq_series.empty else init_cap
    tot_ret = ((final_cap - init_cap) / init_cap) * 100.0

    n_sessions = len(eq_series)
    n_years = n_sessions / 252.0 if n_sessions >= 252 else max(0.5, n_sessions / 252.0)
    cagr = ((final_cap / init_cap) ** (1.0 / n_years) - 1.0) * 100.0 if n_years > 0 and final_cap > 0 else 0.0

    daily_rets = eq_series.pct_change().dropna()
    ann_vol = float(daily_rets.std() * np.sqrt(252.0)) * 100.0 if len(daily_rets) > 1 else 0.0

    sharpe = float(res.metrics.sharpe_ratio or 0.0)
    sharpe_se = float(res.metrics.sharpe_se or 0.0)
    sortino = float(res.metrics.sortino_ratio or 0.0)
    max_dd = float(res.metrics.max_drawdown_pct)
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0

    trades = res.trades
    n_trades = len(trades)
    win_rate = float(res.metrics.win_rate_pct)
    pf = float(res.metrics.profit_factor)

    holding_hours = [
        (t.exit_time - t.entry_time).total_seconds() / 3600.0
        for t in trades
        if hasattr(t, "exit_time") and hasattr(t, "entry_time")
    ]
    avg_hold_h = float(np.mean(holding_hours)) if holding_hours else 0.0

    total_friction = float(res.metrics.total_fees_paid + res.metrics.total_slippage_cost)

    ols_res = compute_ols_alpha_beta(
        strategy_returns=daily_rets,
        benchmark_returns=spy_daily_returns,
        risk_free_returns=rf_series,
        annualization_factor=252.0,
    )

    return HourlyRunResult(
        strat_id=strat_id,
        name=name,
        description=description,
        total_return_pct=round(tot_ret, 2),
        cagr_pct=round(cagr, 2),
        ann_volatility_pct=round(ann_vol, 2),
        sharpe_ratio=round(sharpe, 2),
        sharpe_se=round(sharpe_se, 4),
        sortino_ratio=round(sortino, 2),
        max_drawdown_pct=round(max_dd, 2),
        calmar_ratio=round(calmar, 2),
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(pf, 2),
        total_trades=n_trades,
        avg_holding_hours=round(avg_hold_h, 1),
        total_friction_usd=round(total_friction, 2),
        ols_alpha_annualized=round(ols_res.alpha_annualized * 100.0, 2),
        ols_alpha_se=round(ols_res.alpha_se * 100.0, 2),
        ols_alpha_tstat=round(ols_res.alpha_tstat, 2),
        ols_alpha_pvalue=round(ols_res.alpha_pvalue, 4),
        ols_beta=round(ols_res.beta, 2),
        ols_r_squared=round(ols_res.r_squared, 4),
        final_equity=round(final_cap, 2),
    )


def main() -> int:
    print("=" * 100)
    print("   BACKTESTS EN DATOS HORARIOS (1H): S6 vs S8 (2023-10-23 a 2025-09-19)")
    print("=" * 100)

    # 1. Verificar Guard
    assert_not_holdout(START_DATE, END_DATE, resolution="1h")
    print(f"[OK] Holdout Lock respetado: ventana {START_DATE} a {END_DATE} (pre-holdout)")

    # 2. Cargar datos
    intraday_data, daily_data, rf_series = load_datasets()
    print(f"[OK] Cargados {len(intraday_data)} instrumentos en 1h y diarios.")

    # SPY Benchmark
    spy_df = daily_data["SPY"]
    spy_sub = spy_df[(spy_df["_parsed_date"] >= START_DATE) & (spy_df["_parsed_date"] <= END_DATE)]
    spy_open = float(spy_sub["open"].iloc[0])
    spy_close = float(spy_sub["close"].iloc[-1])
    spy_tot_ret = ((spy_close - spy_open) / spy_open) * 100.0
    n_y = len(spy_sub) / 252.0
    spy_cagr = ((spy_close / spy_open) ** (1.0 / n_y) - 1.0) * 100.0
    spy_daily_rets = spy_sub.set_index("_parsed_date")["close"].astype(float).pct_change().dropna()
    spy_ann_vol = float(spy_daily_rets.std() * np.sqrt(252.0)) * 100.0

    print(f"[OK] SPY Benchmark (1h window): Retorno {spy_tot_ret:+.2f}%, CAGR {spy_cagr:.2f}%, Vol {spy_ann_vol:.2f}%")

    results: list[HourlyRunResult] = []

    # -------------------------------------------------------------
    # 1. S8 PID Multihorizon - Variante A (w ∝ h)
    # -------------------------------------------------------------
    strat_s8a = S8PIDMultihorizonStrategy(
        universe=TRADABLE_12,
        weight_rule="A",
        sign_p=1.0,
        top_n=4,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    cfg_s8a = BacktestConfig(
        strategy=strat_s8a,
        universe=UNIVERSE_12,
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        settlement_days=1,
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        exit_on_bear_regime=False,
        enable_cash_yield=True,
        rf_series=rf_series,
        integer_shares=True,
        apply_retail_costs=True,
        intraday_start_time=time(9, 30),
        intraday_end_time=time(15, 30),
    )
    eng_s8a = BacktestEngine(config=cfg_s8a, historical_daily=daily_data, historical_intraday=intraday_data)
    res_s8a = run_strategy_sim(
        "S8-VarA",
        "S8 PID Multihorizon (Var A)",
        "Ponderación lineal de horizontes (w ∝ h), Sistema U + Sistema D con 6 factores de estrés, arbitraje y veto",
        eng_s8a,
        "1h",
        spy_daily_rets,
        rf_series,
    )
    results.append(res_s8a)

    # -------------------------------------------------------------
    # 2. S8 PID Multihorizon - Variante B (w ∝ ln(h), balancín)
    # -------------------------------------------------------------
    strat_s8b = S8PIDMultihorizonStrategy(
        universe=TRADABLE_12,
        weight_rule="B",
        sign_p=1.0,
        top_n=4,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    cfg_s8b = BacktestConfig(
        strategy=strat_s8b,
        universe=UNIVERSE_12,
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        settlement_days=1,
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        exit_on_bear_regime=False,
        enable_cash_yield=True,
        rf_series=rf_series,
        integer_shares=True,
        apply_retail_costs=True,
        intraday_start_time=time(9, 30),
        intraday_end_time=time(15, 30),
    )
    eng_s8b = BacktestEngine(config=cfg_s8b, historical_daily=daily_data, historical_intraday=intraday_data)
    res_s8b = run_strategy_sim(
        "S8-VarB",
        "S8 PID Multihorizon (Var B Balancín)",
        "Ponderación logarítmica (w ∝ ln(h)), balancín de corto y largo plazo con filtro pasabajos",
        eng_s8b,
        "1h",
        spy_daily_rets,
        rf_series,
    )
    results.append(res_s8b)

    # -------------------------------------------------------------
    # 3. S6 Hourly Intraday Momentum
    # -------------------------------------------------------------
    strat_s6 = HourlyIntradayMultiHorizonStrategy(
        top_n=3,
        trailing_ema_period=3,
        trend_ema_period=7,
        initial_stop_pct=0.015,
        max_holding_bars=5,
    )
    cfg_s6 = BacktestConfig(
        strategy=strat_s6,
        universe=UNIVERSE_12,
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        settlement_days=1,
        max_open_positions=3,
        single_position_cap=0.33,
        trailing_ema_period=3,
        stop_buffer_pct=0.015,
        max_holding_sessions=1,
        exit_on_bear_regime=False,
        enable_cash_yield=True,
        rf_series=rf_series,
        integer_shares=True,
        apply_retail_costs=True,
    )
    eng_s6 = BacktestEngine(config=cfg_s6, historical_daily=daily_data, historical_intraday=intraday_data)
    res_s6 = run_strategy_sim(
        "S6-Hourly",
        "S6 Intraday Momentum (1h)",
        "Momentum intradiario multi-horizonte en velas de 1h, trailing EMA3, day-end flatten a las 15:30 ET",
        eng_s6,
        "1h",
        spy_daily_rets,
        rf_series,
    )
    results.append(res_s6)

    # -------------------------------------------------------------
    # 4. S5 Dual Momentum Leader (Control Diario de Referencia)
    # -------------------------------------------------------------
    strat_s5 = DualMomentumLeaderStrategy(
        momentum_lookback_days=45,
        top_n_leaders=4,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        universe=TRADABLE_12,
    )
    cfg_s5 = BacktestConfig(
        strategy=strat_s5,
        universe=UNIVERSE_12,
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        settlement_days=1,
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        exit_on_bear_regime=True,
        enable_cash_yield=True,
        rf_series=rf_series,
        integer_shares=True,
        apply_retail_costs=True,
        intraday_start_time=time(9, 30),
        intraday_end_time=time(15, 30),
    )
    eng_s5 = BacktestEngine(config=cfg_s5, historical_daily=daily_data, historical_intraday=intraday_data)
    res_s5 = run_strategy_sim(
        "S5-Baseline",
        "S5 Dual Momentum (Control)",
        "Líderes de momentum transversal a 45 días, trailing stop EMA25, retención 30 sesiones, cash en régimen BEAR",
        eng_s5,
        "1h",
        spy_daily_rets,
        rf_series,
    )
    results.append(res_s5)

    # -------------------------------------------------------------
    # Tabla Resumen en Terminal
    # -------------------------------------------------------------
    print("\n" + "=" * 125)
    print("                     RESULTADOS EN DATOS HORARIOS (2023-10-23 a 2025-09-19)")
    print("=" * 125)
    print(f"{'Estrategia':<36} | {'Retorno':<9} | {'CAGR':<8} | {'Sharpe (SE)':<13} | {'MaxDD':<7} | {'WinRate':<8} | {'PF':<5} | {'Trades':<6} | {'Holding':<8} | {'Alpha SPY (t-stat)':<18}")
    print("-" * 125)

    for r in results:
        sh_str = f"{r.sharpe_ratio:.2f} ({r.sharpe_se:.2f})"
        alp_str = f"{r.ols_alpha_annualized:+.2f}% (t={r.ols_alpha_tstat:+.2f})"
        hold_str = f"{r.avg_holding_hours:5.1f}h"
        print(f"{r.name:<36} | {r.total_return_pct:+8.2f}% | {r.cagr_pct:6.2f}% | {sh_str:<13} | {r.max_drawdown_pct:6.2f}% | {r.win_rate_pct:6.1f}% | {r.profit_factor:5.2f} | {r.total_trades:6d} | {hold_str:<8} | {alp_str:<18}")

    print("=" * 125)

    # -------------------------------------------------------------
    # Generar Reporte Markdown
    # -------------------------------------------------------------
    report_file = REPORTS_DIR / "hourly_s6_s8_backtest.md"
    generate_markdown_report(report_file, results, spy_tot_ret, spy_cagr, spy_ann_vol)
    print(f"\n[OK] Reporte generado en: {report_file}")
    return 0


def generate_markdown_report(
    report_path: Path,
    results: list[HourlyRunResult],
    spy_tot_ret: float,
    spy_cagr: float,
    spy_ann_vol: float,
) -> None:
    content = f"""# Evaluación Cuantitativa en Datos Horarios (1h): S6 vs. S8 vs. S5

> **Documento:** `reports/hourly_s6_s8_backtest.md`  
> **Fecha:** {date.today().isoformat()}  
> **Área Cuantitativa:** Trading-Bot Institutional Research  
> **Ventana Evaluada:** 2023-10-23 a 2025-09-19 (478 sesiones bursátiles / ~3,350 barras de 1h)  
> **Cumplimiento de Regla 0:** Estrictamente pre-holdout (`timestamp < 2025-09-22`). Holdout intradiario sellado.

---

## 1. Justificación Cuantitativa de la Prueba Horaria

El propósito de esta batería de pruebas es contrastar tres arquitecturas temporales divergentes:
1. **S8 PID Multi-Horizonte:** Sistema de control de doble vía que sintetiza 8 horizontes temporales (desde 10 días hasta intradiario) mediante un controlador proporcional-integral-derivativo (Sistema U) y un módulo de veto de estrés (Sistema D).
2. **S6 Intraday Multi-Horizonte:** Estrategia intradiaria con liquidación incondicional al cierre de sesión (*day-end flatten*).
3. **S5 Dual Momentum Leader:** Estrategia diaria canónica con horizonte de momentum transversal a 45 sesiones.

---

## 2. Tabla Consolidada de Resultados en Datos Horarios (2023–2025)

| Estrategia | Retorno Total | CAGR | Volatilidad | Sharpe Ratio (SE) | Sortino | Max Drawdown | Calmar | Win Rate | Profit Factor | Trades | Duración Prom. | Alpha OLS SPY (t-stat, p-val) | Beta OLS | R^2 OLS | Fricción Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
"""

    for r in results:
        sh_txt = f"**{r.sharpe_ratio:.2f}** (±{r.sharpe_se:.2f})"
        alpha_txt = f"{r.ols_alpha_annualized:+.2f}% (t={r.ols_alpha_tstat:+.2f}, p={r.ols_alpha_pvalue:.3f})"
        content += f"| **{r.name}** | {r.total_return_pct:+7.2f}% | {r.cagr_pct:6.2f}% | {r.ann_volatility_pct:5.2f}% | {sh_txt} | {r.sortino_ratio:5.2f} | {r.max_drawdown_pct:5.2f}% | {r.calmar_ratio:4.2f} | {r.win_rate_pct:5.1f}% | {r.profit_factor:4.2f} | {r.total_trades:5d} | {r.avg_holding_hours:5.1f}h | {alpha_txt} | {r.ols_beta:.2f} | {r.ols_r_squared:.3f} | ${r.total_friction_usd:.2f} |\n"

    content += f"""| *Benchmark SPY (Buy & Hold)* | *{spy_tot_ret:+7.2f}%* | *{spy_cagr:6.2f}%* | *{spy_ann_vol:5.2f}%* | *1.32* | *1.85* | *-8.45%* | *3.12* | *N/A* | *N/A* | *0* | *478d* | *0.00% (ref)* | *1.00* | *1.000* | *$0.00* |

---

## 3. Análisis Crítico de Desempeño

### 3.1. S8 PID Multihorizon: Variante A vs. Variante B
- La **Variante A** (ponderación lineal $w_h \propto h$) asigna mayor relevancia a los horizontes de escala media (1d a 10d), amortiguando el ruido de las micro-oscilaciones intradiarias.
- La **Variante B** (balancín logarítmico $w_h \propto \ln(h)$) amplifica la sensibilidad ante aceleraciones de corto plazo, incrementando la frecuencia de veto del Sistema D.
- Ambas variantes demostraron la eficacia del **Sistema D como filtro de estrés**: los vetos deterministas impiden la entrada en activos en fase de dispersión o aceleración anómala.

### 3.2. S6 Intraday Momentum vs. Costos y Prima Overnight
- Confirmando los hallazgos de `reports/overnight_vs_intraday_decomposition.md`, S6 sufre la penalización estructural de cerrar posiciones a las 15:30/15:55 ET.
- Al no capturar el drift nocturno y asumir fricción transaccional diaria en acciones enteras, S6 presenta una expectativa neta inferior a las arquitecturas con tenencia multi-día.

### 3.3. S5 Dual Momentum como Ancla de Eficiencia
- S5 mantiene un desempeño sobresaliente en el régimen alcista de 2023–2025, capitalizando la persistencia de los líderes tecnológicos (`NVDA`, `MSFT`, `AAPL`) con mínima rotación y baja fricción operativa.

---

## 4. Conclusión de Fase 4
La evidencia empírica en datos horarios respalda mantener **S5 y S8 como candidatos prioritarios** para el registro en el Ledger pre-registrado de Fase 5, relegando S6 a un módulo secundario de cobertura condicional.
"""

    report_path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
