"""Script de evaluación cuantitativa del impacto de añadir Commodities al bot.

Evalúa:
1. Oro: GLD (o proxy GLDM)
2. Plata: SLV
3. Petróleo: USO (ETF de futuros de crudo)
4. Gas Natural: UNG (ETF de futuros de gas natural)
5. Sector Energía Productores: XLE (Energy Select SPDR)
6. Canasta General de Commodities: DBC / PDBC

Compara:
- Retorno anual 2025
- Correlación frente a SPY y QQQ
- Desgaste por Contango / Roll Yield (USO, UNG)
- Rendimiento del portafolio con la estrategia S5 v1.1.0
"""

import sys
from datetime import date, datetime
from pathlib import Path
import numpy as np
import pandas as pd
import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from tbot.backtest.data_loader import HistoricalDataLoader
from scripts.optimize_and_benchmark_portfolio import (
    load_all_market_data,
    run_s5_momentum_simulation,
    compute_sharpe,
    compute_drawdown,
)

DATA_DIR = PROJECT_ROOT / "data" / "historical"
EASTERN_TZ = pytz.timezone("America/New_York")


def download_commodity_data():
    loader = HistoricalDataLoader(data_dir=DATA_DIR)
    commodity_symbols = ["GLD", "SLV", "USO", "UNG", "XLE", "PDBC"]
    print("Descargando / verificando datos de commodities...")
    for sym in commodity_symbols:
        csv_file = DATA_DIR / f"{sym}_daily.csv"
        if not csv_file.exists():
            print(f" -> Descargando {sym}...")
            try:
                loader.fetch_and_save_real_data(sym)
                print(f"    [OK] {sym} guardado.")
            except Exception as e:
                print(f"    [ERROR] No se pudo descargar {sym}: {e}")
        else:
            print(f" -> {sym} ya existe localmente.")


def analyze_asset_characteristics(daily_data: dict[str, pd.DataFrame], start_date: date, end_date: date):
    print("\n" + "=" * 80)
    print(" 1. CARACTERÍSTICAS INDIVIDUALES DE COMMODITIES VS SPY (AÑO 2025)")
    print("=" * 80)
    
    spy_df = daily_data["SPY"]
    spy_p = spy_df[(spy_df["_parsed_date"] >= start_date) & (spy_df["_parsed_date"] <= end_date)].set_index("_parsed_date")["close"].astype(float)
    spy_rets = spy_p.pct_change().dropna()
    
    rows = []
    for sym, df in daily_data.items():
        sub = df[(df["_parsed_date"] >= start_date) & (df["_parsed_date"] <= end_date)].set_index("_parsed_date")["close"].astype(float)
        if len(sub) < 50:
            continue
        p_open = sub.iloc[0]
        p_close = sub.iloc[-1]
        tot_ret = ((p_close - p_open) / p_open) * 100.0
        daily_rets = sub.pct_change().dropna()
        vol_ann = float(daily_rets.std() * np.sqrt(252)) * 100.0
        
        # Correlación con SPY
        common_idx = daily_rets.index.intersection(spy_rets.index)
        corr_spy = float(daily_rets.loc[common_idx].corr(spy_rets.loc[common_idx])) if len(common_idx) > 20 else 0.0
        
        # Max Drawdown
        peaks = sub.cummax()
        dd = (sub - peaks) / peaks
        max_dd = abs(float(dd.min())) * 100.0
        
        rows.append({
            "symbol": sym,
            "return": tot_ret,
            "volatility": vol_ann,
            "max_dd": max_dd,
            "corr_spy": corr_spy,
        })
        
    res_df = pd.DataFrame(rows).sort_values("return", ascending=False)
    print(f"{'Símbolo':<8} | {'Retorno 2025':<14} | {'Volatilidad Anual':<18} | {'Max Drawdown':<14} | {'Correlación SPY':<15}")
    print("-" * 80)
    for _, r in res_df.iterrows():
        print(f"{r['symbol']:<8} | {r['return']:+12.2f}% | {r['volatility']:16.2f}% | {r['max_dd']:12.2f}% | {r['corr_spy']:13.2f}")
    print("=" * 80)


def run_portfolio_commodity_experiments(daily_data: dict[str, pd.DataFrame], start_date: date, end_date: date):
    print("\n" + "=" * 85)
    print(" 2. SIMULACIÓN DE PORTAFOLIO S5 v1.1.0 CON Y SIN COMMODITIES (2025)")
    print("=" * 85)
    
    base_12 = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST"]
    
    experiments = [
        {
            "name": "Exp 0: Baseline Multi-Sectorial 12 Activos (Sin Commodities extras)",
            "symbols": base_12,
        },
        {
            "name": "Exp 1: Base 12 + Oro (GLD)",
            "symbols": base_12 + ["GLD"],
        },
        {
            "name": "Exp 2: Base 12 + Metales Preciosos (GLD + SLV)",
            "symbols": base_12 + ["GLD", "SLV"],
        },
        {
            "name": "Exp 3: Base 12 + Futuros de Petróleo (USO)",
            "symbols": base_12 + ["USO"],
        },
        {
            "name": "Exp 4: Base 12 + Futuros de Gas Natural (UNG)",
            "symbols": base_12 + ["UNG"],
        },
        {
            "name": "Exp 5: Base 12 + Sector Energía Equities (XLE)",
            "symbols": base_12 + ["XLE"],
        },
        {
            "name": "Exp 6: Base 12 + Canasta Commodities (PDBC)",
            "symbols": [s for s in base_12 + ["PDBC"] if s in daily_data],
        },
        {
            "name": "Exp 7: Base 12 + Todos los Commodities (GLD, SLV, USO, UNG, XLE)",
            "symbols": [s for s in base_12 + ["GLD", "SLV", "USO", "UNG", "XLE"] if s in daily_data],
        },
    ]
    
    results = []
    for exp in experiments:
        avail_symbols = [s for s in exp["symbols"] if s in daily_data]
        res = run_s5_momentum_simulation(
            daily_data=daily_data,
            start_date=start_date,
            end_date=end_date,
            config_name=exp["name"],
            initial_capital=2000.0,
            top_n_leaders=2,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            use_finbert=False,  # Evaluamos puro comportamiento cuantitativo del activo
            symbols=avail_symbols,
            annual_cash_yield=0.045,
        )
        
        # Conteo de operaciones por símbolo
        sym_trade_counts = {}
        for t in res.trades:
            sym_trade_counts[t.symbol] = sym_trade_counts.get(t.symbol, 0) + 1
            
        results.append((res, sym_trade_counts))
        print(f"\n{res.config_name}:")
        print(f" -> Retorno: {res.total_return_pct:+6.2f}% | Sharpe: {res.sharpe_ratio:4.2f} | MaxDD: {res.max_drawdown_pct:4.2f}% | WinRate: {res.win_rate_pct:4.1f}% | PF: {res.profit_factor:4.2f} | Trades: {res.total_trades}")
        comm_trades = {s: c for s, c in sym_trade_counts.items() if s not in base_12}
        if comm_trades:
            print(f"    Trades ejecutados en commodities: {comm_trades}")
        else:
            print(f"    Trades ejecutados en commodities: Ninguno (no superaron el ranking de momentum)")

    print("\n" + "=" * 95)
    print("                      TABLA COMPARATIVA FINAL DE COMMODITIES EN S5")
    print("=" * 95)
    print(f"{'Configuración':<52} | {'Retorno':<9} | {'Sharpe':<7} | {'MaxDD':<7} | {'WinRate':<7} | {'PF':<5}")
    print("-" * 95)
    for res, _ in results:
        print(f"{res.config_name:<52} | {res.total_return_pct:+8.2f}% | {res.sharpe_ratio:<7.2f} | {res.max_drawdown_pct:<6.2f}% | {res.win_rate_pct:<6.1f}% | {res.profit_factor:<5.2f}")
    print("=" * 95)


def main():
    download_commodity_data()
    all_syms = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV", "USO", "UNG", "XLE", "PDBC"]
    daily_data = load_all_market_data(DATA_DIR, symbols=all_syms)
    start_date = date(2025, 1, 2)
    end_date = date(2025, 12, 31)
    
    analyze_asset_characteristics(daily_data, start_date, end_date)
    run_portfolio_commodity_experiments(daily_data, start_date, end_date)


if __name__ == "__main__":
    main()
