#!/usr/bin/env python3
"""Simulación de la Estrategia 7 (S7 PID Scorer de Doble Sistema) en el año 2025.

Período: 2025-01-01 a 2025-12-31.
Universo: 14 activos (`historical_14`) con SPY como benchmark.
Compara directamente:
1. S&P 500 (SPY)
2. High Frequency / Intraday (S6)
3. Swing Trading (S5 / 30 días)
4. S7 PID Scorer (Tendencia s=+1)
5. S7 PID Scorer (Reversión s=-1)
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

from tbot.strategies.s7_pid_scorer import score_universe_pid

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


def run_s7_simulation_2025(sign_p: float = 1.0) -> tuple[float, float, float, pd.Series, int]:
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

    # Fechas de 2025
    dates_2025 = df_spy[(df_spy["date"] >= "2025-01-01") & (df_spy["date"] <= "2025-12-31")]["date"].tolist()

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    equity_hist = {}
    trade_count = 0

    active_universe = [s for s in UNIVERSE_S5_S7 if s in daily_data]

    for d in dates_2025:
        d_str = d.strftime("%Y-%m-%d")

        # Sub-historial disponible estrictamente <= d
        sub_history = {}
        for s in active_universe:
            sub = daily_data[s][daily_data[s]["date"] <= d]
            if len(sub) > 0:
                sub_history[s] = sub
        sub_spy = daily_data["SPY"][daily_data["SPY"]["date"] <= d]

        # Calcular scores PID de S7
        scores_dict = score_universe_pid(sub_history, df_spy=sub_spy, sign_p=sign_p)

        # 1. Salidas
        for s in list(open_pos.keys()):
            pos = open_pos[s]
            pos["days"] += 1
            cur_bar = sub_history[s].iloc[-1]
            c_now = float(cur_bar["close"])
            l_now = float(cur_bar["low"]) if "low" in cur_bar else c_now
            s_ema25 = float(sub_history[s]["close"].ewm(span=25, adjust=False).mean().iloc[-1])

            res_pid = scores_dict.get(s)
            d_stress = res_pid.d_stress if res_pid else 0.0

            # Condiciones de salida
            hit_stop = (l_now <= pos["stop"])
            hit_trail = (c_now < s_ema25)
            hit_max = (pos["days"] >= 30)
            # Salida forzada por estrés de S7 spec (§8): d_i > +2.0
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
            # Filtrar candidatos con is_eligible == True según capa de arbitraje S7:
            # (u_i > 0) y (d_i < 1.0) y gate_absoluto
            eligible_cands = []
            for s, res in scores_dict.items():
                if s in open_pos:
                    continue
                if res.is_eligible:
                    c_now = float(sub_history[s].iloc[-1]["close"])
                    eligible_cands.append((s, res.u_score, res.d_stress, c_now))

            if eligible_cands:
                # Ranking por u_score descendente
                eligible_cands.sort(key=lambda x: x[1], reverse=True)
                tot_eq = cash + sum(p["shares"] * float(sub_history[sym].iloc[-1]["close"]) for sym, p in open_pos.items())
                slots = 4 - len(open_pos)

                for s, u_score, d_stress, c_now in eligible_cands[:slots]:
                    # Ponderación atenuada por d_i si d_i > 0 (§8)
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
        curr_inv = sum(p["shares"] * float(sub_history[s].iloc[-1]["close"]) for s, p in open_pos.items())
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

    spy_ret, spy_sharpe, spy_dd, spy_rets, _, _ = get_2025_spy_metrics()
    hft_ret, hft_sharpe, hft_dd, hft_rets = run_2025_intraday()
    sw_ret, sw_sharpe, sw_dd, sw_rets = run_2025_swing()

    hft_alpha, hft_beta = compute_alpha(hft_rets, spy_rets)
    sw_alpha, sw_beta = compute_alpha(sw_rets, spy_rets)

    # Correr S7 Tendencia (s=+1)
    s7_ret, s7_sharpe, s7_dd, s7_rets, s7_trades = run_s7_simulation_2025(sign_p=1.0)
    s7_alpha, s7_beta = compute_alpha(s7_rets, spy_rets)

    # Correr S7 Reversión (s=-1)
    s7_rev_ret, s7_rev_sharpe, s7_rev_dd, s7_rev_rets, _ = run_s7_simulation_2025(sign_p=-1.0)
    s7_rev_alpha, s7_rev_beta = compute_alpha(s7_rev_rets, spy_rets)

    print("\n" + "=" * 105)
    print("      COMPARATIVA CONSOLIDADA AÑO 2025 (2025-01-01 a 2025-12-31) INCLUYENDO S7 PID SCORER")
    print("=" * 105)
    print(f"{'Estrategia / Modelo':<38} | {'Rentabilidad':<13} | {'Alpha vs SPY':<13} | {'Sharpe':<8} | {'Max Drawdown'}")
    print("-" * 105)
    print(f"{'S&P 500 (SPY — Benchmark)':<38} | {spy_ret:+11.2f}% | {'0.00%':<13} | {spy_sharpe:6.2f}   | {spy_dd:6.2f}%")
    print(f"{'High Frequency / Intraday (S6)':<38} | {hft_ret:+11.2f}% | {hft_alpha:+11.2f}% | {hft_sharpe:6.2f}   | {hft_dd:6.2f}%")
    print(f"{'Swing Trading (S5 / 30 días)':<38} | {sw_ret:+11.2f}% | {sw_alpha:+11.2f}% | {sw_sharpe:6.2f}   | {sw_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Tendencia s=+1)':<38} | {s7_ret:+11.2f}% | {s7_alpha:+11.2f}% | {s7_sharpe:6.2f}   | {s7_dd:6.2f}%")
    print(f"{'S7 PID Scorer (Reversión s=-1)':<38} | {s7_rev_ret:+11.2f}% | {s7_rev_alpha:+11.2f}% | {s7_rev_sharpe:6.2f}   | {s7_rev_dd:6.2f}%")
    print("=" * 105)


if __name__ == "__main__":
    main()
