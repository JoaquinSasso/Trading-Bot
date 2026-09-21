#!/usr/bin/env python3
"""Simulación y Calibración de Inverse Volatility Sizing (P1 - Auditoría Externa).

Evalúa el impacto de reemplazar la asignación fija 50/50 (2 activos) por:
1. Baseline v1.2.0: Top-2 Líderes (50% fijo cada uno).
2. Variante A: Top-4 Líderes Equal-Weight (25% fijo cada uno).
3. Variante B: Top-4 Inverse Volatility (1/σ, cap 30%).
4. Variante C: Top-4 Inverse Volatility + Target Volatility 15% (cap 25% + cash yield).

Períodos evaluados:
- 2025 (Año Completo)
- 2020 (Crash COVID + Rebote)
- 2021 (Mercado Alcista)
- 2022 (Mercado Bajista Severo)
- 2020-2022 (Trienio Compuesto)
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from optimize_and_benchmark_portfolio import (
    EASTERN_TZ,
    SimulationResult,
    TradeRecord,
    compute_drawdown,
    compute_sharpe,
    ema,
    load_all_market_data,
)
from tbot.regime.filter import MarketRegime

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]


def run_s5_sizing_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str,
    initial_capital: float = 2000.0,
    top_n_leaders: int = 4,
    momentum_lookback_days: int = 45,
    trailing_ema_period: int = 25,
    sizing_mode: str = "inv_vol",  # 'equal_weight', 'inv_vol', 'inv_vol_target'
    target_vol: float = 0.15,  # 15% anual para inv_vol_target
    max_asset_weight: float = 0.25,  # Max 25% por activo
    vol_lookback_days: int = 60,
    symbols: list[str] | None = None,
    annual_cash_yield: float = 0.0,
) -> SimulationResult:
    all_symbols = symbols if symbols is not None else list(daily_data.keys())
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

    cash = initial_capital
    open_positions: dict[str, dict] = {}
    closed_trades: list[TradeRecord] = []
    equity_history: dict[date, float] = {}

    daily_yield_rate = (
        (1.0 + annual_cash_yield) ** (1.0 / 252.0) - 1.0
        if annual_cash_yield > 0
        else 0.0
    )

    for cur_date in all_dates:
        # 1. Filtro Macro de Régimen: SPY sobre EMA50
        spy_past = spy_df[spy_df["_parsed_date"] < cur_date]
        regime = MarketRegime.BULL_CALM
        if len(spy_past) >= 50:
            spy_closes = spy_past["close"].astype(float)
            ema50 = ema(spy_closes, 50).iloc[-1]
            if spy_closes.iloc[-1] < ema50:
                regime = MarketRegime.BEAR

        # 2. Si régimen bajista: rotación preventiva 100% a efectivo
        if regime == MarketRegime.BEAR:
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
                ex_p = float(bar["close"])
                pnl = (ex_p - pos["entry_price"]) * pos["shares"]
                pnl_pct = (ex_p - pos["entry_price"]) / pos["entry_price"]
                r_dist = pos["entry_price"] - pos["initial_stop"]
                pnl_r = pnl / (r_dist * pos["shares"]) if r_dist > 0 else 0.0
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
                        pnl_pct=pnl_pct,
                        pnl_r=pnl_r,
                        exit_reason="regime_bear_exit",
                    )
                )
                del open_positions[sym]

            if daily_yield_rate > 0:
                cash *= 1.0 + daily_yield_rate

        # 3. Si régimen alcista: gestionar trailing stop y líderes
        else:
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                pos["days_held"] += 1
                bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
                cur_close = float(bar["close"])

                past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                ema20 = ema(past_bars["close"].astype(float), trailing_ema_period).iloc[-1]

                # Salida si cierra bajo stop o bajo EMA25 tras 3 días
                if cur_close < pos["stop_loss"] or (pos["days_held"] >= 3 and cur_close < ema20):
                    ex_p = cur_close
                    pnl = (ex_p - pos["entry_price"]) * pos["shares"]
                    pnl_pct = (ex_p - pos["entry_price"]) / pos["entry_price"]
                    r_dist = pos["entry_price"] - pos["initial_stop"]
                    pnl_r = pnl / (r_dist * pos["shares"]) if r_dist > 0 else 0.0
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
                            pnl_pct=pnl_pct,
                            pnl_r=pnl_r,
                            exit_reason="trailing_ema25",
                        )
                    )
                    del open_positions[sym]
                else:
                    pos["stop_loss"] = max(pos["stop_loss"], ema20 * 0.985)

            # 4. Asignación de capital en líderes si hay cupo
            available_slots = top_n_leaders - len(open_positions)
            if available_slots > 0:
                candidates = []
                for sym in all_symbols:
                    if sym in open_positions:
                        continue
                    past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                    if len(past_bars) < max(momentum_lookback_days, vol_lookback_days) + 5:
                        continue
                    closes = past_bars["close"].astype(float)
                    cur_close = float(closes.iloc[-1])
                    ema20 = ema(closes, trailing_ema_period).iloc[-1]
                    if cur_close < ema20:
                        continue
                    p_past = float(closes.iloc[-momentum_lookback_days])
                    if p_past <= 0:
                        continue
                    mom_pct = (cur_close - p_past) / p_past
                    if mom_pct <= 0:
                        continue

                    # Volatilidad a 60 días anualizada
                    ret_series = closes.iloc[-vol_lookback_days:].pct_change().dropna()
                    sigma = float(ret_series.std() * np.sqrt(252))
                    if np.isnan(sigma) or sigma <= 0.01:
                        sigma = 0.25

                    candidates.append((sym, mom_pct, cur_close, ema20, sigma))

                # Ordenar descendente por momentum
                candidates.sort(key=lambda x: x[1], reverse=True)
                selected_candidates = candidates[:available_slots]

                # Equidad total del portafolio hoy
                total_equity = cash + sum(
                    p["shares"]
                    * float(
                        daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0]
                    )
                    for s, p in open_positions.items()
                )

                if selected_candidates:
                    # Determinar ponderación según sizing_mode
                    if sizing_mode == "equal_weight":
                        target_pct = min(1.0 / top_n_leaders, max_asset_weight)
                        weights = {c[0]: target_pct for c in selected_candidates}

                    elif sizing_mode == "inv_vol":
                        # Ponderación 1/σ relativa normalizada
                        inv_vols = [1.0 / c[4] for c in selected_candidates]
                        sum_inv = sum(inv_vols)
                        raw_weights = [iv / sum_inv for iv in inv_vols]
                        # Escalar por la fracción de cupos disponibles
                        fraction_available = available_slots / top_n_leaders
                        weights = {}
                        for c, rw in zip(selected_candidates, raw_weights):
                            w = min(rw * fraction_available, max_asset_weight)
                            weights[c[0]] = w

                    elif sizing_mode == "inv_vol_target":
                        # Volatilidad objetivo del portafolio (ej. 15% anual)
                        inv_vols = [1.0 / c[4] for c in selected_candidates]
                        sum_inv = sum(inv_vols)
                        norm_w = [iv / sum_inv for iv in inv_vols]
                        # Volatilidad promedio estimada de los candidatos
                        est_vol = sum(w * c[4] for w, c in zip(norm_w, selected_candidates))
                        # Escala por target vol
                        scale = min(1.0, target_vol / est_vol) if est_vol > 0 else 1.0
                        fraction_available = available_slots / top_n_leaders
                        weights = {}
                        for c, nw in zip(selected_candidates, norm_w):
                            w = min(nw * scale * fraction_available, max_asset_weight)
                            weights[c[0]] = w
                    else:
                        raise ValueError(f"Modo de sizing desconocido: {sizing_mode}")

                    for sym, mom_pct, cur_close, ema20, sigma in selected_candidates:
                        target_weight = weights.get(sym, 0.25)
                        alloc = min(cash, total_equity * target_weight)
                        if alloc >= 50.0:
                            shares = alloc / cur_close
                            cash -= shares * cur_close
                            stop_loss = min(cur_close * 0.95, ema20 * 0.985)
                            open_positions[sym] = {
                                "symbol": sym,
                                "entry_date": cur_date,
                                "entry_price": cur_close,
                                "shares": shares,
                                "stop_loss": stop_loss,
                                "initial_stop": stop_loss,
                                "take_profit": 999999.0,
                                "days_held": 0,
                            }

        # 5. Registrar equidad diaria
        invested_val = sum(
            p["shares"]
            * float(
                daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0]
            )
            for s, p in open_positions.items()
        )
        total_eq = cash + invested_val
        equity_history[cur_date] = total_eq

    # Cerrar posiciones al final
    final_date = all_dates[-1]
    for sym, pos in list(open_positions.items()):
        ex_p = float(
            daily_data[sym][daily_data[sym]["_parsed_date"] == final_date]["close"].iloc[0]
        )
        pnl = (ex_p - pos["entry_price"]) * pos["shares"]
        pnl_pct = (ex_p - pos["entry_price"]) / pos["entry_price"]
        r_dist = pos["entry_price"] - pos["initial_stop"]
        pnl_r = pnl / (r_dist * pos["shares"]) if r_dist > 0 else 0.0
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
                pnl_pct=pnl_pct,
                pnl_r=pnl_r,
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
    avg_days = (
        float(np.mean([(t.exit_date - t.entry_date).days for t in closed_trades]))
        if closed_trades
        else 0.0
    )

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
        avg_holding_days=round(avg_days, 1),
        trades=closed_trades,
        equity_curve=eq_series,
    )


def main() -> int:
    print("=" * 100)
    print("      EVALUACIÓN CUANTITATIVA INSTITUCIONAL: INVERSE VOLATILITY SIZING (P1)")
    print("             Análisis Comparativo de Dimensionamiento de Riesgo y Drawdown")
    print("=" * 100)

    data_2025_dir = PROJECT_ROOT / "data" / "historical"
    data_hist_dir = PROJECT_ROOT / "data" / "historical_2020_2022"

    print("\nCargando datos de mercado...")
    data_2025 = load_all_market_data(data_2025_dir, symbols=UNIVERSE_14)
    data_hist = load_all_market_data(data_hist_dir, symbols=UNIVERSE_14)

    periods = [
        {"name": "2025 (Año Completo)", "data": data_2025, "start": date(2025, 1, 2), "end": date(2025, 12, 31), "yield": 0.045},
        {"name": "2020 (Crash COVID + Rebote)", "data": data_hist, "start": date(2020, 1, 2), "end": date(2020, 12, 31), "yield": 0.005},
        {"name": "2021 (Mercado Alcista)", "data": data_hist, "start": date(2021, 1, 4), "end": date(2021, 12, 31), "yield": 0.005},
        {"name": "2022 (Mercado Bajista)", "data": data_hist, "start": date(2022, 1, 3), "end": date(2022, 12, 30), "yield": 0.025},
        {"name": "2020-2022 (3 Años Compuesto)", "data": data_hist, "start": date(2020, 1, 2), "end": date(2022, 12, 30), "yield": 0.015},
    ]

    configs = [
        {"name": "1. Baseline v1.2.0 (Top-2, 50/50 Fijo)", "top_n": 2, "mode": "equal_weight", "max_w": 0.50, "target_vol": 0.20},
        {"name": "2. Top-4 Equal-Weight (25% c/u)", "top_n": 4, "mode": "equal_weight", "max_w": 0.25, "target_vol": 0.20},
        {"name": "3. Top-4 Inverse Volatility (1/sigma, cap 30%)", "top_n": 4, "mode": "inv_vol", "max_w": 0.30, "target_vol": 0.20},
        {"name": "4. Top-4 Inv-Vol + Target Vol 15% (cap 25%)", "top_n": 4, "mode": "inv_vol_target", "max_w": 0.25, "target_vol": 0.15},
    ]

    all_results = {}

    for p in periods:
        p_name = p["name"]
        print(f"\n" + "-" * 75)
        print(f" SIMULANDO PERÍODO: {p_name.upper()}")
        print("-" * 75)
        all_results[p_name] = []

        for cfg in configs:
            res = run_s5_sizing_simulation(
                daily_data=p["data"],
                start_date=p["start"],
                end_date=p["end"],
                config_name=cfg["name"],
                initial_capital=2000.0,
                top_n_leaders=cfg["top_n"],
                momentum_lookback_days=45,
                trailing_ema_period=25,
                sizing_mode=cfg["mode"],
                target_vol=cfg["target_vol"],
                max_asset_weight=cfg["max_w"],
                symbols=UNIVERSE_14,
                annual_cash_yield=p["yield"],
            )
            all_results[p_name].append(res)
            print(f" -> {cfg['name']:<42} | Ret: {res.total_return_pct:+7.2f}% | Sharpe: {res.sharpe_ratio:4.2f} | MaxDD: {res.max_drawdown_pct:5.2f}% | WinRate: {res.win_rate_pct:4.1f}% | Trades: {res.total_trades}")

    # Tabla Consolidada Multianual
    print("\n" + "=" * 115)
    print("              TABLA MULTIANUAL COMPARATIVA DE DIMENSIONAMIENTO (SIZING BENCHMARK)")
    print("=" * 115)
    print(f"{'Período':<28} | {'Configuración':<40} | {'Retorno':<9} | {'Sharpe':<7} | {'MaxDD':<8} | {'WinRate':<7} | {'PF':<5}")
    print("-" * 115)

    for p_name, res_list in all_results.items():
        for r in res_list:
            print(f"{p_name:<28} | {r.config_name:<40} | {r.total_return_pct:+8.2f}% | {r.sharpe_ratio:<7.2f} | {r.max_drawdown_pct:<7.2f}% | {r.win_rate_pct:<6.1f}% | {r.profit_factor:<5.2f}")
        print("-" * 115)

    print("=" * 115)
    return 0


if __name__ == "__main__":
    sys.exit(main())
