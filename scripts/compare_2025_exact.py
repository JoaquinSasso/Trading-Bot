#!/usr/bin/env python3
"""Comparación exacta en el año 2025: Swing Trading vs High Frequency/Intraday vs S&P 500.

Período exacto: 2025-01-01 a 2025-12-31.
Métricas solicitadas:
- Rentabilidad (Retorno Total Neto)
- Alpha respecto a S&P 500 (Jensen's Alpha anualizado)
- Sharpe Ratio (anualizado)
- Max Drawdown
"""

import math
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.backtest.guards import (
    DEV_HOURLY_END,
    DEV_HOURLY_START,
)
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy

DATA_1H_DIR = PROJECT_ROOT / "data" / "intraday_1h"
DATA_DAILY_DIR = PROJECT_ROOT / "data" / "historical_14"

UNIVERSE_INTRADAY = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]

UNIVERSE_S5 = [
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

def get_2025_spy_metrics():
    df = pd.read_csv(DATA_1H_DIR / "SPY_1h.csv")
    df = df[(df["date"] >= "2025-01-01") & (df["date"] <= "2025-12-31")].sort_values("datetime_et").reset_index(drop=True)
    p0 = float(df.iloc[0]["open"])
    p1 = float(df.iloc[-1]["close"])
    tot_ret = (p1 - p0) / p0 * 100.0

    daily = df.groupby("date")["close"].last()
    rets = daily.pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0))

    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)

    return tot_ret, sharpe, max_dd, rets, p0, p1

def run_2025_intraday(
    start_date: date = DEV_HOURLY_START,
    end_date: date = DEV_HOURLY_END,
) -> tuple[float, float, float, pd.Series]:
    """Intradiario 1h usando BacktestEngine sobre la ventana de desarrollo horaria."""
    try:
        from run_multi_period_backtest import GenericMultiHorizonStrategy
    except ImportError:
        from scripts.run_multi_period_backtest import GenericMultiHorizonStrategy

    intraday_data = {}
    for s in UNIVERSE_INTRADAY:
        p = DATA_1H_DIR / f"{s}_1h.csv"
        df = pd.read_csv(p)
        df["_dt"] = pd.to_datetime(df["datetime_et"])
        intraday_data[s] = df.sort_values("_dt").reset_index(drop=True)

    horizons = {"2h": 2, "4h": 4, "7h": 7, "14h": 14, "28h": 28}
    strat = GenericMultiHorizonStrategy(
        horizons_map=horizons,
        top_n=3,
        trailing_ema_period=21,
        initial_stop_pct=0.018,
        max_holding_bars=42,
        trend_ema_period=21,
        flatten_end_of_day=False,
    )
    cfg = BacktestConfig(
        strategy=strat,
        universe=UNIVERSE_INTRADAY,
        initial_capital=Decimal("2000.00"),
        account_type="cash",
        max_open_positions=3,
        single_position_cap=0.30,
        trailing_ema_period=21,
        stop_buffer_pct=0.005,
        integer_shares=False,
        apply_retail_costs=True,
        etf_half_spread_bps=0.5,
        stock_half_spread_bps=0.5,
        enable_circuit_breakers=True,
    )
    engine = BacktestEngine(config=cfg, historical_intraday=intraday_data)
    res = engine.run(start_date=start_date, end_date=end_date, resolution="1h")
    eq_s = res.equity_curve
    tot_ret = float(res.metrics.total_return_pct)
    sharpe = float(res.metrics.sharpe_ratio or 0.0)
    max_dd = float(res.metrics.max_drawdown_pct)
    rets = eq_s.pct_change().dropna()
    return tot_ret, sharpe, max_dd, rets


def run_2025_swing(
    start_date: date = date(2020, 1, 2),
    end_date: date = date(2022, 12, 30),
) -> tuple[float, float, float, pd.Series]:
    """Swing S5 usando BacktestEngine sobre la ventana de desarrollo."""
    daily_data = {}
    for s in UNIVERSE_S5 + ["SPY"]:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    active_s5 = [s for s in UNIVERSE_S5 if s in daily_data]

    strat = DualMomentumLeaderStrategy(
        top_n_leaders=4,
        universe=active_s5,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_days=30,
    )
    cfg = BacktestConfig(
        strategy=strat,
        universe=active_s5,
        initial_capital=Decimal("2000.00"),
        max_open_positions=4,
        single_position_cap=0.25,
        trailing_ema_period=25,
        stop_buffer_pct=0.035,
        max_holding_sessions=30,
        apply_retail_costs=True,
        integer_shares=False,
    )
    engine = BacktestEngine(config=cfg, historical_daily=daily_data)
    res = engine.run(start_date=start_date, end_date=end_date, resolution="daily")
    eq_s = res.equity_curve
    tot_ret = float(res.metrics.total_return_pct)
    sharpe = float(res.metrics.sharpe_ratio or 0.0)
    max_dd = float(res.metrics.max_drawdown_pct)
    rets = eq_s.pct_change().dropna()
    return tot_ret, sharpe, max_dd, rets


def compute_alpha_beta(strat_rets, spy_rets):
    s_idx = pd.to_datetime(strat_rets.index).date
    b_idx = pd.to_datetime(spy_rets.index).date
    s_s = pd.Series(strat_rets.values, index=s_idx)
    b_s = pd.Series(spy_rets.values, index=b_idx)
    common = s_s.index.intersection(b_s.index)
    if len(common) < 2:
        return 0.0, 1.0
    s = s_s.loc[common]
    b = b_s.loc[common]
    cov = np.cov(s, b)
    var_b = float(cov[1, 1]) if cov.ndim > 1 else float(np.var(b))
    beta = float(cov[0, 1] / var_b) if (cov.ndim > 1 and var_b > 0) else 1.0
    # Jensen's Alpha anualizado
    r_s_ann = (1.0 + float(s.mean())) ** 252 - 1.0
    r_b_ann = (1.0 + float(b.mean())) ** 252 - 1.0
    # rf en 2025 fue aprox 4.12% (BIL)
    rf = 0.0412
    alpha = (r_s_ann - rf) - beta * (r_b_ann - rf)
    return float(alpha * 100.0), float(beta)

def main():
    spy_ret, spy_sharpe, spy_dd, spy_rets, _p0, _p1 = get_2025_spy_metrics()
    hft_ret, hft_sharpe, hft_dd, hft_rets = run_2025_intraday()
    sw_ret, sw_sharpe, sw_dd, sw_rets = run_2025_swing()

    hft_alpha, hft_beta = compute_alpha_beta(hft_rets, spy_rets)
    sw_alpha, sw_beta = compute_alpha_beta(sw_rets, spy_rets)

    print("=== RESULTADOS AÑO 2025 (2025-01-01 a 2025-12-31) ===")
    print(f"SPY: Retorno={spy_ret:+.2f}%, Sharpe={spy_sharpe:.2f}, MaxDD={spy_dd:.2f}%")
    print(f"HFT: Retorno={hft_ret:+.2f}%, Alpha={hft_alpha:+.2f}%, Sharpe={hft_sharpe:.2f}, MaxDD={hft_dd:.2f}% (Beta={hft_beta:.2f})")
    print(f"SWING: Retorno={sw_ret:+.2f}%, Alpha={sw_alpha:+.2f}%, Sharpe={sw_sharpe:.2f}, MaxDD={sw_dd:.2f}% (Beta={sw_beta:.2f})")

if __name__ == "__main__":
    main()
