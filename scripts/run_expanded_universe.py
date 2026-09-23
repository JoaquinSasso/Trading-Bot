import sys
import time
from pathlib import Path
from decimal import Decimal
from datetime import date
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'backend'))

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy

DAILY_2010_DIR = PROJECT_ROOT / 'data' / 'historical_2010_2026'

print("Leyendo universo...", flush=True)
csv_files = list(DAILY_2010_DIR.glob('*_daily.csv'))
UNIVERSE = [f.stem.replace('_daily', '') for f in csv_files]
UNIVERSE.remove('SPY') # We keep SPY for regime filter, but wait, regime filter expects SPY in the historical data dict. 
# We can leave SPY in the universe so it's loaded. But S5 might buy SPY if it has high momentum (which is fine).
# Actually, the user's refuge asset is TLT or IEF, we don't want S5 to buy them as structural momentum.
# S5 target_universe is passed in __init__.
EXCLUDE = ['SPY', 'BIL', 'SGOV', 'TLT', 'IEF']
TRADE_UNIVERSE = [sym for sym in UNIVERSE if sym not in EXCLUDE]

print(f"Universo tradeable: {len(TRADE_UNIVERSE)} activos")

loader = HistoricalDataLoader(DAILY_2010_DIR)
daily_data = {}
for sym in UNIVERSE:
    csv_path = DAILY_2010_DIR / f'{sym}_daily.csv'
    if csv_path.exists():
        daily_data[sym] = loader.load_from_csv(csv_path, symbol=sym, resolution='daily')

DAILY_START = date(2011,1,3)
DAILY_END = date(2022,12,31)

def run_s5(name, universe, refuge, max_pos, initial_cap=10000.0):
    t0 = time.time()
    cfg = BacktestConfig(
        strategy=DualMomentumLeaderStrategy(universe=universe, refuge_asset=refuge),
        universe=universe,
        initial_capital=Decimal(str(initial_cap)),
        max_open_positions=max_pos,
        single_position_cap=1.0/max_pos,
        risk_per_trade_pct=0.5,
        enable_circuit_breakers=True,
        enable_cash_yield=True,
        integer_shares=False
    )
    engine = BacktestEngine(config=cfg, historical_daily=daily_data, historical_intraday={})
    res = engine.run(start_date=DAILY_START, end_date=DAILY_END, resolution='daily')
    ret = (float(res.equity_curve.iloc[-1]) / initial_cap) - 1.0
    print(f"{name}: {ret*100:.2f}% (Tomo {time.time()-t0:.1f}s)", flush=True)

print("Iniciando pruebas comparativas...\n")
run_s5("S5 (Universo Original 14, Refugio Efectivo, Top 4)", ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'JPM', 'LLY', 'XOM', 'COST', 'GLD', 'SLV'], None, 4)
run_s5("S5 (Universo Expandido 100, Refugio Efectivo, Top 4)", TRADE_UNIVERSE, None, 4)
run_s5("S5 (Universo Expandido 100, Refugio TLT, Top 4)", TRADE_UNIVERSE, "TLT", 4)
