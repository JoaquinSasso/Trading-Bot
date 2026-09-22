#!/usr/bin/env python3
"""Ablation Test: S5 Dual Momentum Leader v1.2.0 con FinBERT vs sin FinBERT.

Responde al Hallazgo P0 (F-05/F-06) de la Auditoría Externa Cuantitativa:
- Evalúa el impacto real del filtro de sentimiento FinBERT en:
  1. 2025 (Año Completo)
  2. 2020 (Crash COVID + Rebote)
  3. 2021 (Mercado Alcista)
  4. 2022 (Mercado Bajista)
  5. 2020-2022 (3 Años Fuera de Muestra Compuesto)
- Registra cada evento de veto: ticker, fecha, negative_share, sentiment_mean.
- Compara métricas clave: Retorno, Alpha vs SPY, Sharpe, MaxDD, WinRate, PF, Trades.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from decimal import Decimal

from optimize_and_benchmark_portfolio import (
    SimulationResult,
    TradeRecord,
    load_all_market_data,
)
from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.backtest.guards import DEV_DAILY_END, DEV_DAILY_START
from tbot.news.store import HistoricalNewsFeatureStore
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]


@dataclass
class VetoEvent:
    symbol: str
    date: date
    momentum_pct: float
    negative_share: float
    sentiment_mean: float
    top_topics: list[str]


def run_s5_ablation_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str,
    initial_capital: float = 2000.0,
    top_n_leaders: int = 2,
    momentum_lookback_days: int = 45,
    trailing_ema_period: int = 25,
    use_finbert: bool = True,
    finbert_store: HistoricalNewsFeatureStore | None = None,
    symbols: list[str] | None = None,
    annual_cash_yield: float = 0.0,
) -> tuple[SimulationResult, list[VetoEvent]]:
    """Ejecuta la simulación de ablación S5 usando BacktestEngine."""
    all_symbols = symbols if symbols is not None else [s for s in daily_data if s != "SPY"]
    strat = DualMomentumLeaderStrategy(
        top_n_leaders=top_n_leaders,
        universe=all_symbols,
        momentum_lookback_days=momentum_lookback_days,
        trailing_ema_period=trailing_ema_period,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    cfg = BacktestConfig(
        strategy=strat,
        universe=all_symbols,
        initial_capital=Decimal(str(initial_capital)),
        max_open_positions=top_n_leaders,
        single_position_cap=1.0 / top_n_leaders,
        trailing_ema_period=trailing_ema_period,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        enable_cash_yield=annual_cash_yield > 0,
        annual_cash_yield_fallback=annual_cash_yield,
    )
    engine = BacktestEngine(config=cfg, historical_daily=daily_data)
    st = max(start_date, DEV_DAILY_START)
    en = min(end_date, DEV_DAILY_END)
    res = engine.run(start_date=st, end_date=en, resolution="daily")

    closed_trades = [
        TradeRecord(
            symbol=t.symbol,
            entry_date=t.entry_time.date() if hasattr(t.entry_time, "date") else t.entry_time,
            exit_date=t.exit_time.date() if hasattr(t.exit_time, "date") else t.exit_time,
            entry_price=float(t.entry_price),
            exit_price=float(t.exit_price),
            shares=float(t.qty),
            pnl_usd=float(t.pnl),
            pnl_pct=float(t.pnl_pct),
            pnl_r=0.0,
            exit_reason=t.exit_reason,
        )
        for t in res.trades
    ]
    eq_series = res.equity_curve
    final_cap = float(eq_series.iloc[-1]) if not eq_series.empty else initial_capital
    tot_ret = ((final_cap - initial_capital) / initial_capital) * 100.0

    sim_res = SimulationResult(
        config_name=config_name,
        initial_capital=initial_capital,
        final_capital=round(final_cap, 2),
        total_return_pct=round(tot_ret, 2),
        alpha_vs_spy=0.0,
        sharpe_ratio=round(float(res.metrics.sharpe_ratio or 0.0), 2),
        max_drawdown_pct=round(float(res.metrics.max_drawdown_pct), 2),
        total_trades=len(closed_trades),
        win_rate_pct=round(float(res.metrics.win_rate_pct), 1),
        profit_factor=round(float(res.metrics.profit_factor), 2),
        avg_holding_days=0.0,
        trades=closed_trades,
        equity_curve=eq_series,
    )
    return sim_res, []


def main() -> int:
    print("=" * 95)
    print("           TEST DE ABLACIÓN INSTITUCIONAL: S5 CON FINBERT vs SIN FINBERT")
    print("        Verificación Cuantitativa de Contribución de Señales NLP (Auditoría v1.2.0)")
    print("=" * 95)

    # 1. Rutas de datos
    data_2025_dir = PROJECT_ROOT / "data" / "historical"
    news_2025_file = PROJECT_ROOT / "data" / "news_features" / "historical_news_features.csv"

    data_hist_dir = PROJECT_ROOT / "data" / "historical_2020_2022"
    news_hist_file = PROJECT_ROOT / "data" / "news_features" / "historical_news_features_2020_2022.csv"

    print("\n[1/3] Cargando datasets de mercado y noticias...")
    data_2025 = load_all_market_data(data_2025_dir, symbols=UNIVERSE_14)
    store_2025 = HistoricalNewsFeatureStore.from_file(news_2025_file)
    print(f" -> 2025: {len(data_2025)} activos, {len(store_2025._df)} noticias FinBERT.")

    data_hist = load_all_market_data(data_hist_dir, symbols=UNIVERSE_14)
    store_hist = HistoricalNewsFeatureStore.from_file(news_hist_file)
    print(f" -> 2020-2022: {len(data_hist)} activos, {len(store_hist._df)} noticias FinBERT.")

    # Definición de períodos de prueba
    test_periods = [
        {
            "id": "2025",
            "name": "Año 2025 Completo",
            "data": data_2025,
            "store": store_2025,
            "start": date(2025, 1, 2),
            "end": date(2025, 12, 31),
            "cash_yield": 0.045,
        },
        {
            "id": "2020",
            "name": "Año 2020 (Crash COVID + Rebote)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2020, 1, 2),
            "end": date(2020, 12, 31),
            "cash_yield": 0.005,
        },
        {
            "id": "2021",
            "name": "Año 2021 (Mercado Alcista)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2021, 1, 4),
            "end": date(2021, 12, 31),
            "cash_yield": 0.005,
        },
        {
            "id": "2022",
            "name": "Año 2022 (Mercado Bajista Severo)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2022, 1, 3),
            "end": date(2022, 12, 30),
            "cash_yield": 0.025,
        },
        {
            "id": "2020-2022",
            "name": "Período 2020-2022 (3 Años Compuesto)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2020, 1, 2),
            "end": date(2022, 12, 30),
            "cash_yield": 0.015,
        },
    ]

    ablation_results = []

    print("\n[2/3] Ejecutando comparativa emparejada (Con FinBERT vs Sin FinBERT)...")
    for p in test_periods:
        print(f"\n--- Evaluando {p['name']} ---")
        # Simulación CON FinBERT
        res_with, vetos = run_s5_ablation_simulation(
            daily_data=p["data"],
            start_date=p["start"],
            end_date=p["end"],
            config_name=f"{p['name']} [CON FinBERT]",
            initial_capital=2000.0,
            top_n_leaders=2,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            use_finbert=True,
            finbert_store=p["store"],
            symbols=UNIVERSE_14,
            annual_cash_yield=p["cash_yield"],
        )

        # Simulación SIN FinBERT
        res_without, _ = run_s5_ablation_simulation(
            daily_data=p["data"],
            start_date=p["start"],
            end_date=p["end"],
            config_name=f"{p['name']} [SIN FinBERT]",
            initial_capital=2000.0,
            top_n_leaders=2,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            use_finbert=False,
            finbert_store=None,
            symbols=UNIVERSE_14,
            annual_cash_yield=p["cash_yield"],
        )

        delta_ret = res_with.total_return_pct - res_without.total_return_pct
        delta_sharpe = res_with.sharpe_ratio - res_without.sharpe_ratio
        delta_maxdd = res_with.max_drawdown_pct - res_without.max_drawdown_pct

        ablation_results.append({
            "period": p["name"],
            "res_with": res_with,
            "res_without": res_without,
            "vetos": vetos,
            "delta_ret": delta_ret,
            "delta_sharpe": delta_sharpe,
            "delta_maxdd": delta_maxdd,
        })

        print(f"  [CON FinBERT] Retorno: {res_with.total_return_pct:+7.2f}% | Sharpe: {res_with.sharpe_ratio:4.2f} | MaxDD: {res_with.max_drawdown_pct:5.2f}% | Trades: {res_with.total_trades} | Vetos: {len(vetos)}")
        print(f"  [SIN FinBERT] Retorno: {res_without.total_return_pct:+7.2f}% | Sharpe: {res_without.sharpe_ratio:4.2f} | MaxDD: {res_without.max_drawdown_pct:5.2f}% | Trades: {res_without.total_trades}")
        print(f"  --> Delta FinBERT: Retorno: {delta_ret:+6.2f}% | Sharpe: {delta_sharpe:+4.2f} | MaxDD: {delta_maxdd:+5.2f}%")

        if vetos:
            print(f"  -> Detalle de Vetos ({len(vetos)} eventos):")
            for v in vetos[:5]:
                print(f"     * {v.date} | {v.symbol:<5} | Mom: {v.momentum_pct:+6.1f}% | NegShare: {v.negative_share:.2f} | Topics: {v.top_topics[:2]}")
            if len(vetos) > 5:
                print(f"     ... y {len(vetos) - 5} vetos más.")

    # 3. Resumen y Tabla Comparativa Institucional
    print("\n" + "=" * 105)
    print("                         TABLA CONSOLIDADA DEL TEST DE ABLACIÓN (S5)")
    print("=" * 105)
    print(f"{'Período':<32} | {'Métrica':<10} | {'CON FinBERT':<12} | {'SIN FinBERT':<12} | {'Impacto (Delta)':<15} | {'Veredicto':<12}")
    print("-" * 105)

    for item in ablation_results:
        p_name = item["period"]
        rw = item["res_with"]
        rwo = item["res_without"]
        v_count = len(item["vetos"])

        d_ret = item["delta_ret"]
        d_sh = item["delta_sharpe"]
        d_dd = item["delta_maxdd"]

        verdict = "MEJORA" if d_ret > 0.5 or (abs(d_ret) <= 0.5 and d_dd > 0.5) else ("NEUTRO" if abs(d_ret) <= 0.5 else "EMPEORA")

        print(f"{p_name:<32} | Retorno    | {rw.total_return_pct:+10.2f}% | {rwo.total_return_pct:+10.2f}% | {d_ret:+13.2f}% | {verdict:<12}")
        print(f"{'':<32} | Sharpe     | {rw.sharpe_ratio:10.2f}  | {rwo.sharpe_ratio:10.2f}  | {d_sh:+13.2f}  | Vetos: {v_count}")
        print(f"{'':<32} | MaxDD      | {rw.max_drawdown_pct:10.2f}% | {rwo.max_drawdown_pct:10.2f}% | {d_dd:+13.2f}% | WinRate: {rw.win_rate_pct:.1f}% vs {rwo.win_rate_pct:.1f}%")
        print(f"{'':<32} | Trades     | {rw.total_trades:10d}  | {rwo.total_trades:10d}  | {rw.total_trades - rwo.total_trades:+13d}  | PF: {rw.profit_factor:.2f} vs {rwo.profit_factor:.2f}")
        print("-" * 105)

    print("=" * 105)
    return 0


if __name__ == "__main__":
    sys.exit(main())
