import sys
import warnings
from datetime import date
from pathlib import Path
from typing import Any
from decimal import Decimal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from tbot.backtest.metrics import load_risk_free_rate_bil

from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy
from tbot.strategies.s9_turn_of_month_momentum import TurnOfMonthMomentumStrategy
from tbot.strategies.s11_volatility_squeeze import VolatilitySqueezeStrategy

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

INITIAL_CAPITAL = Decimal("2000.00")
REPORTS_DIR = PROJECT_ROOT / "reports"
CHARTS_DIR = REPORTS_DIR / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "JPM", "LLY", "XOM", "COST", "GLD", "SLV",
]

DAILY_2010_DIR = PROJECT_ROOT / "data" / "historical_2010_2026"
RF_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil_2010_2026.csv"

def load_daily_data(data_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    loader = HistoricalDataLoader(data_dir)
    data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        csv_path = data_dir / f"{sym}_daily.csv"
        if csv_path.exists():
            data[sym] = loader.load_from_csv(csv_path, symbol=sym, resolution="daily")
    return data

def compute_spy_buy_hold(spy_df: pd.DataFrame, start_date: date, end_date: date, capital: float) -> pd.Series:
    if spy_df is None or spy_df.empty:
        return pd.Series(dtype=float)
    df = spy_df.copy()
    if "_parsed_ts" in df.columns:
        df["_date"] = pd.to_datetime(df["_parsed_ts"]).dt.date
    elif "date" in df.columns:
        df["_date"] = pd.to_datetime(df["date"]).dt.date
    else:
        df["_date"] = pd.to_datetime(df.index).date
    df = df[(df["_date"] >= start_date) & (df["_date"] <= end_date)]
    if df.empty:
        return pd.Series(dtype=float)
    closes = df["close"].astype(float)
    returns = closes.pct_change().fillna(0.0)
    eq = capital * (1 + returns).cumprod()
    eq.index = pd.to_datetime(df["_date"])
    return eq

def run_strategy_backtest(strategy, daily_data, universe, start, end, rf_series):
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
    )
    config = BacktestConfig(**cfg_kwargs)
    engine = BacktestEngine(config=config, historical_daily=daily_data, historical_intraday={})
    return engine.run(start_date=start, end_date=end, resolution="daily")

def plot_equity_comparison_leverage(results_dict, spy_eq, strat_name, save_path):
    fig, ax = plt.subplots(figsize=(12, 6))
    capital = float(INITIAL_CAPITAL)
    colors = {"x1": "#2196F3", "x2": "#4CAF50", "x3": "#FF9800", "x5": "#E91E63"}

    for lev_key, eq_curve in results_dict.items():
        if not eq_curve.empty:
            ax.plot(eq_curve.index, eq_curve.values, linewidth=1.5,
                    label=f"{strat_name} ({lev_key})", color=colors.get(lev_key, "#000000"))

    if not spy_eq.empty:
        ax.plot(spy_eq.index, spy_eq.values, linewidth=1.2,
                label="S&P 500 (SPY)", color="#888888", linestyle="--")

    ax.axhline(y=capital, color="#e0e0e0", linestyle=":", linewidth=0.8)
    ax.set_title(f"{strat_name} (Apalancamiento) vs S&P 500", fontsize=13, fontweight="bold")
    ax.set_ylabel("Valor del Portafolio (USD) - Escala Log", fontsize=10)
    ax.set_xlabel("Fecha", fontsize=10)
    ax.set_yscale('log')
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=12))
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    fig.savefig(save_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  Grafico guardado: {save_path}")

def main():
    rf_series = None
    if RF_FILE.exists():
        rf_series = load_risk_free_rate_bil(RF_FILE)
    
    DAILY_START = date(2011, 1, 3)
    DAILY_END = date(2022, 12, 31)
    
    daily_data_14 = load_daily_data(DAILY_2010_DIR, UNIVERSE_14)
    spy_14 = daily_data_14.get("SPY")
    spy_eq = compute_spy_buy_hold(spy_14, DAILY_START, DAILY_END, float(INITIAL_CAPITAL))

    strategies = [
        ("S5 - Dual Momentum Leader", "s5_leverage", DualMomentumLeaderStrategy()),
        ("S9 - Turn of Month Momentum", "s9_leverage", TurnOfMonthMomentumStrategy()),
        ("S11 - Volatility Squeeze", "s11_leverage", VolatilitySqueezeStrategy())
    ]

    leverages = [1, 2, 3, 5]
    
    md_lines = []
    md_lines.append("# 📈 Informe de Estrategias y Apalancamiento (x1, x2, x3, x5)")
    md_lines.append(f"**Periodo:** {DAILY_START} a {DAILY_END}")
    md_lines.append("**Universo:** 14 Activos Principales (Tech, Finanzas, Salud, Energía, Metales)")
    md_lines.append("**Capital Inicial:** $2,000.00 USD\n")
    
    md_lines.append("> [!NOTE]")
    md_lines.append("> La simulación de apalancamiento multiplica linealmente el retorno diario de la estrategia (sin descontar costos de margen/préstamo). Los resultados reales serían ligeramente menores debido a estos costos.\n")
    
    spy_ret = (spy_eq.iloc[-1] / float(INITIAL_CAPITAL)) - 1
    md_lines.append(f"> **S&P 500 (SPY)** Retorno en el periodo: **{spy_ret*100:.2f}%**\n")

    for name, short_name, strat in strategies:
        print(f"Ejecutando {name}...")
        try:
            res = run_strategy_backtest(strat, daily_data_14, UNIVERSE_14, DAILY_START, DAILY_END, rf_series)
            
            eq_curve = res.equity_curve
            if eq_curve.empty:
                print(f"  {name} falló o no generó equity curve.")
                continue
                
            pct_ret = eq_curve.pct_change().fillna(0.0)
            
            results_dict = {}
            md_lines.append(f"## {name}")
            md_lines.append("| Apalancamiento | Retorno Total | Capital Final |")
            md_lines.append("|---|---|---|")
            
            for L in leverages:
                lev_pct = pct_ret * L
                lev_eq = float(INITIAL_CAPITAL) * (1 + lev_pct).cumprod()
                results_dict[f"x{L}"] = lev_eq
                
                final_cap = lev_eq.iloc[-1]
                total_ret = (final_cap / float(INITIAL_CAPITAL)) - 1
                
                md_lines.append(f"| **x{L}** | {total_ret*100:.2f}% | ${final_cap:,.2f} |")
                
            chart_path = CHARTS_DIR / f"{short_name}.png"
            plot_equity_comparison_leverage(results_dict, spy_eq, name, chart_path)
            
            md_lines.append(f"\n![Gráfico {name}](charts/{short_name}.png)\n")
            
        except Exception as e:
            print(f"  Error en {name}: {e}")

    with open(REPORTS_DIR / "informe_apalancamiento.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print("Informe generado exitosamente.")

if __name__ == "__main__":
    main()
