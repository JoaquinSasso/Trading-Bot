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
from tbot.strategies.s9_turn_of_month_momentum import TurnOfMonthMomentumStrategy
from tbot.strategies.s11_volatility_squeeze import VolatilitySqueezeStrategy

UNIVERSE_14 = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'JPM', 'LLY', 'XOM', 'COST', 'GLD', 'SLV']
DAILY_2010_DIR = PROJECT_ROOT / 'data' / 'historical_2010_2026'

print("Loading data...", flush=True)
loader = HistoricalDataLoader(DAILY_2010_DIR)
daily_data = {}
for sym in UNIVERSE_14:
    csv_path = DAILY_2010_DIR / f'{sym}_daily.csv'
    if csv_path.exists():
        daily_data[sym] = loader.load_from_csv(csv_path, symbol=sym, resolution='daily')

DAILY_START = date(2011,1,3)
DAILY_END = date(2022,12,31)

def run_strat(strategy, capital):
    cfg = BacktestConfig(
        strategy=strategy,
        universe=UNIVERSE_14,
        initial_capital=Decimal(str(capital)),
        max_open_positions=4,
        single_position_cap=0.25,
        risk_per_trade_pct=0.5,
        enable_circuit_breakers=True,
        enable_cash_yield=True,
        integer_shares=False
    )
    engine = BacktestEngine(config=cfg, historical_daily=daily_data, historical_intraday={})
    return engine.run(start_date=DAILY_START, end_date=DAILY_END, resolution='daily').equity_curve

t0 = time.time()
print('Corriendo S5 (80% = $8000)...', flush=True)
eq_s5 = run_strat(DualMomentumLeaderStrategy(), 8000.0)

print(f'Corriendo S9 (20% = $2000)... [S5 tomo {time.time()-t0:.1f}s]', flush=True)
t1 = time.time()
eq_s9 = run_strat(TurnOfMonthMomentumStrategy(), 2000.0)

print(f'Corriendo S11 (20% = $2000)... [S9 tomo {time.time()-t1:.1f}s]', flush=True)
t2 = time.time()
eq_s11 = run_strat(VolatilitySqueezeStrategy(), 2000.0)

print(f'Calculando... [S11 tomo {time.time()-t2:.1f}s]', flush=True)
eq_s5_s9 = eq_s5 + eq_s9
eq_s5_s11 = eq_s5 + eq_s11

ret_s5_s9 = (float(eq_s5_s9.iloc[-1]) / 10000.0) - 1.0
ret_s5_s11 = (float(eq_s5_s11.iloc[-1]) / 10000.0) - 1.0
ret_s5_only = (float(eq_s5.iloc[-1]) / 8000.0) - 1.0
ret_s9_only = (float(eq_s9.iloc[-1]) / 2000.0) - 1.0
ret_s11_only = (float(eq_s11.iloc[-1]) / 2000.0) - 1.0

print(f'\nRESULTADOS FINALES (Base $10,000):')
print(f'S5 Pura (80%): {ret_s5_only*100:.2f}%')
print(f'S9 Pura (20%): {ret_s9_only*100:.2f}%')
print(f'S11 Pura (20%): {ret_s11_only*100:.2f}%')
print(f'Retorno Silo S5(80%) + S9(20%): {ret_s5_s9*100:.2f}%')
print(f'Retorno Silo S5(80%) + S11(20%): {ret_s5_s11*100:.2f}%')
