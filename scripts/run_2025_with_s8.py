#!/usr/bin/env python3
"""Simulación de la Estrategia 8 (S8 PID Multi-Horizonte con 8 Escalas) en 2025.

Período exacto: 2025-01-01 a 2025-12-31.
Compara directamente:
1. S&P 500 (SPY — Benchmark)
2. High Frequency / Intraday (S6)
3. Swing Trading (S5 / 30 días)
4. S7 PID Scorer (Tendencia s=+1)
5. S7 PID Scorer (Reversión s=-1)
6. S8 PID Multi-Horizonte (Variante A: w ∝ h)
7. S8 PID Multi-Horizonte (Variante B: w ∝ ln h)
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

from tbot.strategies.s8_pid_multihorizon import score_universe_s8

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


def run_s8_simulation_2025(weight_rule: str = "A", sign_p: float = 1.0) -> tuple[float, float, float, pd.Series, int]:
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

    # 2. Cargar datos intradiarios (1h)
    intraday_data = {}
    for s in UNIVERSE_S8 + ["SPY"]:
        p = DATA_1H_DIR / f"{s}_1h.csv"
        if p.exists():
            df_i = pd.read_csv(p)
            df_i["_dt"] = pd.to_datetime(df_i["datetime_et"])
            intraday_data[s] = df_i.sort_values("_dt").reset_index(drop=True)

    dates_2025 = df_spy_d[(df_spy_d["date"] >= "2025-01-01") & (df_spy_d["date"] <= "2025-12-31")]["date"].tolist()
    active_universe = [s for s in UNIVERSE_S8 if s in daily_data]

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    equity_hist = {}
    trade_count = 0

    for d in dates_2025:
        d_str = d.strftime("%Y-%m-%d")

        # Sub-historial diario estrictamente <= d
        sub_daily = {}
        for s in active_universe:
            sub = daily_data[s][daily_data[s]["date"] <= d]
            if len(sub) > 0:
                sub_daily[s] = sub
        sub_spy_d = daily_data["SPY"][daily_data["SPY"]["date"] <= d]

        # Sub-historial intradiario disponible estrictamente <= d 16:00
        sub_intraday = {}
        for s in active_universe:
            if s in intraday_data:
                sub_i = intraday_data[s][intraday_data[s]["date"] <= d_str]
                if len(sub_i) > 0:
                    sub_intraday[s] = sub_i
        sub_spy_i = intraday_data["SPY"][intraday_data["SPY"]["date"] <= d_str] if "SPY" in intraday_data else None

        # Calcular scores S8 multi-horizonte
        scores_dict = score_universe_s8(
            universe_daily=sub_daily,
            universe_intraday=sub_intraday,
            df_spy=sub_spy_d,
            sign_p=sign_p,
            weight_rule=weight_rule,
        )

        # 1. Salidas
        for s in list(open_pos.keys()):
            pos = open_pos[s]
            pos["days"] += 1
            cur_bar = sub_daily[s].iloc[-1]
            c_now = float(cur_bar["close"])
            l_now = float(cur_bar["low"]) if "low" in cur_bar else c_now
            s_ema25 = float(sub_daily[s]["close"].ewm(span=25, adjust=False).mean().iloc[-1])

            res_s8 = scores_dict.get(s)
            d_stress = res_s8.d_stress if res_s8 else 0.0

            hit_stop = (l_now <= pos["stop"])
            hit_trail = (c_now < s_ema25)
            hit_max = (pos["days"] >= 30)
            forced_exit_stress = (d_stress > 2.0)

            if hit_stop or hit_trail or hit_max or forced_exit_stress:
                exit_p = pos["stop"] if hit_stop else c_now
                exit_p *= (1.0 - 0.0003)  # spread Alpaca
                gross = exit_p * pos["shares"]
                fees = calculate_alpaca_fees(gross, pos["shares"])
                cash += (gross - fees)
                trade_count += 1
                del open_pos[s]
            else:
                pos["stop"] = max(pos["stop"], s_ema25 * 0.98)

        # 2. Entradas
        if len(open_pos) < 4:
            eligible_cands = []
            for s, res in scores_dict.items():
                if s in open_pos:
                    continue
                if res.is_eligible:
                    c_now = float(sub_daily[s].iloc[-1]["close"])
                    eligible_cands.append((s, res.u_score, res.d_stress, c_now))

            if eligible_cands:
                eligible_cands.sort(key=lambda x: x[1], reverse=True)
                tot_eq = cash + sum(p["shares"] * float(sub_daily[sym].iloc[-1]["close"]) for sym, p in open_pos.items())
                slots = 4 - len(open_pos)

                for s, u_score, d_stress, c_now in eligible_cands[:slots]:
                    attenuation = max(0.5, 1.0 - 0.5 * max(0.0, d_stress))
                    alloc = min(cash, tot_eq * 0.25 * attenuation)
                    if alloc < 20.0:
                        break
                    entry_p = c_now * (1.0 + 0.0003)
                    shares = round(alloc / entry_p, 4)
                    if shares <= 0:
                        continue
                    cost = shares * entry_p
                    cash -= cost
                    trade_count += 1
                    open_pos[s] = {
                        "shares": shares,
                        "entry_p": entry_p,
                        "cost": cost,
                        "stop": entry_p * (1.0 - 0.035),
                        "days": 0,
                    }

        # 3. Equidad
        curr_inv = sum(p["shares"] * float(sub_daily[s].iloc[-1]["close"]) for s, p in open_pos.items())
        equity_hist[d_str] = cash + curr_inv

    eq_s = pd.Series(equity_hist)
    ret, sharpe, max_dd, rets = compute_metrics(eq_s, initial_cap)
    return ret, sharpe, max_dd, rets, trade_count


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
    from scripts.compare_2025_exact import get_2025_spy_metrics, run_2025_intraday, run_2025_swing
    from scripts.run_2025_with_s7 import run_s7_simulation_2025

    print("Ejecutando simulación comparativa completa del año 2025...")
    spy_ret, spy_sharpe, spy_dd, spy_rets, _, _ = get_2025_spy_metrics()
    hft_ret, hft_sharpe, hft_dd, hft_rets = run_2025_intraday()
    sw_ret, sw_sharpe, sw_dd, sw_rets = run_2025_swing()

    hft_alpha, _ = compute_alpha(hft_rets, spy_rets)
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
    print("        COMPARATIVA CONSOLIDADA AÑO 2025 (2025-01-01 a 2025-12-31) INCLUYENDO S8 MULTI-HORIZONTE")
    print("=" * 115)
    print(f"{'Estrategia / Modelo':<45} | {'Rentabilidad':<13} | {'Alpha vs SPY':<13} | {'Sharpe':<8} | {'Max Drawdown'}")
    print("-" * 115)
    print(f"{'S&P 500 (SPY — Benchmark)':<45} | {spy_ret:+11.2f}% | {'0.00%':<13} | {spy_sharpe:6.2f}   | {spy_dd:6.2f}%")
    print(f"{'High Frequency / Intraday (S6)':<45} | {hft_ret:+11.2f}% | {hft_alpha:+11.2f}% | {hft_sharpe:6.2f}   | {hft_dd:6.2f}%")
    print(f"{'Swing Trading (S5 / 30 días)':<45} | {sw_ret:+11.2f}% | {sw_alpha:+11.2f}% | {sw_sharpe:6.2f}   | {sw_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Tendencia s=+1)':<45} | {s7_ret:+11.2f}% | {s7_alpha:+11.2f}% | {s7_sharpe:6.2f}   | {s7_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Reversión s=-1)':<45} | {s7_rev_ret:+11.2f}% | {s7_rev_alpha:+11.2f}% | {s7_rev_sharpe:6.2f}   | {s7_rev_dd:6.2f}%")
    print(f"{'S8 PID Multi-Horizonte (Variante A: w ~ h)':<45} | {s8_a_ret:+11.2f}% | {s8_a_alpha:+11.2f}% | {s8_a_sharpe:6.2f}   | {s8_a_dd:6.2f}%")
    print(f"{'S8 PID Multi-Horizonte (Variante B: w ~ ln h)':<45} | {s8_b_ret:+11.2f}% | {s8_b_alpha:+11.2f}% | {s8_b_sharpe:6.2f}   | {s8_b_dd:6.2f}%")
    print("=" * 115)


if __name__ == "__main__":
    main()
