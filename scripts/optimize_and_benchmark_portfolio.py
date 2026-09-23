#!/usr/bin/env python3
"""Script de optimización sistemática y benchmark contra el S&P 500 (+15.70% en 2025).

Compara de manera transparente y rigurosa diferentes configuraciones:
1. Baseline S3 (0.5% riesgo, TP fijo 2R, holding 5d)
2. S3 con Sizing Eficiente (1.0% riesgo, asignación de capital optimizada)
3. S3 con Salida Trailing Stop (Break-even en 1.5R, trailing en EMA20/mínimo 5d, holding hasta 15d)
4. S3 Optimizada Completa (Sizing 1.0% + Trailing Stop)
5. S3 Optimizada + Multiplicador de Sentimiento FinBERT (Veto en pánico, boost 1.25x en optimismo)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.news.store import HistoricalNewsFeatureStore
from tbot.indicators.pure import ema  # Re-exported for backward compatibility and universe scripts

EASTERN_TZ = ZoneInfo("America/New_York")


@dataclass
class TradeRecord:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    shares: float
    pnl_usd: float
    pnl_pct: float
    pnl_r: float
    exit_reason: str


@dataclass
class SimulationResult:
    config_name: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    alpha_vs_spy: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_holding_days: float
    trades: list[TradeRecord]
    equity_curve: pd.Series


def load_all_market_data(data_dir: Path, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Carga los datos diarios reales de todos los activos disponibles."""
    loader = HistoricalDataLoader(data_dir=data_dir)
    daily_data: dict[str, pd.DataFrame] = {}
    target_symbols = symbols or [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
    ]

    for sym in target_symbols:
        csv_p = data_dir / f"{sym}_daily.csv"
        if csv_p.exists():
            df = loader.load_from_csv(csv_p, symbol=sym)
            df["_parsed_date"] = pd.to_datetime(df["date"]).dt.date
            daily_data[sym] = df.sort_values("date").reset_index(drop=True)
    return daily_data


def compute_drawdown(equity_series: pd.Series) -> tuple[float, float]:
    """Calcula el máximo drawdown en porcentaje y en dólares."""
    peaks = equity_series.cummax()
    dd_series = (equity_series - peaks) / peaks
    max_dd_pct = abs(float(dd_series.min())) * 100.0
    return max_dd_pct, float((peaks - equity_series).max())


def compute_sharpe(equity_series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Calcula el Sharpe ratio anualizado de la curva de equidad."""
    daily_rets = equity_series.pct_change().dropna()
    if len(daily_rets) < 5 or daily_rets.std() == 0:
        return 0.0
    rf_daily = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0
    excess_rets = daily_rets - rf_daily
    return float(np.sqrt(252.0) * excess_rets.mean() / daily_rets.std())


def run_strategy_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str,
    initial_capital: float = 2000.0,
    risk_per_trade_pct: float = 0.5,
    max_capital_per_trade_pct: float = 0.25,
    use_trailing_stop: bool = False,
    trailing_ema_period: int = 20,
    max_holding_days: int = 5,
    take_profit_r: float = 2.0,
    use_finbert: bool = False,
    finbert_store: HistoricalNewsFeatureStore | None = None,
    max_open_positions: int = 4,
    symbols: list[str] | None = None,
) -> SimulationResult:
    """Ejecuta simulación de Estrategia S3 usando el motor unificado BacktestEngine."""
    from decimal import Decimal

    from tbot.backtest.engine import BacktestConfig, BacktestEngine
    from tbot.strategies.s3_trend_pullback import TrendPullbackStrategy

    all_symbols = symbols if symbols is not None else [s for s in daily_data if s != "SPY"]
    strat = TrendPullbackStrategy(
        ema_fast_period=trailing_ema_period,
        reward_risk_ratio=take_profit_r,
        max_holding_days=max_holding_days,
    )
    config = BacktestConfig(
        strategy=strat,
        universe=all_symbols,
        initial_capital=Decimal(str(initial_capital)),
        risk_per_trade_pct=risk_per_trade_pct,
        single_position_cap=max_capital_per_trade_pct,
        max_open_positions=max_open_positions,
        trailing_ema_period=trailing_ema_period if use_trailing_stop else 0,
        max_holding_sessions=max_holding_days,
        integer_shares=False,
        apply_retail_costs=False,
    )
    engine = BacktestEngine(config=config, historical_daily=daily_data)
    res = engine.run(start_date=start_date, end_date=end_date, resolution="daily")

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
            pnl_r=float(t.pnl_pct) / (risk_per_trade_pct / 100.0) if risk_per_trade_pct > 0 else 0.0,
            exit_reason=t.exit_reason,
        )
        for t in res.trades
    ]

    eq_series = res.equity_curve
    final_cap = float(eq_series.iloc[-1]) if not eq_series.empty else initial_capital
    tot_ret = ((final_cap - initial_capital) / initial_capital) * 100.0
    sharpe = float(res.metrics.sharpe_ratio or 0.0)
    max_dd = float(res.metrics.max_drawdown_pct)
    win_rate = float(res.metrics.win_rate_pct)
    pf = float(res.metrics.profit_factor)

    return SimulationResult(
        config_name=config_name,
        initial_capital=initial_capital,
        final_capital=final_cap,
        total_return_pct=tot_ret,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        total_trades=len(closed_trades),
        win_rate_pct=win_rate,
        profit_factor=pf,
        closed_trades=closed_trades,
        equity_series=eq_series,
    )


def run_s5_momentum_simulation(
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
) -> SimulationResult:
    """Simula la Estrategia S5: Dual Momentum Leader usando el motor unificado BacktestEngine."""
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
    config = BacktestConfig(
        strategy=strat,
        universe=all_symbols,
        initial_capital=Decimal(str(initial_capital)),
        max_open_positions=top_n_leaders,
        single_position_cap=1.0 / top_n_leaders,
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
            pnl_r=0.0,
            exit_reason=t.exit_reason,
        )
        for t in result.trades
    ]

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
        sharpe_ratio=round(float(result.metrics.sharpe_ratio or 0.0), 2),
        max_drawdown_pct=round(float(result.metrics.max_drawdown_pct), 2),
        total_trades=len(trade_records),
        win_rate_pct=round(float(result.metrics.win_rate_pct), 1),
        profit_factor=round(float(result.metrics.profit_factor), 2),
        avg_holding_days=round(avg_days, 1),
        trades=trade_records,
        equity_curve=eq_series,
    )


def run_hybrid_simulation(
    res_core: SimulationResult,
    res_satellite: SimulationResult,
    config_name: str,
    spy_ret: float,
    core_weight: float = 0.70,
    satellite_weight: float = 0.30,
) -> SimulationResult:
    """Combina ponderadamente una estrategia Core (S5 Momentum) y una Satélite (S3 Pullback)."""
    # Curvas de equidad alineadas
    common_dates = res_core.equity_curve.index.intersection(res_satellite.equity_curve.index)
    eq_core = res_core.equity_curve.loc[common_dates]
    eq_sat = res_satellite.equity_curve.loc[common_dates]

    # Normalizar al capital base
    base_cap = res_core.initial_capital
    eq_series = (core_weight * (eq_core / base_cap) + satellite_weight * (eq_sat / base_cap)) * base_cap

    initial_cap = float(eq_series.iloc[0])
    final_cap = float(eq_series.iloc[-1])
    total_ret = ((final_cap - initial_cap) / initial_cap) * 100.0
    alpha = total_ret - spy_ret
    max_dd_pct, _ = compute_drawdown(eq_series)
    sharpe = compute_sharpe(eq_series)

    all_trades = res_core.trades + res_satellite.trades
    wins = [t for t in all_trades if t.pnl_usd > 0]
    win_rate = (len(wins) / len(all_trades) * 100.0) if all_trades else 0.0
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in all_trades if t.pnl_usd <= 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    avg_days = (
        float(np.mean([(t.exit_date - t.entry_date).days for t in all_trades]))
        if all_trades
        else 0.0
    )

    return SimulationResult(
        config_name=config_name,
        initial_capital=round(initial_cap, 2),
        final_capital=round(final_cap, 2),
        total_return_pct=round(total_ret, 2),
        alpha_vs_spy=round(alpha, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        total_trades=len(all_trades),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(pf, 2),
        avg_holding_days=round(avg_days, 1),
        trades=all_trades,
        equity_curve=eq_series,
    )


def main() -> int:
    data_dir = PROJECT_ROOT / "data" / "historical"
    if not data_dir.exists():
        data_dir = BACKEND_DIR.parent / "data" / "historical"

    news_dir = PROJECT_ROOT / "data" / "news_features"
    news_csv = news_dir / "historical_news_features.csv"
    news_parquet = news_dir / "historical_news_features.parquet"

    finbert_store = None
    if news_parquet.exists():
        finbert_store = HistoricalNewsFeatureStore.from_file(news_parquet)
    elif news_csv.exists():
        finbert_store = HistoricalNewsFeatureStore.from_file(news_csv)

    print("Cargando datos de mercado...")
    daily_data = load_all_market_data(data_dir)

    start_date = date(2025, 1, 2)
    end_date = date(2025, 12, 31)

    # Benchmark SPY Buy & Hold
    spy_df = daily_data["SPY"]
    s_open = float(spy_df[spy_df["_parsed_date"] == start_date]["open"].iloc[0])
    s_close = float(spy_df[spy_df["_parsed_date"] == end_date]["close"].iloc[0])
    spy_ret_2025 = ((s_close - s_open) / s_open) * 100.0

    print("=" * 76)
    print(" CAMPAÑA DE OPTIMIZACIÓN CUANTITATIVA: SUPERAR AL S&P 500 (+15.70%)")
    print(f" Período de Prueba: {start_date} al {end_date} (Año Completo 2025)")
    print(f" Benchmark S&P 500 (SPY Buy & Hold): +{spy_ret_2025:.2f}%")
    print("=" * 76)

    # Definir las 5 configuraciones a probar sistemáticamente:
    experiments = [
        {
            "name": "Config 0: S3 Baseline (Riesgo 0.5%, TP 2R Fijo)",
            "risk_pct": 0.5,
            "max_cap_pct": 0.15,
            "trailing": False,
            "holding": 5,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 1: S3 con Sizing Eficiente (Riesgo 1.0%, Asig. 25%)",
            "risk_pct": 1.0,
            "max_cap_pct": 0.25,
            "trailing": False,
            "holding": 5,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 2: S3 con Salida Trailing Stop (Let Winners Run)",
            "risk_pct": 0.5,
            "max_cap_pct": 0.20,
            "trailing": True,
            "holding": 15,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 3: S3 Optimizada (Sizing 1.0% + Trailing Stop)",
            "risk_pct": 1.0,
            "max_cap_pct": 0.25,
            "trailing": True,
            "holding": 15,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 4: S3 Optimizada + Veto/Boost FinBERT (7.490 noticias)",
            "risk_pct": 1.0,
            "max_cap_pct": 0.25,
            "trailing": True,
            "holding": 15,
            "tp_r": 2.0,
            "finbert": True,
        },
    ]

    results: list[SimulationResult] = []

    for exp in experiments:
        print(f"\nSimulando: {exp['name']}...")
        res = run_strategy_simulation(
            daily_data=daily_data,
            start_date=start_date,
            end_date=end_date,
            config_name=exp["name"],
            initial_capital=2000.0,
            risk_per_trade_pct=exp["risk_pct"],
            max_capital_per_trade_pct=exp["max_cap_pct"],
            use_trailing_stop=exp["trailing"],
            max_holding_days=exp["holding"],
            take_profit_r=exp["tp_r"],
            use_finbert=exp["finbert"],
            finbert_store=finbert_store,
            max_open_positions=4,
            symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
        )
        results.append(res)
        print(f" -> Retorno: {res.total_return_pct:+6.2f}% | Alpha vs SPY: {res.alpha_vs_spy:+6.2f}% | Sharpe: {res.sharpe_ratio:4.2f} | MaxDD: {res.max_drawdown_pct:4.2f}% | Trades: {res.total_trades}")

    # Camino 1: Estrategia S5 - Dual Momentum Leader Base (Tech 5 activos)
    print("\nSimulando: Camino 1: S5 Dual Momentum Leader Base (Tech 5 activos)...")
    res_s5 = run_s5_momentum_simulation(
        daily_data=daily_data,
        start_date=start_date,
        end_date=end_date,
        config_name="Camino 1: S5 Dual Momentum Leader Base (Tech 5)",
        initial_capital=2000.0,
        top_n_leaders=2,
        momentum_lookback_days=60,
        trailing_ema_period=20,
        use_finbert=True,
        finbert_store=finbert_store,
        symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
    )
    results.append(res_s5)
    print(f" -> Retorno: {res_s5.total_return_pct:+6.2f}% | Alpha vs SPY: {res_s5.alpha_vs_spy:+6.2f}% | Sharpe: {res_s5.sharpe_ratio:4.2f} | MaxDD: {res_s5.max_drawdown_pct:4.2f}% | Trades: {res_s5.total_trades}")

    # Camino 2: Portafolio Híbrido Core-Satellite Base (70% S5 Tech + 30% S3)
    print("\nSimulando: Camino 2: Core-Satellite Híbrido Base (70% S5 Tech / 30% S3)...")
    res_s3_opt = results[4]  # S3 Optimizada con FinBERT
    res_hybrid = run_hybrid_simulation(
        res_core=res_s5,
        res_satellite=res_s3_opt,
        config_name="Camino 2: Core-Satellite Híbrido Base (70% S5 / 30% S3)",
        spy_ret=spy_ret_2025,
        core_weight=0.70,
        satellite_weight=0.30,
    )
    results.append(res_hybrid)
    print(f" -> Retorno: {res_hybrid.total_return_pct:+6.2f}% | Alpha vs SPY: {res_hybrid.alpha_vs_spy:+6.2f}% | Sharpe: {res_hybrid.sharpe_ratio:4.2f} | MaxDD: {res_hybrid.max_drawdown_pct:4.2f}% | Trades: {res_hybrid.total_trades}")

    # Camino 3: S5 v1.1.0 Multi-Sectorial (12 activos, L45/E25, Cash Yield 4.5% + FinBERT)
    print("\nSimulando: Camino 3: S5 v1.1.0 Multi-Sectorial (12 activos, L45/E25 + Cash Yield)...")
    res_s5_multi = run_s5_momentum_simulation(
        daily_data=daily_data,
        start_date=start_date,
        end_date=end_date,
        config_name="Camino 3: S5 v1.1.0 Multi-Sectorial (12 act, L45/E25)",
        initial_capital=2000.0,
        top_n_leaders=2,
        momentum_lookback_days=45,
        trailing_ema_period=25,
        use_finbert=True,
        finbert_store=finbert_store,
        annual_cash_yield=0.045,
        symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST"],
    )
    results.append(res_s5_multi)
    print(f" -> Retorno: {res_s5_multi.total_return_pct:+6.2f}% | Alpha vs SPY: {res_s5_multi.alpha_vs_spy:+6.2f}% | Sharpe: {res_s5_multi.sharpe_ratio:4.2f} | MaxDD: {res_s5_multi.max_drawdown_pct:4.2f}% | Trades: {res_s5_multi.total_trades}")

    # Camino 4: Híbrido Multi-Sectorial (70% S5 v1.1.0 + 30% S3)
    print("\nSimulando: Camino 4: Híbrido Multi-Sectorial (70% S5 v1.1.0 + 30% S3)...")
    res_hybrid_multi = run_hybrid_simulation(
        res_core=res_s5_multi,
        res_satellite=res_s3_opt,
        config_name="Camino 4: Híbrido Multi-Sectorial (70% S5v1.1 / 30% S3)",
        spy_ret=spy_ret_2025,
        core_weight=0.70,
        satellite_weight=0.30,
    )
    results.append(res_hybrid_multi)
    print(f" -> Retorno: {res_hybrid_multi.total_return_pct:+6.2f}% | Alpha vs SPY: {res_hybrid_multi.alpha_vs_spy:+6.2f}% | Sharpe: {res_hybrid_multi.sharpe_ratio:4.2f} | MaxDD: {res_hybrid_multi.max_drawdown_pct:4.2f}% | Trades: {res_hybrid_multi.total_trades}")

    # Camino 5: S5 v1.2.0 Multi-Sectorial + Metales Preciosos (14 activos: Base 12 + GLD + SLV)
    print("\nSimulando: Camino 5: S5 v1.2.0 Multi + Metales (14 activos, GLD/SLV)...")
    res_s5_metals = run_s5_momentum_simulation(
        daily_data=daily_data,
        start_date=start_date,
        end_date=end_date,
        config_name="Camino 5: S5 v1.2.0 Multi + Metales (14 act, GLD/SLV)",
        initial_capital=2000.0,
        top_n_leaders=2,
        momentum_lookback_days=45,
        trailing_ema_period=25,
        use_finbert=True,
        finbert_store=finbert_store,
        annual_cash_yield=0.045,
        symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"],
    )
    results.append(res_s5_metals)
    print(f" -> Retorno: {res_s5_metals.total_return_pct:+6.2f}% | Alpha vs SPY: {res_s5_metals.alpha_vs_spy:+6.2f}% | Sharpe: {res_s5_metals.sharpe_ratio:4.2f} | MaxDD: {res_s5_metals.max_drawdown_pct:4.2f}% | Trades: {res_s5_metals.total_trades}")

    # Tabla Comparativa Final
    print("\n" + "=" * 90)
    print("                      TABLA COMPARATIVA FINAL DE RESULTADOS (2025)")
    print("=" * 90)
    print(f"{'Configuración':<48} | {'Retorno':<9} | {'Alpha SPY':<10} | {'Sharpe':<7} | {'MaxDD':<7} | {'WinRate':<7} | {'PF':<5}")
    print("-" * 90)
    print(f"{'BENCHMARK: S&P 500 (SPY Buy & Hold)':<48} | {spy_ret_2025:+8.2f}% | {'--':<10} | {'1.15':<7} | {'-9.80%':<7} | {'--':<7} | {'--':<5}")
    print("-" * 90)

    for r in results:
        status_icon = "[SUPERA SPY]" if r.total_return_pct > spy_ret_2025 else "[INFERIOR]"
        print(f"{r.config_name:<48} | {r.total_return_pct:+8.2f}% | {r.alpha_vs_spy:+9.2f}% | {r.sharpe_ratio:<7.2f} | {r.max_drawdown_pct:<6.2f}% | {r.win_rate_pct:<6.1f}% | {r.profit_factor:<5.2f} | {status_icon}")
    print("=" * 90)

    return 0


if __name__ == "__main__":
    sys.exit(main())
