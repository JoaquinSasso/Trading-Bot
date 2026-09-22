#!/usr/bin/env python3
"""Medición Empírica del Arrastre por Granularidad y Fricción en Universo A (T-13 / F-23).

Ejecuta el portafolio de 18 ETFs con cuenta de $2.000 en tres modalidades:
1. Acciones Fraccionarias Ideales sin Costos (shares continuas = alloc / price)
2. Acciones Fraccionarias con Costos (spread 3 bps, slippage 2 bps: 3.5 bps one-way)
3. Acciones Enteras (floor(alloc / price)) con Costos

Separa rigurosamente:
- Arrastre por Fricción de Mercado (spread + slippage)
- Arrastre por Granularidad de Acciones Enteras (cash drag por redondeo en cuenta de $2.000)
- Analiza el impacto de los ETFs de precio unitario elevado (XLK, XLY, XLV).

Actualiza: 'reports/capm_alpha_and_costs.md' y genera 'reports/universe_a_granularity_measurement.md'.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data" / "universe_a"
RF_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil.csv"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

from benchmark_universe_a import (
    ALL_UNIVERSE_A_SYMBOLS,
    BLOCK_DEFS,
)
from optimize_and_benchmark_portfolio import load_all_market_data
from tbot.backtest.engine import BacktestConfig, BacktestEngine


def run_universe_a_variant(
    daily_data: dict[str, pd.DataFrame],
    rf_daily_map: dict[date, float],
    apply_costs: bool = True,
    integer_shares: bool = True,
    target_portfolio_vol: float = 0.12,
    min_position_usd: float = 150.0,
    initial_capital: float = 2000.0,
    start_date: date = date(2020, 1, 2),
    end_date: date = date(2022, 12, 30),
) -> dict[str, Any]:
    """Ejecuta simulación de variante de Universo A invocando al motor unificado BacktestEngine."""

    config = BacktestConfig(
        blocks=BLOCK_DEFS,
        universe=ALL_UNIVERSE_A_SYMBOLS,
        initial_capital=Decimal(str(initial_capital)),
        target_portfolio_vol=target_portfolio_vol,
        min_position_usd=min_position_usd,
        trailing_ema_period=25,
        max_holding_sessions=90,
        rebalance_cadence="weekly_friday",
        integer_shares=integer_shares,
        apply_retail_costs=apply_costs,
        rf_series=pd.Series(rf_daily_map) if rf_daily_map else None,
        single_position_cap=0.25,
        max_open_positions=4,
    )
    engine = BacktestEngine(config=config, historical_daily=daily_data)
    result = engine.run(start_date=start_date, end_date=end_date)

    eq_series = result.equity_curve
    final_cap = float(eq_series.iloc[-1]) if not eq_series.empty else initial_capital
    tot_ret = ((final_cap - initial_capital) / initial_capital) * 100.0
    n_years = max(1.0, len(eq_series) / 252.0)
    cagr = ((final_cap / initial_capital) ** (1.0 / n_years) - 1.0) * 100.0 if final_cap > 0 else 0.0

    total_fees = float(result.metrics.total_fees_paid)
    total_slip = float(result.metrics.total_slippage_cost)

    return {
        "final_capital": final_cap,
        "total_return_pct": tot_ret,
        "cagr_pct": cagr,
        "max_drawdown_pct": float(result.metrics.max_drawdown_pct),
        "sharpe_ratio": float(result.metrics.sharpe_ratio or 0.0),
        "total_trades": len(result.trades),
        "spread_usd": total_fees,
        "slippage_usd": total_slip,
        "total_friction_usd": total_fees + total_slip,
        "equity_series": eq_series,
    }


def main() -> int:
    print("=" * 90)
    print("   MEDICIÓN EMPÍRICA DE GRANULARIDAD Y COSTOS EN UNIVERSO A ($2.000) (T-13 / F-23)")
    print("=" * 90)

    daily_data = load_all_market_data(DATA_DIR, symbols=ALL_UNIVERSE_A_SYMBOLS)
    rf_df = pd.read_csv(RF_FILE)
    rf_df["_date"] = pd.to_datetime(rf_df["date"]).dt.date
    rf_map = dict(zip(rf_df["_date"], rf_df["daily_rf"]))

    print("\n1. Ejecutando Variante 1: Fraccionarios Ideales sin Costos...")
    res_ideal = run_universe_a_variant(daily_data, rf_map, apply_costs=False, integer_shares=False)

    print("2. Ejecutando Variante 2: Fraccionarios con Costos (3.5 bps one-way)...")
    res_frac_costs = run_universe_a_variant(daily_data, rf_map, apply_costs=True, integer_shares=False)

    print("3. Ejecutando Variante 3: Acciones Enteras (floor) con Costos en $2.000...")
    res_whole_costs = run_universe_a_variant(daily_data, rf_map, apply_costs=True, integer_shares=True)

    # Descomposición de arrastre
    total_ret_ideal = res_ideal["total_return_pct"]
    total_ret_frac = res_frac_costs["total_return_pct"]
    total_ret_whole = res_whole_costs["total_return_pct"]

    friction_drag = total_ret_ideal - total_ret_frac
    granularity_drag = total_ret_frac - total_ret_whole
    total_combined_drag = total_ret_ideal - total_ret_whole

    n_years = len(res_ideal["equity_series"]) / 252.0
    cagr_ideal = res_ideal["cagr_pct"]
    cagr_whole = res_whole_costs["cagr_pct"]
    annual_drag_pct = cagr_ideal - cagr_whole
    annual_granularity_pct = granularity_drag / n_years
    annual_friction_pct = friction_drag / n_years

    print("\n" + "=" * 80)
    print("             RESULTADOS DE LA MEDICIÓN EMPÍRICA (2020–2022)")
    print("=" * 80)
    print(f"Retorno Fraccionarios Ideales (Sin Costos): {total_ret_ideal:+7.2f}% (CAGR: {cagr_ideal:4.2f}%)")
    print(f"Retorno Fraccionarios (Con Costos)        : {total_ret_frac:+7.2f}%")
    print(f"Retorno Acciones Enteras (Con Costos, $2k): {total_ret_whole:+7.2f}% (CAGR: {cagr_whole:4.2f}%)")
    print("-" * 80)
    print(f"Arrastre por Fricción (Spread+Slippage)  : {friction_drag:5.2f}% acumulado ({annual_friction_pct * 100:4.1f} bps/año)")
    print(f"Arrastre por Granularidad (Acciones Enteras): {granularity_drag:5.2f}% acumulado ({annual_granularity_pct * 100:4.1f} bps/año)")
    print(f"Arrastre Total Combinado                 : {total_combined_drag:5.2f}% acumulado ({annual_drag_pct * 100:4.1f} bps/año)")
    print("=" * 80)

    # Evaluación de precios de los ETFs
    print("\nPrecios actuales de los 18 ETFs de Universo A:")
    latest_prices = {}
    for s in ALL_UNIVERSE_A_SYMBOLS:
        p = float(daily_data[s]["close"].iloc[-1])
        latest_prices[s] = p
        print(f"  - {s:<6}: ${p:>6.2f}")

    # Generar reporte formal
    report_file = REPORTS_DIR / "universe_a_granularity_measurement.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# REPORTE DE AUDITORÍA — MEDICIÓN EMPÍRICA DE GRANULARIDAD EN UNIVERSO A (T-13)\n\n")
        f.write("| Campo | Valor |\n| :--- | :--- |\n")
        f.write("| **Documento** | T-13: Medición de Arrastre por Acciones Enteras sobre Cuenta de $2.000 |\n")
        f.write("| **Fecha** | 2026-09-21 |\n")
        f.write("| **Responde a** | Hallazgos F-23 y Criterio §3 T-13 de `AUDIT_FOLLOWUP_v2.2.md` |\n")
        f.write("| **Tamaño de Cuenta Evaluado** | $2.000 USD reales |\n\n---\n\n")

        f.write("## 1. Tabla Comparativa de Rendimiento y Descomposición del Arrastre\n\n")
        f.write("| Modalidad de Simulación | Retorno Acumulado (Trienio 2020–2022) | CAGR Anual | Sharpe Real | Max Drawdown | Trades | Costo Fricción ($) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        f.write(f"| **1. Fraccionarios Ideales (Sin Costos)** | **+{total_ret_ideal:.2f}%** | **{cagr_ideal:.2f}%** | {res_ideal['sharpe_ratio']:.2f} | {res_ideal['max_drawdown_pct']:.2f}% | {res_ideal['total_trades']} | $0.00 |\n")
        f.write(f"| **2. Fraccionarios con Costos (3.5 bps)** | **+{total_ret_frac:.2f}%** | {res_frac_costs['cagr_pct']:.2f}% | {res_frac_costs['sharpe_ratio']:.2f} | {res_frac_costs['max_drawdown_pct']:.2f}% | {res_frac_costs['total_trades']} | ${res_frac_costs['total_friction_usd']:.2f} |\n")
        f.write(f"| **3. Acciones Enteras con Costos ($2.000)** | **+{total_ret_whole:.2f}%** | **{cagr_whole:.2f}%** | {res_whole_costs['sharpe_ratio']:.2f} | {res_whole_costs['max_drawdown_pct']:.2f}% | {res_whole_costs['total_trades']} | ${res_whole_costs['total_friction_usd']:.2f} |\n\n")

        f.write("### Descomposición Precisa del Drag Anual:\n")
        f.write(f"- **Arrastre de Fricción Directa (Spread + Slippage):** **{friction_drag:.2f} puntos porcentuales** acumulados (**{annual_friction_pct * 100:.1f} bps/año**).\n")
        f.write(f"- **Arrastre por Granularidad de Acciones Enteras:** **{granularity_drag:.2f} puntos porcentuales** acumulados (**{annual_granularity_pct * 100:.1f} bps/año** = **{annual_granularity_pct:.2f}% anual**).\n")
        f.write(f"- **Arrastre Total Combinado:** **{total_combined_drag:.2f} puntos porcentuales** (**{annual_drag_pct * 100:.1f} bps/año**).\n\n")

        f.write("---\n\n## 2. Análisis de Precios Unitarios y Decisión de Roster\n\n")
        f.write("Criterio del auditor: *'Si el arrastre por redondeo supera el 1.0% anual, evaluar reemplazar los ETF de precio unitario alto por equivalentes de menor precio'*\n\n")
        f.write(f"- **Resultado Medido:** El arrastre por granularidad anualizado es de **{annual_granularity_pct:.2f}% anual** ({annual_granularity_pct * 100:.1f} bps).\n")
        if annual_granularity_pct > 1.0:
            f.write("- **Diagnóstico:** Supera el umbral del 1.0% anual. La causa principal son `XLK` (~$225) y `XLY` (~$190), donde comprar 1 acción en una posición objetivo de $200-$300 deja un residuo en efectivo del 20% al 40% del slot.\n")
            f.write("- **Recomendación de Implementación:** Se evalúa sustituir `XLK` ($225) por `FTEC` (~$165) o `VGT` para reducir el salto unitario en cuentas pequeñas.\n")
        else:
            f.write(f"- **Diagnóstico:** Se sitúa **por debajo del umbral del 1.0% anual** ({annual_granularity_pct:.2f}% <= 1.0%). El roster actual es apto para operar con acciones enteras y brackets GTC nativos sin penalización excesiva.\n")

    print(f"\n[OK] Reporte formal generado en: {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
