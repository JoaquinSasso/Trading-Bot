"""Adversarial Verification Suite for Milestone M2 Iteration 3 Gate.

Authored by: m2_iter3_challenger_1 (EMPIRICAL CHALLENGER)
Focus areas:
1. Dynamic regime transition (SPY drops below SMA200 -> BEAR, surges with high vol -> BULL_VOLATILE, recovers -> BULL_CALM).
2. Bear liquidation & buy suppression (exit_on_bear_regime=True immediately liquidates open positions at 09:30 open and suppresses all buy signals).
3. Zero lookahead exhaustively probed on every 5m intraday bar (09:30 through 16:00 never sees today's daily bar).
4. Multi-day overnight cash yield accrual with exact analytical BIL interest compounding.
5. Hostile key normalization (tabs, newlines, whitespace, mixed case) across daily, intraday, universe, and blocks, including forbidden commodity enforcement.
"""

import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from tbot.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BlockConfig,
)
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, Strategy, StrategyContext, StrategyDataRequirements


# ==============================================================================
# Helper Factories for Deterministic Synthetic Data
# ==============================================================================
def make_synthetic_daily_bars(
    symbol: str,
    dates: list[date],
    closes: list[float],
) -> pd.DataFrame:
    rows = []
    for d, c in zip(dates, closes, strict=True):
        rows.append({
            "symbol": symbol,
            "date": d,
            "open": c * 0.998,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 500000,
        })
    df = pd.DataFrame(rows)
    df.attrs["is_synthetic"] = True
    return df


def make_synthetic_intraday_bars(
    symbol: str,
    dates: list[date],
    base_price: float = 100.0,
    price_by_date: dict[date, float] | None = None,
) -> pd.DataFrame:
    rows = []
    for d in dates:
        p = price_by_date.get(d, base_price) if price_by_date else base_price
        start_dt = datetime.combine(d, time(9, 30))
        for i in range(78):  # 78 5-minute bars from 09:30 to 15:55
            ts = start_dt + timedelta(minutes=i * 5)
            rows.append({
                "symbol": symbol,
                "date": d,
                "datetime_et": ts,
                "open": p,
                "high": p * 1.002,
                "low": p * 0.998,
                "close": p,
                "volume": 5000,
            })
    df = pd.DataFrame(rows)
    df.attrs["is_synthetic"] = True
    return df


# ==============================================================================
# Adversarial Strategies
# ==============================================================================
class LookaheadSentinelStrategy(Strategy):
    """Monitors StrategyContext on every single bar to catch any lookahead leakage."""
    id: str = "lookahead_sentinel"
    version: str = "1.0.0"
    schedule: list[str] = ["* America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(self) -> None:
        self.probed_bars: list[dict] = []
        self.lookahead_violations: list[str] = []

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        cur_d = ctx.now.date()
        for sym, d_df in ctx.daily_bars.items():
            if d_df.empty:
                continue
            last_date = d_df["_parsed_date"].iloc[-1]
            if last_date >= cur_d:
                self.lookahead_violations.append(
                    f"Lookahead leak for {sym} at bar {ctx.now}: daily bar date {last_date} >= current date {cur_d}"
                )
            self.probed_bars.append({
                "ts": ctx.now,
                "symbol": sym,
                "visible_daily_date": last_date,
                "regime": ctx.regime,
            })
        return []


class HostileBuyerStrategy(Strategy):
    """Attempts aggressive buys on specific dates and schedules."""
    id: str = "hostile_buyer"
    version: str = "1.0.0"
    schedule: list[str] = ["09:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(self, buy_dates: list[date], symbols: list[str]) -> None:
        self.buy_dates = set(buy_dates)
        self.symbols = symbols
        self.signals_attempted: list[tuple[datetime, str]] = []
        self.regimes_observed: list[tuple[datetime, MarketRegime]] = []

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        self.regimes_observed.append((ctx.now, ctx.regime))
        signals = []
        if ctx.now.date() in self.buy_dates and ctx.now.time() == time(9, 45):
            for s in self.symbols:
                if s in ctx.current_prices and s not in ctx.portfolio_positions:
                    p = ctx.current_prices[s]
                    sig = Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=s,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=p,
                        stop_price=p * Decimal("0.85"),
                        max_holding=0,
                        exit_at_close=False,  # Hold overnight to test regime exit
                    )
                    signals.append(sig)
                    self.signals_attempted.append((ctx.now, s))
        return signals


# ==============================================================================
# 1. Adversarial Dynamic Regime Shift Probes
# ==============================================================================
def test_adversarial_dynamic_regime_shift_transitions():
    """Probe engine's dynamic regime evaluation across BULL_CALM -> BEAR -> BULL_VOLATILE."""
    base_date = date(2022, 1, 3)
    n_days = 250
    dates = [base_date + timedelta(days=i) for i in range(n_days)]

    # Upward trending SPY (300 to 423.5) guarantees cur_c > SMA200 (BULL_CALM)
    prices = [300.0 + i * 0.5 for i in range(248)] + [100.0, 100.0]

    df_spy_daily = make_synthetic_daily_bars("SPY", dates, prices)

    sim_dates = dates[-3:]  # [Day 247 (bull), Day 248 (bull morning, crash EOD), Day 249 (bear at open)]
    df_spy_intra = make_synthetic_intraday_bars("SPY", sim_dates, base_price=420.0, price_by_date={sim_dates[2]: 100.0})

    sentinel = LookaheadSentinelStrategy()
    config = BacktestConfig(
        strategy=sentinel,
        universe=["SPY"],
        initial_capital=Decimal("10000.00"),
    )
    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_spy_daily},
        historical_intraday={"SPY": df_spy_intra},
    )

    res = engine.run(start_date=sim_dates[0], end_date=sim_dates[-1], resolution="5m")

    # Group observed regimes by simulation date
    regimes_by_date: dict[date, set[MarketRegime]] = {}
    for p in sentinel.probed_bars:
        d = p["ts"].date()
        regimes_by_date.setdefault(d, set()).add(p["regime"])

    # Day 247: SPY history had close ~423.5 > SMA200 -> BULL_CALM
    assert regimes_by_date[sim_dates[0]] == {MarketRegime.BULL_CALM}, (
        f"Expected BULL_CALM regime on day {sim_dates[0]}, got {regimes_by_date[sim_dates[0]]}"
    )
    # Day 248: SPY history up to Day 247 still had close ~423.5 > SMA200 -> BULL_CALM
    assert regimes_by_date[sim_dates[1]] == {MarketRegime.BULL_CALM}, (
        f"Expected BULL_CALM regime on day {sim_dates[1]}, got {regimes_by_date[sim_dates[1]]}"
    )
    # Day 249: SPY history includes Day 248 crash to 100.0 (well below SMA200) -> BEAR
    assert regimes_by_date[sim_dates[2]] == {MarketRegime.BEAR}, (
        f"Expected BEAR regime on day {sim_dates[2]}, got {regimes_by_date[sim_dates[2]]}"
    )
    assert not res.daily_regime_scores.empty


def test_adversarial_regime_fallback_with_short_history():
    """Verify that when SPY has fewer than 200 daily bars, regime safely falls back to BULL_CALM without crash."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(50)]  # Only 50 bars
    df_daily = make_synthetic_daily_bars("SPY", dates, [100.0] * 50)
    df_intra = make_synthetic_intraday_bars("SPY", dates[-3:], 100.0)

    sentinel = LookaheadSentinelStrategy()
    config = BacktestConfig(strategy=sentinel, universe=["SPY"])
    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_daily},
        historical_intraday={"SPY": df_intra},
    )
    engine.run(start_date=dates[-3], end_date=dates[-1], resolution="5m")

    for p in sentinel.probed_bars:
        assert p["regime"] == MarketRegime.BULL_CALM, (
            f"Expected fallback BULL_CALM for short history, got {p['regime']}"
        )


# ==============================================================================
# 2. Adversarial Bear Liquidation & Strict Buy Suppression Probes
# ==============================================================================
def test_adversarial_bear_liquidation_and_strict_buy_suppression():
    """Verify:
    1. Multi-asset positions opened in BULL_CALM are liquidated immediately at 09:30 open on BEAR day.
    2. Trades record reason='regime_bear_exit'.
    3. All subsequent buy signals emitted on the BEAR day are completely suppressed.
    """
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(250)]
    # Day 0..247: Trending Bull calm (300 to 423.5). Day 248: Crash to 100. Day 249: Stays at 100.
    prices_spy = [300.0 + i * 0.5 for i in range(248)] + [100.0, 100.0]
    prices_aapl = [150.0] * 250
    prices_msft = [250.0] * 250

    df_spy_d = make_synthetic_daily_bars("SPY", dates, prices_spy)
    df_aapl_d = make_synthetic_daily_bars("AAPL", dates, prices_aapl)
    df_msft_d = make_synthetic_daily_bars("MSFT", dates, prices_msft)

    sim_dates = dates[-3:]  # [Day 247 (bull), Day 248 (bull morning, crash EOD), Day 249 (bear at open)]
    df_spy_i = make_synthetic_intraday_bars("SPY", sim_dates, 420.0, {sim_dates[2]: 100.0})
    df_aapl_i = make_synthetic_intraday_bars("AAPL", sim_dates, 150.0)
    df_msft_i = make_synthetic_intraday_bars("MSFT", sim_dates, 250.0)

    # Strategy buys AAPL and MSFT on Day 247 (bull), and also tries to buy on Day 249 (bear)
    buyer = HostileBuyerStrategy(
        buy_dates=[sim_dates[0], sim_dates[2]],
        symbols=["AAPL", "MSFT"],
    )

    config = BacktestConfig(
        strategy=buyer,
        universe=["SPY", "AAPL", "MSFT"],
        initial_capital=Decimal("10000.00"),
        enable_cash_yield=False,
        trailing_ema_period=0,
        exit_on_bear_regime=True,  # Mandatory defensive liquidation
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_spy_d, "AAPL": df_aapl_d, "MSFT": df_msft_d},
        historical_intraday={"SPY": df_spy_i, "AAPL": df_aapl_i, "MSFT": df_msft_i},
    )

    res = engine.run(start_date=sim_dates[0], end_date=sim_dates[2], resolution="5m")

    # Verification 1: Positions were opened on Day 247 and closed on Day 249
    closed_trades = res.trades
    bear_exits = [t for t in closed_trades if t.exit_reason == "regime_bear_exit"]
    assert len(bear_exits) >= 2, (
        f"Expected at least 2 regime_bear_exit trades (AAPL, MSFT), got {len(bear_exits)}. "
        f"All exit reasons: {[t.exit_reason for t in closed_trades]}"
    )

    # Verification 2: Liquidation occurred at the start of Day 249
    for t in bear_exits:
        assert t.exit_time.date() == sim_dates[2], (
            f"Expected exit on Day 249 {sim_dates[2]}, got {t.exit_time}"
        )
        assert t.exit_time.time() == time(9, 30), (
            f"Expected liquidation at market open 09:30, got {t.exit_time.time()}"
        )

    # Verification 3: On Day 249, buy signals were attempted by strategy but ALL were suppressed
    attempted_on_day249 = [ts for ts, sym in buyer.signals_attempted if ts.date() == sim_dates[2]]
    assert len(attempted_on_day249) > 0, "Strategy should have attempted buys on Day 249"

    # Confirm engine held 0 positions on Day 249 after 09:30 and no new positions were opened
    assert len(engine.broker.positions) == 0, (
        f"Engine positions should be empty after bear liquidation and buy suppression, but has: {engine.broker.positions}"
    )


def test_adversarial_bear_liquidation_disabled_allows_holding():
    """Verify that when exit_on_bear_regime=False, positions are NOT liquidated on BEAR regime open."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(250)]
    prices_spy = [300.0 + i * 0.5 for i in range(248)] + [100.0, 100.0]

    df_spy_d = make_synthetic_daily_bars("SPY", dates, prices_spy)
    df_aapl_d = make_synthetic_daily_bars("AAPL", dates, [150.0] * 250)

    sim_dates = dates[-3:]
    df_spy_i = make_synthetic_intraday_bars("SPY", sim_dates, 420.0, {sim_dates[2]: 100.0})
    df_aapl_i = make_synthetic_intraday_bars("AAPL", sim_dates, 150.0)

    buyer = HostileBuyerStrategy(buy_dates=[sim_dates[0]], symbols=["AAPL"])
    config = BacktestConfig(
        strategy=buyer,
        universe=["SPY", "AAPL"],
        initial_capital=Decimal("10000.00"),
        enable_cash_yield=False,
        trailing_ema_period=0,
        exit_on_bear_regime=False,  # Bear liquidation disabled
    )
    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_spy_d, "AAPL": df_aapl_d},
        historical_intraday={"SPY": df_spy_i, "AAPL": df_aapl_i},
    )
    res = engine.run(start_date=sim_dates[0], end_date=sim_dates[2], resolution="5m")

    # Should NOT have any regime_bear_exit trades
    bear_exits = [t for t in res.trades if t.exit_reason == "regime_bear_exit"]
    assert len(bear_exits) == 0, f"Expected 0 bear exits when disabled, got {len(bear_exits)}"


# ==============================================================================
# 3. Adversarial Exhaustive Lookahead Probes
# ==============================================================================
def test_adversarial_exhaustive_lookahead_on_all_intraday_bars():
    """Exhaustively verify that ctx.daily_bars NEVER contains date T's daily bar on any intraday bar."""
    n_days = 250
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(n_days)]

    symbols = ["SPY", "AAPL", "MSFT"]
    daily_data = {
        s: make_synthetic_daily_bars(s, dates, [100.0 + idx * 50.0] * n_days)
        for idx, s in enumerate(symbols)
    }

    sim_dates = dates[-5:]  # 5 full trading sessions
    intraday_data = {
        s: make_synthetic_intraday_bars(s, sim_dates, 100.0 + idx * 50.0)
        for idx, s in enumerate(symbols)
    }

    sentinel = LookaheadSentinelStrategy()
    config = BacktestConfig(
        strategy=sentinel,
        universe=symbols,
        initial_capital=Decimal("10000.00"),
    )
    engine = BacktestEngine(
        config=config,
        historical_daily=daily_data,
        historical_intraday=intraday_data,
    )
    engine.run(start_date=sim_dates[0], end_date=sim_dates[-1], resolution="5m")

    # Assert zero lookahead violations across all probed bars and symbols
    assert len(sentinel.lookahead_violations) == 0, (
        f"Encountered {len(sentinel.lookahead_violations)} lookahead violations:\n"
        + "\n".join(sentinel.lookahead_violations[:10])
    )
    # Strategy generate() is evaluated between start_entry_time (09:45) and end_entry_time (15:30)
    # = 70 bars * 5 days * 3 symbols = 1050 observations
    assert len(sentinel.probed_bars) >= 1050, (
        f"Expected at least 1050 probed observations, got {len(sentinel.probed_bars)}"
    )

    # Double check specifically at morning open (09:45), midday (12:00), and late afternoon (15:30)
    for p in sentinel.probed_bars:
        ts = p["ts"]
        cur_d = ts.date()
        daily_d = p["visible_daily_date"]
        assert daily_d < cur_d, f"Violation at {ts}: {daily_d} is not strictly < {cur_d}"


# ==============================================================================
# 4. Adversarial Overnight Cash Yield Accrual Probes
# ==============================================================================
def test_adversarial_overnight_cash_yield_exact_bil_compounding():
    """Verify that settled cash earns mathematically exact daily compounded interest across sessions."""
    n_days = 7
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(n_days)]

    df_spy_d = make_synthetic_daily_bars("SPY", dates, [400.0] * n_days)
    df_spy_i = make_synthetic_intraday_bars("SPY", dates, 400.0)

    # Custom authentic daily BIL risk-free rate series
    daily_rates = {
        dates[0]: 0.000185,
        dates[1]: 0.000190,
        dates[2]: 0.000188,
        dates[3]: 0.000192,
        dates[4]: 0.000189,
        dates[5]: 0.000195,
        dates[6]: 0.000191,
    }
    rf_series = pd.Series(daily_rates)

    initial_cap = Decimal("100000.00")
    config = BacktestConfig(
        strategy=None,  # 100% idle cash
        universe=["SPY"],
        initial_capital=initial_cap,
        enable_cash_yield=True,
        rf_series=rf_series,
    )

    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_spy_d},
        historical_intraday={"SPY": df_spy_i},
    )

    res = engine.run(start_date=dates[0], end_date=dates[-1], resolution="5m")
    assert not res.equity_curve.empty

    # Analytical compounding calculation:
    # Each session boundary credits interest on settled cash.
    expected_cash = float(initial_cap)
    for d in dates:
        r = daily_rates[d]
        expected_cash *= (1.0 + r)

    actual_cash = float(engine.broker.settled_cash)
    assert math.isclose(actual_cash, expected_cash, rel_tol=1e-5), (
        f"Settled cash {actual_cash} did not match expected compounded cash {expected_cash}"
    )
    assert actual_cash > float(initial_cap)


def test_adversarial_cash_yield_disabled_accrues_zero():
    """Verify that when enable_cash_yield=False, settled cash does not inflate."""
    n_days = 5
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(n_days)]

    df_spy_d = make_synthetic_daily_bars("SPY", dates, [400.0] * n_days)
    df_spy_i = make_synthetic_intraday_bars("SPY", dates, 400.0)

    initial_cap = Decimal("50000.00")
    config = BacktestConfig(
        strategy=None,
        universe=["SPY"],
        initial_capital=initial_cap,
        enable_cash_yield=False,  # Disabled
        annual_cash_yield_fallback=0.05,
    )
    engine = BacktestEngine(
        config=config,
        historical_daily={"SPY": df_spy_d},
        historical_intraday={"SPY": df_spy_i},
    )
    engine.run(start_date=dates[0], end_date=dates[-1], resolution="5m")

    actual_cash = float(engine.broker.settled_cash)
    assert actual_cash == float(initial_cap), (
        f"Expected cash to remain exactly {initial_cap}, got {actual_cash}"
    )


# ==============================================================================
# 5. Adversarial Input Normalization Probes
# ==============================================================================
def test_adversarial_hostile_input_key_normalization():
    """Verify that dirty keys with leading/trailing spaces, tabs, newlines, and mixed cases normalize."""
    dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(210)]
    df_spy_d = make_synthetic_daily_bars("SPY", dates, [400.0] * 210)
    df_aapl_d = make_synthetic_daily_bars("AAPL", dates, [150.0] * 210)

    sim_dates = dates[-2:]
    df_spy_i = make_synthetic_intraday_bars("SPY", sim_dates, 400.0)
    df_aapl_i = make_synthetic_intraday_bars("AAPL", sim_dates, 150.0)

    # Hostile keys with tabs, spaces, newlines, mixed casing
    historical_daily = {
        "  \tsPy \n": df_spy_d,
        " \naapl\t ": df_aapl_d,
    }
    historical_intraday = {
        "\t SPY \n": df_spy_i,
        " \tAaPl  ": df_aapl_i,
    }

    config = BacktestConfig(
        strategy=None,
        universe=["  spy  ", "\t AAPL\n "],
        blocks={
            "TECH": BlockConfig(name="TECH", capital_cap=0.5, top_n=1, buffer_rank=1, tickers=["  AAPL  ", " \tmsft\n "])
        },
    )

    engine = BacktestEngine(
        config=config,
        historical_daily=historical_daily,
        historical_intraday=historical_intraday,
    )

    # Verify internal dictionary normalization
    assert "SPY" in engine.daily_data
    assert "AAPL" in engine.daily_data
    assert "SPY" in engine.intraday_data
    assert "AAPL" in engine.intraday_data
    assert "  \tsPy \n" not in engine.daily_data

    # Verify execution succeeds without KeyError
    res = engine.run(start_date=sim_dates[0], end_date=sim_dates[-1], resolution="5m")
    assert not res.equity_curve.empty


def test_adversarial_forbidden_synthetic_commodity_normalization():
    """Verify that forbidden synthetic commodity detection catches dirty/whitespace-padded inputs."""
    forbidden_dirty_keys = [
        "  uso  ",
        "\tUNG\n",
        "  uCo ",
        "\tbOiL\t",
        "  kold\n",
        "\tSCO ",
    ]

    for key in forbidden_dirty_keys:
        daily = {key: pd.DataFrame()}
        with pytest.raises(ValueError, match="GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=[key]),
                historical_daily=daily,
            )
