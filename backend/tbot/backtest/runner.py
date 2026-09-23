"""Runner CLI para ejecución de backtests y generación de reportes Markdown.

Uso:
    python -m tbot.backtest.runner --strategy s5 --start 2020-01-01 --end 2022-12-31 --capital 2000 \
        --trial-ledger reports/trials.json
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import BacktestConfig, ReplayEngine
from tbot.backtest.guards import assert_not_holdout
from tbot.strategies.s2_mean_reversion_rsi2 import MeanReversionRSI2Strategy
from tbot.strategies.s3_trend_pullback import TrendPullbackStrategy
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy
from tbot.strategies.s9_turn_of_month_momentum import TurnOfMonthMomentumStrategy
from tbot.strategies.s10_antonacci_dual_momentum import AntonacciDualMomentumStrategy
from tbot.strategies.s11_volatility_squeeze import VolatilitySqueezeStrategy

# S1 y S4 fueron retiradas del repositorio: el runner ya no las importa.
STRATEGY_MAP = {
    "s2": MeanReversionRSI2Strategy,
    "mean_reversion_rsi2": MeanReversionRSI2Strategy,
    "s3": TrendPullbackStrategy,
    "trend_pullback": TrendPullbackStrategy,
    "s5": DualMomentumLeaderStrategy,
    "dual_momentum_leader": DualMomentumLeaderStrategy,
    "s9": TurnOfMonthMomentumStrategy,
    "s10": AntonacciDualMomentumStrategy,
    "s11": VolatilitySqueezeStrategy,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ejecuta backtests cuantitativos y genera reporte Markdown."
    )
    parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        default="s5",
        help="Estrategia a evaluar (s2, s3, s5, s9, s10, s11 o nombre completo).",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2020-01-01",
        help="Fecha inicio (YYYY-MM-DD) [Ventana desarrollo: 2010-01-01 a 2022-12-31].",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2022-12-31",
        help="Fecha fin (YYYY-MM-DD) [Ventana desarrollo: 2010-01-01 a 2022-12-31].",
    )
    parser.add_argument(
        "--capital", type=float, default=2000.0, help="Capital inicial en USD (default 2000)."
    )
    parser.add_argument(
        "--trials", type=int, default=5, help="Cantidad mínima de combinaciones probadas para DSR."
    )
    parser.add_argument(
        "--trial-ledger",
        type=str,
        default=None,
        help="Ledger JSON de variantes probadas; el DSR usa max(--trials, variantes registradas).",
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Generar datos sintéticos si falta un CSV (solo para pruebas; nunca para decidir).",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/historical_2020_2022",
        help="Directorio de datos históricos de desarrollo.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="docs/backtest_results",
        help="Directorio destino para reportes Markdown.",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()

    strat_key = args.strategy.lower()
    if strat_key not in STRATEGY_MAP:
        print(
            f"Error: Estrategia '{args.strategy}' no reconocida. Opciones: {list(STRATEGY_MAP.keys())}"
        )
        return 1

    strat_cls = STRATEGY_MAP[strat_key]
    strategy = strat_cls()

    start_d = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_d = datetime.strptime(args.end, "%Y-%m-%d").date()

    # Validación inmediata de Guarda de Holdout en CLI
    assert_not_holdout(start=start_d, end=end_d, resolution="daily")

    loader = HistoricalDataLoader(data_dir=args.data_dir)
    print(f"=== Iniciando Backtest Oficial: {strategy.id} v{strategy.version} ===")
    print(
        f"Período: {start_d} al {end_d} | Capital: ${args.capital:,.2f} | Ensayos DSR: {args.trials}"
    )

    # Verificar o generar datos históricos
    symbols = (
        strategy.universe
        if strategy.universe is not None
        else ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]
    )
    daily_bars: dict[str, pd.DataFrame] = {}
    intraday_bars: dict[str, pd.DataFrame] = {}

    # Si hay SPY en los requerimientos o universo, asegurar que esté cargado para el régimen
    needed_symbols = set(symbols) | {"SPY"}

    for sym in needed_symbols:
        csv_file = Path(args.data_dir) / f"{sym}_daily.csv"
        hist_start = start_d - pd.Timedelta(days=365)
        h_start_d = hist_start.date() if hasattr(hist_start, "date") else hist_start

        if csv_file.exists():
            print(f"Cargando {sym} desde {csv_file}...")
            df_d = loader.load_from_csv(
                csv_file,
                symbol=sym,
                start_date=h_start_d,
                end_date=end_d,
                resolution="daily",
            )
            daily_bars[sym] = df_d
        else:
            if not args.allow_synthetic:
                print(
                    f"Error: falta {csv_file}. No se generan datos sintéticos salvo con --allow-synthetic."
                )
                return 1
            print(f"Generando histórico sintético para {sym} ({start_d} a {end_d})...")
            # Incluir 1 año previo para cálculo de medias móviles (SMA200)
            df_d, df_i = loader.generate_synthetic_history(
                symbol=sym,
                start_date=h_start_d,
                end_date=end_d,
                seed=hash(sym) % 100000,
            )
            daily_bars[sym] = df_d
            if strategy.data_requirements.needs_intraday_bars:
                intraday_bars[sym] = df_i

    # Crear y ejecutar motor de replay
    config = BacktestConfig(
        strategy=strategy,
        initial_capital=Decimal(str(args.capital)),
        num_tested_trials=args.trials,
        trial_ledger_path=args.trial_ledger,
        trial_family=strategy.id if args.trial_ledger else None,
    )
    engine = ReplayEngine(
        config=config,
        historical_daily=daily_bars,
        historical_intraday=intraday_bars
        if strategy.data_requirements.needs_intraday_bars
        else None,
    )

    metrics, trades, equity_curve = engine.run(start_date=start_d, end_date=end_d)

    # Imprimir reporte en consola
    print("\n" + metrics.summary_markdown())

    # Guardar reporte en archivo Markdown
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"backtest_{strategy.id}_{args.start}_{args.end}.md"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"# Reporte Oficial de Backtest — {strategy.id}\n\n")
        f.write(metrics.summary_markdown())
        f.write("\n\n### Detalle de Primeras 10 Operaciones\n\n")
        f.write(
            "| Trade ID | Símbolo | Entrada | Salida | Cantidad | P&L ($) | P&L (R) | Motivo Salida |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|\n")
        for t in trades[:10]:
            f.write(
                f"| {t.trade_id} | {t.symbol} | ${t.entry_price:,.2f} | ${t.exit_price:,.2f} | {t.qty} | "
                f"${t.pnl:+,.2f} | {t.pnl_r:+.2f}R | {t.exit_reason} |\n"
            )
        f.write(f"\n*Total de operaciones en el período:* {len(trades)}\n")

    print(f"Reporte guardado exitosamente en: {report_file}")
    return 0 if metrics.passed_gate else 2


if __name__ == "__main__":
    sys.exit(main())
