#!/usr/bin/env python3
"""Prueba de Estrés Fuera de Muestra (Out-of-Sample): 2020, 2021 y 2022.

Evalúa la robustez del sistema de toma de decisiones frente al riesgo de sobreajuste (overfitting):
- 2020: Crash de COVID-19 (febrero-marzo) + Estímulo histórico y rally tecnológico.
- 2021: Mercado alcista continuado y rotación de momentum.
- 2022: Mercado bajista severo (S&P 500 cayó -18.11%, Nasdaq -33%, inflación y suba de tasas).
- Período Completo (2020-2022): Rendimiento acumulado compuesto durante 3 años turbulentos.

Utiliza el sistema entero: universo de 14 activos, datos de mercado reales, ranking a 45 días,
trailing stop EMA(25), filtro de régimen macro (preservación en efectivo) y veto preventivo FinBERT.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Asegurar backend en path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from optimize_and_benchmark_portfolio import (
    SimulationResult,
    load_all_market_data,
    run_s5_momentum_simulation,
    run_strategy_simulation,
)
from tbot.backtest.engine import BacktestEngine  # noqa: F401
from tbot.news.store import HistoricalNewsFeatureStore

DATA_DIR = PROJECT_ROOT / "data" / "historical_2020_2022"
NEWS_FILE = PROJECT_ROOT / "data" / "news_features" / "historical_news_features_2020_2022.csv"

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]


def run_benchmark_period(
    daily_data: dict,
    finbert_store: HistoricalNewsFeatureStore,
    start_d: date,
    end_d: date,
    period_label: str,
    annual_cash_yield: float = 0.015,
) -> tuple[float, list[SimulationResult]]:
    """Ejecuta la comparación completa para un período específico."""
    spy_df = daily_data["SPY"]
    s_open = float(spy_df[spy_df["_parsed_date"] >= start_d]["open"].iloc[0])
    s_close = float(spy_df[spy_df["_parsed_date"] <= end_d]["close"].iloc[-1])
    spy_ret = ((s_close - s_open) / s_open) * 100.0

    period_results: list[SimulationResult] = []

    # 1. S3 Baseline (Trend Pullback con TP 2R Fijo, sin trailing)
    res_s3 = run_strategy_simulation(
        daily_data=daily_data,
        start_date=start_d,
        end_date=end_d,
        config_name=f"S3 Baseline ({period_label})",
        initial_capital=2000.0,
        risk_per_trade_pct=0.5,
        max_capital_per_trade_pct=0.15,
        use_trailing_stop=False,
        max_holding_days=5,
        take_profit_r=2.0,
        use_finbert=False,
        symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
    )
    period_results.append(res_s3)

    # 2. S5 Dual Momentum Leader v1.2.0 Oficial (14 activos + Metales + Veto FinBERT)
    res_s5 = run_s5_momentum_simulation(
        daily_data=daily_data,
        start_date=start_d,
        end_date=end_d,
        config_name=f"S5 v1.2.0 Oficial ({period_label})",
        initial_capital=2000.0,
        top_n_leaders=2,
        momentum_lookback_days=45,
        trailing_ema_period=25,
        use_finbert=True,
        finbert_store=finbert_store,
        annual_cash_yield=annual_cash_yield,
        symbols=UNIVERSE_14,
    )
    period_results.append(res_s5)

    return spy_ret, period_results


def main() -> None:
    print("=" * 80)
    print(" PRUEBA DE ESTRÉS FUERA DE MUESTRA (OUT-OF-SAMPLE): 2020, 2021 Y 2022")
    print(" Validación Anti-Sobreajuste (Anti-Overfitting) del Trading-Bot Cuantitativo")
    print(f" Universo Oficial ({len(UNIVERSE_14)} activos): {UNIVERSE_14}")
    print(f" Fuente de Datos de Mercado: {DATA_DIR}")
    print(f" Almacén de Noticias FinBERT: {NEWS_FILE.name}")
    print("=" * 80)

    print("\nCargando datos de mercado históricos (2019-2023)...")
    daily_data = load_all_market_data(DATA_DIR, symbols=UNIVERSE_14)
    print(f"[OK] Cargados {len(daily_data)} activos con éxito.")

    print("Cargando observaciones de noticias y FinBERT (2020-2022)...")
    finbert_store = HistoricalNewsFeatureStore.from_file(NEWS_FILE)
    print(f"[OK] Cargadas {len(finbert_store._df)} observaciones de noticias con éxito.")

    periods = [
        (date(2020, 1, 2), date(2020, 12, 31), "Año 2020 (Crash COVID + Rebote)", 0.005),
        (date(2021, 1, 4), date(2021, 12, 31), "Año 2021 (Mercado Alcista)", 0.005),
        (date(2022, 1, 3), date(2022, 12, 30), "Año 2022 (Mercado Bajista Severo)", 0.025),
        (date(2020, 1, 2), date(2022, 12, 30), "Período Completo (2020-2022: 3 Años)", 0.015),
    ]

    all_summaries = []

    for start_d, end_d, label, cash_yield in periods:
        print("\n" + "-" * 80)
        print(f" SIMULANDO: {label.upper()}")
        print(f" Rango: {start_d} a {end_d} | Tasa Efectivo (Cash Yield): {cash_yield*100:.1f}% anual")
        print("-" * 80)

        spy_ret, res_list = run_benchmark_period(
            daily_data=daily_data,
            finbert_store=finbert_store,
            start_d=start_d,
            end_d=end_d,
            period_label=label,
            annual_cash_yield=cash_yield,
        )

        res_s3, res_s5 = res_list[0], res_list[1]
        all_summaries.append({
            "period": label,
            "spy_ret": spy_ret,
            "s3": res_s3,
            "s5": res_s5,
        })

        print(f" [BENCHMARK] S&P 500 (SPY):        {spy_ret:+8.2f}%")
        print(f" [BASELINE]  S3 Trend Pullback:    {res_s3.total_return_pct:+8.2f}% | Alpha vs SPY: {res_s3.total_return_pct - spy_ret:+8.2f}% | Sharpe: {res_s3.sharpe_ratio:4.2f} | MaxDD: {res_s3.max_drawdown_pct:4.2f}% | Trades: {res_s3.total_trades}")
        print(f" [SISTEMA]   S5 Dual Momentum 1.2: {res_s5.total_return_pct:+8.2f}% | Alpha vs SPY: {res_s5.total_return_pct - spy_ret:+8.2f}% | Sharpe: {res_s5.sharpe_ratio:4.2f} | MaxDD: {res_s5.max_drawdown_pct:4.2f}% | WinRate: {res_s5.win_rate_pct:4.1f}% | PF: {res_s5.profit_factor:4.2f} | Trades: {res_s5.total_trades}")

    # Tabla Consolidada Final
    print("\n" + "=" * 96)
    print("        TABLA CONSOLIDADA DE ESTRÉS FUERA DE MUESTRA (OUT-OF-SAMPLE: 2020-2022)")
    print("=" * 96)
    print(f"{'Período Evaluado':<34} | {'SPY B&H':<9} | {'S3 Ret':<9} | {'S5 Retorno':<11} | {'Alpha SPY':<10} | {'Sharpe':<7} | {'MaxDD':<7} | {'WinRate':<7} | {'PF':<5}")
    print("-" * 96)

    for item in all_summaries:
        p_name = item["period"]
        sp_r = item["spy_ret"]
        s3_r = item["s3"].total_return_pct
        s5 = item["s5"]
        alpha = s5.total_return_pct - sp_r
        print(f"{p_name:<34} | {sp_r:+8.2f}% | {s3_r:+8.2f}% | {s5.total_return_pct:+10.2f}% | {alpha:+9.2f}% | {s5.sharpe_ratio:<7.2f} | {s5.max_drawdown_pct:<6.2f}% | {s5.win_rate_pct:<6.1f}% | {s5.profit_factor:<5.2f}")

    print("=" * 96)


if __name__ == "__main__":
    main()
