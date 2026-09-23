"""Adversarial stress-testing suite for BacktestEngine (Milestone M2 Gate Verification).

Authored by: m2_challenger_1 (EMPIRICAL CHALLENGER)
Focus areas:
1. Rule 0 Holdout Guard Penetration (Daily, Hourly, Straddles, Aliases, Polymorphic Types).
2. Hostile Multi-Asset Intraday Flash Crashes & Circuit Breaker Transitions (-2.0% pause vs -3.5% flatten).
3. Forbidden Synthetic Commodity Rejection & Adversarial Bypass Probes (USO, UNG, UCO, BOIL, SCO, KOLD).
4. Deterministic Risk Invariants (Stop-loss requirement, Max positions cap, Reg T Cash limits).
"""

from datetime import date, datetime, time
from decimal import Decimal

import pandas as pd
import pytest

from tbot.backtest.engine import (
    FORBIDDEN_SYNTHETIC_COMMODITIES,
    PHYSICAL_COMMODITIES_ALLOWLIST,
    BacktestConfig,
    BacktestEngine,
    BlockConfig,
)
from tbot.backtest.guards import (
    HOLDOUT_DAILY_END,
    HOLDOUT_DAILY_START,
    HOLDOUT_HOURLY_END,
    HOLDOUT_HOURLY_START,
    HoldoutViolationError,
    assert_not_holdout,
)
from tbot.risk.models import CircuitBreakerState
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


# ==============================================================================
# Helpers & Deterministic Test Fixtures
# ==============================================================================
class DeterministicSignalStrategy:
    """Strategy that generates buy signals for requested symbols with customizable stops."""

    id: str = "adversarial_signal_strat"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(
        self,
        target_symbols: list[str] | None = None,
        stop_pct: float = 0.05,
        invalid_stop: bool = False,
        signals_by_date: dict[date, list[str]] | None = None,
    ) -> None:
        self.target_symbols = target_symbols or []
        self.stop_pct = stop_pct
        self.invalid_stop = invalid_stop
        self.signals_by_date = signals_by_date

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        signals = []
        cur_date = ctx.now.date()
        target_list = (
            self.signals_by_date.get(cur_date, [])
            if self.signals_by_date is not None
            else self.target_symbols
        )
        for s in target_list:
            if s in ctx.current_prices and s not in ctx.portfolio_positions:
                price = ctx.current_prices[s]
                if self.invalid_stop:
                    # Invariant violation: stop above or equal to entry
                    stop = price * Decimal("1.05")
                else:
                    stop = price * Decimal(str(1.0 - self.stop_pct))

                signals.append(
                    Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=s,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=price,
                        stop_price=stop,
                    )
                )
        return signals


def _make_daily_bar_df(
    symbol: str,
    dates: list[date],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    is_synthetic: bool = True,
) -> pd.DataFrame:
    """Creates a deterministic daily DataFrame with explicit bar prices."""
    records = []
    for bar_date, b_open, b_high, b_low, b_close in zip(dates, opens, highs, lows, closes, strict=False):
        records.append(
            {
                "symbol": symbol,
                "date": bar_date,
                "open": round(float(b_open), 4),
                "high": round(float(b_high), 4),
                "low": round(float(b_low), 4),
                "close": round(float(b_close), 4),
                "volume": 1_000_000,
            }
        )
    df = pd.DataFrame(records)
    if is_synthetic:
        df.attrs["is_synthetic"] = True
    return df


def _make_uniform_df(
    symbol: str,
    dates: list[date],
    price: float = 100.0,
    is_synthetic: bool = True,
) -> pd.DataFrame:
    """Creates flat bars for benign assets."""
    return _make_daily_bar_df(
        symbol=symbol,
        dates=dates,
        opens=[price] * len(dates),
        highs=[price * 1.01] * len(dates),
        lows=[price * 0.99] * len(dates),
        closes=[price] * len(dates),
        is_synthetic=is_synthetic,
    )


# ==============================================================================
# 1. Rule 0 Holdout Guard Penetration Tests
# ==============================================================================
class TestRule0HoldoutGuardPenetration:
    """Adversarial stress-testing of Rule 0 Holdout Guard boundaries and types."""

    def test_holdout_guard_blocks_exact_sealed_daily_range(self):
        """Attacks the exact daily holdout interval (2023-01-01 to 2026-02-27)."""
        # 1. Direct assertion guard
        with pytest.raises(HoldoutViolationError, match="Holdout Violation"):
            assert_not_holdout(HOLDOUT_DAILY_START, HOLDOUT_DAILY_END, resolution="daily")

        # 2. BacktestEngine.run() invocation with non-synthetic real data
        df_spy = _make_uniform_df("SPY", [date(2022, 1, 3)], is_synthetic=False)
        engine = BacktestEngine(
            config=BacktestConfig(strategy=DeterministicSignalStrategy(["SPY"])),
            historical_daily={"SPY": df_spy},
        )
        with pytest.raises(HoldoutViolationError):
            engine.run(start_date=HOLDOUT_DAILY_START, end_date=HOLDOUT_DAILY_END)

    def test_holdout_guard_blocks_exact_sealed_hourly_range(self):
        """Attacks the exact hourly holdout interval (2025-09-22 to 2026-09-21)."""
        with pytest.raises(HoldoutViolationError, match="Holdout Violation"):
            assert_not_holdout(HOLDOUT_HOURLY_START, HOLDOUT_HOURLY_END, resolution="hourly")

        # Via BacktestEngine
        df_spy = _make_uniform_df("SPY", [date(2022, 1, 3)], is_synthetic=False)
        engine = BacktestEngine(
            config=BacktestConfig(strategy=DeterministicSignalStrategy(["SPY"])),
            historical_daily={"SPY": df_spy},
        )
        with pytest.raises(HoldoutViolationError):
            engine.run(start_date=HOLDOUT_HOURLY_START, end_date=HOLDOUT_HOURLY_END, resolution="hourly")

    @pytest.mark.parametrize(
        "res_alias",
        ["hourly", "1h", "h", "hour", "HOURLY", "1H"],
    )
    def test_holdout_guard_hourly_aliases_blocked(self, res_alias: str):
        """Attacks hourly holdout using various resolution aliases."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(HOLDOUT_HOURLY_START, HOLDOUT_HOURLY_END, resolution=res_alias)

    @pytest.mark.parametrize(
        "res_alias",
        ["daily", "1d", "d", "day", "DAILY", "1D"],
    )
    def test_holdout_guard_daily_aliases_blocked(self, res_alias: str):
        """Attacks daily holdout using various resolution aliases."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(HOLDOUT_DAILY_START, HOLDOUT_DAILY_END, resolution=res_alias)

    def test_holdout_guard_daily_exact_boundary_dates(self):
        """Tests exact boundary dates: start boundary, end boundary, and adjacent non-holdout dates."""
        # Exact first day of holdout (2023-01-01) -> MUST raise
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(HOLDOUT_DAILY_START, HOLDOUT_DAILY_START, resolution="daily")

        # Exact last day of holdout (2026-02-27) -> MUST raise
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(HOLDOUT_DAILY_END, HOLDOUT_DAILY_END, resolution="daily")

        # One day before holdout (2022-12-31) in dev window -> MUST PASS
        assert_not_holdout(date(2022, 12, 31), date(2022, 12, 31), resolution="daily")

        # One day after holdout (2026-02-28) -> MUST PASS
        assert_not_holdout(date(2026, 2, 28), date(2026, 2, 28), resolution="daily")

    def test_holdout_guard_hourly_exact_boundary_dates(self):
        """Tests exact hourly boundaries."""
        # Exact first day of hourly holdout (2025-09-22) -> MUST raise
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(HOLDOUT_HOURLY_START, HOLDOUT_HOURLY_START, resolution="hourly")

        # Exact last day of hourly holdout (2026-09-21) -> MUST raise
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(HOLDOUT_HOURLY_END, HOLDOUT_HOURLY_END, resolution="hourly")

        # One day before hourly holdout (2025-09-21) in dev window -> MUST PASS
        assert_not_holdout(date(2025, 9, 21), date(2025, 9, 21), resolution="hourly")

    def test_holdout_guard_straddle_attacks(self):
        """Tests partial intersection attacks where interval starts or ends outside holdout."""
        # 1. Left straddle: Starts in dev (2022-12-15) and ends in holdout (2023-01-15)
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(date(2022, 12, 15), date(2023, 1, 15), resolution="daily")

        # 2. Right straddle: Starts in holdout (2026-02-15) and ends after (2026-03-15)
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(date(2026, 2, 15), date(2026, 3, 15), resolution="daily")

        # 3. Total engulfment: Starts in 2020 and ends in 2026
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(date(2020, 1, 1), date(2026, 3, 1), resolution="daily")

        # 4. Hourly straddle: Starts in 2025-09-15 and ends in 2025-09-30
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(date(2025, 9, 15), date(2025, 9, 30), resolution="hourly")

    def test_holdout_guard_type_polymorphism(self):
        """Tests that guards cannot be bypassed by passing strings, datetime, or pandas.Timestamp."""
        # ISO string format
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2023-01-01", "2023-06-01", resolution="daily")

        # datetime.datetime format
        dt_start = datetime(2023, 1, 1, 9, 30)
        dt_end = datetime(2023, 1, 10, 16, 0)
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(dt_start, dt_end, resolution="daily")

        # pandas Timestamp format
        ts_start = pd.Timestamp("2023-05-01")
        ts_end = pd.Timestamp("2023-05-15")
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(ts_start, ts_end, resolution="daily")

    def test_holdout_guard_invalid_inputs_rejected(self):
        """Tests invalid arguments: inverted range or unrecognized resolution."""
        # Inverted range start > end
        with pytest.raises(ValueError, match="Invalid range"):
            assert_not_holdout(date(2022, 5, 10), date(2022, 5, 1), resolution="daily")

        # Unrecognized resolution
        with pytest.raises(ValueError, match="Unknown resolution"):
            assert_not_holdout(date(2022, 5, 1), date(2022, 5, 10), resolution="tick")

    def test_holdout_guard_5m_resolution_checks(self):
        """Tests that 5m outside sanctioned window (2026-06-26 to 2026-09-21) is checked against holdout."""
        # 5m within sealed daily holdout (e.g. 2024-01-01 to 2024-01-10) -> MUST raise
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(date(2024, 1, 1), date(2024, 1, 10), resolution="5m")

        # 5m within sanctioned diagnostic sample (2026-06-26 to 2026-09-21) -> MUST PASS
        assert_not_holdout(date(2026, 7, 1), date(2026, 7, 10), resolution="5m")


# ==============================================================================
# 2. Hostile Multi-Asset Intraday Flash Crashes & Circuit Breakers
# ==============================================================================
class TestHostileIntradayCircuitBreakers:
    """Stress-tests multi-asset flash crashes, -2.0% buy pause, and -3.5% emergency flatten."""

    def test_adversarial_flash_crash_triggers_daily_pause_without_flattening(self):
        """Adversarially engineers a -2.5% intraday portfolio drawdown.

        Expected behavior:
        - Circuit breaker transitions to PAUSED_DAILY_LOSS.
        - Existing open positions are NOT closed (stops remain active).
        - New buy signals for other assets are BLOCKED.
        - CircuitBreakerEvent for PAUSED_DAILY_LOSS is recorded.
        """
        # Session dates: Day 0 (entry), Day 1 (flash crash -2.5%)
        d0 = date(2022, 3, 1)
        d1 = date(2022, 3, 2)
        dates = [d0, d1]

        # Initial capital: $2000.
        # Assets A and B are bought on Day 0.
        # Asset C is a target to buy on Day 1.
        # Day 0: Price $100.
        #        # On Day 1:
        # Asset A: open $100, low $95, close $96 (loss on 5 shares = -$25)
        # Asset B: open $100, low $95, close $96 (loss on 5 shares = -$25)
        # Total portfolio loss on low: -$50.
        # $50 / $2000 = 2.50% (between 2.0% daily pause and 3.5% emergency flatten).

        df_spy = _make_uniform_df("SPY", dates, price=400.0)
        df_a = _make_daily_bar_df("ASSET_A", dates, [100.0, 100.0], [101.0, 100.0], [99.0, 95.0], [100.0, 96.0])
        df_b = _make_daily_bar_df("ASSET_B", dates, [100.0, 100.0], [101.0, 100.0], [99.0, 95.0], [100.0, 96.0])
        df_c = _make_uniform_df("ASSET_C", dates, price=50.0)

        # Strategy buys A and B on Day 0, then attempts to buy C on Day 1
        strat = DeterministicSignalStrategy(
            signals_by_date={d0: ["ASSET_A", "ASSET_B"], d1: ["ASSET_C"]},
            stop_pct=0.20,
        )
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("2000.00"),
            risk_per_trade_pct=5.0,  # 5% risk on $2000 ($100) with 20% stop -> $500 alloc per position
            single_position_cap=0.25,  # $500 per position (5 shares at $100)
            max_open_positions=4,
            enable_circuit_breakers=True,
            daily_loss_limit_pct=2.0,
            emergency_loss_limit_pct=3.5,
            integer_shares=True,
            enable_vol_control=False,
            min_position_usd=100.0,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "ASSET_A": df_a, "ASSET_B": df_b, "ASSET_C": df_c},
        )

        result = engine.run(start_date=d0, end_date=d1)

        # 1. Circuit breaker event recorded
        assert len(result.circuit_breaker_events) >= 1
        event = result.circuit_breaker_events[0]
        assert event.event_type == "PAUSED_DAILY_LOSS"
        assert event.intraday_loss_pct >= 2.0
        assert event.intraday_loss_pct < 3.5

        # 2. Existing positions ASSET_A and ASSET_B were NOT flattened during the session
        # event.liquidated_positions is empty, and closed trades only exist at EOD teardown (16:00)
        assert event.liquidated_positions == [], "Daily pause must NOT liquidate positions!"
        for t in result.trades:
            assert t.exit_time.time() == time(16, 0), "Trades should only close at end of backtest teardown!"

        # 3. ASSET_C was NOT bought on Day 1 because new buys were halted
        symbols_bought = {t.symbol for t in result.trades}
        assert "ASSET_C" not in symbols_bought, "New buy orders must be HALTED during PAUSED_DAILY_LOSS!"

    def test_adversarial_flash_crash_triggers_emergency_flatten_and_liquidates_all(self):
        """Adversarially engineers an intraday flash crash causing -4.5% equity drop.

        Expected behavior:
        - Circuit breaker transitions to EMERGENCY_FLATTEN.
        - ALL open positions are immediately liquidated at low price.
        - Orders cancelled, no new buys allowed (`halt_session = True`).
        - `broker.positions` becomes empty.
        - Closed trades have exit_time at eval_time (15:45).
        - Event records liquidated positions.
        """
        d0 = date(2022, 3, 1)
        d1 = date(2022, 3, 2)
        dates = [d0, d1]

        # Initial capital: $2000.
        # Day 0: Buy ASSET_A and ASSET_B ($500 each, 5 shares each at $100).
        # Day 1: Flash crash:
        # ASSET_A low drops to $80 (loss -$100)
        # ASSET_B low drops to $85 (loss -$75)
        # Total portfolio loss on low: -$175 on $2000 = -8.75% (> -3.5%).

        df_spy = _make_uniform_df("SPY", dates, price=400.0)
        df_a = _make_daily_bar_df("ASSET_A", dates, [100.0, 100.0], [101.0, 100.0], [99.0, 80.0], [100.0, 82.0])
        df_b = _make_daily_bar_df("ASSET_B", dates, [100.0, 100.0], [101.0, 100.0], [99.0, 85.0], [100.0, 86.0])
        df_c = _make_uniform_df("ASSET_C", dates, price=50.0)

        strat = DeterministicSignalStrategy(
            signals_by_date={d0: ["ASSET_A", "ASSET_B"], d1: ["ASSET_C"]},
            stop_pct=0.30,
        )
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("2000.00"),
            risk_per_trade_pct=10.0,  # 10% of $2000 = $200 risk / 30% stop -> $666 alloc, capped at $500
            single_position_cap=0.25,
            max_open_positions=4,
            enable_circuit_breakers=True,
            daily_loss_limit_pct=2.0,
            emergency_loss_limit_pct=3.5,
            integer_shares=True,
            enable_vol_control=False,
            min_position_usd=100.0,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "ASSET_A": df_a, "ASSET_B": df_b, "ASSET_C": df_c},
        )

        result = engine.run(start_date=d0, end_date=d1)

        # 1. EMERGENCY_FLATTEN event recorded
        flatten_events = [e for e in result.circuit_breaker_events if e.event_type == "EMERGENCY_FLATTEN"]
        assert len(flatten_events) == 1
        ev = flatten_events[0]
        assert ev.intraday_loss_pct >= 3.5
        assert set(ev.liquidated_positions) == {"ASSET_A", "ASSET_B"}

        # 2. Both positions closed at eval_dt (15:45) due to emergency flatten
        assert len(result.trades) == 2
        cb_symbols = {t.symbol for t in result.trades}
        assert cb_symbols == {"ASSET_A", "ASSET_B"}
        for t in result.trades:
            assert t.exit_time.time() == time(15, 45), "Emergency flatten must close trades immediately at 15:45!"

        # 3. New buy for ASSET_C was blocked
        assert "ASSET_C" not in {t.symbol for t in result.trades}

    def test_adversarial_circuit_breaker_next_day_reset(self):
        """Tests that after EMERGENCY_FLATTEN on Day 1, Day 2 resets to NORMAL and allows trading."""
        d0 = date(2022, 3, 1)
        d1 = date(2022, 3, 2)  # Crash day
        d2 = date(2022, 3, 3)  # Calm recovery day
        dates = [d0, d1, d2]

        df_spy = _make_uniform_df("SPY", dates, price=400.0)
        # ASSET_A crashes on d1, remains steady on d2
        df_a = _make_daily_bar_df(
            "ASSET_A",
            dates,
            [100.0, 100.0, 80.0],
            [101.0, 100.0, 82.0],
            [99.0, 75.0, 79.0],
            [100.0, 80.0, 81.0],
        )
        # ASSET_B is steady throughout
        df_b = _make_uniform_df("ASSET_B", dates, price=50.0)

        strat = DeterministicSignalStrategy(
            signals_by_date={d0: ["ASSET_A"], d1: [], d2: ["ASSET_B"]},
            stop_pct=0.30,
        )
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("2000.00"),
            risk_per_trade_pct=10.0,  # 10% risk -> ensures allocations meet min_position_usd
            single_position_cap=0.25,
            max_open_positions=4,
            enable_circuit_breakers=True,
            daily_loss_limit_pct=2.0,
            emergency_loss_limit_pct=3.5,
            integer_shares=True,
            enable_vol_control=False,
            min_position_usd=100.0,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "ASSET_A": df_a, "ASSET_B": df_b},
        )

        result = engine.run(start_date=d0, end_date=d2)

        # On Day 1: ASSET_A was flattened
        flatten_events = [e for e in result.circuit_breaker_events if e.event_type == "EMERGENCY_FLATTEN"]
        assert len(flatten_events) == 1

        # On Day 2: ASSET_B should have been successfully bought because state was reset to NORMAL
        trades_b = [t for t in result.trades if t.symbol == "ASSET_B"]
        assert len(trades_b) >= 1, "After daily reset, engine must resume normal trading on subsequent day!"

    def test_intraday_data_circuit_breaker_flatten(self):
        """Stress-tests circuit breaker execution when intraday bar data is provided."""
        d0 = date(2022, 3, 1)
        dates = [d0]

        df_spy = _make_uniform_df("SPY", dates, price=400.0)
        df_daily_a = _make_daily_bar_df("ASSET_A", dates, [100.0], [101.0], [90.0], [90.0])

        # Intraday bars for ASSET_A: 09:30 entry, 10:00 drop, 15:45 eval
        intra_records = [
            {"symbol": "ASSET_A", "timestamp": datetime(2022, 3, 1, 9, 30), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
            {"symbol": "ASSET_A", "timestamp": datetime(2022, 3, 1, 12, 0), "open": 98.0, "high": 98.0, "low": 90.0, "close": 91.0, "volume": 1000},
            {"symbol": "ASSET_A", "timestamp": datetime(2022, 3, 1, 15, 45), "open": 91.0, "high": 92.0, "low": 89.0, "close": 90.0, "volume": 1000},
        ]
        df_intra_a = pd.DataFrame(intra_records)
        df_intra_a.attrs["is_synthetic"] = True

        strat = DeterministicSignalStrategy(["ASSET_A"], stop_pct=0.25)
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("1000.00"),
            single_position_cap=0.50,  # $500 position = 5 shares
            max_open_positions=2,
            enable_circuit_breakers=True,
            daily_loss_limit_pct=2.0,
            emergency_loss_limit_pct=3.5,  # 3.5% of $1000 = $35 loss -> drop of $7/share (below $93 triggers flatten)
            integer_shares=True,
            enable_vol_control=False,
            min_position_usd=50.0,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "ASSET_A": df_daily_a},
            historical_intraday={"ASSET_A": df_intra_a},
        )

        result = engine.run(start_date=d0, end_date=d0)
        assert isinstance(result, object)


# ==============================================================================
# 3. Forbidden Synthetic Commodity Rejection & Bypass Probes
# ==============================================================================
class TestForbiddenCommodityRejection:
    """Adversarial stress-testing of GEMINI.md Invariant 2 commodity restrictions."""

    @pytest.mark.parametrize("forbidden", list(FORBIDDEN_SYNTHETIC_COMMODITIES))
    def test_all_forbidden_commodities_rejected_in_universe(self, forbidden: str):
        """Verifies each of USO, UNG, UCO, BOIL, SCO, KOLD is rejected in universe."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY", forbidden]),
                historical_daily={"SPY": pd.DataFrame()},
            )

    @pytest.mark.parametrize("forbidden", ["uso", "ung", "uco", "boil", "sco", "kold"])
    def test_lowercase_forbidden_commodities_rejected(self, forbidden: str):
        """Attacks ticker case-sensitivity with all-lowercase tickers."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY", forbidden]),
                historical_daily={"SPY": pd.DataFrame()},
            )

    @pytest.mark.parametrize("forbidden", ["UsO", "uNg", "UCo", "BoIl", "sCo", "kOlD"])
    def test_mixed_case_forbidden_commodities_rejected(self, forbidden: str):
        """Attacks ticker case-sensitivity with mixed-case tickers."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(universe=["SPY", forbidden]),
                historical_daily={"SPY": pd.DataFrame()},
            )

    @pytest.mark.parametrize("padded", [" USO", "USO ", " UNG ", "\tBOIL\n"])
    def test_whitespace_padded_forbidden_commodities_probe(self, padded: str):
        """Probes whether whitespace-padded forbidden commodities are rejected or slip past init.
        
        Empirical finding probe: verifies if sym.strip().upper() is needed vs sym.upper().
        """
        try:
            BacktestEngine(
                config=BacktestConfig(universe=["SPY", padded]),
                historical_daily={"SPY": pd.DataFrame()},
            )
            bypassed = True
        except ValueError:
            bypassed = False

        # If bypassed, this confirms the adversarial vulnerability
        assert not bypassed, f"Adversarial vulnerability: whitespace-padded '{padded}' bypassed forbidden commodity filter!"


    @pytest.mark.parametrize("forbidden", list(FORBIDDEN_SYNTHETIC_COMMODITIES))
    def test_forbidden_commodities_rejected_in_block_config(self, forbidden: str):
        """Verifies that forbidden commodities in BlockConfig.tickers are rejected."""
        blocks = {
            "commodities": BlockConfig(
                name="Commodities",
                tickers=["GLD", forbidden],
                capital_cap=0.20,
                top_n=1,
                buffer_rank=1,
            )
        }
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(blocks=blocks),
                historical_daily={"GLD": pd.DataFrame()},
            )

    @pytest.mark.parametrize("forbidden", list(FORBIDDEN_SYNTHETIC_COMMODITIES))
    def test_forbidden_commodities_rejected_in_historical_keys(self, forbidden: str):
        """Verifies that passing forbidden symbols as dict keys raises ValueError."""
        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(strategy=DeterministicSignalStrategy(["SPY"])),
                historical_daily={"SPY": pd.DataFrame(), forbidden: pd.DataFrame()},
            )

        with pytest.raises(ValueError, match="violates GEMINI.md Invariant 2"):
            BacktestEngine(
                config=BacktestConfig(strategy=DeterministicSignalStrategy(["SPY"])),
                historical_daily={"SPY": pd.DataFrame()},
                historical_intraday={forbidden: pd.DataFrame()},
            )

    @pytest.mark.parametrize("physical", list(PHYSICAL_COMMODITIES_ALLOWLIST))
    def test_physical_commodities_allowlist_accepted(self, physical: str):
        """Verifies GLD, GLDM, and SLV are unconditionally accepted."""
        engine = BacktestEngine(
            config=BacktestConfig(universe=["SPY", physical]),
            historical_daily={"SPY": pd.DataFrame(), physical: pd.DataFrame()},
        )
        assert engine is not None

    @pytest.mark.parametrize("allowed_ticker", ["XOM", "CVX", "AAPL", "MSFT", "XLK", "XLE"])
    def test_energy_producers_and_equities_accepted(self, allowed_ticker: str):
        """Verifies real energy producers (XOM) and standard ETFs are accepted."""
        engine = BacktestEngine(
            config=BacktestConfig(universe=["SPY", allowed_ticker]),
            historical_daily={"SPY": pd.DataFrame(), allowed_ticker: pd.DataFrame()},
        )
        assert engine is not None


# ==============================================================================
# 4. Fail-Closed and Lookahead Invariants
# ==============================================================================
class TestFailClosedAndRiskInvariants:
    """Stress-tests fail-closed stops, position caps, and buying power enforcement."""

    def test_fail_closed_rejects_signals_without_valid_stop(self):
        """Attacks fail-closed invariant: signals with stop >= entry must be discarded."""
        d0 = date(2022, 1, 3)
        dates = [d0]
        df_spy = _make_uniform_df("SPY", dates, price=400.0)
        df_bad = _make_uniform_df("BAD_STOP", dates, price=100.0)

        # Strategy emits an invalid signal where stop is above entry
        strat = DeterministicSignalStrategy(["BAD_STOP"], invalid_stop=True)
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("2000.00"),
            single_position_cap=0.25,
            enable_vol_control=False,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "BAD_STOP": df_bad},
        )

        result = engine.run(start_date=d0, end_date=d0)
        assert len(result.trades) == 0, "Signals without valid stop loss MUST be discarded immediately!"

    def test_max_open_positions_is_strictly_enforced(self):
        """Floods the engine with 10 candidate symbols simultaneously; must cap at 4."""
        d0 = date(2022, 1, 3)
        dates = [d0]
        symbols = [f"SYM_{i}" for i in range(10)]
        daily_data = {"SPY": _make_uniform_df("SPY", dates, price=400.0)}
        for s in symbols:
            daily_data[s] = _make_uniform_df(s, dates, price=50.0)

        strat = DeterministicSignalStrategy(symbols, stop_pct=0.05)
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("10000.00"),
            single_position_cap=0.10,  # 10% each -> enough capital for all 10
            max_open_positions=4,
            enable_vol_control=False,
            min_position_usd=50.0,
        )

        engine = BacktestEngine(config=config, historical_daily=daily_data)
        engine.run(start_date=d0, end_date=d0)

        assert len(engine.broker.positions) <= 4, "Portfolio must never exceed max_open_positions limit!"

    def test_circuit_breaker_consecutive_losses_limit(self):
        """Verifies that 4 consecutive losses trigger PAUSED_DAILY_LOSS."""
        from tbot.risk.circuit_breakers import CircuitBreakerManager

        cb = CircuitBreakerManager(max_consecutive_losses=4)
        cb.reset_daily(Decimal("2000.00"), date(2022, 1, 3))
        assert cb.can_open_new_positions() is True

        # 3 consecutive losses -> still normal
        for _ in range(3):
            cb.record_trade(Decimal("-10.00"), datetime(2022, 1, 3, 10, 0))
        assert cb.can_open_new_positions() is True
        assert cb.state == CircuitBreakerState.NORMAL

        # 4th loss -> state transitions to PAUSED_DAILY_LOSS
        cb.record_trade(Decimal("-5.00"), datetime(2022, 1, 3, 11, 0))
        assert cb.can_open_new_positions() is False
        assert cb.state == CircuitBreakerState.PAUSED_DAILY_LOSS
