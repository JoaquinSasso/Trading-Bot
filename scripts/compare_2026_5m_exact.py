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

from tbot.strategies.s7_pid_scorer import score_universe_pid
from tbot.strategies.s8_pid_multihorizon import score_universe_s8

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


def compute_metrics(eq_s: pd.Series, initial_cap: float = 2000.0) -> tuple[float, float, float, float, pd.Series]:
    tot_ret = (float(eq_s.iloc[-1]) - initial_cap) / initial_cap * 100.0
    daily = eq_s.groupby(lambda x: str(x)[:10]).last()
    rets = daily.pct_change().dropna()
    n_days = len(daily)
    n_years = n_days / 252.0 if n_days > 0 else 1.0
    cagr = ((float(eq_s.iloc[-1]) / initial_cap) ** (1.0 / n_years) - 1.0) * 100.0 if n_years > 0 else 0.0
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0)) if rets.std() > 0 else 0.0
    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, cagr, sharpe, max_dd, rets


def compute_alpha_beta(strat_rets: pd.Series, spy_rets: pd.Series, strat_cagr: float, spy_cagr: float) -> tuple[float, float]:
    common = strat_rets.index.intersection(spy_rets.index)
    s = strat_rets.loc[common]
    b = spy_rets.loc[common]
    cov = np.cov(s, b)[0][1]
    var_b = np.var(b)
    beta = float(cov / var_b) if var_b > 0 else 1.0
    # Alpha anualizado respecto al benchmark
    alpha = strat_cagr - (beta * spy_cagr)
    return alpha, beta


def get_2026_spy_metrics() -> tuple[float, float, float, float, pd.Series]:
    df_spy = pd.read_csv(DATA_5M_DIR / "SPY_5m.csv")
    p0 = float(df_spy.iloc[0]["open"])
    p1 = float(df_spy.iloc[-1]["close"])
    tot_ret = (p1 - p0) / p0 * 100.0

    daily = df_spy.groupby("date")["close"].last()
    rets = daily.pct_change().dropna()
    n_days = len(daily)
    n_years = n_days / 252.0
    cagr = ((p1 / p0) ** (1.0 / n_years) - 1.0) * 100.0
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0))
    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)
    return tot_ret, cagr, sharpe, max_dd, rets


def run_2026_s6_intraday() -> tuple[float, float, float, float, pd.Series]:
    from scripts.run_backtest_intraday_5m import load_intraday_data, run_alpaca_intraday_simulation
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
    return res.total_return_pct, res.cagr_pct, res.sharpe_ratio, res.max_drawdown_pct, res.daily_returns


def run_2026_s5_swing() -> tuple[float, float, float, float, pd.Series]:
    # Swing S5 sobre datos diarios de 2026
    daily_data = {}
    for s in UNIVERSE_12:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    df_spy = daily_data["SPY"]
    dates_2026 = df_spy[(df_spy["date"] >= "2026-06-26") & (df_spy["date"] <= "2026-09-21")]["date"].tolist()

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    equity_hist = {}

    for d in dates_2026:
        d_str = d.strftime("%Y-%m-%d")
        sub_history = {s: daily_data[s][daily_data[s]["date"] <= d] for s in UNIVERSE_12}

        spy_sub = sub_history["SPY"]
        spy_c = float(spy_sub.iloc[-1]["close"])
        spy_e50 = float(spy_sub["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        market_bull = (spy_c >= spy_e50)

        # Salidas
        for s in list(open_pos.keys()):
            pos = open_pos[s]
            pos["days"] += 1
            cur_bar = sub_history[s].iloc[-1]
            c_now = float(cur_bar["close"])
            l_now = float(cur_bar["low"]) if "low" in cur_bar else c_now
            s_ema25 = float(sub_history[s]["close"].ewm(span=25, adjust=False).mean().iloc[-1])

            hit_stop = (l_now <= pos["stop"])
            hit_trail = (c_now < s_ema25)
            hit_max = (pos["days"] >= 30)
            regime_exit = not market_bull

            if hit_stop or hit_trail or hit_max or regime_exit:
                exit_p = (pos["stop"] if hit_stop else c_now) * (1.0 - 0.0003)
                gross = exit_p * pos["shares"]
                fees = calculate_alpaca_fees(gross, pos["shares"])
                cash += (gross - fees)
                del open_pos[s]
            else:
                pos["stop"] = max(pos["stop"], s_ema25 * 0.98)

        # Entradas
        if market_bull and len(open_pos) < 4:
            cands = []
            for s in UNIVERSE_12:
                if s in open_pos or s == "SPY":
                    continue
                sub = sub_history[s]
                if len(sub) < 50:
                    continue
                c_now = float(sub.iloc[-1]["close"])
                c_45 = float(sub.iloc[-46]["close"]) if len(sub) > 46 else float(sub.iloc[0]["close"])
                m45 = (c_now - c_45) / c_45
                e50 = float(sub["close"].ewm(span=50, adjust=False).mean().iloc[-1])

                if c_now > e50 and m45 > 0:
                    cands.append((s, m45, c_now))

            if cands:
                cands.sort(key=lambda x: x[1], reverse=True)
                tot_eq = cash + sum(p["shares"] * float(sub_history[sym].iloc[-1]["close"]) for sym, p in open_pos.items())
                slots = 4 - len(open_pos)
                for s, m45, c_now in cands[:slots]:
                    alloc = min(cash, tot_eq * 0.25)
                    if alloc < 20:
                        break
                    entry_p = c_now * (1.0 + 0.0003)
                    shares = round(alloc / entry_p, 4)
                    if shares <= 0:
                        continue
                    cost = shares * entry_p
                    cash -= cost
                    open_pos[s] = {
                        "shares": shares,
                        "entry_p": entry_p,
                        "cost": cost,
                        "stop": entry_p * (1.0 - 0.035),
                        "days": 0,
                    }

        curr_inv = sum(p["shares"] * float(sub_history[s].iloc[-1]["close"]) for s, p in open_pos.items())
        equity_hist[d_str] = cash + curr_inv

    eq_s = pd.Series(equity_hist)
    ret, cagr, sharpe, max_dd, rets = compute_metrics(eq_s, initial_cap)
    return ret, cagr, sharpe, max_dd, rets


def run_2026_s7_simulation(sign_p: float = 1.0) -> tuple[float, float, float, float, pd.Series]:
    daily_data = {}
    for s in UNIVERSE_12:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    df_spy = daily_data["SPY"]
    dates_2026 = df_spy[(df_spy["date"] >= "2026-06-26") & (df_spy["date"] <= "2026-09-21")]["date"].tolist()

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    equity_hist = {}

    for d in dates_2026:
        d_str = d.strftime("%Y-%m-%d")
        sub_history = {s: daily_data[s][daily_data[s]["date"] <= d] for s in UNIVERSE_12 if s in daily_data}
        sub_spy = daily_data["SPY"][daily_data["SPY"]["date"] <= d]

        scores_dict = score_universe_pid(sub_history, df_spy=sub_spy, sign_p=sign_p)

        # Salidas
        for s in list(open_pos.keys()):
            pos = open_pos[s]
            pos["days"] += 1
            cur_bar = sub_history[s].iloc[-1]
            c_now = float(cur_bar["close"])
            l_now = float(cur_bar["low"]) if "low" in cur_bar else c_now
            s_ema25 = float(sub_history[s]["close"].ewm(span=25, adjust=False).mean().iloc[-1])

            res_pid = scores_dict.get(s)
            d_stress = res_pid.d_stress if res_pid else 0.0

            hit_stop = (l_now <= pos["stop"])
            hit_trail = (c_now < s_ema25)
            hit_max = (pos["days"] >= 30)
            forced_exit_stress = (d_stress > 2.0)

            if hit_stop or hit_trail or hit_max or forced_exit_stress:
                exit_p = (pos["stop"] if hit_stop else c_now) * (1.0 - 0.0003)
                gross = exit_p * pos["shares"]
                fees = calculate_alpaca_fees(gross, pos["shares"])
                cash += (gross - fees)
                del open_pos[s]
            else:
                pos["stop"] = max(pos["stop"], s_ema25 * 0.98)

        # Entradas
        if len(open_pos) < 4:
            eligible_cands = []
            for s, res in scores_dict.items():
                if s in open_pos or s == "SPY":
                    continue
                if res.is_eligible:
                    c_now = float(sub_history[s].iloc[-1]["close"])
                    eligible_cands.append((s, res.u_score, res.d_stress, c_now))

            if eligible_cands:
                eligible_cands.sort(key=lambda x: x[1], reverse=True)
                tot_eq = cash + sum(p["shares"] * float(sub_history[sym].iloc[-1]["close"]) for sym, p in open_pos.items())
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
                    open_pos[s] = {
                        "shares": shares,
                        "entry_p": entry_p,
                        "cost": cost,
                        "stop": entry_p * (1.0 - 0.035),
                        "days": 0,
                    }

        curr_inv = sum(p["shares"] * float(sub_history[s].iloc[-1]["close"]) for s, p in open_pos.items())
        equity_hist[d_str] = cash + curr_inv

    eq_s = pd.Series(equity_hist)
    ret, cagr, sharpe, max_dd, rets = compute_metrics(eq_s, initial_cap)
    return ret, cagr, sharpe, max_dd, rets


def run_2026_s8_simulation(weight_rule: str = "A", sign_p: float = 1.0) -> tuple[float, float, float, float, pd.Series]:
    # S8 cargando barras de 5m reales para horizontes intradiarios completos
    daily_data = {}
    for s in UNIVERSE_12:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    intraday_5m_data = {}
    for s in UNIVERSE_12:
        p = DATA_5M_DIR / f"{s}_5m.csv"
        df_i = pd.read_csv(p)
        df_i["_dt"] = pd.to_datetime(df_i["datetime_et"])
        intraday_5m_data[s] = df_i.sort_values("_dt").reset_index(drop=True)

    df_spy_d = daily_data["SPY"]
    dates_2026 = df_spy_d[(df_spy_d["date"] >= "2026-06-26") & (df_spy_d["date"] <= "2026-09-21")]["date"].tolist()

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    equity_hist = {}

    for d in dates_2026:
        d_str = d.strftime("%Y-%m-%d")
        sub_daily = {s: daily_data[s][daily_data[s]["date"] <= d] for s in UNIVERSE_12}
        sub_spy_d = daily_data["SPY"][daily_data["SPY"]["date"] <= d]

        # Barras de 5m disponibles hasta el final de la sesión d
        sub_intraday = {
            s: intraday_5m_data[s][intraday_5m_data[s]["date"] <= d_str]
            for s in UNIVERSE_12 if s in intraday_5m_data
        }

        # Calcular scores S8 con los 8 horizontes (incluyendo 5m, 15m, 30m, 1h, 2h)
        scores_dict = score_universe_s8(
            universe_daily=sub_daily,
            universe_intraday=sub_intraday,
            df_spy=sub_spy_d,
            sign_p=sign_p,
            weight_rule=weight_rule,
        )

        # Salidas
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
                exit_p = (pos["stop"] if hit_stop else c_now) * (1.0 - 0.0003)
                gross = exit_p * pos["shares"]
                fees = calculate_alpaca_fees(gross, pos["shares"])
                cash += (gross - fees)
                del open_pos[s]
            else:
                pos["stop"] = max(pos["stop"], s_ema25 * 0.98)

        # Entradas
        if len(open_pos) < 4:
            eligible_cands = []
            for s, res in scores_dict.items():
                if s in open_pos or s == "SPY":
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
                    open_pos[s] = {
                        "shares": shares,
                        "entry_p": entry_p,
                        "cost": cost,
                        "stop": entry_p * (1.0 - 0.035),
                        "days": 0,
                    }

        curr_inv = sum(p["shares"] * float(sub_daily[s].iloc[-1]["close"]) for s, p in open_pos.items())
        equity_hist[d_str] = cash + curr_inv

    eq_s = pd.Series(equity_hist)
    ret, cagr, sharpe, max_dd, rets = compute_metrics(eq_s, initial_cap)
    return ret, cagr, sharpe, max_dd, rets


def main():
    print("Ejecutando pruebas sobre las 60 jornadas disponibles de 2026 con barras de 5 minutos...")
    spy_ret, spy_cagr, spy_sharpe, spy_dd, spy_rets = get_2026_spy_metrics()
    s6_ret, s6_cagr, s6_sharpe, s6_dd, s6_rets = run_2026_s6_intraday()
    s5_ret, s5_cagr, s5_sharpe, s5_dd, s5_rets = run_2026_s5_swing()
    s7_ret, s7_cagr, s7_sharpe, s7_dd, s7_rets = run_2026_s7_simulation(sign_p=1.0)
    s7_rev_ret, s7_rev_cagr, s7_rev_sharpe, s7_rev_dd, s7_rev_rets = run_2026_s7_simulation(sign_p=-1.0)
    s8_a_ret, s8_a_cagr, s8_a_sharpe, s8_a_dd, s8_a_rets = run_2026_s8_simulation(weight_rule="A", sign_p=1.0)
    s8_b_ret, s8_b_cagr, s8_b_sharpe, s8_b_dd, s8_b_rets = run_2026_s8_simulation(weight_rule="B", sign_p=1.0)

    s6_alpha, _ = compute_alpha_beta(s6_rets, spy_rets, s6_cagr, spy_cagr)
    s5_alpha, _ = compute_alpha_beta(s5_rets, spy_rets, s5_cagr, spy_cagr)
    s7_alpha, _ = compute_alpha_beta(s7_rets, spy_rets, s7_cagr, spy_cagr)
    s7_rev_alpha, _ = compute_alpha_beta(s7_rev_rets, spy_rets, s7_rev_cagr, spy_cagr)
    s8_a_alpha, _ = compute_alpha_beta(s8_a_rets, spy_rets, s8_a_cagr, spy_cagr)
    s8_b_alpha, _ = compute_alpha_beta(s8_b_rets, spy_rets, s8_b_cagr, spy_cagr)

    print("\n" + "=" * 130)
    print("   COMPARATIVA 2026 CON BARRAS DE 5 MINUTOS (2026-06-26 a 2026-09-21: 60 Sesiones, 4.632 Barras de 5m)")
    print("=" * 130)
    print(f"{'Estrategia / Modelo':<45} | {'Ret. Acumulado':<15} | {'CAGR Anual':<12} | {'Alpha vs SPY':<13} | {'Sharpe':<7} | {'MaxDD'}")
    print("-" * 130)
    print(f"{'S&P 500 (SPY — Benchmark)':<45} | {spy_ret:+13.2f}% | {spy_cagr:+10.2f}% | {'0.00%':<13} | {spy_sharpe:5.2f} | {spy_dd:5.2f}%")
    print(f"{'High Frequency / Intraday (S6 5m)':<45} | {s6_ret:+13.2f}% | {s6_cagr:+10.2f}% | {s6_alpha:+11.2f}% | {s6_sharpe:5.2f} | {s6_dd:5.2f}%")
    print(f"{'Swing Trading (S5 / 30 días)':<45} | {s5_ret:+13.2f}% | {s5_cagr:+10.2f}% | {s5_alpha:+11.2f}% | {s5_sharpe:5.2f} | {s5_dd:5.2f}%")
    print(f"{'S7 PID Scorer (Tendencia s=+1)':<45} | {s7_ret:+13.2f}% | {s7_cagr:+10.2f}% | {s7_alpha:+11.2f}% | {s7_sharpe:5.2f} | {s7_dd:5.2f}%")
    print(f"{'S7 PID Scorer (Reversión s=-1)':<45} | {s7_rev_ret:+13.2f}% | {s7_rev_cagr:+10.2f}% | {s7_rev_alpha:+11.2f}% | {s7_rev_sharpe:5.2f} | {s7_rev_dd:5.2f}%")
    print(f"{'S8 PID Multi-Horizonte (Variante A: w ~ h)':<45} | {s8_a_ret:+13.2f}% | {s8_a_cagr:+10.2f}% | {s8_a_alpha:+11.2f}% | {s8_a_sharpe:5.2f} | {s8_a_dd:5.2f}%")
    print(f"{'S8 PID Multi-Horizonte (Variante B: w ~ ln h)':<45} | {s8_b_ret:+13.2f}% | {s8_b_cagr:+10.2f}% | {s8_b_alpha:+11.2f}% | {s8_b_sharpe:5.2f} | {s8_b_dd:5.2f}%")
    print("=" * 130)


if __name__ == "__main__":
    main()
