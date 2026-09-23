"""Adversarial stress-testing suite for Milestone M2 Iteration 2 Gate Verification.

Authored by: m2_iter2_challenger_1 (EMPIRICAL CHALLENGER)
Focus areas:
1. Re-test M2-VULN-01: Whitespace-padded/case-varied forbidden commodities strictly raise ValueError.
2. Multi-asset 5m intraday chronological execution (_run_intraday).
3. Intraday end-of-day flattening (exit_at_close, custom flatten_time, overnight isolation).
4. Intraday circuit breaker enforcement:
   - Emergency flatten (-3.5% loss threshold, multi-asset liquidation, same-day halt).
   - Daily loss pause (-2.0% loss threshold, buy suppression without liquidation).
   - Session boundary daily reset.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from tbot.backtest.engine import (
    PHYSICAL_COMMODITIES_ALLOWLIST,
    BacktestConfig,
    BacktestEngine,
    BlockConfig,
)
from tbot.strategies.interfaces import Signal, Strategy, StrategyContext, StrategyDataRequirements


# ==============================================================================
# Deterministic Strategy Fixtures for Intraday Probes
# ==============================================================================
class IntradayProbeStrategy(Strategy):
    """Deterministic intraday strategy with controlled signal triggers."""

    id: str = "intraday_probe_strategy"
    version: str = "1.0.0"
    schedule: list[str] = ["09:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(
        self,
        signals_schedule: dict[time, list[str]] | None = None,
        stop_pct: float = 0.10,
        exit_at_close: bool = True,
        max_holding: int = 0,
    ) -> None:
        self.signals_schedule = signals_schedule or {}
        self.stop_pct = stop_pct
        self.exit_at_close = exit_at_close
        self.max_holding = max_holding

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        cur_time = ctx.now.time()
        targets = self.signals_schedule.get(cur_time, [])
        signals = []
        for sym in targets:
            if sym in ctx.current_prices and sym not in ctx.portfolio_positions:
                price = ctx.current_prices[sym]
                stop = price * Decimal(str(1.0 - self.stop_pct))
                signals.append(
                    Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=sym,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=price,
                        stop_price=stop,
                        exit_at_close=self.exit_at_close,
                        max_holding=self.max_holding,
                    )
                )
        return signals


# ==============================================================================
# Bar Data Helpers
# ==============================================================================
def create_intraday_5m_bars(
    symbol: str,
    trading_days: list[date],
    price_paths: dict[date, list[float]] | None = None,
    default_price: float = 100.0,
) -> pd.DataFrame:
    """Generates exactly 78 5-minute bars per day (09:30 to 15:55) with synthetic tag."""
    records = []
    for d in trading_days:
        day_prices = price_paths.get(d) if price_paths else None
        if day_prices is None:
            day_prices = [default_price] * 78
        elif len(day_prices) != 78:
            raise ValueError(f"Price path for {d} must have exactly 78 bars, got {len(day_prices)}")

        start_dt = datetime.combine(d, time(9, 30))
        for bar_idx in range(78):
            bar_ts = start_dt + timedelta(minutes=bar_idx * 5)
            p = float(day_prices[bar_idx])
            records.append(
                {
                    "symbol": symbol,
                    "timestamp": bar_ts,
                    "open": round(p, 4),
                    "high": round(p * 1.002, 4),
                    "low": round(p * 0.998, 4),
                    "close": round(p, 4),
                    "volume": 100_000,
                }
            )

    df = pd.DataFrame(records)
    df.attrs["is_synthetic"] = True
    return df


def create_daily_mock_df(symbol: str, trading_days: list[date], price: float = 100.0) -> pd.DataFrame:
    """Generates mock daily bars matching trading days."""
    records = []
    for d in trading_days:
        records.append(
            {
                "symbol": symbol,
                "date": d,
                "open": round(price, 4),
                "high": round(price * 1.01, 4),
                "low": round(price * 0.99, 4),
                "close": round(price, 4),
                "volume": 1_000_000,
            }
        )
    df = pd.DataFrame(records)
    df.attrs["is_synthetic"] = True
    return df


# ==============================================================================
# 1. Adversarial Suite: M2-VULN-01 & Invariant 2 Rejection Hardening
# ==============================================================================
class TestAdversarialForbiddenCommodityPaddedVariants:
    """Rigorous challenge against whitespace, newline, tab, and casing evasions."""

    @pytest.mark.parametrize(
        "padded_symbol",
        [
            " USO", "USO ", "  USO  ", "\tUSO", "USO\n", "\r\nUSO\t", " \t USO \n ",
            " UNG", "UNG ", "  UNG  ", "\tUNG", "UNG\n", "\r\nUNG\t",
            " BOIL", "BOIL ", "\tBOIL\n", "  BOIL  ",
            " UCO", "UCO ", "  UCO  ", "\tUCO\n",
            " SCO", "SCO ", "  SCO  ", "\tSCO\n",
            " KOLD", "KOLD ", "  KOLD  ", "\tKOLD\n",
            "  uso  ", "\tuso\n", "  ung  ", "\tung\n", "  boil  ", "  kold  ",
            " \u00a0USO\u00a0 ", " \u00a0UNG\u00a0 ",  # non-breaking space
        ],
    )
    def test_padded_forbidden_symbols_in_universe_strictly_raise(self, padded_symbol: str):
        """Attacks Invariant 2 via universe config with hostile whitespace and casing padding."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY", padded_symbol]),
                historical_daily={"SPY": pd.DataFrame()},
            )

    @pytest.mark.parametrize(
        "padded_symbol",
        [
            " USO", "USO ", "  USO  ", "\tUSO\n",
            " UNG", "UNG ", "  UNG  ", "\tUNG\n",
            "  BOIL  ", "\tBOIL\n", "  uso  ",
        ],
    )
    def test_padded_forbidden_symbols_in_block_tickers_strictly_raise(self, padded_symbol: str):
        """Attacks Invariant 2 via BlockConfig tickers with whitespace/casing padding."""
        block = BlockConfig(
            name="HostileBlock",
            tickers=["GLD", padded_symbol],
            capital_cap=0.25,
            top_n=1,
            buffer_rank=1,
        )
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(blocks={"hostile": block}),
                historical_daily={"GLD": pd.DataFrame()},
            )

    @pytest.mark.parametrize(
        "padded_symbol",
        [
            " USO", "USO ", "  USO  ", "\tUSO\n",
            " UNG", "UNG ", "\tBOIL\n", "  uso  ",
        ],
    )
    def test_padded_forbidden_symbols_in_historical_daily_keys_strictly_raise(self, padded_symbol: str):
        """Attacks Invariant 2 via historical_daily dict keys with whitespace/casing padding."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY"]),
                historical_daily={"SPY": pd.DataFrame(), padded_symbol: pd.DataFrame()},
            )

    @pytest.mark.parametrize(
        "padded_symbol",
        [
            " USO", "USO ", "  USO  ", "\tUSO\n",
            " UNG", "UNG ", "\tBOIL\n", "  uso  ",
        ],
    )
    def test_padded_forbidden_symbols_in_historical_intraday_keys_strictly_raise(self, padded_symbol: str):
        """Attacks Invariant 2 via historical_intraday dict keys with whitespace/casing padding."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY"]),
                historical_daily={"SPY": pd.DataFrame()},
                historical_intraday={padded_symbol: pd.DataFrame()},
            )

    @pytest.mark.parametrize("physical", list(PHYSICAL_COMMODITIES_ALLOWLIST))
    def test_physical_commodities_with_or_without_whitespace_accepted(self, physical: str):
        """Verifies physical commodities allowlist (GLD, GLDM, SLV) is preserved."""
        engine = BacktestEngine(
            config=BacktestConfig(universe=["SPY", f" {physical} "]),
            historical_daily={"SPY": pd.DataFrame(), physical: pd.DataFrame()},
        )
        assert engine is not None


# ==============================================================================
# 2. Adversarial Suite: Multi-Asset 5m Execution (_run_intraday)
# ==============================================================================
class TestIntradayMultiAssetExecution:
    """Stress tests multi-asset 5m execution, position caps, and chronological sync."""

    def test_multi_asset_5m_synchronization_and_position_limits(self):
        """Verifies 3 assets trade concurrently on 5m bars respecting max_open_positions."""
        days = [date(2022, 1, 10), date(2022, 1, 11)]
        syms = ["AAPL", "MSFT", "GOOG"]

        # Strategy attempts to buy all 3 at 10:00 AM each day
        strat = IntradayProbeStrategy(
            signals_schedule={time(10, 0): syms},
            stop_pct=0.05,
            exit_at_close=True,
        )

        daily_data = {s: create_daily_mock_df(s, days, price=100.0) for s in syms}
        daily_data["SPY"] = create_daily_mock_df("SPY", days, price=400.0)

        intraday_data = {s: create_intraday_5m_bars(s, days, default_price=100.0) for s in syms}
        intraday_data["SPY"] = create_intraday_5m_bars("SPY", days, default_price=400.0)

        config = BacktestConfig(
            strategy=strat,
            universe=syms,
            initial_capital=Decimal("2000.00"),
            max_open_positions=2,  # Strict cap: max 2 open positions
            single_position_cap=0.40,
            integer_shares=True,
            enable_circuit_breakers=True,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=days[0], end_date=days[-1], resolution="5m")

        assert res is not None
        assert isinstance(res.trades, list)
        assert len(res.trades) > 0

        # Across both days, each day should execute exactly 2 buys at 10:00 AM
        # because max_open_positions = 2 suppresses the 3rd asset
        day1_trades = [t for t in res.trades if t.entry_time.date() == days[0]]
        day2_trades = [t for t in res.trades if t.entry_time.date() == days[1]]

        assert len(day1_trades) == 2, f"Expected exactly 2 trades on day 1, got {len(day1_trades)}"
        assert len(day2_trades) == 2, f"Expected exactly 2 trades on day 2, got {len(day2_trades)}"

        # Verify buying power was respected (initial $2000, 2 positions <= $800 each + retail friction)
        for t in res.trades:
            cost = float(t.qty * t.entry_price)
            assert cost <= 805.0, f"Trade exceeded 40% cap (+ retail friction): {cost}"


# ==============================================================================
# 3. Adversarial Suite: Intraday End-of-Day Flattening
# ==============================================================================
class TestIntradayEndOfDayFlattening:
    """Stress tests intraday position flattening (day_end_flatten)."""

    def test_intraday_positions_flattened_at_session_end(self):
        """Verifies positions with exit_at_close=True are closed at flatten_time."""
        days = [date(2022, 1, 10)]
        sym = "AAPL"

        # Signal emitted at 10:00 AM with exit_at_close=True
        strat = IntradayProbeStrategy(
            signals_schedule={time(10, 0): [sym]},
            stop_pct=0.10,  # wide stop so it doesn't get stopped out early
            exit_at_close=True,
        )

        daily_data = {
            "SPY": create_daily_mock_df("SPY", days, price=400.0),
            sym: create_daily_mock_df(sym, days, price=150.0),
        }
        intraday_data = {
            "SPY": create_intraday_5m_bars("SPY", days, default_price=400.0),
            sym: create_intraday_5m_bars(sym, days, default_price=150.0),
        }

        config = BacktestConfig(
            strategy=strat,
            universe=[sym],
            initial_capital=Decimal("2000.00"),
            flatten_time=time(15, 55),
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=days[0], end_date=days[0], resolution="5m")

        assert len(res.trades) == 1
        trade = res.trades[0]
        assert trade.exit_reason == "day_end_flatten"
        assert trade.exit_time.time() >= time(15, 55)
        # Verify no open positions remain in the broker
        assert len(engine.broker.positions) == 0

    def test_custom_flatten_time_earlier_in_session(self):
        """Verifies flattening triggers earlier when config.flatten_time is adjusted (e.g. 15:30)."""
        days = [date(2022, 1, 10)]
        sym = "MSFT"

        strat = IntradayProbeStrategy(
            signals_schedule={time(10, 0): [sym]},
            stop_pct=0.10,
            exit_at_close=True,
        )

        daily_data = {
            "SPY": create_daily_mock_df("SPY", days, price=400.0),
            sym: create_daily_mock_df(sym, days, price=200.0),
        }
        intraday_data = {
            "SPY": create_intraday_5m_bars("SPY", days, default_price=400.0),
            sym: create_intraday_5m_bars(sym, days, default_price=200.0),
        }

        # Set early flatten time at 15:30
        config = BacktestConfig(
            strategy=strat,
            universe=[sym],
            initial_capital=Decimal("2000.00"),
            flatten_time=time(15, 30),
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=days[0], end_date=days[0], resolution="5m")

        assert len(res.trades) == 1
        trade = res.trades[0]
        assert trade.exit_reason == "day_end_flatten"
        assert trade.exit_time.time() == time(15, 30)


# ==============================================================================
# 4. Adversarial Suite: Intraday Circuit Breaker Emergency Flatten (-3.5%) & Pause (-2.0%)
# ==============================================================================
class TestIntradayCircuitBreakersAdversarial:
    """Stress tests intraday circuit breaker reactions under hostile market crashes."""

    def test_intraday_flash_crash_triggers_emergency_flatten_and_liquidates_all(self):
        """Attacks portfolio with severe intraday crash (> 3.5% portfolio loss):
        
        1. Buys 2 positions at 10:00 AM.
        2. At 11:30 AM, SYM_CRASH drops 50%, dragging total equity down > 10%.
        3. Verifies immediate EMERGENCY_FLATTEN liquidation of BOTH positions.
        4. Verifies halt_session suppresses subsequent buy attempts.
        """
        d0 = date(2022, 1, 10)
        days = [d0]
        sym_crash = "SYM_CRASH"
        sym_safe = "SYM_SAFE"

        # Signal buy at 10:00 AM, and another signal attempt at 13:00 PM
        strat = IntradayProbeStrategy(
            signals_schedule={
                time(10, 0): [sym_crash, sym_safe],
                time(13, 0): [sym_safe],  # should be blocked by halt_session
            },
            stop_pct=0.80,  # wide regular stop so circuit breaker triggers first
            exit_at_close=True,
        )

        # 78 bars for SYM_SAFE (steady at 100.0)
        safe_prices = [100.0] * 78

        # 78 bars for SYM_CRASH (steady at 100.0 until bar 24 = 11:30 AM, then drops to 40.0)
        # Bar 0 is 09:30, Bar 6 is 10:00, Bar 24 is 11:30 (9:30 + 24*5min = 11:30)
        crash_prices = [100.0] * 78
        for b in range(24, 78):
            crash_prices[b] = 40.0

        daily_data = {
            "SPY": create_daily_mock_df("SPY", days, price=400.0),
            sym_crash: create_daily_mock_df(sym_crash, days, price=100.0),
            sym_safe: create_daily_mock_df(sym_safe, days, price=100.0),
        }
        intraday_data = {
            "SPY": create_intraday_5m_bars("SPY", days, default_price=400.0),
            sym_crash: create_intraday_5m_bars(sym_crash, days, price_paths={d0: crash_prices}),
            sym_safe: create_intraday_5m_bars(sym_safe, days, price_paths={d0: safe_prices}),
        }

        config = BacktestConfig(
            strategy=strat,
            universe=[sym_crash, sym_safe],
            initial_capital=Decimal("2000.00"),
            single_position_cap=0.45,  # $900 in each position
            enable_circuit_breakers=True,
            daily_loss_limit_pct=2.0,
            emergency_loss_limit_pct=3.5,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=d0, end_date=d0, resolution="5m")

        # 1. Verify emergency circuit breaker event was triggered
        assert len(res.circuit_breaker_events) > 0, "Emergency circuit breaker event MUST be recorded!"
        cb_event = res.circuit_breaker_events[0]
        assert cb_event.event_type == "EMERGENCY_FLATTEN"
        assert cb_event.timestamp.time() == time(11, 30)
        assert cb_event.intraday_loss_pct >= 3.5

        # 2. Both positions must be liquidated with reason circuit_breaker_emergency_flatten
        assert len(res.trades) == 2
        for t in res.trades:
            assert t.exit_reason == "circuit_breaker_emergency_flatten"
            assert t.exit_time.time() == time(11, 30)

        # 3. Subsequent buy signal at 13:00 MUST be suppressed
        # Trades count remains strictly 2 (no new trades opened)
        assert len(engine.broker.positions) == 0

    def test_intraday_daily_pause_suppresses_buys_without_liquidating_positions(self):
        """Attacks portfolio with moderate drawdown (between 2.0% and 3.5%):
        
        1. Enters SYM_MOD at 10:00 AM.
        2. At 11:30 AM, price drops to trigger ~2.5% portfolio loss (>= 2.0% daily limit, < 3.5% emergency).
        3. Verifies CircuitBreakerManager enters PAUSED_DAILY_LOSS.
        4. Verifies existing position is NOT liquidated.
        5. Verifies subsequent buy signal for SYM_NEW at 12:00 PM is SUPPRESSED.
        """
        d0 = date(2022, 1, 10)
        days = [d0]
        sym_mod = "SYM_MOD"
        sym_new = "SYM_NEW"

        strat = IntradayProbeStrategy(
            signals_schedule={
                time(10, 0): [sym_mod],
                time(12, 0): [sym_new],  # Should be suppressed by PAUSED_DAILY_LOSS
            },
            stop_pct=0.20,
            exit_at_close=True,
        )

        # Capital $2000, 50% cap = $1000 in SYM_MOD (10 shares @ $100)
        # To cause 2.5% portfolio loss ($50 loss), price drops from 100 to 95 at bar 24 (11:30 AM)
        mod_prices = [100.0] * 78
        for b in range(24, 78):
            mod_prices[b] = 95.0  # 10 shares * $5 drop = $50 loss -> 50 / 2000 = 2.5% loss

        daily_data = {
            "SPY": create_daily_mock_df("SPY", days, price=400.0),
            sym_mod: create_daily_mock_df(sym_mod, days, price=100.0),
            sym_new: create_daily_mock_df(sym_new, days, price=50.0),
        }
        intraday_data = {
            "SPY": create_intraday_5m_bars("SPY", days, price_paths={d0: mod_prices}),
            sym_mod: create_intraday_5m_bars(sym_mod, days, price_paths={d0: mod_prices}),
            sym_new: create_intraday_5m_bars(sym_new, days, default_price=50.0),
        }

        config = BacktestConfig(
            strategy=strat,
            universe=[sym_mod, sym_new],
            initial_capital=Decimal("2000.00"),
            single_position_cap=0.50,
            enable_circuit_breakers=True,
            daily_loss_limit_pct=2.0,       # 2.0% threshold
            emergency_loss_limit_pct=3.5,   # 3.5% threshold
            trailing_ema_period=0,          # Disable trailing EMA exit to isolate circuit breaker
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=d0, end_date=d0, resolution="5m")

        # SYM_MOD was NOT emergency-flattened at 11:30 AM; it exited at day_end_flatten
        assert len(res.trades) == 1
        trade = res.trades[0]
        assert trade.symbol == sym_mod
        assert trade.exit_reason == "day_end_flatten"

        # SYM_NEW was NEVER bought because PAUSED_DAILY_LOSS suppressed can_open_new_positions()
        traded_symbols = {t.symbol for t in res.trades}
        assert sym_new not in traded_symbols, "SYM_NEW buy should have been suppressed by daily loss pause!"

    def test_intraday_circuit_breaker_resets_on_next_day(self):
        """Verifies that an intraday circuit breaker halt on Day 1 is reset on Day 2."""
        days = [date(2022, 1, 10), date(2022, 1, 11)]
        sym = "SYM_RESET"

        strat = IntradayProbeStrategy(
            signals_schedule={time(10, 0): [sym]},
            stop_pct=0.80,
            exit_at_close=True,
        )

        # Day 1: Crashes at bar 24 (11:30) causing emergency flatten
        day1_prices = [100.0] * 78
        for b in range(24, 78):
            day1_prices[b] = 50.0

        # Day 2: Calm day (stays at 100.0)
        day2_prices = [100.0] * 78

        daily_data = {
            "SPY": create_daily_mock_df("SPY", days, price=400.0),
            sym: create_daily_mock_df(sym, days, price=100.0),
        }
        intraday_data = {
            "SPY": create_intraday_5m_bars("SPY", days, default_price=400.0),
            sym: create_intraday_5m_bars(sym, days, price_paths={days[0]: day1_prices, days[1]: day2_prices}),
        }

        config = BacktestConfig(
            strategy=strat,
            universe=[sym],
            initial_capital=Decimal("2000.00"),
            single_position_cap=0.50,
            enable_circuit_breakers=True,
            emergency_loss_limit_pct=3.5,
            weekly_loss_limit_pct=50.0,  # Isolate daily reset from weekly limit
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=days[0], end_date=days[1], resolution="5m")

        assert len(res.trades) == 2

        # Trade 1 on Day 1: Emergency flattened
        t1 = res.trades[0]
        assert t1.entry_time.date() == days[0]
        assert t1.exit_reason == "circuit_breaker_emergency_flatten"

        # Trade 2 on Day 2: Reset allowed entry at 10:00 AM, flattened at end of day
        t2 = res.trades[1]
        assert t2.entry_time.date() == days[1]
        assert t2.exit_reason == "day_end_flatten"

    def test_weekly_circuit_breaker_persists_across_days_in_same_week(self):
        """Verifies that exceeding the weekly loss limit (5.0%) maintains pause on Day 2."""
        days = [date(2022, 1, 10), date(2022, 1, 11)]
        sym = "SYM_WEEK"

        strat = IntradayProbeStrategy(
            signals_schedule={time(10, 0): [sym]},
            stop_pct=0.80,
            exit_at_close=True,
        )

        # Day 1: Crashes causing 25% loss (exceeding 5% weekly limit)
        day1_prices = [100.0] * 78
        for b in range(24, 78):
            day1_prices[b] = 50.0

        # Day 2: Calm day (stays at 100.0)
        day2_prices = [100.0] * 78

        daily_data = {
            "SPY": create_daily_mock_df("SPY", days, price=400.0),
            sym: create_daily_mock_df(sym, days, price=100.0),
        }
        intraday_data = {
            "SPY": create_intraday_5m_bars("SPY", days, default_price=400.0),
            sym: create_intraday_5m_bars(sym, days, price_paths={days[0]: day1_prices, days[1]: day2_prices}),
        }

        # Default weekly_loss_limit_pct is 5.0%
        config = BacktestConfig(
            strategy=strat,
            universe=[sym],
            initial_capital=Decimal("2000.00"),
            single_position_cap=0.50,
            enable_circuit_breakers=True,
            emergency_loss_limit_pct=3.5,
            weekly_loss_limit_pct=5.0,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily=daily_data,
            historical_intraday=intraday_data,
        )

        res = engine.run(start_date=days[0], end_date=days[1], resolution="5m")

        # Only Day 1 trade executed; Day 2 was blocked because weekly loss limit remains active
        assert len(res.trades) == 1
        assert res.trades[0].exit_reason == "circuit_breaker_emergency_flatten"
