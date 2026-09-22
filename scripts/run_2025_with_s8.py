#!/usr/bin/env python3
"""Simulación de la Estrategia 8 (S8 PID Multi-Horizonte con 8 Escalas) en Ventana de Desarrollo.

Período: 2020-01-02 a 2022-12-30 (Ventana de Desarrollo Oficial).
Compara directamente:
1. S&P 500 (SPY — Benchmark)
2. Swing Trading (S5 / 30 días)
3. S7 PID Scorer (Tendencia s=+1)
4. S7 PID Scorer (Reversión s=-1)
5. S8 PID Multi-Horizonte (Variante A: w ∝ h)
6. S8 PID Multi-Horizonte (Variante B: w ∝ ln h)
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
while not (PROJECT_ROOT / "data").exists() and PROJECT_ROOT != PROJECT_ROOT.parent:
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


DATA_1H_DIR = PROJECT_ROOT / "data" / "intraday_1h"
DATA_DAILY_DIR = PROJECT_ROOT / "data" / "historical_14"

UNIVERSE_S8 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]


def calculate_alpaca_fees(sale_notional: float, shares: float) -> float:
    if sale_notional <= 0.0 or shares <= 0.0:
        return 0.0
    sec = max(0.01, math.ceil(sale_notional * 0.0000206 * 100.0) / 100.0)
    taf = min(8.98, max(0.01, math.ceil(shares * 0.000195 * 100.0) / 100.0))
    cat = math.ceil(shares * 0.00003 * 100.0) / 100.0 if (shares * 0.00003 >= 0.005) else 0.0
    return sec + taf + cat


def compute_metrics(eq_s: pd.Series, initial_cap: float = 2000.0) -> tuple[float, float, float, pd.Series]:
    tot_ret = (float(eq_s.iloc[-1]) - initial_cap) / initial_cap * 100.0
    daily = eq_s.groupby(lambda x: str(x)[:10]).last()
    rets = daily.pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0)) if rets.std() > 0 else 0.0
    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, sharpe, max_dd, rets


def run_s8_simulation_2025(
    weight_rule: str = "A",
    sign_p: float = 1.0,
    start_date: str = "2020-01-02",
    end_date: str = "2022-12-30",
) -> tuple[float, float, float, pd.Series, int]:
    """Ejecuta simulación de S8 PID Multi-Horizonte usando el motor unificado BacktestEngine."""
    from datetime import date
    from decimal import Decimal

    from tbot.backtest.engine import BacktestConfig, BacktestEngine
    from tbot.strategies.s8_pid_multihorizon import S8PIDMultihorizonStrategy

    # 1. Cargar datos diarios
    daily_data = {}
    for s in UNIVERSE_S8:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    df_spy_d = pd.read_csv(DATA_DAILY_DIR / "SPY_daily.csv")
    df_spy_d["date"] = pd.to_datetime(df_spy_d["date"])
    daily_data["SPY"] = df_spy_d.sort_values("date").reset_index(drop=True)

    # 2. Cargar datos intradiarios (1h) si existen
    intraday_data = {}
    for s in UNIVERSE_S8 + ["SPY"]:
        p = DATA_1H_DIR / f"{s}_1h.csv"
        if p.exists():
            df_i = pd.read_csv(p)
            df_i["_dt"] = pd.to_datetime(df_i["datetime_et"])
            intraday_data[s] = df_i.sort_values("_dt").reset_index(drop=True)

    strat = S8PIDMultihorizonStrategy(
        universe=UNIVERSE_S8,
        weight_rule=weight_rule,
        sign_p=sign_p,
        top_n=4,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    config = BacktestConfig(
        strategy=strat,
        universe=UNIVERSE_S8,
        initial_capital=Decimal("2000.00"),
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        enable_vol_control=True,
        target_portfolio_vol=0.12,
        exit_on_bear_regime=False,
    )
    engine = BacktestEngine(
        config=config,
        historical_daily=daily_data,
        historical_intraday=intraday_data if intraday_data else None,
    )

    s_d = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    e_d = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    result = engine.run(start_date=s_d, end_date=e_d)

    eq_s = result.equity_curve
    ret, sharpe, max_dd, rets = compute_metrics(eq_s, 2000.0)
    return ret, sharpe, max_dd, rets, len(result.trades)


def compute_alpha(strat_rets: pd.Series, spy_rets: pd.Series, rf: float = 0.0412) -> tuple[float, float]:
    common = strat_rets.index.intersection(spy_rets.index)
    s = strat_rets.loc[common]
    b = spy_rets.loc[common]
    cov = np.cov(s, b)[0][1]
    var_b = np.var(b)
    beta = float(cov / var_b) if var_b > 0 else 1.0
    r_s_ann = (1.0 + s.mean()) ** 252 - 1.0
    r_b_ann = (1.0 + b.mean()) ** 252 - 1.0
    alpha = (r_s_ann - rf) - beta * (r_b_ann - rf)
    return alpha * 100.0, beta


def main():
    try:
        from run_2025_with_s7 import (
            get_dev_spy_metrics,
            run_s5_simulation_dev,
            run_s7_simulation_2025,
        )
    except ImportError:
        from scripts.run_2025_with_s7 import (
            get_dev_spy_metrics,
            run_s5_simulation_dev,
            run_s7_simulation_2025,
        )

    print("Ejecutando simulación comparativa de ventana de desarrollo (2020-2022)...")
    df_spy = pd.read_csv(DATA_DAILY_DIR / "SPY_daily.csv")
    df_spy["date"] = pd.to_datetime(df_spy["date"])
    daily_data = {"SPY": df_spy.sort_values("date").reset_index(drop=True)}

    spy_ret, spy_sharpe, spy_dd, spy_rets = get_dev_spy_metrics(daily_data)
    sw_ret, sw_sharpe, sw_dd, sw_rets, _ = run_s5_simulation_dev()
    sw_alpha, _ = compute_alpha(sw_rets, spy_rets)

    # S7 PID Scorer
    s7_ret, s7_sharpe, s7_dd, s7_rets, _ = run_s7_simulation_2025(sign_p=1.0)
    s7_alpha, _ = compute_alpha(s7_rets, spy_rets)

    s7_rev_ret, s7_rev_sharpe, s7_rev_dd, s7_rev_rets, _ = run_s7_simulation_2025(sign_p=-1.0)
    s7_rev_alpha, _ = compute_alpha(s7_rev_rets, spy_rets)

    # S8 PID Multi-Horizonte (Variante A: w ∝ h)
    s8_a_ret, s8_a_sharpe, s8_a_dd, s8_a_rets, _ = run_s8_simulation_2025(weight_rule="A", sign_p=1.0)
    s8_a_alpha, _ = compute_alpha(s8_a_rets, spy_rets)

    # S8 PID Multi-Horizonte (Variante B: w ∝ ln h)
    s8_b_ret, s8_b_sharpe, s8_b_dd, s8_b_rets, _ = run_s8_simulation_2025(weight_rule="B", sign_p=1.0)
    s8_b_alpha, _ = compute_alpha(s8_b_rets, spy_rets)

    print("\n" + "=" * 115)
    print("      COMPARATIVA CONSOLIDADA TRIENIO 2020-2022 (DESARROLLO) INCLUYENDO S8 MULTI-HORIZONTE")
    print("=" * 115)
    print(f"{'Estrategia / Modelo':<45} | {'Rentabilidad':<13} | {'Alpha vs SPY':<13} | {'Sharpe':<8} | {'Max Drawdown'}")
    print("-" * 115)
    print(f"{'S&P 500 (SPY — Benchmark)':<45} | {spy_ret:+11.2f}% | {'0.00%':<13} | {spy_sharpe:6.2f}   | {spy_dd:6.2f}%")
    print(f"{'Swing Trading (S5 / 30 días)':<45} | {sw_ret:+11.2f}% | {sw_alpha:+11.2f}% | {sw_sharpe:6.2f}   | {sw_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Tendencia s=+1)':<45} | {s7_ret:+11.2f}% | {s7_alpha:+11.2f}% | {s7_sharpe:6.2f}   | {s7_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Reversión s=-1)':<45} | {s7_rev_ret:+11.2f}% | {s7_rev_alpha:+11.2f}% | {s7_rev_sharpe:6.2f}   | {s7_rev_dd:6.2f}%")
    print(f"{'S8 PID Multi-Horizonte (Variante A: w ~ h)':<45} | {s8_a_ret:+11.2f}% | {s8_a_alpha:+11.2f}% | {s8_a_sharpe:6.2f}   | {s8_a_dd:6.2f}%")
    print(f"{'S8 PID Multi-Horizonte (Variante B: w ~ ln h)':<45} | {s8_b_ret:+11.2f}% | {s8_b_alpha:+11.2f}% | {s8_b_sharpe:6.2f}   | {s8_b_dd:6.2f}%")
    print("=" * 115)


if __name__ == "__main__":
    main()
