#!/usr/bin/env python3
"""Comparación en el ciclo disponible de 2026 con acceso a barras de 5 minutos.

Período exacto: 2026-06-26 a 2026-09-21 (60 sesiones / 4.632 barras de 5m).
Evalúa:
1. S&P 500 (SPY — Benchmark)
2. High Frequency / Intraday (S6 en 5m)
3. Swing Trading (S5 / 30 días)
4. S7 PID Scorer (Tendencia s=+1)
5. S7 PID Scorer (Reversión s=-1)
6. S8 PID Multi-Horizonte (Variante A: w ~ h, con barras de 5m y los 8 horizontes)
7. S8 PID Multi-Horizonte (Variante B: w ~ ln h, con barras de 5m y los 8 horizontes)
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from decimal import Decimal

from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.backtest.guards import SAMPLE_5M_END, SAMPLE_5M_START
from tbot.backtest.metrics import AlphaRegressionResult, compute_ols_alpha_beta
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy
from tbot.strategies.s7_pid_scorer import S7PIDScorerStrategy
from tbot.strategies.s8_pid_multihorizon import (
    S8PIDMultihorizonStrategy,
)

DATA_5M_DIR = PROJECT_ROOT / "data" / "intraday_5m"
DATA_DAILY_DIR = PROJECT_ROOT / "data" / "daily_2026"

UNIVERSE_12 = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]


def calculate_alpaca_fees(sale_notional: float, shares: float) -> float:
    if sale_notional <= 0.0 or shares <= 0.0:
        return 0.0
    sec = max(0.01, math.ceil(sale_notional * 0.0000206 * 100.0) / 100.0)
    taf = min(8.98, max(0.01, math.ceil(shares * 0.000195 * 100.0) / 100.0))
    cat = math.ceil(shares * 0.00003 * 100.0) / 100.0 if (shares * 0.00003 >= 0.005) else 0.0
    return sec + taf + cat


def compute_metrics(
    eq_s: pd.Series, initial_cap: float = 2000.0
) -> tuple[float, float | None, float | None, float, pd.Series, float]:
    tot_ret = (float(eq_s.iloc[-1]) - initial_cap) / initial_cap * 100.0
    daily = eq_s.groupby(lambda x: str(x)[:10]).last()
    rets = daily.pct_change().dropna()
    n_days = len(daily)

    # B-08: Anualización estrictamente prohibida para T < 252 sesiones
    if n_days >= 252:
        n_years = n_days / 252.0
        cagr = ((float(eq_s.iloc[-1]) / initial_cap) ** (1.0 / n_years) - 1.0) * 100.0
        sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0)) if rets.std() > 0 else 0.0
    else:
        cagr = None
        sharpe = None

    # Error estándar de Sharpe: SE(S) = sqrt((1 + S^2/2)/T)
    if len(rets) > 1 and rets.std() > 0:
        s_periodic = float(rets.mean() / rets.std())
        sharpe_se = math.sqrt((1.0 + 0.5 * (s_periodic**2)) / n_days)
    else:
        sharpe_se = 0.0

    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, cagr, sharpe, max_dd, rets, sharpe_se


def compute_alpha_beta(
    strat_rets: pd.Series,
    spy_rets: pd.Series,
    strat_cagr: float | None = None,
    spy_cagr: float | None = None,
) -> AlphaRegressionResult:
    """Calcula Alpha de Jensen y Beta mediante OLS statsmodels sobre retornos excedentes (B-07)."""
    return compute_ols_alpha_beta(strat_rets, spy_rets)


def get_2026_spy_metrics() -> tuple[float, float | None, float | None, float, pd.Series, float]:
    df_spy = pd.read_csv(DATA_5M_DIR / "SPY_5m.csv")
    p0 = float(df_spy.iloc[0]["open"])
    p1 = float(df_spy.iloc[-1]["close"])
    tot_ret = (p1 - p0) / p0 * 100.0

    daily = df_spy.groupby("date")["close"].last()
    rets = daily.pct_change().dropna()
    n_days = len(daily)

    if n_days >= 252:
        n_years = n_days / 252.0
        cagr = ((p1 / p0) ** (1.0 / n_years) - 1.0) * 100.0
        sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0)) if rets.std() > 0 else 0.0
    else:
        cagr = None
        sharpe = None

    if len(rets) > 1 and rets.std() > 0:
        s_periodic = float(rets.mean() / rets.std())
        sharpe_se = math.sqrt((1.0 + 0.5 * (s_periodic**2)) / n_days)
    else:
        sharpe_se = 0.0

    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, cagr, sharpe, max_dd, rets, sharpe_se


def run_2026_s6_intraday() -> tuple[float, float | None, float | None, float, pd.Series, float]:
    try:
        from run_backtest_intraday_5m import (
            load_intraday_data,
            run_alpaca_intraday_simulation,
        )
    except ImportError:
        from scripts.run_backtest_intraday_5m import (
            load_intraday_data,
            run_alpaca_intraday_simulation,
        )

    data = load_intraday_data()
    res = run_alpaca_intraday_simulation(
        daily_data=data,
        config_name="S6 Optimizada 5m",
        initial_capital=2000.0,
        half_spread_mode="fixed_0.5bps",
        trailing_ema_period=21,
        initial_stop_pct=0.012,
        max_holding_bars=36,
    )
    daily_rets = res.daily_returns
    n_days = len(daily_rets)
    cagr = res.cagr_pct if n_days >= 252 else None
    sharpe = res.sharpe_ratio if n_days >= 252 else None
    if len(daily_rets) > 1 and daily_rets.std() > 0:
        s_periodic = float(daily_rets.mean() / daily_rets.std())
        sharpe_se = math.sqrt((1.0 + 0.5 * (s_periodic**2)) / max(1, n_days))
    else:
        sharpe_se = 0.0

    return res.total_return_pct, cagr, sharpe, res.max_drawdown_pct, daily_rets, sharpe_se


def load_daily_2026_data() -> dict[str, pd.DataFrame]:
    daily_data = {}
    for s in UNIVERSE_12:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)
    return daily_data


def load_5m_2026_data() -> dict[str, pd.DataFrame]:
    intraday_5m = {}
    for s in UNIVERSE_12:
        p = DATA_5M_DIR / f"{s}_5m.csv"
        df = pd.read_csv(p)
        df["timestamp"] = pd.to_datetime(df["datetime_et"])
        intraday_5m[s] = df.sort_values("timestamp").reset_index(drop=True)
    return intraday_5m


def run_2026_s5_swing() -> tuple[float, float | None, float | None, float, pd.Series, float]:
    """Swing S5 sobre datos diarios de 2026 usando BacktestEngine."""
    daily_data = load_daily_2026_data()
    symbols = [s for s in UNIVERSE_12 if s != "SPY"]
    strat = DualMomentumLeaderStrategy(
        top_n_leaders=4,
        universe=symbols,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    config = BacktestConfig(
        strategy=strat,
        universe=symbols,
        initial_capital=Decimal("2000.00"),
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        apply_retail_costs=True,
        integer_shares=False,
    )
    engine = BacktestEngine(config=config, historical_daily=daily_data)
    res = engine.run(start_date=SAMPLE_5M_START, end_date=SAMPLE_5M_END, resolution="daily")
    ret, cagr, sharpe, max_dd, rets, se = compute_metrics(res.equity_curve, 2000.0)
    return ret, cagr, sharpe, max_dd, rets, se


def run_2026_s7_simulation(
    sign_p: float = 1.0,
) -> tuple[float, float | None, float | None, float, pd.Series, float]:
    """S7 PID Scorer sobre datos de 2026 usando BacktestEngine."""
    daily_data = load_daily_2026_data()
    symbols = [s for s in UNIVERSE_12 if s != "SPY"]
    strat = S7PIDScorerStrategy(universe=symbols, sign_p=sign_p, top_n=4)
    config = BacktestConfig(
        strategy=strat,
        universe=symbols,
        initial_capital=Decimal("2000.00"),
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        apply_retail_costs=True,
        integer_shares=False,
    )
    engine = BacktestEngine(config=config, historical_daily=daily_data)
    res = engine.run(start_date=SAMPLE_5M_START, end_date=SAMPLE_5M_END, resolution="daily")
    ret, cagr, sharpe, max_dd, rets, se = compute_metrics(res.equity_curve, 2000.0)
    return ret, cagr, sharpe, max_dd, rets, se


def run_2026_s8_simulation(
    weight_rule: str = "A", sign_p: float = 1.0
) -> tuple[float, float | None, float | None, float, pd.Series, float]:
    """S8 PID Multi-Horizonte sobre 2026 usando BacktestEngine."""
    daily_data = load_daily_2026_data()
    intraday_5m = load_5m_2026_data()
    symbols = [s for s in UNIVERSE_12 if s != "SPY"]
    strat = S8PIDMultihorizonStrategy(universe=symbols, weight_rule=weight_rule, sign_p=sign_p, top_n=4)
    config = BacktestConfig(
        strategy=strat,
        universe=symbols,
        initial_capital=Decimal("2000.00"),
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        apply_retail_costs=True,
        integer_shares=False,
    )
    engine = BacktestEngine(config=config, historical_daily=daily_data, historical_intraday=intraday_5m)
    res = engine.run(start_date=SAMPLE_5M_START, end_date=SAMPLE_5M_END, resolution="daily")
    ret, cagr, sharpe, max_dd, rets, se = compute_metrics(res.equity_curve, 2000.0)
    return ret, cagr, sharpe, max_dd, rets, se


def main():
    print("Ejecutando pruebas sobre las 60 jornadas disponibles de 2026 con barras de 5 minutos...")
    spy_ret, spy_cagr, spy_sharpe, spy_dd, spy_rets, spy_se = get_2026_spy_metrics()
    s6_ret, s6_cagr, s6_sharpe, s6_dd, s6_rets, s6_se = run_2026_s6_intraday()
    s5_ret, s5_cagr, s5_sharpe, s5_dd, s5_rets, s5_se = run_2026_s5_swing()
    s7_ret, s7_cagr, s7_sharpe, s7_dd, s7_rets, s7_se = run_2026_s7_simulation(sign_p=1.0)
    s7_rev_ret, s7_rev_cagr, s7_rev_sharpe, s7_rev_dd, s7_rev_rets, s7_rev_se = run_2026_s7_simulation(sign_p=-1.0)
    s8_a_ret, s8_a_cagr, s8_a_sharpe, s8_a_dd, s8_a_rets, s8_a_se = run_2026_s8_simulation(weight_rule="A", sign_p=1.0)
    s8_b_ret, s8_b_cagr, s8_b_sharpe, s8_b_dd, s8_b_rets, s8_b_se = run_2026_s8_simulation(weight_rule="B", sign_p=1.0)

    s6_ols = compute_alpha_beta(s6_rets, spy_rets)
    s5_ols = compute_alpha_beta(s5_rets, spy_rets)
    s7_ols = compute_alpha_beta(s7_rets, spy_rets)
    s7_rev_ols = compute_alpha_beta(s7_rev_rets, spy_rets)
    s8_a_ols = compute_alpha_beta(s8_a_rets, spy_rets)
    s8_b_ols = compute_alpha_beta(s8_b_rets, spy_rets)

    print("\n" + "=" * 145)
    print("   COMPARATIVA 2026 CON BARRAS DE 5 MINUTOS (2026-06-26 a 2026-09-21: 60 Sesiones, 4.632 Barras de 5m)")
    print("   NOTA METODOLÓGICA (B-08): Muestra T=60 < 252 sesiones; anualización prohibida. Se reporta Ret. Período y SE(Sharpe).")
    print("=" * 145)
    print(f"{'Estrategia / Modelo':<45} | {'Ret. Periodo':<13} | {'CAGR':<12} | {'Sharpe':<18} | {'Alpha OLS (SE)':<20} | {'t-stat (p-val)':<16} | {'Beta':<7} | {'R2':<6} | {'MaxDD'}")
    print("-" * 145)

    models = [
        ("S&P 500 (SPY — Benchmark)", spy_ret, spy_cagr, spy_sharpe, spy_se, None, spy_dd),
        ("High Frequency / Intraday (S6 5m)", s6_ret, s6_cagr, s6_sharpe, s6_se, s6_ols, s6_dd),
        ("Swing Trading (S5 / 30 días)", s5_ret, s5_cagr, s5_sharpe, s5_se, s5_ols, s5_dd),
        ("S7 PID Scorer (Tendencia s=+1)", s7_ret, s7_cagr, s7_sharpe, s7_se, s7_ols, s7_dd),
        ("S7 PID Scorer (Reversión s=-1)", s7_rev_ret, s7_rev_cagr, s7_rev_sharpe, s7_rev_se, s7_rev_ols, s7_rev_dd),
        ("S8 PID Multi-Horizonte (Variante A: w ~ h)", s8_a_ret, s8_a_cagr, s8_a_sharpe, s8_a_se, s8_a_ols, s8_a_dd),
        ("S8 PID Multi-Horizonte (Variante B: w ~ ln h)", s8_b_ret, s8_b_cagr, s8_b_sharpe, s8_b_se, s8_b_ols, s8_b_dd),
    ]

    for name, ret_p, cagr_v, sh_v, se_v, ols_v, dd_v in models:
        cagr_s = f"{cagr_v:+6.2f}%" if cagr_v is not None else "N/A (T<252)"
        sh_s = f"{sh_v:5.2f}" if sh_v is not None else f"N/A (SE={se_v:.3f})"
        if ols_v is not None:
            alpha_s = f"{ols_v.alpha_annualized:+6.2f}% ({ols_v.alpha_se:.2f}%)"
            t_s = f"{ols_v.alpha_tstat:+5.2f} (p={ols_v.alpha_pvalue:.3f})"
            beta_s = f"{ols_v.beta:5.2f}"
            r2_s = f"{ols_v.r_squared:5.2f}"
        else:
            alpha_s = "0.00% (ref)"
            t_s = "N/A"
            beta_s = "1.00"
            r2_s = "1.00"
        print(f"{name:<45} | {ret_p:+11.2f}% | {cagr_s:<12} | {sh_s:<18} | {alpha_s:<20} | {t_s:<16} | {beta_s:<7} | {r2_s:<6} | {dd_v:5.2f}%")
    print("=" * 145)


if __name__ == "__main__":
    main()
