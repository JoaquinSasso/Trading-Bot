#!/usr/bin/env python3
"""Simulación de la Estrategia 7 (S7 PID Scorer de Doble Sistema) en Ventana de Desarrollo.

Período: 2020-01-02 a 2022-12-30 (Ventana de Desarrollo Oficial).
Universo: 14 activos (`historical_14`) con SPY como benchmark.
Compara directamente:
1. S&P 500 (SPY — Benchmark)
2. Swing Trading (S5 / 30 días)
3. S7 PID Scorer (Tendencia s=+1)
4. S7 PID Scorer (Reversión s=-1)
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

UNIVERSE_S5_S7 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]

UNIVERSE_HFT = [
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


def compute_metrics(eq_s: pd.Series, initial_cap: float = 2000.0) -> tuple[float, float, float, pd.Series]:
    tot_ret = (float(eq_s.iloc[-1]) - initial_cap) / initial_cap * 100.0
    daily = eq_s.groupby(lambda x: str(x)[:10]).last()
    rets = daily.pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0)) if rets.std() > 0 else 0.0
    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, sharpe, max_dd, rets


def run_s7_simulation_2025(
    sign_p: float = 1.0,
    start_date: str = "2020-01-02",
    end_date: str = "2022-12-30",
) -> tuple[float, float, float, pd.Series, int]:
    """Ejecuta simulación de S7 PID Scorer usando el motor unificado BacktestEngine."""
    from datetime import date
    from decimal import Decimal

    from tbot.backtest.engine import BacktestConfig, BacktestEngine
    from tbot.strategies.s7_pid_scorer import S7PIDScorerStrategy

    # 1. Cargar datos diarios
    daily_data = {}
    for s in UNIVERSE_S5_S7:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    df_spy = pd.read_csv(DATA_DAILY_DIR / "SPY_daily.csv")
    df_spy["date"] = pd.to_datetime(df_spy["date"])
    daily_data["SPY"] = df_spy.sort_values("date").reset_index(drop=True)

    active_universe = [s for s in UNIVERSE_S5_S7 if s in daily_data]

    strat = S7PIDScorerStrategy(
        universe=active_universe,
        sign_p=sign_p,
        top_n=4,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    config = BacktestConfig(
        strategy=strat,
        universe=active_universe,
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
    engine = BacktestEngine(config=config, historical_daily=daily_data)

    s_d = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    e_d = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    result = engine.run(start_date=s_d, end_date=e_d)

    eq_s = result.equity_curve
    ret, sharpe, max_dd, rets = compute_metrics(eq_s, 2000.0)
    return ret, sharpe, max_dd, rets, len(result.trades)


def run_s5_simulation_dev(
    start_date: str = "2020-01-02",
    end_date: str = "2022-12-30",
) -> tuple[float, float, float, pd.Series, int]:
    """Ejecuta simulación de Swing Trading (S5 / 30 días) en la ventana de desarrollo usando BacktestEngine."""
    from datetime import date
    from decimal import Decimal

    from tbot.backtest.engine import BacktestConfig, BacktestEngine
    from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy

    daily_data = {}
    for s in UNIVERSE_S5_S7:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    df_spy = pd.read_csv(DATA_DAILY_DIR / "SPY_daily.csv")
    df_spy["date"] = pd.to_datetime(df_spy["date"])
    daily_data["SPY"] = df_spy.sort_values("date").reset_index(drop=True)

    active_universe = [s for s in UNIVERSE_S5_S7 if s in daily_data]

    strat = DualMomentumLeaderStrategy(
        universe=active_universe,
        top_n_leaders=4,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    config = BacktestConfig(
        strategy=strat,
        universe=active_universe,
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
    engine = BacktestEngine(config=config, historical_daily=daily_data)

    s_d = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    e_d = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    result = engine.run(start_date=s_d, end_date=e_d)

    eq_s = result.equity_curve
    ret, sharpe, max_dd, rets = compute_metrics(eq_s, 2000.0)
    return ret, sharpe, max_dd, rets, len(result.trades)


def get_dev_spy_metrics(
    daily_data: dict[str, pd.DataFrame],
    start_date: str = "2020-01-02",
    end_date: str = "2022-12-30",
) -> tuple[float, float, float, pd.Series]:
    """Calcula métricas de referencia de SPY en la ventana de desarrollo."""
    df_spy = daily_data["SPY"]
    s_d = pd.to_datetime(start_date)
    e_d = pd.to_datetime(end_date)
    sub = df_spy[(df_spy["date"] >= s_d) & (df_spy["date"] <= e_d)].sort_values("date").reset_index(drop=True)
    p0 = float(sub.iloc[0]["open"])
    p1 = float(sub.iloc[-1]["close"])
    tot_ret = (p1 - p0) / p0 * 100.0
    sub["date_str"] = sub["date"].dt.strftime("%Y-%m-%d")
    rets = sub.set_index("date_str")["close"].pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0)) if rets.std() > 0 else 0.0
    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, sharpe, max_dd, rets


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
    df_spy = pd.read_csv(DATA_DAILY_DIR / "SPY_daily.csv")
    df_spy["date"] = pd.to_datetime(df_spy["date"])
    daily_data = {"SPY": df_spy.sort_values("date").reset_index(drop=True)}

    spy_ret, spy_sharpe, spy_dd, spy_rets = get_dev_spy_metrics(daily_data)
    sw_ret, sw_sharpe, sw_dd, sw_rets, _sw_trades = run_s5_simulation_dev()
    sw_alpha, _sw_beta = compute_alpha(sw_rets, spy_rets)

    # Correr S7 Tendencia (s=+1)
    s7_ret, s7_sharpe, s7_dd, s7_rets, _s7_trades = run_s7_simulation_2025(sign_p=1.0)
    s7_alpha, _s7_beta = compute_alpha(s7_rets, spy_rets)

    # Correr S7 Reversión (s=-1)
    s7_rev_ret, s7_rev_sharpe, s7_rev_dd, s7_rev_rets, _ = run_s7_simulation_2025(sign_p=-1.0)
    s7_rev_alpha, _s7_rev_beta = compute_alpha(s7_rev_rets, spy_rets)

    print("\n" + "=" * 105)
    print("      COMPARATIVA CONSOLIDADA TRIENIO 2020-2022 (DESARROLLO) INCLUYENDO S7 PID SCORER")
    print("=" * 105)
    print(f"{'Estrategia / Modelo':<38} | {'Rentabilidad':<13} | {'Alpha vs SPY':<13} | {'Sharpe':<8} | {'Max Drawdown'}")
    print("-" * 105)
    print(f"{'S&P 500 (SPY — Benchmark)':<38} | {spy_ret:+11.2f}% | {'0.00%':<13} | {spy_sharpe:6.2f}   | {spy_dd:6.2f}%")
    print(f"{'Swing Trading (S5 / 30 días)':<38} | {sw_ret:+11.2f}% | {sw_alpha:+11.2f}% | {sw_sharpe:6.2f}   | {sw_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Tendencia s=+1)':<38} | {s7_ret:+11.2f}% | {s7_alpha:+11.2f}% | {s7_sharpe:6.2f}   | {s7_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Reversión s=-1)':<38} | {s7_rev_ret:+11.2f}% | {s7_rev_alpha:+11.2f}% | {s7_rev_sharpe:6.2f}   | {s7_rev_dd:6.2f}%")
    print("=" * 105)


if __name__ == "__main__":
    main()
