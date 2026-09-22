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
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "intraday_5m"
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from decimal import Decimal

from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.backtest.guards import SAMPLE_5M_END, SAMPLE_5M_START
from tbot.strategies.s6_intraday_5m_multi_horizon import (
    Intraday5mMultiHorizonStrategy,
)

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
    """Ejecuta la simulación minorista de Alpaca invocando BacktestEngine."""
    half_spread_bps_map = {
        "fixed_0bps": 0.0,
        "zero": 0.0,
        "fixed_0.25bps": 0.25,
        "fixed_0.5bps": 0.50,
        "fixed_1bps": 1.00,
        "nbbo": 1.50,
    }
    h_spd_bps = half_spread_bps_map.get(half_spread_mode, 0.50)

    strat = Intraday5mMultiHorizonStrategy(
        top_n=top_n,
        trailing_ema_period=trailing_ema_period,
        trend_ema_period=trend_ema_period,
        initial_stop_pct=initial_stop_pct,
        max_holding_bars=max_holding_bars,
    )
    config = BacktestConfig(
        strategy=strat,
        universe=INTRADAY_UNIVERSE,
        initial_capital=Decimal(str(initial_capital)),
        account_type="cash",
        max_open_positions=top_n,
        single_position_cap=max_weight_per_asset,
        integer_shares=not use_fractional,
        apply_retail_costs=apply_reg_fees,
        etf_half_spread_bps=h_spd_bps,
        stock_half_spread_bps=h_spd_bps,
        enable_circuit_breakers=True,
    )
    engine = BacktestEngine(config=config, historical_intraday=daily_data)
    res = engine.run(start_date=SAMPLE_5M_START, end_date=SAMPLE_5M_END, resolution="5m")

    tot_sec = sum(float(t.fees * Decimal("0.33")) for t in res.trades)
    tot_taf = sum(float(t.fees * Decimal("0.66")) for t in res.trades)
    tot_cat = sum(float(t.fees * Decimal("0.01")) for t in res.trades)
    tot_reg = sum(float(t.fees) for t in res.trades)
    tot_spread = sum(float(t.slippage_cost) for t in res.trades)
    tot_fric = tot_reg + tot_spread

    eq_s = res.equity_curve
    final_cap = float(eq_s.iloc[-1]) if not eq_s.empty else initial_capital
    fric_drag = (tot_fric / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    return AlpacaSimResult(
        name=config_name,
        initial_capital=initial_capital,
        final_capital=final_cap,
        total_return_pct=float(res.metrics.total_return_pct),
        sharpe_ratio=float(res.metrics.sharpe_ratio or 0.0),
        max_drawdown_pct=float(res.metrics.max_drawdown_pct),
        total_trades=len(res.trades),
        win_rate_pct=float(res.metrics.win_rate_pct),
        profit_factor=float(res.metrics.profit_factor),
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
