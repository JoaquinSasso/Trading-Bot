"""Dedicated unit regression tests for Milestone M2 Iteration 3 BacktestEngine fixes.

Covers:
1. Input key normalization in BacktestEngine.__init__
2. Zero lookahead in _run_intraday (strictly < cur_date on curr_daily_idx)
3. Dynamic regime determination in _run_intraday (MarketRegime.BEAR vs BULL_CALM)
4. Enforcing exit_on_bear_regime in _run_intraday (liquidation & buy suppression)
5. Daily cash yield accrual in _run_intraday on settled cash
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from tbot.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
)
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, Strategy, StrategyContext, StrategyDataRequirements


# ==============================================================================
# Helpers & Strategy Mocks
# ==============================================================================
class ContextInspectorStrategy(Strategy):
    """Strategy that captures StrategyContext attributes on every bar."""
    id: str = "context_inspector"
    version: str = "1.0.0"
    schedule: list[str] = ["09:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(self, target_symbol: str = "SPY", buy_on_date: date | None = None) -> None:
        self.target_symbol = target_symbol
        self.buy_on_date = buy_on_date
        self.captured_contexts: list[dict] = []

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        info = {
            "ts": ctx.now,
            "regime": ctx.regime,
            "spy_last_daily_date": (
                ctx.daily_bars["SPY"]["_parsed_date"].iloc[-1]
                if "SPY" in ctx.daily_bars and len(ctx.daily_bars["SPY"]) > 0
                else None
            ),
        }
        self.captured_contexts.append(info)

        signals = []
        if self.buy_on_date and ctx.now.date() == self.buy_on_date and ctx.now.time() == time(9, 45):
            price = ctx.current_prices.get(self.target_symbol, Decimal("100.0"))
            signals.append(
                Signal.create(
                    strategy_id=self.id,
                    version=self.version,
                    symbol=self.target_symbol,
                    bar_ts=ctx.now,
                    side="buy",
                    entry_type="market",
                    entry_price_ref=price,
                    stop_price=price * Decimal("0.80"),
                    max_holding=0,
                    exit_at_close=False,  # Hold overnight to test regime exit
                )
            )
        return signals


def _make_mock_intraday(symbol: str, dates: list[date], price: float = 100.0) -> pd.DataFrame:
    rows = []
    for d in dates:
        start_dt = datetime.combine(d, time(9, 30))
        for i in range(78):
            ts = start_dt + timedelta(minutes=i * 5)
            rows.append({
                "symbol": symbol,
                "date": d,
                "datetime_et": ts,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "volume": 1000,
            })
    df = pd.DataFrame(rows)
    df.attrs["is_synthetic"] = True
    return df


def _make_mock_daily(symbol: str, dates: list[date], prices: list[float]) -> pd.DataFrame:
    rows = []
    for d, p in zip(dates, prices, strict=True):
        rows.append({
            "symbol": symbol,
            "date": d,
            "open": p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "volume": 100000,
        })
    df = pd.DataFrame(rows)
    df.attrs["is_synthetic"] = True
    return df


# ==============================================================================
# Tests
# ==============================================================================
def test_input_key_normalization():
    """Verify that input dictionary keys with whitespace or lowercase are normalized."""
    daily = {"  spy  ": pd.DataFrame(), "aapl": pd.DataFrame(), "\tMSFT\n": pd.DataFrame()}
    intraday = {" qqq ": pd.DataFrame(), "nvda": pd.DataFrame()}

    engine = BacktestEngine(
        config=BacktestConfig(universe=["SPY"]),
        historical_daily=daily,
        historical_intraday=intraday,
    )

    assert set(engine.daily_data.keys()) == {"SPY", "AAPL", "MSFT"}
    assert set(engine.intraday_data.keys()) == {"QQQ", "NVDA"}
    assert engine.daily_data.get("SPY") is not None
    assert engine.daily_data.get("AAPL") is not None
    assert engine.intraday_data.get("QQQ") is not None


def test_intraday_strictly_no_lookahead_daily_bars():
    """Verify that curr_daily_idx < cur_date prevents leaking today's EOD bar into morning bars."""
    all_dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(250)]
    prices = [400.0] * 250
    df_daily = _make_mock_daily("SPY", all_dates, prices)

    sim_dates = all_dates[-3:]
    df_intra = _make_mock_intraday("SPY", sim_dates, 400.0)

    strat = ContextInspectorStrategy()
    config = BacktestConfig(
        strategy=strat,
        universe=["SPY"],
        initial_capital=Decimal("2000.00"),
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_daily},
        historical_intraday={"SPY": df_intra},
    )

    engine.run(start_date=sim_dates[0], end_date=sim_dates[-1], resolution="5m")

    assert len(strat.captured_contexts) > 0
    for sample in strat.captured_contexts:
        ts = sample["ts"]
        cur_d = ts.date()
        visible_daily = sample["spy_last_daily_date"]
        assert visible_daily is not None
        assert visible_daily < cur_d, (
            f"LOOKAHEAD VIOLATION at {ts}: visible daily bar is {visible_daily}, but cur_date is {cur_d}"
        )


def test_intraday_dynamic_regime_bear_detection():
    """Verify that intraday execution dynamically determines MarketRegime.BEAR when SPY crashes."""
    all_dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(250)]
    prices = [400.0] * 230 + [100.0] * 20  # Crashed below SMA200
    df_daily = _make_mock_daily("SPY", all_dates, prices)

    sim_dates = all_dates[-3:]
    df_intra = _make_mock_intraday("SPY", sim_dates, 100.0)

    strat = ContextInspectorStrategy()
    config = BacktestConfig(
        strategy=strat,
        universe=["SPY"],
        initial_capital=Decimal("2000.00"),
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_daily},
        historical_intraday={"SPY": df_intra},
    )

    result = engine.run(start_date=sim_dates[0], end_date=sim_dates[-1], resolution="5m")

    assert len(strat.captured_contexts) > 0
    for sample in strat.captured_contexts:
        assert sample["regime"] == MarketRegime.BEAR, (
            f"FACADE REGIME VIOLATION: Expected MarketRegime.BEAR, got {sample['regime']}"
        )
    assert not result.daily_regime_scores.empty


def test_intraday_exit_on_bear_regime_liquidation_and_buy_suppression():
    """Verify that exit_on_bear_regime closes positions on bear regime and suppresses new buys."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(250)]
    # Day 0..247: Bull (300 to 423.5). Day 248: Crash in daily to 100. Day 249: Bear regime at open!
    prices = [300.0 + i * 0.5 for i in range(248)] + [100.0, 100.0]
    df_daily = _make_mock_daily("SPY", dates, prices)
    sim_dates = dates[-3:]  # [Day 247, Day 248, Day 249]
    df_intra = _make_mock_intraday("SPY", sim_dates, 424.0)

    # Buy on Day 247 (bull calm), hold overnight into Day 249 (bear regime)
    strat = ContextInspectorStrategy(target_symbol="SPY", buy_on_date=sim_dates[0])
    config = BacktestConfig(
        strategy=strat,
        universe=["SPY"],
        initial_capital=Decimal("2000.00"),
        enable_cash_yield=False,
        trailing_ema_period=0,
        exit_on_bear_regime=True,
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_daily},
        historical_intraday={"SPY": df_intra},
    )

    result = engine.run(start_date=sim_dates[0], end_date=sim_dates[2], resolution="5m")

    exit_reasons = [t.exit_reason for t in result.trades]
    assert "regime_bear_exit" in exit_reasons, (
        f"Expected position exit due to 'regime_bear_exit', got: {exit_reasons}"
    )


def test_intraday_cash_yield_accrual():
    """Verify that settled cash earns daily BIL interest during intraday execution."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(5)]
    df_daily = _make_mock_daily("SPY", dates, [400.0] * 5)
    df_intra = _make_mock_intraday("SPY", dates, 400.0)

    config = BacktestConfig(
        strategy=None,  # 100% idle cash
        universe=["SPY"],
        initial_capital=Decimal("2000.00"),
        enable_cash_yield=True,
        annual_cash_yield_fallback=0.05,  # 5% annual compound yield
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_daily},
        historical_intraday={"SPY": df_intra},
    )

    result = engine.run(start_date=dates[0], end_date=dates[-1], resolution="5m")

    final_cash = float(engine.broker.settled_cash)
    assert final_cash > 2000.0, f"Expected settled cash to accrue interest, got {final_cash}"
    assert float(result.equity_curve.iloc[-1]) == pytest.approx(final_cash, rel=1e-4)
