#!/usr/bin/env python3
"""Simulación de Rentabilidad con Modelo Estándar Retail de Alpaca.

Evalúa la estrategia S6 (barras de 5 minutos, multi-horizonte 10m-60m):
- Comisión de corretaje: $0 (Commission-Free).
- Costos regulatorios obligatorios (Pass-Through en ventas):
  * SEC Fee: ~$0.0000206 del valor total de la orden (min $0.01).
  * FINRA TAF: ~$0.000195 por acción (min $0.01, tope $8.98).
  * CAT Fee: Fracción mínima por acción (~$0.00003/acción).
- Microestructura PFOF:
  * Barrido de microvariaciones de spread: 0.0 bps (Midpoint), 0.25 bps, 0.50 bps, 1.0 bps, y Spread Completo NBBO.
- Acciones fraccionarias vs enteras (cuenta $2.000 y $25.000).
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "intraday_5m"
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tbot.indicators.pure import ema
from tbot.strategies.s6_intraday_5m_multi_horizon import HORIZON_BARS_MAP

INTRADAY_UNIVERSE = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]

TICKER_SPREAD_BPS = {
    "SPY": 0.00008,
    "QQQ": 0.00010,
    "AAPL": 0.00012,
    "MSFT": 0.00015,
    "NVDA": 0.00015,
    "AMZN": 0.00018,
    "META": 0.00020,
    "GOOGL": 0.00020,
    "TSLA": 0.00025,
    "AMD": 0.00025,
    "IWM": 0.00015,
    "GLD": 0.00015,
}


@dataclass
class AlpacaTrade:
    symbol: str
    entry_datetime: str
    exit_datetime: str
    entry_price: float
    exit_price: float
    shares: float
    gross_pnl: float
    net_pnl: float
    sec_fee: float
    taf_fee: float
    cat_fee: float
    spread_cost: float
    bars_held: int
    exit_reason: str


@dataclass
class AlpacaSimResult:
    name: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    total_sec_fees: float
    total_taf_fees: float
    total_cat_fees: float
    total_spread_cost: float
    total_regulatory_fees: float
    total_friction_usd: float
    friction_drag_pct: float


def load_intraday_data() -> dict[str, pd.DataFrame]:
    data = {}
    for sym in INTRADAY_UNIVERSE:
        p = DATA_DIR / f"{sym}_5m.csv"
        df = pd.read_csv(p)
        df["_dt"] = pd.to_datetime(df["datetime_et"])
        data[sym] = df.sort_values("_dt").reset_index(drop=True)
    return data


def calculate_alpaca_fees(sale_notional: float, shares: float) -> tuple[float, float, float]:
    """SEC Fee, FINRA TAF, y CAT Fee según reglas de liquidación en ventas."""
    if sale_notional <= 0.0 or shares <= 0.0:
        return 0.0, 0.0, 0.0
    sec_fee = max(0.01, math.ceil(sale_notional * 0.0000206 * 100.0) / 100.0)
    taf_fee = min(8.98, max(0.01, math.ceil(shares * 0.000195 * 100.0) / 100.0))
    cat_fee = math.ceil(shares * 0.00003 * 100.0) / 100.0 if (shares * 0.00003 >= 0.005) else 0.0
    return sec_fee, taf_fee, cat_fee


def run_alpaca_simulation(
    daily_data: dict[str, pd.DataFrame],
    config_name: str,
    initial_capital: float = 2000.0,
    half_spread_mode: str = "fixed_0bps",  # "fixed_0bps", "fixed_0.25bps", "fixed_0.5bps", "fixed_1bps", "nbbo"
    use_fractional: bool = True,
    apply_reg_fees: bool = True,
    top_n: int = 3,
    max_weight_per_asset: float = 0.30,
    trailing_ema_period: int = 9,
    trend_ema_period: int = 21,
    initial_stop_pct: float = 0.008,
    max_holding_bars: int = 24,
) -> AlpacaSimResult:
    all_timestamps = sorted(
        set.intersection(*[set(df["timestamp"]) for df in daily_data.values()])
    )
    bar_maps = {
        sym: {row["timestamp"]: row for _, row in df.iterrows()}
        for sym, df in daily_data.items()
    }

    cash = initial_capital
    open_positions: dict[str, dict] = {}
    closed_trades: list[AlpacaTrade] = []
    equity_history: dict[str, float] = {}

    tot_sec = 0.0
    tot_taf = 0.0
    tot_cat = 0.0
    tot_spread = 0.0

    rolling_closes: dict[str, list[float]] = {s: [] for s in INTRADAY_UNIVERSE}

    for idx, ts in enumerate(all_timestamps):
        bar_sample = bar_maps["SPY"][ts]
        cur_dt_str = bar_sample["datetime_et"]
        time_parts = [int(p) for p in bar_sample["time"].split(":")]
        cur_time = time(time_parts[0], time_parts[1], time_parts[2])

        for sym in INTRADAY_UNIVERSE:
            b = bar_maps[sym].get(ts)
            if b is not None:
                rolling_closes[sym].append(float(b["close"]))

        is_flatten_time = (cur_time >= time(15, 55))

        # 1. Salidas
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            pos["bars_held"] += 1
            cur_bar = bar_maps[sym][ts]
            c_now = float(cur_bar["close"])
            l_now = float(cur_bar["low"])

            closes_s = pd.Series(rolling_closes[sym][-30:])
            trailing_ema = float(ema(closes_s, trailing_ema_period).iloc[-1])

            hit_stop = (l_now <= pos["stop_loss"])
            hit_trailing = (pos["bars_held"] >= 2 and c_now < trailing_ema)
            hit_max_bars = (pos["bars_held"] >= max_holding_bars)
            day_end_exit = is_flatten_time

            if hit_stop or hit_trailing or hit_max_bars or day_end_exit:
                if hit_stop:
                    raw_exit = pos["stop_loss"]
                    reason = "initial_stop_loss"
                else:
                    raw_exit = c_now
                    reason = "day_end_flatten" if day_end_exit else ("trailing_ema" if hit_trailing else "max_holding_bars")

                # Spread de salida
                if half_spread_mode == "fixed_0bps":
                    h_spd = 0.0
                elif half_spread_mode == "fixed_0.25bps":
                    h_spd = 0.000025
                elif half_spread_mode == "fixed_0.5bps":
                    h_spd = 0.000050
                elif half_spread_mode == "fixed_1bps":
                    h_spd = 0.000100
                elif half_spread_mode == "nbbo":
                    h_spd = TICKER_SPREAD_BPS.get(sym, 0.00015)
                else:
                    h_spd = 0.0

                exit_price = raw_exit * (1.0 - h_spd)
                gross_proceeds = exit_price * pos["shares"]
                spd_loss_exit = raw_exit * h_spd * pos["shares"]
                tot_spread += spd_loss_exit

                if apply_reg_fees:
                    sec_f, taf_f, cat_f = calculate_alpaca_fees(gross_proceeds, pos["shares"])
                else:
                    sec_f, taf_f, cat_f = 0.0, 0.0, 0.0

                tot_sec += sec_f
                tot_taf += taf_f
                tot_cat += cat_f

                net_proceeds = gross_proceeds - (sec_f + taf_f + cat_f)
                cash += net_proceeds

                gross_pnl = (raw_exit - pos["raw_entry_price"]) * pos["shares"]
                net_pnl = net_proceeds - pos["entry_cost"]

                closed_trades.append(
                    AlpacaTrade(
                        symbol=sym,
                        entry_datetime=pos["entry_dt"],
                        exit_datetime=cur_dt_str,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        shares=pos["shares"],
                        gross_pnl=gross_pnl,
                        net_pnl=net_pnl,
                        sec_fee=sec_f,
                        taf_fee=taf_f,
                        cat_fee=cat_f,
                        spread_cost=pos["entry_spd_cost"] + spd_loss_exit,
                        bars_held=pos["bars_held"],
                        exit_reason=reason,
                    )
                )
                del open_positions[sym]
            else:
                pos["stop_loss"] = max(pos["stop_loss"], trailing_ema * 0.995)

        # 2. Entradas
        can_open = (time(9, 45) <= cur_time <= time(15, 30)) and not is_flatten_time

        if can_open and len(open_positions) < top_n and idx >= 25:
            spy_closes_s = pd.Series(rolling_closes["SPY"][-35:])
            spy_trend_ema = float(ema(spy_closes_s, trend_ema_period).iloc[-1])
            spy_c = rolling_closes["SPY"][-1]

            if spy_c >= spy_trend_ema:
                metrics_list = []
                for sym in INTRADAY_UNIVERSE:
                    if sym in open_positions or sym == "SPY":
                        continue
                    rc = rolling_closes[sym]
                    if len(rc) < 20:
                        continue
                    c_now = rc[-1]
                    m10 = (c_now - rc[-1 - HORIZON_BARS_MAP[10]]) / rc[-1 - HORIZON_BARS_MAP[10]]
                    m15 = (c_now - rc[-1 - HORIZON_BARS_MAP[15]]) / rc[-1 - HORIZON_BARS_MAP[15]]
                    m30 = (c_now - rc[-1 - HORIZON_BARS_MAP[30]]) / rc[-1 - HORIZON_BARS_MAP[30]]
                    m45 = (c_now - rc[-1 - HORIZON_BARS_MAP[45]]) / rc[-1 - HORIZON_BARS_MAP[45]]
                    m60 = (c_now - rc[-1 - HORIZON_BARS_MAP[60]]) / rc[-1 - HORIZON_BARS_MAP[60]]

                    s_closes = pd.Series(rc[-35:])
                    e21 = float(ema(s_closes, trend_ema_period).iloc[-1])

                    if c_now > e21 and m60 > 0.0 and m10 > 0.0:
                        metrics_list.append({
                            "sym": sym,
                            "m10": m10,
                            "m15": m15,
                            "m30": m30,
                            "m45": m45,
                            "m60": m60,
                            "price": c_now,
                        })

                if metrics_list:
                    for h_k in ["m10", "m15", "m30", "m45", "m60"]:
                        metrics_list.sort(key=lambda x: x[h_k], reverse=True)
                        for r_i, item in enumerate(metrics_list, 1):
                            item[f"r_{h_k}"] = r_i

                    for item in metrics_list:
                        item["composite_rank"] = (
                            item["r_m10"] + item["r_m15"] + item["r_m30"] + item["r_m45"] + item["r_m60"]
                        ) / 5.0

                    metrics_list.sort(key=lambda x: x["composite_rank"])
                    available_slots = top_n - len(open_positions)
                    selected = metrics_list[:available_slots]

                    curr_inv = sum(
                        p["shares"] * float(bar_maps[s][ts]["close"])
                        for s, p in open_positions.items()
                    )
                    tot_eq = cash + curr_inv

                    for cand in selected:
                        alloc = min(cash, tot_eq * max_weight_per_asset)
                        if alloc < 20.0:
                            break

                        sym = cand["sym"]
                        raw_p = cand["price"]

                        if half_spread_mode == "fixed_0bps":
                            h_spd = 0.0
                        elif half_spread_mode == "fixed_0.25bps":
                            h_spd = 0.000025
                        elif half_spread_mode == "fixed_0.5bps":
                            h_spd = 0.000050
                        elif half_spread_mode == "fixed_1bps":
                            h_spd = 0.000100
                        elif half_spread_mode == "nbbo":
                            h_spd = TICKER_SPREAD_BPS.get(sym, 0.00015)
                        else:
                            h_spd = 0.0

                        entry_price = raw_p * (1.0 + h_spd)

                        if use_fractional:
                            shares = round(alloc / entry_price, 4)
                        else:
                            shares = float(math.floor(alloc / entry_price))

                        if shares <= 0:
                            continue

                        cost_val = shares * entry_price
                        spd_loss_entry = raw_p * h_spd * shares
                        tot_spread += spd_loss_entry

                        cash -= cost_val
                        open_positions[sym] = {
                            "symbol": sym,
                            "entry_dt": cur_dt_str,
                            "raw_entry_price": raw_p,
                            "entry_price": entry_price,
                            "entry_cost": cost_val,
                            "shares": shares,
                            "stop_loss": entry_price * (1.0 - initial_stop_pct),
                            "bars_held": 0,
                            "entry_spd_cost": spd_loss_entry,
                        }

        # 3. Equidad
        curr_inv = sum(
            p["shares"] * float(bar_maps[s][ts]["close"])
            for s, p in open_positions.items()
        )
        equity_history[cur_dt_str] = cash + curr_inv

    eq_s = pd.Series(equity_history)
    final_cap = float(eq_s.iloc[-1])
    tot_ret = ((final_cap - initial_capital) / initial_capital) * 100.0

    cummax = eq_s.cummax()
    max_dd = float(abs(((eq_s - cummax) / cummax).min()) * 100.0)

    daily_closes = eq_s.groupby(lambda x: x[:10]).last()
    daily_rets = daily_closes.pct_change().dropna()
    mean_d = float(daily_rets.mean())
    std_d = float(daily_rets.std())
    sharpe = float((mean_d / std_d) * np.sqrt(252.0)) if std_d > 0 else 0.0

    wins = [t for t in closed_trades if t.net_pnl > 0]
    losses = [t for t in closed_trades if t.net_pnl <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    gp = sum(t.net_pnl for t in wins)
    gl = abs(sum(t.net_pnl for t in losses))
    pf = (gp / gl) if gl > 0 else 99.0

    tot_reg = tot_sec + tot_taf + tot_cat
    tot_fric = tot_reg + tot_spread
    fric_drag = (tot_fric / initial_capital) * 100.0

    return AlpacaSimResult(
        name=config_name,
        initial_capital=initial_capital,
        final_capital=final_cap,
        total_return_pct=tot_ret,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        total_trades=len(closed_trades),
        win_rate_pct=win_rate,
        profit_factor=pf,
        total_sec_fees=tot_sec,
        total_taf_fees=tot_taf,
        total_cat_fees=tot_cat,
        total_spread_cost=tot_spread,
        total_regulatory_fees=tot_reg,
        total_friction_usd=tot_fric,
        friction_drag_pct=fric_drag,
    )


def main():
    data = load_intraday_data()
    print("=" * 110)
    print("        EVALUACIÓN DE RENTABILIDAD EN ALPACA RETAIL (COMISIÓN $0 + FEES REGULATORIOS + PFOF)")
    print("=" * 110)

    sims = [
        # Cuenta $2.000 (Fraccionarias - Alpaca Retail Standard)
        ("1. Señal Pura Teórica (Sin Fricción)", 2000.0, "fixed_0bps", True, False),
        ("2. Alpaca Retail $2k (Fees Regulatorios Solos - Midpoint)", 2000.0, "fixed_0bps", True, True),
        ("3. Alpaca Retail $2k (PFOF Tight: 0.25 bps + Reg Fees)", 2000.0, "fixed_0.25bps", True, True),
        ("4. Alpaca Retail $2k (PFOF Estándar: 0.50 bps + Reg Fees)", 2000.0, "fixed_0.5bps", True, True),
        ("5. Alpaca Retail $2k (PFOF Conservador: 1.00 bps + Reg Fees)", 2000.0, "fixed_1bps", True, True),
        ("6. Alpaca Retail $2k (NBBO Completo: ~1.5 bps + Reg Fees)", 2000.0, "nbbo", True, True),
        # Cuenta $2.000 (Enteras vs Fraccionarias)
        ("7. Alpaca Retail $2k (Enteras, PFOF 0.50 bps + Reg Fees)", 2000.0, "fixed_0.5bps", False, True),
        # Cuenta $25.000
        ("8. Alpaca Retail $25k (PFOF Estándar: 0.50 bps + Reg Fees)", 25000.0, "fixed_0.5bps", True, True),
    ]

    results = []
    for name, cap, spd_mode, frac, reg in sims:
        res = run_alpaca_simulation(
            daily_data=data,
            config_name=name,
            initial_capital=cap,
            half_spread_mode=spd_mode,
            use_fractional=frac,
            apply_reg_fees=reg,
        )
        results.append(res)

    print(f"\n{'Configuración':<48} | {'Retorno':<8} | {'Sharpe':<6} | {'MaxDD':<6} | {'SEC+TAF':<8} | {'PFOF Spd':<9} | {'Fric Tot':<9} | {'WinRate':<7} | {'PF':<5}")
    print("-" * 125)
    for r in results:
        print(
            f"{r.name:<48} | {r.total_return_pct:+7.2f}% | {r.sharpe_ratio:5.2f}  | {r.max_drawdown_pct:5.2f}% | "
            f"${r.total_regulatory_fees:6.2f}  | ${r.total_spread_cost:7.2f}  | ${r.total_friction_usd:7.2f}  | {r.win_rate_pct:5.1f}% | {r.profit_factor:4.2f}"
        )
    print("=" * 125)


if __name__ == "__main__":
    main()
