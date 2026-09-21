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
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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

def run_2025_intraday():
    # Cargar barras de 1h de 2025
    data_1h = {}
    for sym in UNIVERSE_INTRADAY:
        df = pd.read_csv(DATA_1H_DIR / f"{sym}_1h.csv")
        df = df[(df["date"] >= "2025-01-01") & (df["date"] <= "2025-12-31")].sort_values("datetime_et").reset_index(drop=True)
        data_1h[sym] = df

    all_ts = sorted(set.intersection(*[set(df["timestamp"]) for df in data_1h.values()]))
    bar_maps = {s: {r["timestamp"]: r for _, r in df.iterrows()} for s, df in data_1h.items()}

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    closed_trades = []
    equity_hist = {}
    rolling_closes = {s: [] for s in UNIVERSE_INTRADAY}
    horizons = {"2h": 2, "4h": 4, "7h": 7, "14h": 14, "28h": 28}
    half_spd = 0.000050  # 0.50 bps PFOF

    for idx, ts in enumerate(all_ts):
        sample = bar_maps["SPY"][ts]
        cur_dt = sample["datetime_et"]
        cur_time = sample["time"]

        for s in UNIVERSE_INTRADAY:
            b = bar_maps[s].get(ts)
            if b is not None:
                rolling_closes[s].append(float(b["close"]))

        # Salidas
        for s in list(open_pos.keys()):
            pos = open_pos[s]
            pos["bars"] += 1
            c_now = float(bar_maps[s][ts]["close"])
            l_now = float(bar_maps[s][ts]["low"])

            s_closes = pd.Series(rolling_closes[s][-40:])
            e_trail = float(s_closes.ewm(span=21, adjust=False).mean().iloc[-1])

            hit_stop = (l_now <= pos["stop"])
            hit_trail = (pos["bars"] >= 2 and c_now < e_trail)
            hit_max = (pos["bars"] >= 42)

            if hit_stop or hit_trail or hit_max:
                raw_exit = pos["stop"] if hit_stop else c_now
                exit_p = raw_exit * (1.0 - half_spd)
                gross = exit_p * pos["shares"]
                fees = calculate_alpaca_fees(gross, pos["shares"])
                net = gross - fees
                cash += net
                closed_trades.append(net - pos["cost"])
                del open_pos[s]
            else:
                pos["stop"] = max(pos["stop"], e_trail * 0.995)

        # Entradas
        if len(open_pos) < 3 and idx >= 30:
            spy_c = pd.Series(rolling_closes["SPY"][-40:])
            spy_e = float(spy_c.ewm(span=21, adjust=False).mean().iloc[-1])

            if rolling_closes["SPY"][-1] >= spy_e:
                candidates = []
                for s in UNIVERSE_INTRADAY:
                    if s in open_pos or s == "SPY":
                        continue
                    rc = rolling_closes[s]
                    if len(rc) < 35:
                        continue
                    c_now = rc[-1]
                    m28 = (c_now - rc[-29]) / rc[-29]
                    m2 = (c_now - rc[-3]) / rc[-3]
                    e_trend = float(pd.Series(rc[-40:]).ewm(span=21, adjust=False).mean().iloc[-1])

                    if c_now > e_trend and m28 > 0 and m2 > 0:
                        candidates.append((s, m28, c_now))

                if candidates:
                    candidates.sort(key=lambda x: x[1], reverse=True)
                    tot_eq = cash + sum(p["shares"] * float(bar_maps[sym][ts]["close"]) for sym, p in open_pos.items())
                    slots = 3 - len(open_pos)
                    for s, m28, raw_p in candidates[:slots]:
                        alloc = min(cash, tot_eq * 0.30)
                        if alloc < 20:
                            break
                        entry_p = raw_p * (1.0 + half_spd)
                        shares = round(alloc / entry_p, 4)
                        if shares <= 0:
                            continue
                        cost = shares * entry_p
                        cash -= cost
                        open_pos[s] = {
                            "shares": shares,
                            "entry_p": entry_p,
                            "cost": cost,
                            "stop": entry_p * (1.0 - 0.018),
                            "bars": 0,
                        }

        curr_inv = sum(p["shares"] * float(bar_maps[s][ts]["close"]) for s, p in open_pos.items())
        equity_hist[cur_dt] = cash + curr_inv

    eq_s = pd.Series(equity_hist)
    tot_ret = (float(eq_s.iloc[-1]) - initial_cap) / initial_cap * 100.0

    daily = eq_s.groupby(lambda x: x[:10]).last()
    rets = daily.pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0))

    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)

    return tot_ret, sharpe, max_dd, rets

def run_2025_swing():
    # Swing S5 (Daily, 45d momentum, 30d max hold, EMA25 trailing stop, SPY regime filter)
    # Cargar datos diarios de 2024 a 2025 para tener historial de momentum a 45 días
    daily_data = {}
    for s in UNIVERSE_S5:
        p = DATA_DAILY_DIR / f"{s}_daily.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        daily_data[s] = df.sort_values("date").reset_index(drop=True)

    df_spy = pd.read_csv(DATA_DAILY_DIR / "SPY_daily.csv")
    df_spy["date"] = pd.to_datetime(df_spy["date"])
    daily_data["SPY"] = df_spy.sort_values("date").reset_index(drop=True)

    # Alinear fechas 2025
    spy_dates_2025 = df_spy[(df_spy["date"] >= "2025-01-01") & (df_spy["date"] <= "2025-12-31")]["date"].tolist()

    cash = 2000.0
    initial_cap = 2000.0
    open_pos = {}
    equity_hist = {}

    for d in spy_dates_2025:
        d_str = d.strftime("%Y-%m-%d")

        # Precios de cierre actuales
        closes = {}
        for s in daily_data:
            sub = daily_data[s][daily_data[s]["date"] <= d]
            if len(sub) > 0:
                closes[s] = sub

        # Verificar filtro macro SPY (Close > SMA200 / EMA50)
        spy_sub = closes["SPY"]
        spy_c = float(spy_sub.iloc[-1]["close"])
        spy_ema50 = float(spy_sub["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        market_bull = (spy_c >= spy_ema50)

        # Salidas
        for s in list(open_pos.keys()):
            pos = open_pos[s]
            pos["days"] += 1
            c_now = float(closes[s].iloc[-1]["close"])
            l_now = float(closes[s].iloc[-1]["low"]) if "low" in closes[s] else c_now
            s_ema25 = float(closes[s]["close"].ewm(span=25, adjust=False).mean().iloc[-1])

            hit_stop = (l_now <= pos["stop"])
            hit_trail = (c_now < s_ema25)
            hit_max = (pos["days"] >= 30)
            regime_exit = not market_bull

            if hit_stop or hit_trail or hit_max or regime_exit:
                exit_p = pos["stop"] if hit_stop else c_now
                # Spread 3 bps
                exit_p *= (1.0 - 0.0003)
                gross = exit_p * pos["shares"]
                fees = calculate_alpaca_fees(gross, pos["shares"])
                cash += (gross - fees)
                del open_pos[s]
            else:
                pos["stop"] = max(pos["stop"], s_ema25 * 0.98)

        # Entradas si régimen alcista
        if market_bull and len(open_pos) < 4:
            cands = []
            for s in UNIVERSE_S5:
                if s in open_pos or s == "SPY":
                    continue
                sub = closes.get(s)
                if sub is None or len(sub) < 50:
                    continue
                c_now = float(sub.iloc[-1]["close"])
                c_45d = float(sub.iloc[-46]["close"]) if len(sub) > 46 else float(sub.iloc[0]["close"])
                m45 = (c_now - c_45d) / c_45d
                e50 = float(sub["close"].ewm(span=50, adjust=False).mean().iloc[-1])

                if c_now > e50 and m45 > 0:
                    cands.append((s, m45, c_now))

            if cands:
                cands.sort(key=lambda x: x[1], reverse=True)
                tot_eq = cash + sum(p["shares"] * float(closes[sym].iloc[-1]["close"]) for sym, p in open_pos.items())
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

        curr_inv = sum(p["shares"] * float(closes[s].iloc[-1]["close"]) for s, p in open_pos.items())
        equity_hist[d_str] = cash + curr_inv

    eq_s = pd.Series(equity_hist)
    tot_ret = (float(eq_s.iloc[-1]) - initial_cap) / initial_cap * 100.0

    rets = eq_s.pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * np.sqrt(252.0))

    cum = (1.0 + rets).cumprod()
    max_dd = float(abs(((cum - cum.cummax()) / cum.cummax()).min()) * 100.0)

    return tot_ret, sharpe, max_dd, rets

def compute_alpha_beta(strat_rets, spy_rets):
    common = strat_rets.index.intersection(spy_rets.index)
    s = strat_rets.loc[common]
    b = spy_rets.loc[common]
    cov = np.cov(s, b)[0][1]
    var_b = np.var(b)
    beta = float(cov / var_b) if var_b > 0 else 1.0
    # Jensen's Alpha anualizado
    r_s_ann = (1.0 + s.mean()) ** 252 - 1.0
    r_b_ann = (1.0 + b.mean()) ** 252 - 1.0
    # rf en 2025 fue aprox 4.12% (BIL)
    rf = 0.0412
    alpha = (r_s_ann - rf) - beta * (r_b_ann - rf)
    return alpha * 100.0, beta

def main():
    spy_ret, spy_sharpe, spy_dd, spy_rets, p0, p1 = get_2025_spy_metrics()
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
