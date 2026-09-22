#!/usr/bin/env python3
"""Benchmark y Validación Oficial: Universo A (Trading-Bot v2.0).

Implementa estrictamente la especificación UNIVERSE_CONSTRUCTION_SPEC.md:
- 18 Instrumentos ETF (+ SPY como benchmark y señal de régimen):
  * 11 Sectores GICS: XLK, XLC, XLY, XLP, XLV, XLF, XLI, XLE, XLU, XLB, XLRE
  * 2 Internacionales: IEFA, IEMG
  * 2 Metales Físicos: GLDM, SLV
  * 2 Renta Fija / Defensivos: IEF, TIP
  * 1 Efectivo / T-Bills: BIL
- Arquitectura de Bloques y Presupuesto de Capital:
  * US Sectors: Cap 55% (modulado por regime_score), Top 4, cap por activo 20%
  * Internacional: Cap 15% (modulado por regime_score), Top 1, cap por activo 15%
  * Metales: Cap 20% (NO modulado por régimen de equity), Top 2 (GLDM 15%, SLV 8%)
  * Renta Fija: Cap 20% (NO modulado por régimen de equity), Top 1, cap por activo 20%
  * Efectivo: Residuo natural remunerado (BIL / T-Bills)
- Régimen Graduado:
  * SPY > SMA200 (1/3)
  * SPY > EMA50 (1/3)
  * Amplitud: >50% de los 11 sectores GICS > SMA200 (1/3)
  * regime_score in {0.0, 0.33, 0.67, 1.0}
- Momentum Multi-Horizonte:
  * Ventanas 21, 63 y 126 días, excluyendo las últimas 5 sesiones (skip 5d)
  * Promedio de rankings ordinales dentro del bloque
- Gate de Momentum Absoluto:
  * Retorno 126d > BIL 126d
  * Cierre > EMA50 del activo
- Ponderación y Control de Volatilidad:
  * Volatilidad inversa (1/sigma, 60d) dentro de cada bloque
  * Volatilidad objetivo del portafolio: target_vol = 12% anual
  * Piso de posición: $150
- Rebalanceo y Salida:
  * Cadencia semanal (viernes) con buffer asimétrico (Top 4 para entrar, Top 7 para salir)
  * Trailing Stop diario en EMA25
  * Tenencia máxima: 90 sesiones
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from tbot.indicators.pure import ema
from optimize_and_benchmark_portfolio import (
    SimulationResult,
    TradeRecord,
    load_all_market_data,
)

DATA_DIR = PROJECT_ROOT / "data" / "universe_a"

ALL_UNIVERSE_A_SYMBOLS = [
    "SPY",
    "XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE",
    "IEFA", "IEMG",
    "GLDM", "SLV",
    "IEF", "TIP", "BIL",
]

US_SECTORS = ["XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE"]


@dataclass
class BlockConfig:
    name: str
    tickers: list[str]
    capital_cap: float
    top_n: int
    buffer_rank: int
    per_instrument_cap: dict[str, float]
    modulate_by_regime: bool


BLOCK_DEFS = {
    "us_sectors": BlockConfig(
        name="Sectores EE.UU.",
        tickers=US_SECTORS,
        capital_cap=0.55,
        top_n=4,
        buffer_rank=7,
        per_instrument_cap={"default": 0.20},
        modulate_by_regime=True,
    ),
    "international": BlockConfig(
        name="Internacional",
        tickers=["IEFA", "IEMG"],
        capital_cap=0.15,
        top_n=1,
        buffer_rank=1,
        per_instrument_cap={"default": 0.15},
        modulate_by_regime=True,
    ),
    "metals": BlockConfig(
        name="Metales Físicos",
        tickers=["GLDM", "SLV"],
        capital_cap=0.20,
        top_n=2,
        buffer_rank=2,
        per_instrument_cap={"GLDM": 0.15, "SLV": 0.08, "default": 0.15},
        modulate_by_regime=False,
    ),
    "fixed_income": BlockConfig(
        name="Renta Fija / Defensivo",
        tickers=["IEF", "TIP"],
        capital_cap=0.20,
        top_n=1,
        buffer_rank=1,
        per_instrument_cap={"default": 0.20},
        modulate_by_regime=False,
    ),
}


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def calculate_graduated_regime(
    daily_data: dict[str, pd.DataFrame],
    cur_date: date,
) -> float:
    """Calcula el score de régimen graduado (0.0 a 1.0) usando SPY y amplitud sectorial GICS."""
    spy_df = daily_data["SPY"]
    past_spy = spy_df[spy_df["_parsed_date"] < cur_date]
    if len(past_spy) < 200:
        return 1.0  # Warm-up inicial defensivo

    closes_spy = past_spy["close"].astype(float)
    c_last = closes_spy.iloc[-1]

    sma200_spy = compute_sma(closes_spy, 200).iloc[-1]
    ema50_spy = ema(closes_spy, 50).iloc[-1]

    cond_sma200 = 1.0 if c_last > sma200_spy else 0.0
    cond_ema50 = 1.0 if c_last > ema50_spy else 0.0

    # Amplitud de mercado: % de los 11 sectores GICS > SMA(200)
    above_count = 0
    total_valid_sectors = 0
    for s in US_SECTORS:
        s_df = daily_data.get(s)
        if s_df is None:
            continue
        past_s = s_df[s_df["_parsed_date"] < cur_date]
        if len(past_s) >= 200:
            total_valid_sectors += 1
            s_closes = past_s["close"].astype(float)
            s_sma200 = compute_sma(s_closes, 200).iloc[-1]
            if s_closes.iloc[-1] > s_sma200:
                above_count += 1

    breadth_pct = (above_count / total_valid_sectors) if total_valid_sectors > 0 else 0.50
    cond_breadth = 1.0 if breadth_pct > 0.50 else 0.0

    score = (cond_sma200 + cond_ema50 + cond_breadth) / 3.0
    return score


def calculate_multi_horizon_momentum(
    daily_data: dict[str, pd.DataFrame],
    tickers: list[str],
    cur_date: date,
    skip_recent_days: int = 5,
) -> dict[str, float]:
    """Calcula el momentum multi-horizonte (21d, 63d, 126d) promediando rankings ordinales."""
    ret_21 = {}
    ret_63 = {}
    ret_126 = {}

    for sym in tickers:
        df = daily_data.get(sym)
        if df is None:
            continue
        past_bars = df[df["_parsed_date"] < cur_date]
        if len(past_bars) < 135:
            continue

        closes = past_bars["close"].astype(float)
        # Skip recent days
        p_ref = float(closes.iloc[-skip_recent_days])

        p_21 = float(closes.iloc[-(skip_recent_days + 21)])
        p_63 = float(closes.iloc[-(skip_recent_days + 63)])
        p_126 = float(closes.iloc[-(skip_recent_days + 126)])

        if p_21 > 0:
            ret_21[sym] = (p_ref - p_21) / p_21
        if p_63 > 0:
            ret_63[sym] = (p_ref - p_63) / p_63
        if p_126 > 0:
            ret_126[sym] = (p_ref - p_126) / p_126

    common_syms = set(ret_21.keys()) & set(ret_63.keys()) & set(ret_126.keys())
    if not common_syms:
        return {}

    # Rankings ordinales (1 = mejor)
    sorted_21 = sorted(common_syms, key=lambda s: ret_21[s], reverse=True)
    sorted_63 = sorted(common_syms, key=lambda s: ret_63[s], reverse=True)
    sorted_126 = sorted(common_syms, key=lambda s: ret_126[s], reverse=True)

    rank_21 = {s: i + 1 for i, s in enumerate(sorted_21)}
    rank_63 = {s: i + 1 for i, s in enumerate(sorted_63)}
    rank_126 = {s: i + 1 for i, s in enumerate(sorted_126)}

    avg_ranks = {
        s: (rank_21[s] + rank_63[s] + rank_126[s]) / 3.0
        for s in common_syms
    }
    return avg_ranks


def passes_absolute_gate(
    daily_data: dict[str, pd.DataFrame],
    sym: str,
    cur_date: date,
) -> bool:
    """Verifica el gate absoluto: retorno 126d > BIL 126d y cierre > EMA50."""
    df = daily_data.get(sym)
    bil_df = daily_data.get("BIL")
    if df is None or bil_df is None:
        return False

    past = df[df["_parsed_date"] < cur_date]
    past_bil = bil_df[bil_df["_parsed_date"] < cur_date]

    if len(past) < 130 or len(past_bil) < 130:
        return False

    closes = past["close"].astype(float)
    bil_closes = past_bil["close"].astype(float)

    c_now = closes.iloc[-1]
    c_126 = closes.iloc[-126]
    ret_126 = (c_now - c_126) / c_126

    bil_now = bil_closes.iloc[-1]
    bil_126 = bil_closes.iloc[-126]
    bil_ret_126 = (bil_now - bil_126) / bil_126

    if ret_126 <= bil_ret_126:
        return False

    ema50 = ema(closes, 50).iloc[-1]
    return c_now >= ema50


def run_universe_a_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str = "Universo A (v2.0)",
    initial_capital: float = 2000.0,
    target_portfolio_vol: float = 0.12,  # 12% anual
    min_position_usd: float = 150.0,
    trailing_ema_period: int = 25,
    max_holding_days: int = 90,
    annual_cash_yield: float = 0.045,
    integer_shares: bool = False,
    apply_retail_costs: bool = False,
) -> SimulationResult:
    """Ejecuta la simulación declarativa de Universo A invocando al motor unificado BacktestEngine."""
    from tbot.backtest.engine import BacktestConfig, BacktestEngine

    spy_df = daily_data.get("SPY", pd.DataFrame())
    if not spy_df.empty and "_parsed_date" not in spy_df.columns:
        spy_df["_parsed_date"] = pd.to_datetime(spy_df["date"]).dt.date

    # SPY return
    if not spy_df.empty:
        spy_past_start = spy_df[spy_df["_parsed_date"] >= start_date]
        spy_past_end = spy_df[spy_df["_parsed_date"] <= end_date]
        if not spy_past_start.empty and not spy_past_end.empty:
            spy_start = float(spy_past_start["open"].iloc[0])
            spy_end = float(spy_past_end["close"].iloc[-1])
            spy_ret = ((spy_end - spy_start) / spy_start) * 100.0
        else:
            spy_ret = 0.0
    else:
        spy_ret = 0.0

    config = BacktestConfig(
        blocks=BLOCK_DEFS,
        universe=ALL_UNIVERSE_A_SYMBOLS,
        initial_capital=Decimal(str(initial_capital)),
        target_portfolio_vol=target_portfolio_vol,
        min_position_usd=min_position_usd,
        trailing_ema_period=trailing_ema_period,
        max_holding_sessions=max_holding_days,
        annual_cash_yield_fallback=annual_cash_yield,
        rebalance_cadence="weekly_friday",
        integer_shares=integer_shares,
        apply_retail_costs=apply_retail_costs,
        single_position_cap=0.25,
        max_open_positions=4,
    )

    engine = BacktestEngine(config=config, historical_daily=daily_data)
    result = engine.run(start_date=start_date, end_date=end_date)

    trade_records = [
        TradeRecord(
            symbol=t.symbol,
            entry_date=t.entry_time.date() if isinstance(t.entry_time, datetime) else t.entry_time,
            exit_date=t.exit_time.date() if isinstance(t.exit_time, datetime) else t.exit_time,
            entry_price=float(t.entry_price),
            exit_price=float(t.exit_price),
            shares=float(t.qty),
            pnl_usd=float(t.pnl),
            pnl_pct=float(t.pnl_pct),
            pnl_r=float(t.pnl_r),
            exit_reason=t.exit_reason,
        )
        for t in result.trades
    ]

    final_cap = float(result.equity_curve.iloc[-1]) if not result.equity_curve.empty else initial_capital
    total_ret = ((final_cap - initial_capital) / initial_capital) * 100.0
    alpha = total_ret - spy_ret

    wins = [t for t in trade_records if t.pnl_usd > 0]
    win_rate = (len(wins) / len(trade_records) * 100.0) if trade_records else 0.0
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
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(float(result.metrics.profit_factor), 2),
        avg_holding_days=round(avg_days, 1),
        trades=trade_records,
        equity_curve=result.equity_curve,
    )


def main() -> int:
    print("=" * 105)
    print("        VALIDACIÓN INSTITUCIONAL: UNIVERSO A (18 ETFs DIVERSIFICADOS - v2.0)")
    print("   Cumplimiento de UNIVERSE_CONSTRUCTION_SPEC.md & Requisitos de Auditoría Externa")
    print("=" * 105)

    print("\nCargando datos históricos de Universo A (2018-2022 ventana de desarrollo)...")
    daily_data = load_all_market_data(DATA_DIR, symbols=ALL_UNIVERSE_A_SYMBOLS)
    print(f"[OK] Cargados {len(daily_data)} instrumentos.")

    periods = [
        {"name": "2020 (Crash COVID + Rebote)", "start": date(2020, 1, 2), "end": date(2020, 12, 31), "yield": 0.005},
        {"name": "2021 (Mercado Alcista)", "start": date(2021, 1, 4), "end": date(2021, 12, 31), "yield": 0.005},
        {"name": "2022 (Mercado Bajista Severo)", "start": date(2022, 1, 3), "end": date(2022, 12, 30), "yield": 0.025},
        {"name": "2020-2022 (3 Años Compuesto)", "start": date(2020, 1, 2), "end": date(2022, 12, 30), "yield": 0.015},
    ]

    all_summaries = []

    for p in periods:
        p_name = p["name"]
        print("\n" + "-" * 85)
        print(f" SIMULANDO: {p_name.upper()} ({p['start']} a {p['end']})")
        print("-" * 85)

        # SPY Benchmark
        spy_df = daily_data["SPY"]
        s_open = float(spy_df[spy_df["_parsed_date"] >= p["start"]]["open"].iloc[0])
        s_close = float(spy_df[spy_df["_parsed_date"] <= p["end"]]["close"].iloc[-1])
        spy_ret = ((s_close - s_open) / s_open) * 100.0

        res_v2 = run_universe_a_simulation(
            daily_data=daily_data,
            start_date=p["start"],
            end_date=p["end"],
            config_name=f"Universo A v2.0 ({p_name})",
            initial_capital=2000.0,
            target_portfolio_vol=0.12,
            min_position_usd=150.0,
            trailing_ema_period=25,
            max_holding_days=90,
            annual_cash_yield=p["yield"],
        )

        all_summaries.append({
            "period": p_name,
            "spy_ret": spy_ret,
            "v2": res_v2,
        })

        print(f" [BENCHMARK] S&P 500 (SPY):    {spy_ret:+8.2f}%")
        print(f" [UNIVERSO A] v2.0 ETFs (18):  {res_v2.total_return_pct:+8.2f}% | Alpha: {res_v2.total_return_pct - spy_ret:+7.2f}% | Sharpe: {res_v2.sharpe_ratio:4.2f} | MaxDD: {res_v2.max_drawdown_pct:5.2f}% | WinRate: {res_v2.win_rate_pct:4.1f}% | PF: {res_v2.profit_factor:4.2f} | Trades: {res_v2.total_trades}")

    # Tabla Final Consolidada
    print("\n" + "=" * 115)
    print("                    TABLA CONSOLIDADA: UNIVERSO A v2.0 vs S&P 500 (SPY)")
    print("=" * 115)
    print(f"{'Período':<32} | {'Retorno v2.0':<12} | {'SPY':<9} | {'Alpha SPY':<10} | {'Sharpe':<7} | {'MaxDD':<8} | {'WinRate':<7} | {'Trades':<6}")
    print("-" * 115)

    for item in all_summaries:
        p_name = item["period"]
        sp_r = item["spy_ret"]
        v2 = item["v2"]
        alp = v2.total_return_pct - sp_r
        print(f"{p_name:<32} | {v2.total_return_pct:+10.2f}% | {sp_r:+7.2f}% | {alp:+8.2f}% | {v2.sharpe_ratio:6.2f}  | {v2.max_drawdown_pct:6.2f}% | {v2.win_rate_pct:5.1f}% | {v2.total_trades:5d}")

    print("=" * 115)
    return 0


if __name__ == "__main__":
    sys.exit(main())
