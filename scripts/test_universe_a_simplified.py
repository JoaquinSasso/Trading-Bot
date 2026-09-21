#!/usr/bin/env python3
"""Variante Simplificada de Universo A: Sin Ranking Sectorial (T-16 / Auditoría v2.2).

Motivación del Auditor:
El test de monotonicidad sobre sectores GICS demostró que el ranking es plano
(spread rank 1,2 vs 6,7 es de -0.34%, t = -0.47, p = 0.635).
Un parámetro que no discrimina es superficie de sobreajuste sin contrapartida (PBO = 84.45%).

Implementa y compara dos arquitecturas sobre Universo A ($2.000, acciones enteras, costos):
1. Versión con Ranking (Baseline): Multi-horizon momentum (21, 63, 126d), Top-N por bloque (Top-4 sectores, Top-1 intl, etc.) y buffer rank.
2. Versión Simplificada (Propuesta T-16): Elimina el ranking y el Top-N.
   Todos los instrumentos que superan el Gate Absoluto (Ret 126d > BIL 126d y Cierre > EMA50)
   entran al portafolio y se equiponderan por Volatilidad Inversa (1/sigma, 60d),
   conservando caps de bloque, modulación por régimen graduado y objetivo de volatilidad (12%).

Genera: 'reports/universe_a_simplified.md'.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "universe_a"
RF_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil.csv"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from benchmark_universe_a import (
    ALL_UNIVERSE_A_SYMBOLS,
    BLOCK_DEFS,
    calculate_graduated_regime,
    calculate_multi_horizon_momentum,
    passes_absolute_gate,
)
from optimize_and_benchmark_portfolio import compute_drawdown, ema, load_all_market_data


@dataclass
class UnivASimResult:
    config_name: str
    period_name: str
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int
    total_friction_usd: float
    avg_invested_pct: float
    equity_series: pd.Series


def run_universe_a_simulation(
    daily_data: dict[str, pd.DataFrame],
    rf_daily_map: dict[date, float],
    start_date: date,
    end_date: date,
    config_name: str,
    period_name: str,
    use_ranking: bool = True,
    target_portfolio_vol: float = 0.12,
    min_position_usd: float = 150.0,
    initial_capital: float = 2000.0,
    apply_costs: bool = True,
    integer_shares: bool = True,
) -> UnivASimResult:
    trailing_ema_period = 25
    max_holding_days = 90

    dates = sorted(
        set.intersection(
            *[
                set(df["_parsed_date"].unique())
                for sym, df in daily_data.items()
                if sym in ALL_UNIVERSE_A_SYMBOLS
            ]
        )
    )
    valid_dates = [d for d in dates if start_date <= d <= end_date]

    cash = initial_capital
    open_positions: dict[str, dict] = {}
    equity_history: dict[date, float] = {}
    invested_pct_history: list[float] = []
    total_spread_usd = 0.0
    total_slippage_usd = 0.0
    total_trades = 0

    half_spread = 0.00015
    slippage = 0.00020

    for cur_date in valid_dates:
        daily_rf = rf_daily_map.get(cur_date, 0.0)
        is_friday = cur_date.weekday() == 4

        # 1. Salidas Diarias
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            pos["days_held"] += 1
            bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
            cur_close = float(bar["close"])

            past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
            ema25 = ema(past_bars["close"].astype(float), trailing_ema_period).iloc[-1]

            hit_stop = cur_close < pos["stop_loss"]
            hit_ema = (pos["days_held"] >= 3 and cur_close < ema25)
            hit_max_hold = (pos["days_held"] >= max_holding_days)

            if hit_stop or hit_ema or hit_max_hold:
                if apply_costs:
                    ex_p = cur_close * (1.0 - half_spread - slippage)
                    total_spread_usd += cur_close * half_spread * pos["shares"]
                    total_slippage_usd += cur_close * slippage * pos["shares"]
                else:
                    ex_p = cur_close

                cash += ex_p * pos["shares"]
                total_trades += 1
                del open_positions[sym]
            else:
                pos["stop_loss"] = max(pos["stop_loss"], ema25 * 0.985)

        # 2. Rebalanceo Semanal
        if is_friday:
            regime_score = calculate_graduated_regime(daily_data, cur_date)
            total_equity = cash + sum(
                p["shares"] * float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
                for s, p in open_positions.items()
            )

            for block_key, b_cfg in BLOCK_DEFS.items():
                effective_block_cap = b_cfg.capital_cap
                if b_cfg.modulate_by_regime:
                    effective_block_cap *= regime_score

                if effective_block_cap <= 0.01:
                    # En régimen 0, no abrimos nuevas posiciones de este bloque
                    continue

                # Filtrar instrumentos que pasan el gate absoluto
                qualified_syms = [
                    sym for sym in b_cfg.tickers
                    if passes_absolute_gate(daily_data, sym, cur_date)
                ]

                block_open = [s for s in open_positions.keys() if s in b_cfg.tickers]

                if use_ranking:
                    # MODELO CON RANKING Y TOP-N (BASELINE)
                    avg_ranks = calculate_multi_horizon_momentum(daily_data, b_cfg.tickers, cur_date)
                    qualified = {s: avg_ranks[s] for s in qualified_syms if s in avg_ranks}
                    sorted_qualified = sorted(qualified.keys(), key=lambda s: qualified[s])

                    # Buffer rank exit
                    for s in list(block_open):
                        if s not in qualified or sorted_qualified.index(s) + 1 > b_cfg.buffer_rank:
                            c_p = float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
                            if apply_costs:
                                ex_p = c_p * (1.0 - half_spread - slippage)
                                total_spread_usd += c_p * half_spread * open_positions[s]["shares"]
                                total_slippage_usd += c_p * slippage * open_positions[s]["shares"]
                            else:
                                ex_p = c_p
                            cash += ex_p * open_positions[s]["shares"]
                            total_trades += 1
                            del open_positions[s]
                            block_open.remove(s)

                    available_slots = b_cfg.top_n - len(block_open)
                    entry_candidates = [
                        s for s in sorted_qualified[:b_cfg.top_n]
                        if s not in open_positions
                    ][:available_slots]

                else:
                    # MODELO SIMPLIFICADO T-16: SIN RANKING, TODOS LOS CALIFICADOS POR GATE
                    # Salida: Si una posición abierta ya no pasa el gate absoluto, se cierra
                    for s in list(block_open):
                        if s not in qualified_syms:
                            c_p = float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
                            if apply_costs:
                                ex_p = c_p * (1.0 - half_spread - slippage)
                                total_spread_usd += c_p * half_spread * open_positions[s]["shares"]
                                total_slippage_usd += c_p * slippage * open_positions[s]["shares"]
                            else:
                                ex_p = c_p
                            cash += ex_p * open_positions[s]["shares"]
                            total_trades += 1
                            del open_positions[s]
                            block_open.remove(s)

                    entry_candidates = [s for s in qualified_syms if s not in open_positions]

                if not entry_candidates:
                    continue

                # Ponderación por Volatilidad Inversa (60d)
                vols = {}
                for s in entry_candidates:
                    past_closes = daily_data[s][daily_data[s]["_parsed_date"] <= cur_date]["close"].astype(float)
                    ret_s = past_closes.iloc[-60:].pct_change().dropna()
                    sig = float(ret_s.std() * np.sqrt(252))
                    vols[s] = sig if (not np.isnan(sig) and sig > 0.01) else 0.20

                inv_vols = {s: 1.0 / vols[s] for s in entry_candidates}
                sum_inv = sum(inv_vols.values())
                norm_w = {s: inv_vols[s] / sum_inv for s in entry_candidates}

                curr_block_val = sum(
                    open_positions[s]["shares"] * float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
                    for s in block_open
                )
                max_block_capital = total_equity * effective_block_cap
                avail_block_capital = max(0.0, max_block_capital - curr_block_val)

                avg_v = sum(norm_w[s] * vols[s] for s in entry_candidates)
                vol_scale = min(1.0, target_portfolio_vol / avg_v) if avg_v > 0 else 1.0

                for s in entry_candidates:
                    target_alloc = avail_block_capital * norm_w[s] * vol_scale
                    if target_alloc < min_position_usd:
                        continue

                    alloc = min(cash, target_alloc)
                    if alloc < 25.0:
                        break

                    raw_p = float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
                    if apply_costs:
                        entry_p = raw_p * (1.0 + half_spread + slippage)
                    else:
                        entry_p = raw_p

                    if integer_shares:
                        shares = math.floor(alloc / entry_p)
                    else:
                        shares = alloc / entry_p

                    if shares <= 0:
                        continue

                    cost_val = shares * entry_p
                    if apply_costs:
                        total_spread_usd += raw_p * half_spread * shares
                        total_slippage_usd += raw_p * slippage * shares

                    cash -= cost_val
                    total_trades += 1
                    open_positions[s] = {
                        "entry_date": cur_date,
                        "entry_price": entry_p,
                        "shares": shares,
                        "stop_loss": entry_p * 0.95,
                        "days_held": 0,
                    }

        # 3. Efectivo gana BIL
        if daily_rf > 0 and cash > 0:
            cash *= (1.0 + daily_rf)

        # 4. Equidad al cierre
        invested = sum(
            p["shares"] * float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
            for s, p in open_positions.items()
        )
        current_eq = cash + invested
        equity_history[cur_date] = current_eq
        invested_pct_history.append((invested / current_eq) * 100.0 if current_eq > 0 else 0.0)

    # Cierre final
    final_date = valid_dates[-1]
    for sym, pos in list(open_positions.items()):
        raw_ex = float(daily_data[sym][daily_data[sym]["_parsed_date"] == final_date]["close"].iloc[0])
        ex_p = raw_ex * (1.0 - half_spread - slippage) if apply_costs else raw_ex
        if apply_costs:
            total_spread_usd += raw_ex * half_spread * pos["shares"]
            total_slippage_usd += raw_ex * slippage * pos["shares"]
        cash += ex_p * pos["shares"]
        total_trades += 1
    equity_history[final_date] = cash

    eq_series = pd.Series(equity_history)
    final_cap = float(eq_series.iloc[-1])
    tot_ret = ((final_cap - initial_capital) / initial_capital) * 100.0
    n_years = len(eq_series) / 252.0
    cagr = ((final_cap / initial_capital) ** (1.0 / n_years) - 1.0) * 100.0 if n_years > 0 else 0.0

    max_dd, _ = compute_drawdown(eq_series)

    daily_rets = eq_series.pct_change().dropna()
    excess_rets = pd.Series([daily_rets[d] - rf_daily_map.get(d, 0.0) for d in daily_rets.index], index=daily_rets.index)
    std_excess = float(excess_rets.std())
    sharpe = float((excess_rets.mean() / std_excess) * np.sqrt(252.0)) if std_excess > 0 else 0.0

    avg_inv = float(np.mean(invested_pct_history)) if invested_pct_history else 0.0

    return UnivASimResult(
        config_name=config_name,
        period_name=period_name,
        total_return_pct=tot_ret,
        cagr_pct=cagr,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        total_trades=total_trades,
        total_friction_usd=total_spread_usd + total_slippage_usd,
        avg_invested_pct=avg_inv,
        equity_series=eq_series,
    )


def main() -> int:
    print("=" * 95)
    print("   TEST DE SIMPLIFICACIÓN DE UNIVERSO A: SIN RANKING SECTORIAL (T-16 / AUDITORÍA v2.2)")
    print("=" * 95)

    daily_data = load_all_market_data(DATA_DIR, symbols=ALL_UNIVERSE_A_SYMBOLS)
    rf_df = pd.read_csv(RF_FILE)
    rf_df["_date"] = pd.to_datetime(rf_df["date"]).dt.date
    rf_map = dict(zip(rf_df["_date"], rf_df["daily_rf"]))

    periods = [
        ("Muestra Completa 2018–2026 (7.6a)", date(2018, 6, 25), date(2026, 2, 27)),
        ("Trienio 2020–2022", date(2020, 1, 2), date(2022, 12, 30)),
        ("2020", date(2020, 1, 2), date(2020, 12, 31)),
        ("2021", date(2021, 1, 4), date(2021, 12, 31)),
        ("2022", date(2022, 1, 3), date(2022, 12, 30)),
        ("2025", date(2025, 1, 2), date(2025, 12, 31)),
    ]

    print("\n1. Simulando Versión con Ranking Multi-Horizonte (Baseline Actual)...")
    results_ranked = {}
    for p_name, st, en in periods:
        results_ranked[p_name] = run_universe_a_simulation(
            daily_data=daily_data,
            rf_daily_map=rf_map,
            start_date=st,
            end_date=en,
            config_name="Con Ranking (Baseline)",
            period_name=p_name,
            use_ranking=True,
        )

    print("2. Simulando Versión Simplificada T-16 (Sin Ranking, Gate Absoluto + Vol Inversa)...")
    results_simplified = {}
    for p_name, st, en in periods:
        results_simplified[p_name] = run_universe_a_simulation(
            daily_data=daily_data,
            rf_daily_map=rf_map,
            start_date=st,
            end_date=en,
            config_name="Simplificado T-16 (Sin Ranking)",
            period_name=p_name,
            use_ranking=False,
        )

    # Imprimir Comparativa
    print("\n" + "=" * 110)
    print("           COMPARATIVA: UNIVERSO A CON RANKING vs SIMPLIFICADO SIN RANKING (T-16)")
    print("=" * 110)
    print(f"{'Período':<32} | {'Con Ranking (Ret / SR / DD)':<30} | {'Simplificado T-16 (Ret / SR / DD)':<32} | {'Delta Ret':<10}")
    print("-" * 110)

    for p_name, _, _ in periods:
        rr = results_ranked[p_name]
        rs = results_simplified[p_name]
        diff = rs.total_return_pct - rr.total_return_pct
        print(
            f"{p_name:<32} | "
            f"{rr.total_return_pct:+6.2f}% (SR: {rr.sharpe_ratio:4.2f}, DD: {rr.max_drawdown_pct:4.2f}%) | "
            f"{rs.total_return_pct:+6.2f}% (SR: {rs.sharpe_ratio:4.2f}, DD: {rs.max_drawdown_pct:4.2f}%) | "
            f"{diff:+6.2f}%"
        )
    print("=" * 110)

    # Generar Reporte Formal
    report_file = REPORTS_DIR / "universe_a_simplified.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# REPORTE DE AUDITORÍA — SIMPLIFICACIÓN DE UNIVERSO A (T-16)\n\n")
        f.write("| Campo | Valor |\n| :--- | :--- |\n")
        f.write("| **Documento** | T-16: Variante Simplificada de Universo A sin Ranking Sectorial |\n")
        f.write("| **Fecha** | 2026-09-21 |\n")
        f.write("| **Responde a** | Hallazgos F-20, F-13 y Mandato §3 T-16 de `AUDIT_FOLLOWUP_v2.2.md` |\n")
        f.write("| **Modelo de Ejecución** | Cuenta $2.000 USD, acciones enteras (`floor`), costos 3.5 bps one-way |\n\n---\n\n")

        f.write("## 1. Motivación y Principio de Parsimonia (F-20 / T-16)\n\n")
        f.write("> *'El propio test mostró que el ranking sectorial es plano (~+2.0% en todos los ranks, spread long-short t=-0.47, p=0.635). Un parámetro que no discrimina es superficie de sobreajuste sin contrapartida, y el PBO de 84.45% indica que sobra exactamente eso.'*\n\n")
        f.write("### Criterio Falsable del Auditor:\n")
        f.write("> *'Si el desempeño es equivalente, adoptar la variante simplificada como candidato primario. Menos parámetros libres reducen directamente el PBO en la próxima medición, que es el obstáculo real para capital.'*\n\n---\n\n")

        f.write("## 2. Tabla Comparativa por Períodos de Simulación\n\n")
        f.write("| Período | Variante | Retorno Acumulado | CAGR Anual | Sharpe Real | Max Drawdown | Trades | Exposición Media | Fricción ($) |\n")
        f.write("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for p_name, _, _ in periods:
            rr = results_ranked[p_name]
            rs = results_simplified[p_name]
            f.write(f"| {p_name} | **Con Ranking (Baseline)** | **{rr.total_return_pct:+.2f}%** | {rr.cagr_pct:.2f}% | {rr.sharpe_ratio:.2f} | {rr.max_drawdown_pct:.2f}% | {rr.total_trades} | {rr.avg_invested_pct:.1f}% | ${rr.total_friction_usd:.2f} |\n")
            f.write(f"| {p_name} | **Simplificado (Sin Ranking)** | **{rs.total_return_pct:+.2f}%** | {rs.cagr_pct:.2f}% | {rs.sharpe_ratio:.2f} | {rs.max_drawdown_pct:.2f}% | {rs.total_trades} | {rs.avg_invested_pct:.1f}% | ${rs.total_friction_usd:.2f} |\n")
            f.write("| | *Diferencia (Simplificado - Ranking)* | *" + f"{rs.total_return_pct - rr.total_return_pct:+.2f}%* | *" + f"{rs.cagr_pct - rr.cagr_pct:+.2f}%* | *" + f"{rs.sharpe_ratio - rr.sharpe_ratio:+.2f}* | *" + f"{rs.max_drawdown_pct - rr.max_drawdown_pct:+.2f}%* | | | |\n")

        f.write("\n---\n\n## 3. Eliminación de Parámetros Libres y Reducción de Complejidad\n\n")
        f.write("| Componente de Arquitectura | Versión con Ranking (Baseline) | Versión Simplificada (T-16) | Impacto en PBO |\n")
        f.write("| :--- | :--- | :--- | :--- |\n")
        f.write("| **Ventanas de Momentum** | 3 ventanas (21d, 63d, 126d) | **Ninguna** (0 ventanas) | Elimina 3 hiperparámetros |\n")
        f.write("| **Skip Period** | 5 días de omisión | **Ninguno** | Elimina 1 hiperparámetro |\n")
        f.write("| **Ponderación de Ranks** | Promedio de ordinales | **Ninguno** | Elimina regla de ordenamiento |\n")
        f.write("| **Truncamiento Top-N** | Top 4 sectores, Top 1 intl, etc. | **Ninguno** (todos los calificados) | Elimina 4 umbrales arbitrarios |\n")
        f.write("| **Buffer de Salida** | Buffer rank 7 (sectores) | **Ninguno** (salida si falla gate) | Elimina 3 buffers asimétricos |\n")
        f.write("| **Total Parámetros Eliminados** | Baseline | **11 Parámetros Libres Eliminados** | **Reducción directa de PBO** |\n\n")

        f.write("---\n\n## 4. Dictamen Institucional y Decisión de Candidato Primario\n\n")
        r_full_r = results_ranked["Muestra Completa 2018–2026 (7.6a)"]
        r_full_s = results_simplified["Muestra Completa 2018–2026 (7.6a)"]

        f.write(f"- **Desempeño Muestra Completa (2018–2026):**\n")
        f.write(f"  * Versión con Ranking    : **{r_full_r.total_return_pct:+.2f}%** (CAGR: {r_full_r.cagr_pct:.2f}%, Sharpe: {r_full_r.sharpe_ratio:.2f}, MaxDD: {r_full_r.max_drawdown_pct:.2f}%)\n")
        f.write(f"  * Versión Simplificada   : **{r_full_s.total_return_pct:+.2f}%** (CAGR: {r_full_s.cagr_pct:.2f}%, Sharpe: {r_full_s.sharpe_ratio:.2f}, MaxDD: {r_full_s.max_drawdown_pct:.2f}%)\n")
        f.write(f"  * Diferencia de Retorno : **{r_full_s.total_return_pct - r_full_r.total_return_pct:+.2f} puntos porcentuales** (Diferencia Sharpe: {r_full_s.sharpe_ratio - r_full_r.sharpe_ratio:+.2f})\n\n")

        if abs(r_full_s.sharpe_ratio - r_full_r.sharpe_ratio) <= 0.10 or r_full_s.sharpe_ratio >= r_full_r.sharpe_ratio:
            f.write("### Veredicto T-16: ADOPCIÓN RECOMENDADA.\n")
            f.write("El desempeño de la variante simplificada es cuantitativamente equivalente (o superior) al de la versión con ranking, pero **con 11 parámetros libres menos**.\n")
            f.write("Al no existir ventaja estadística en ordenar los sectores GICS (confirmado por F-20), el ranking es sobreajuste puro que infló el PBO al 84.45%.\n")
            f.write("Se adopta formalmente la **Variante Simplificada T-16** (Gate Absoluto + Volatilidad Inversa sobre todos los calificados) como la especificación del Candidato Primario para la Fase 3 de Paper Trading.\n")
        else:
            f.write("### Veredicto T-16: DESVIACIÓN MATERIAL.\n")
            f.write("La variante simplificada exhibe un comportamiento divergente que requiere análisis adicional antes de sustituir la arquitectura base.\n")

    print(f"\n[OK] Reporte formal generado en: {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
