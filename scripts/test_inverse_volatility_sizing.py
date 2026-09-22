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
from datetime import date
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
    SimulationResult,
    TradeRecord,
    compute_drawdown,
    compute_sharpe,
    load_all_market_data,
)

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
    """Ejecuta la simulación de dimensionamiento de S5 usando el motor unificado BacktestEngine."""
    from decimal import Decimal

    from tbot.backtest.engine import BacktestConfig, BacktestEngine
    from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy

    all_symbols = symbols if symbols is not None else [s for s in daily_data if s != "SPY"]
    strat = DualMomentumLeaderStrategy(
        momentum_lookback_days=momentum_lookback_days,
        top_n_leaders=top_n_leaders,
        trailing_ema_period=trailing_ema_period,
        stop_buffer_pct=0.05,
        max_holding_days=30,
        universe=all_symbols,
    )
    enable_vc = (sizing_mode == "inv_vol_target")
    config = BacktestConfig(
        strategy=strat,
        universe=all_symbols,
        initial_capital=Decimal(str(initial_capital)),
        max_open_positions=top_n_leaders,
        single_position_cap=max_asset_weight,
        enable_vol_control=enable_vc,
        target_portfolio_vol=target_vol if enable_vc else 0.12,
        vol_lookback_days=vol_lookback_days,
        trailing_ema_period=trailing_ema_period,
        stop_buffer_pct=0.015,
        max_holding_sessions=30,
        exit_on_bear_regime=True,
        enable_cash_yield=annual_cash_yield > 0,
        annual_cash_yield_fallback=annual_cash_yield,
        integer_shares=False,
        apply_retail_costs=False,
    )
    engine = BacktestEngine(config=config, historical_daily=daily_data)
    result = engine.run(start_date=start_date, end_date=end_date)

    eq_series = result.equity_curve
    final_cap = float(eq_series.iloc[-1]) if not eq_series.empty else initial_capital
    total_ret = ((final_cap - initial_capital) / initial_capital) * 100.0

    spy_df = daily_data["SPY"]
    spy_sub = spy_df[(spy_df["_parsed_date"] >= start_date) & (spy_df["_parsed_date"] <= end_date)]
    if not spy_sub.empty:
        spy_start = float(spy_sub["open"].iloc[0])
        spy_end = float(spy_sub["close"].iloc[-1])
        spy_ret = ((spy_end - spy_start) / spy_start) * 100.0
    else:
        spy_ret = 0.0
    alpha = total_ret - spy_ret

    trade_records = [
        TradeRecord(
            symbol=t.symbol,
            entry_date=t.entry_time.date() if hasattr(t.entry_time, "date") else t.entry_time,
            exit_date=t.exit_time.date() if hasattr(t.exit_time, "date") else t.exit_time,
            entry_price=float(t.entry_price),
            exit_price=float(t.exit_price),
            shares=float(t.qty),
            pnl_usd=float(t.pnl),
            pnl_pct=float(t.pnl_pct),
            pnl_r=float(t.pnl_r),
            exit_reason=t.exit_reason,
        )
        for t in result.trades
    ]

    max_dd_pct, _ = compute_drawdown(eq_series)
    sharpe = compute_sharpe(eq_series)

    wins = [t for t in trade_records if t.pnl_usd > 0]
    losses = [t for t in trade_records if t.pnl_usd <= 0]
    win_rate = (len(wins) / len(trade_records) * 100.0) if trade_records else 0.0

    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    avg_days = (
        float(np.mean([(t.exit_date - t.entry_date).days for t in trade_records]))
        if trade_records
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
        total_trades=len(trade_records),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(pf, 2),
        avg_holding_days=round(avg_days, 1),
        trades=trade_records,
        equity_curve=eq_series,
    )


def main() -> int:
    print("=" * 100)
    print("      EVALUACIÓN CUANTITATIVA INSTITUCIONAL: INVERSE VOLATILITY SIZING (P1)")
    print("             Análisis Comparativo de Dimensionamiento de Riesgo y Drawdown")
    print("=" * 100)

    data_hist_dir = PROJECT_ROOT / "data" / "historical_2020_2022"

    print("\nCargando datos de mercado...")
    data_hist = load_all_market_data(data_hist_dir, symbols=UNIVERSE_14)

    # Ventanas de desarrollo (2020-2022) conformes a Rule 0 Holdout Guard
    periods = [
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
        print("\n" + "-" * 75)
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
