#!/usr/bin/env python3
"""Test de Monotonicidad del Ranking (T-01 - Auditoría Externa v2.0).

Determina empíricamente si el ranking de momentum ordena los retornos futuros:
1. Tabla A: Comparación de Top-2, Top-4, Top-6, Top-8 y Equiponderado (14 activos).
2. Tabla B: Análisis de Buckets (retorno forward a 45 días por posición de ranking 1..14).
3. Tabla C: Análisis de Buckets sobre Universo A (Sectores GICS, 11 ETFs).
4. Genera el informe institucional formal 'reports/rank_monotonicity.md'.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from optimize_and_benchmark_portfolio import (
    SimulationResult,
    TradeRecord,
    compute_drawdown,
    compute_sharpe,
    ema,
    load_all_market_data,
)
from tbot.regime.filter import MarketRegime

DATA_14_DIR = PROJECT_ROOT / "data" / "historical_14"
DATA_UNIV_A_DIR = PROJECT_ROOT / "data" / "universe_a"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]

US_SECTORS_11 = ["XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE"]


# -----------------------------------------------------------------------------
# 1. Simulación Paramétrica por Top-N y Equiponderado
# -----------------------------------------------------------------------------
def run_s5_top_n_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str,
    top_n: int | None = 4,  # None significa equiponderado sobre todos los elegibles
    momentum_lookback_days: int = 45,
    trailing_ema_period: int = 25,
    annual_cash_yield: float = 0.0,
    symbols: list[str] | None = None,
) -> SimulationResult:
    all_symbols = symbols if symbols is not None else [s for s in daily_data.keys() if s != "SPY"]
    spy_df = daily_data["SPY"]

    all_dates = sorted(
        set.intersection(
            *[
                set(
                    df[
                        (df["_parsed_date"] >= start_date)
                        & (df["_parsed_date"] <= end_date)
                    ]["_parsed_date"]
                )
                for s, df in daily_data.items()
                if s in all_symbols or s == "SPY"
            ]
        )
    )

    cash = 2000.0
    initial_capital = 2000.0
    open_positions: dict[str, dict] = {}
    closed_trades: list[TradeRecord] = []
    equity_history: dict[date, float] = {}

    daily_yield_rate = (
        (1.0 + annual_cash_yield) ** (1.0 / 252.0) - 1.0
        if annual_cash_yield > 0
        else 0.0
    )

    for cur_date in all_dates:
        # Filtro de Régimen SPY > EMA50
        spy_past = spy_df[spy_df["_parsed_date"] < cur_date]
        regime = MarketRegime.BULL_CALM
        if len(spy_past) >= 50:
            spy_closes = spy_past["close"].astype(float)
            ema50 = ema(spy_closes, 50).iloc[-1]
            if spy_closes.iloc[-1] < ema50:
                regime = MarketRegime.BEAR

        if regime == MarketRegime.BEAR:
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
                ex_p = float(bar["close"])
                pnl = (ex_p - pos["entry_price"]) * pos["shares"]
                cash += ex_p * pos["shares"]
                closed_trades.append(
                    TradeRecord(
                        symbol=sym,
                        entry_date=pos["entry_date"],
                        exit_date=cur_date,
                        entry_price=pos["entry_price"],
                        exit_price=ex_p,
                        shares=pos["shares"],
                        pnl_usd=pnl,
                        pnl_pct=(ex_p - pos["entry_price"]) / pos["entry_price"],
                        pnl_r=0.0,
                        exit_reason="regime_bear",
                    )
                )
                del open_positions[sym]

            if daily_yield_rate > 0:
                cash *= (1.0 + daily_yield_rate)

        else:
            # Gestión de trailing stop en posiciones abiertas
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                pos["days_held"] += 1
                bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
                cur_close = float(bar["close"])

                past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                ema25 = ema(past_bars["close"].astype(float), trailing_ema_period).iloc[-1]

                if cur_close < pos["stop_loss"] or (pos["days_held"] >= 3 and cur_close < ema25):
                    ex_p = cur_close
                    pnl = (ex_p - pos["entry_price"]) * pos["shares"]
                    cash += ex_p * pos["shares"]
                    closed_trades.append(
                        TradeRecord(
                            symbol=sym,
                            entry_date=pos["entry_date"],
                            exit_date=cur_date,
                            entry_price=pos["entry_price"],
                            exit_price=ex_p,
                            shares=pos["shares"],
                            pnl_usd=pnl,
                            pnl_pct=(ex_p - pos["entry_price"]) / pos["entry_price"],
                            pnl_r=0.0,
                            exit_reason="trailing_ema25",
                        )
                    )
                    del open_positions[sym]
                else:
                    pos["stop_loss"] = max(pos["stop_loss"], ema25 * 0.985)

            # Buscar candidatos elegibles
            candidates = []
            for sym in all_symbols:
                if sym in open_positions:
                    continue
                past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                if len(past_bars) < momentum_lookback_days + 5:
                    continue
                closes = past_bars["close"].astype(float)
                cur_close = float(closes.iloc[-1])
                ema25 = ema(closes, trailing_ema_period).iloc[-1]
                if cur_close < ema25:
                    continue
                p_past = float(closes.iloc[-momentum_lookback_days])
                if p_past <= 0:
                    continue
                mom = (cur_close - p_past) / p_past
                if mom <= 0:
                    continue

                candidates.append((sym, mom, cur_close, ema25))

            candidates.sort(key=lambda x: x[1], reverse=True)

            total_equity = cash + sum(
                p["shares"]
                * float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
                for s, p in open_positions.items()
            )

            if top_n is not None:
                max_slots = top_n
                available_slots = max_slots - len(open_positions)
                selected = candidates[:available_slots]
                target_pct = 1.0 / max_slots
            else:
                # Equiponderado sobre todos los elegibles
                max_slots = max(1, len(all_symbols))
                available_slots = max_slots - len(open_positions)
                selected = candidates[:available_slots]
                target_pct = 1.0 / max(len(candidates) + len(open_positions), 1)

            for sym, mom, cur_close, ema25 in selected:
                alloc = min(cash, total_equity * target_pct)
                if alloc >= 50.0:
                    shares = alloc / cur_close
                    cash -= shares * cur_close
                    stop_loss = min(cur_close * 0.95, ema25 * 0.985)
                    open_positions[sym] = {
                        "symbol": sym,
                        "entry_date": cur_date,
                        "entry_price": cur_close,
                        "shares": shares,
                        "stop_loss": stop_loss,
                        "initial_stop": stop_loss,
                        "days_held": 0,
                    }

        invested_val = sum(
            p["shares"]
            * float(daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0])
            for s, p in open_positions.items()
        )
        equity_history[cur_date] = cash + invested_val

    # Cierre final
    final_date = all_dates[-1]
    for sym, pos in list(open_positions.items()):
        ex_p = float(daily_data[sym][daily_data[sym]["_parsed_date"] == final_date]["close"].iloc[0])
        pnl = (ex_p - pos["entry_price"]) * pos["shares"]
        cash += ex_p * pos["shares"]
        closed_trades.append(
            TradeRecord(
                symbol=sym,
                entry_date=pos["entry_date"],
                exit_date=final_date,
                entry_price=pos["entry_price"],
                exit_price=ex_p,
                shares=pos["shares"],
                pnl_usd=pnl,
                pnl_pct=(ex_p - pos["entry_price"]) / pos["entry_price"],
                pnl_r=0.0,
                exit_reason="end_of_period",
            )
        )
    equity_history[final_date] = cash

    eq_series = pd.Series(equity_history)
    final_cap = float(eq_series.iloc[-1])
    total_ret = ((final_cap - initial_capital) / initial_capital) * 100.0

    spy_start = float(spy_df[spy_df["_parsed_date"] == all_dates[0]]["open"].iloc[0])
    spy_end = float(spy_df[spy_df["_parsed_date"] == all_dates[-1]]["close"].iloc[0])
    spy_ret = ((spy_end - spy_start) / spy_start) * 100.0
    alpha = total_ret - spy_ret

    max_dd_pct, _ = compute_drawdown(eq_series)
    sharpe = compute_sharpe(eq_series)

    wins = [t for t in closed_trades if t.pnl_usd > 0]
    losses = [t for t in closed_trades if t.pnl_usd <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0

    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0

    return SimulationResult(
        config_name=config_name,
        initial_capital=initial_capital,
        final_capital=round(final_cap, 2),
        total_return_pct=round(total_ret, 2),
        alpha_vs_spy=round(alpha, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        total_trades=len(closed_trades),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(pf, 2),
        avg_holding_days=0.0,
        trades=closed_trades,
        equity_curve=eq_series,
    )


# -----------------------------------------------------------------------------
# 2. Análisis de Buckets: Retorno Forward 45d por Posición de Ranking
# -----------------------------------------------------------------------------
def analyze_rank_monotonicity_buckets(
    daily_data: dict[str, pd.DataFrame],
    symbols: list[str],
    start_date: date,
    end_date: date,
    fwd_days: int = 45,
    lookback_days: int = 45,
    rebalance_step_days: int = 5,  # Muestreo semanal
) -> pd.DataFrame:
    """Calcula el retorno forward a N días para cada posición ordinal de ranking."""
    # Obtener fechas de negociación alineadas
    common_dates = sorted(
        set.intersection(
            *[
                set(df["_parsed_date"])
                for s, df in daily_data.items()
                if s in symbols
            ]
        )
    )

    valid_dates = [d for d in common_dates if start_date <= d <= end_date]

    observations = []  # dict: {date, rank, symbol, mom_lookback, fwd_return}

    for idx, cur_date in enumerate(valid_dates):
        # Muestreo cada N sesiones para evitar overlap excesivo
        if idx % rebalance_step_days != 0:
            continue

        # Verificar si hay 45 sesiones hacia adelante
        fwd_idx = idx + fwd_days
        if fwd_idx >= len(valid_dates):
            break
        fwd_date = valid_dates[fwd_idx]

        ranks_today = []
        for sym in symbols:
            df = daily_data[sym]
            past_bars = df[df["_parsed_date"] <= cur_date]
            if len(past_bars) < lookback_days + 5:
                continue

            closes = past_bars["close"].astype(float)
            c_now = float(closes.iloc[-1])
            c_past = float(closes.iloc[-lookback_days])
            if c_past <= 0:
                continue

            mom = (c_now - c_past) / c_past

            # Precio futuro a fwd_days sesiones
            future_bar = df[df["_parsed_date"] == fwd_date]
            if future_bar.empty:
                continue
            c_future = float(future_bar["close"].iloc[0])
            fwd_ret = (c_future - c_now) / c_now

            ranks_today.append((sym, mom, fwd_ret))

        if len(ranks_today) < 5:
            continue

        # Ordenar por momentum descendente (Rank 1 = mayor momentum)
        ranks_today.sort(key=lambda x: x[1], reverse=True)

        for rank_pos, (sym, mom, fwd_ret) in enumerate(ranks_today, 1):
            observations.append({
                "date": cur_date,
                "rank": rank_pos,
                "symbol": sym,
                "momentum": mom,
                "fwd_return": fwd_ret * 100.0,
            })

    obs_df = pd.DataFrame(observations)
    if obs_df.empty:
        return pd.DataFrame()

    universe_mean = obs_df["fwd_return"].mean()

    bucket_stats = []
    for rank_val, group in obs_df.groupby("rank"):
        n_obs = len(group)
        mean_ret = group["fwd_return"].mean()
        median_ret = group["fwd_return"].median()
        std_ret = group["fwd_return"].std()

        # t-statistic vs media del universo
        t_stat, p_val = stats.ttest_1samp(group["fwd_return"], universe_mean)

        bucket_stats.append({
            "rank": int(rank_val),
            "n_obs": n_obs,
            "mean_fwd_ret": mean_ret,
            "median_fwd_ret": median_ret,
            "std_ret": std_ret,
            "t_stat": t_stat if not np.isnan(t_stat) else 0.0,
            "p_val": p_val if not np.isnan(p_val) else 1.0,
        })

    b_df = pd.DataFrame(bucket_stats)
    return b_df, universe_mean, obs_df


# -----------------------------------------------------------------------------
# 3. Main Execution y Generación del Reporte T-01
# -----------------------------------------------------------------------------
def main() -> int:
    print("=" * 95)
    print("      TEST INSTITUCIONAL DE MONOTONICIDAD DEL RANKING (T-01)")
    print("      Respuesta al Hallazgo Crítico F-13 — Auditoría v2.0")
    print("=" * 95)

    print("\n[1/3] Cargando datos continuos de mercado...")
    daily_14 = load_all_market_data(DATA_14_DIR, symbols=UNIVERSE_14)
    daily_univ_a = load_all_market_data(DATA_UNIV_A_DIR, symbols=US_SECTORS_11 + ["SPY"])
    print(f" -> 14 Activos cargados: {len(daily_14)} símbolos.")
    print(f" -> Sectores GICS cargados: {len(daily_univ_a)} símbolos.")

    # -------------------------------------------------------------------------
    # TABLA A: Comparación de Configuraciones Top-N
    # -------------------------------------------------------------------------
    print("\n[2/3] Ejecutando simulaciones comparativas Top-N (2020 a 2025)...")
    configs = [
        ("Top-2 (50% c/u)", 2),
        ("Top-4 (25% c/u)", 4),
        ("Top-6 (16.7% c/u)", 6),
        ("Top-8 (12.5% c/u)", 8),
        ("Equiponderado Elegibles", None),
    ]

    periods = [
        ("2020", date(2020, 1, 2), date(2020, 12, 31), 0.005),
        ("2021", date(2021, 1, 4), date(2021, 12, 31), 0.005),
        ("2022", date(2022, 1, 3), date(2022, 12, 30), 0.025),
        ("2025", date(2025, 1, 2), date(2025, 12, 31), 0.045),
        ("Trienio 2020-22", date(2020, 1, 2), date(2022, 12, 30), 0.015),
    ]

    results_matrix = {c[0]: {} for c in configs}

    for c_name, top_n_val in configs:
        print(f"  Simulando {c_name:<25} ...")
        for p_name, start_d, end_d, yld in periods:
            res = run_s5_top_n_simulation(
                daily_data=daily_14,
                start_date=start_d,
                end_date=end_d,
                config_name=f"{c_name} ({p_name})",
                top_n=top_n_val,
                momentum_lookback_days=45,
                trailing_ema_period=25,
                annual_cash_yield=yld,
                symbols=[s for s in UNIVERSE_14 if s != "SPY"],
            )
            results_matrix[c_name][p_name] = res

    # -------------------------------------------------------------------------
    # TABLA B: Análisis de Buckets sobre 14 Activos
    # -------------------------------------------------------------------------
    print("\n[3/3] Calculando retornos forward por posición de ranking (Buckets 1..14)...")
    bucket_df_14, univ_mean_14, obs_df_14 = analyze_rank_monotonicity_buckets(
        daily_data=daily_14,
        symbols=[s for s in UNIVERSE_14 if s != "SPY"],
        start_date=date(2020, 1, 2),
        end_date=date(2025, 12, 31),
        fwd_days=45,
        lookback_days=45,
        rebalance_step_days=5,
    )

    # -------------------------------------------------------------------------
    # TABLA C: Análisis de Buckets sobre Sectores GICS (11 ETFs)
    # -------------------------------------------------------------------------
    bucket_df_gics, univ_mean_gics, obs_df_gics = analyze_rank_monotonicity_buckets(
        daily_data=daily_univ_a,
        symbols=US_SECTORS_11,
        start_date=date(2020, 1, 2),
        end_date=date(2025, 12, 31),
        fwd_days=45,
        lookback_days=45,
        rebalance_step_days=5,
    )

    # -------------------------------------------------------------------------
    # Imprimir en Consola
    # -------------------------------------------------------------------------
    print("\n" + "=" * 105)
    print("                    TABLA A: CONFIGURACIONES TOP-N (14 ACTIVOS)")
    print("=" * 105)
    print(f"{'Configuración':<26} | {'2020':<8} | {'2021':<8} | {'2022':<8} | {'2025':<8} | {'Trienio':<9} | {'Sharpe':<6} | {'MaxDD':<7}")
    print("-" * 105)

    for c_name, _ in configs:
        r20 = results_matrix[c_name]["2020"].total_return_pct
        r21 = results_matrix[c_name]["2021"].total_return_pct
        r22 = results_matrix[c_name]["2022"].total_return_pct
        r25 = results_matrix[c_name]["2025"].total_return_pct
        r3y = results_matrix[c_name]["Trienio 2020-22"]
        print(f"{c_name:<26} | {r20:+7.2f}% | {r21:+7.2f}% | {r22:+7.2f}% | {r25:+7.2f}% | {r3y.total_return_pct:+8.2f}% | {r3y.sharpe_ratio:5.2f} | {r3y.max_drawdown_pct:6.2f}%")
    print("=" * 105)

    print("\n" + "=" * 105)
    print(f"      TABLA B: RETORNO FORWARD 45D POR RANKING (14 ACTIVOS) — Media Universo: {univ_mean_14:+.2f}%")
    print("=" * 105)
    print(f"{'Rank':<5} | {'N Obs':<6} | {'Retorno Medio 45d':<18} | {'Mediano':<9} | {'Desv. Est.':<10} | {'t-stat vs Univ':<15} | {'p-value':<7}")
    print("-" * 105)

    for _, row in bucket_df_14.iterrows():
        print(f"{int(row['rank']):<5} | {int(row['n_obs']):<6} | {row['mean_fwd_ret']:+16.2f}% | {row['median_fwd_ret']:+7.2f}% | {row['std_ret']:9.2f}% | {row['t_stat']:+13.2f} | {row['p_val']:6.3f}")
    print("=" * 105)

    print("\n" + "=" * 105)
    print(f"      TABLA C: RETORNO FORWARD 45D POR RANKING (SECTORES GICS) — Media Universo: {univ_mean_gics:+.2f}%")
    print("=" * 105)
    print(f"{'Rank':<5} | {'N Obs':<6} | {'Retorno Medio 45d':<18} | {'Mediano':<9} | {'Desv. Est.':<10} | {'t-stat vs Univ':<15} | {'p-value':<7}")
    print("-" * 105)

    for _, row in bucket_df_gics.iterrows():
        print(f"{int(row['rank']):<5} | {int(row['n_obs']):<6} | {row['mean_fwd_ret']:+16.2f}% | {row['median_fwd_ret']:+7.2f}% | {row['std_ret']:9.2f}% | {row['t_stat']:+13.2f} | {row['p_val']:6.3f}")
    print("=" * 105)

    # -------------------------------------------------------------------------
    # Generar Reporte Markdown
    # -------------------------------------------------------------------------
    report_path = REPORTS_DIR / "rank_monotonicity.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# REPORTE DE AUDITORÍA — TEST DE MONOTONICIDAD DEL RANKING (T-01)\n\n")
        f.write("| Campo | Valor |\n| :--- | :--- |\n")
        f.write("| **Documento** | T-01: Verificación de Monotonicidad de la Señal de Momentum |\n")
        f.write("| **Fecha** | 2026-09-20 |\n")
        f.write("| **Responde a** | Hallazgo F-13 de `docs/AUDIT_FOLLOWUP_v2.0.md` |\n")
        f.write(f"| **Media Universo 14 Activos** | {univ_mean_14:+.2f}% a 45 días |\n")
        f.write(f"| **Media Universo GICS** | {univ_mean_gics:+.2f}% a 45 días |\n\n---\n\n")

        f.write("## 1. Tabla A: Comparación de Configuraciones Top-N\n\n")
        f.write("Simulación sobre el universo de 14 activos, mismo filtro de régimen macro (SPY > EMA50) y mismas salidas (trailing EMA25).\n\n")
        f.write("| Configuración | 2020 | 2021 | 2022 | 2025 | Trienio 2020-22 | Sharpe | MaxDD |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for c_name, _ in configs:
            r20 = results_matrix[c_name]["2020"].total_return_pct
            r21 = results_matrix[c_name]["2021"].total_return_pct
            r22 = results_matrix[c_name]["2022"].total_return_pct
            r25 = results_matrix[c_name]["2025"].total_return_pct
            r3y = results_matrix[c_name]["Trienio 2020-22"]
            f.write(f"| **{c_name}** | {r20:+.2f}% | {r21:+.2f}% | {r22:+.2f}% | {r25:+.2f}% | **{r3y.total_return_pct:+.2f}%** | {r3y.sharpe_ratio:.2f} | {r3y.max_drawdown_pct:.2f}% |\n")

        f.write("\n---\n\n## 2. Tabla B: Retorno Forward a 45 Días por Posición de Ranking (14 Activos)\n\n")
        f.write("Muestreo semanal de cada rebalanceo de 2020 a 2025. Cada observación mide el retorno real obtenido a 45 sesiones futuras por el activo que ocupaba ese puesto de ranking.\n\n")
        f.write("| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media univ | p-value |\n")
        f.write("| :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for _, row in bucket_df_14.iterrows():
            f.write(f"| **Rank {int(row['rank'])}** | {int(row['n_obs'])} | **{row['mean_fwd_ret']:+.2f}%** | {row['median_fwd_ret']:+.2f}% | {row['std_ret']:.2f}% | {row['t_stat']:+.2f} | {row['p_val']:.3f} |\n")

        f.write("\n---\n\n## 3. Tabla C: Retorno Forward a 45 Días por Ranking (Sectores GICS)\n\n")
        f.write("| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media univ | p-value |\n")
        f.write("| :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")
        for _, row in bucket_df_gics.iterrows():
            f.write(f"| **Rank {int(row['rank'])}** | {int(row['n_obs'])} | **{row['mean_fwd_ret']:+.2f}%** | {row['median_fwd_ret']:+.2f}% | {row['std_ret']:.2f}% | {row['t_stat']:+.2f} | {row['p_val']:.3f} |\n")

        f.write("\n---\n\n## 4. Diagnóstico e Interpretación Institucional (Criterios §3 T-01)\n\n")
        # Evaluación automática de monotonicidad
        r1_ret = bucket_df_14[bucket_df_14["rank"] == 1]["mean_fwd_ret"].iloc[0]
        r2_ret = bucket_df_14[bucket_df_14["rank"] == 2]["mean_fwd_ret"].iloc[0]
        r3_ret = bucket_df_14[bucket_df_14["rank"] == 3]["mean_fwd_ret"].iloc[0]
        r4_ret = bucket_df_14[bucket_df_14["rank"] == 4]["mean_fwd_ret"].iloc[0]
        bottom_ret = bucket_df_14[bucket_df_14["rank"] >= 10]["mean_fwd_ret"].mean()

        f.write(f"- **Top Ranks (1-2) vs Mid Ranks (3-4):** Rank 1 ({r1_ret:+.2f}%) y Rank 2 ({r2_ret:+.2f}%) vs Rank 3 ({r3_ret:+.2f}%) y Rank 4 ({r4_ret:+.2f}%).\n")
        f.write(f"- **Top Ranks vs Fondo de Tabla (Ranks 10-13):** Media Ranks 1-4 ({bucket_df_14[bucket_df_14['rank'] <= 4]['mean_fwd_ret'].mean():+.2f}%) vs Fondo ({bottom_ret:+.2f}%).\n")

    print(f"\n[OK] Reporte formal generado en: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
