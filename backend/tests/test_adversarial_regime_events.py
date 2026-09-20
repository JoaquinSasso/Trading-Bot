"""Adversarial stress tests for Milestone 2: Regime & Event Fail-Closed behaviors.

Designed and executed by Challenger 2 (teamwork_preview_challenger).
Empirically tests and challenges:
1. Regime classification edge cases:
   - Exactly 199 bars vs 200 bars
   - Flat prices (0 vol) and constant return series
   - Non-positive prices (zero, negative, partial, full)
   - Single NaN inside the series (inside calculation window vs historical)
   - Extreme volatility spikes (10x, 100x price jumps, 99% crash)
2. Finnhub outage simulation:
   - Cache age exactly 71.99 hours (CACHE_VALID) vs 72.01 hours (EARNINGS_DATA_UNAVAILABLE)
   - Facade integration with is_symbol_blocked
3. Finnhub rate limiter stress:
   - Burst 100 concurrent requests ensuring no hangs, deadlocks, or rate violations
4. MacroFilter blackout boundary:
   - Exactly 30 minutes before, 1 second before 30m, at event time, 30m after, 30m + 1 second after
5. FOMC swing lockout vs intraday strategy:
   - Swing blocked all day (FOMC_ALL_DAY_SWING_BLOCK)
   - Intraday only blocked during the 30-minute symmetric window
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.events.calendar import (
    AsyncRateLimiter,
    EventCalendar,
    FinnhubEarningsClient,
    MacroFilter,
)
from tbot.regime.filter import MarketRegime, RegimeFilter

ET_TIMEZONE = ZoneInfo("America/New_York")


# ===========================================================================
# 1. Regime Filter Adversarial Stress Tests
# ===========================================================================


class TestRegimeFilterAdversarialEdgeCases:
    """Stress tests for RegimeFilter edge cases."""

    def test_regime_exactly_199_bars_boundary(self, sim_clock: SimulatedClock) -> None:
        """199 bars (< 200) must fail-closed to UNKNOWN with is_blocked=True."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(199)]
        prices = [500.0 + i * 0.1 for i in range(199)]
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["bar_count"] == 199
        assert "INSUFFICIENT_BARS" in snapshot.inputs["reason"]

    def test_regime_exactly_200_bars_boundary(self, sim_clock: SimulatedClock) -> None:
        """200 bars (exact threshold) must calculate SMA(200) and allow entry."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(200)]
        prices = [500.0 + i * 0.1 for i in range(200)]
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime in (MarketRegime.BULL_CALM.value, MarketRegime.BULL_VOLATILE.value)
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["bar_count"] == 200
        assert snapshot.inputs["reason"] == "OK"

    def test_regime_flat_prices_zero_volatility(self, sim_clock: SimulatedClock) -> None:
        """Flat prices (0 realized volatility) must evaluate gracefully without math errors.

        Because close == SMA(200), close <= SMA evaluates to BEAR.
        Vol is 0.0, p70 is 0.0. No ZeroDivisionError or NaN crash.
        """
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(200)]
        prices = [500.0] * 200
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BEAR.value
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["realized_vol_20d"] == 0.0
        assert snapshot.inputs["vol_70th_percentile"] == 0.0
        assert snapshot.inputs["spy_close"] == snapshot.inputs["sma_200"]

    def test_regime_constant_percentage_returns_zero_volatility(self, sim_clock: SimulatedClock) -> None:
        """Constant percentage returns (close > SMA200, 0 vol std) evaluate gracefully."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(250)]
        prices = [500.0 * (1.001 ** i) for i in range(250)]
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["realized_vol_20d"] == 0.0
        assert snapshot.inputs["vol_70th_percentile"] == 0.0
        # By default inclusive_volatile=True -> vol >= p70 (0 >= 0) -> BULL_VOLATILE
        assert snapshot.regime == MarketRegime.BULL_VOLATILE.value

    @pytest.mark.parametrize(
        ("case_name", "price_mutator"),
        [
            ("single_zero_at_middle", lambda p: [0.0 if i == 100 else v for i, v in enumerate(p)]),
            ("single_zero_at_start", lambda p: [0.0 if i == 0 else v for i, v in enumerate(p)]),
            ("single_zero_at_end", lambda p: [0.0 if i == 199 else v for i, v in enumerate(p)]),
            ("single_negative_at_middle", lambda p: [-10.0 if i == 100 else v for i, v in enumerate(p)]),
            ("single_negative_at_start", lambda p: [-500.0 if i == 0 else v for i, v in enumerate(p)]),
            ("all_zeros", lambda p: [0.0] * len(p)),
            ("all_negatives", lambda p: [-100.0] * len(p)),
        ],
    )
    def test_regime_non_positive_prices_fail_closed(
        self, sim_clock: SimulatedClock, case_name: str, price_mutator: Any
    ) -> None:
        """Any zero or negative price anywhere in the series must fail-closed to UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(200)]
        base_prices = [500.0 + i * 0.1 for i in range(200)]
        prices = price_mutator(base_prices)
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["reason"] == "NON_POSITIVE_PRICE_DETECTED"

    @pytest.mark.parametrize("nan_index", [0, 50, 100, 150, 180, 198, 199])
    def test_regime_single_nan_inside_200_bars_fails_closed(
        self, sim_clock: SimulatedClock, nan_index: int
    ) -> None:
        """In a 200-bar series, a single NaN at any index must trigger fail-closed UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(200)]
        prices = [500.0 + i * 0.1 for i in range(200)]
        prices[nan_index] = np.nan
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["reason"] in ("LATEST_CLOSE_IS_NAN", "SMA_CALCULATION_NAN")

    def test_regime_single_nan_inside_active_sma_window_250_bars(self, sim_clock: SimulatedClock) -> None:
        """In 250 bars, a NaN inside the last 200 bars (e.g. index 100) triggers SMA_CALCULATION_NAN."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(250)]
        prices = [500.0 + i * 0.1 for i in range(250)]
        prices[100] = np.nan  # Index 100 is within bars 50..249 (the active 200-bar window)
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["reason"] == "SMA_CALCULATION_NAN"

    def test_regime_single_nan_outside_active_sma_window_in_long_series(
        self, sim_clock: SimulatedClock
    ) -> None:
        """In 250 bars, a NaN at index 10 (outside active 200-bar window) leaves indicators valid."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(250)]
        prices = [500.0 + i * 0.1 for i in range(250)]
        prices[10] = np.nan  # Index 10 is older than the last 200 bars (50..249)
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        # The last 200 bars for SMA200 and last 20 bars for realized vol are unaffected
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["reason"] == "OK"
        assert snapshot.regime in (MarketRegime.BULL_CALM.value, MarketRegime.BULL_VOLATILE.value)

    @pytest.mark.parametrize("spike_factor", [10.0, 100.0])
    def test_regime_extreme_upward_volatility_spikes(
        self, sim_clock: SimulatedClock, spike_factor: float
    ) -> None:
        """Extreme price spikes (10x, 100x) classify stably into BULL_VOLATILE without overflow."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(250)]
        prices = [500.0 + i * 0.1 for i in range(250)]
        prices[-1] = 500.0 * spike_factor
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BULL_VOLATILE.value
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["realized_vol_20d"] > snapshot.inputs["vol_70th_percentile"]
        assert np.isfinite(snapshot.inputs["realized_vol_20d"])

    def test_regime_extreme_downward_crash_spike(self, sim_clock: SimulatedClock) -> None:
        """99% price crash (e.g. $500 -> $5) classifies into BEAR without error."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(250)]
        prices = [500.0 + i * 0.1 for i in range(250)]
        prices[-1] = 5.0
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": prices})

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BEAR.value
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["spy_close"] < snapshot.inputs["sma_200"]


# ===========================================================================
# 2. Finnhub Fail-Closed Outage & Cache Age Precision
# ===========================================================================


class TestFinnhubFailClosedOutageSimulation:
    """Rigorous boundary verification of Finnhub cache age at 71.99h vs 72.01h."""

    @pytest.mark.asyncio
    async def test_cache_age_71_99_hours_permits_trading(self, sim_clock: SimulatedClock) -> None:
        """Cache age of exactly 71.99 hours (< 72.0h) permits trading with CACHE_VALID."""
        client = FinnhubEarningsClient(clock=sim_clock)
        client.network_error = True  # Network is down

        # 71.99 hours old
        cached_at = sim_clock.now() - timedelta(hours=71.99)
        earnings_date = sim_clock.today() + timedelta(days=20)
        client.seed_earnings("AAPL", earnings_date, cached_at=cached_at)

        edate, is_blocked, reason = await client.get_earnings_date("AAPL")

        assert is_blocked is False
        assert reason == "CACHE_VALID"
        assert edate == earnings_date

    @pytest.mark.asyncio
    async def test_cache_age_72_01_hours_blocks_fail_closed(self, sim_clock: SimulatedClock) -> None:
        """Cache age of exactly 72.01 hours (> 72.0h) blocks trading with EARNINGS_DATA_UNAVAILABLE."""
        client = FinnhubEarningsClient(clock=sim_clock)
        client.network_error = True  # Network is down

        # 72.01 hours old
        cached_at = sim_clock.now() - timedelta(hours=72.01)
        earnings_date = sim_clock.today() + timedelta(days=20)
        client.seed_earnings("AAPL", earnings_date, cached_at=cached_at)

        edate, is_blocked, reason = await client.get_earnings_date("AAPL")

        assert is_blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"
        assert edate is None

    def test_event_calendar_facade_cache_boundary(self, sim_clock: SimulatedClock) -> None:
        """EventCalendar facade is_symbol_blocked adheres to 71.99h vs 72.01h boundary."""
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)
        cal.network_error = True
        earnings_date = sim_clock.today() + timedelta(days=15)

        # 1. 71.99 hours: permitted
        cal.seed_earnings("AAPL", earnings_date, cached_at=sim_clock.now() - timedelta(hours=71.99))
        blocked_71, reason_71 = cal.is_symbol_blocked("AAPL")
        assert blocked_71 is False
        assert reason_71 == "OK"

        # 2. 72.01 hours: blocked fail-closed
        cal.seed_earnings("AAPL", earnings_date, cached_at=sim_clock.now() - timedelta(hours=72.01))
        blocked_72, reason_72 = cal.is_symbol_blocked("AAPL")
        assert blocked_72 is True
        assert reason_72 == "EARNINGS_DATA_UNAVAILABLE"


# ===========================================================================
# 3. Finnhub Rate Limiter Stress Test (100 Concurrent Burst)
# ===========================================================================


class TestFinnhubRateLimiterStress:
    """Stress tests for AsyncRateLimiter with concurrent bursts."""

    @pytest.mark.asyncio
    async def test_burst_100_concurrent_requests_no_hangs(self) -> None:
        """Burst 100 concurrent tasks through AsyncRateLimiter without deadlocks or hangs."""
        # Limiter configured with 50 tokens bursting and fast refill (0.2s)
        limiter = AsyncRateLimiter(rate=50, per_seconds=0.2)
        t_start = time.time()
        completion_times: list[float] = []

        async def worker(idx: int) -> None:
            await limiter.acquire()
            completion_times.append(time.time() - t_start)

        tasks = [asyncio.create_task(worker(i)) for i in range(100)]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)

        total_elapsed = time.time() - t_start
        assert len(completion_times) == 100
        # The first 50 burst immediately (< 0.05s)
        assert completion_times[49] < 0.1
        # The next 50 are smoothed over time
        assert total_elapsed >= 0.15
        assert total_elapsed < 3.0  # Finished well within timeout, absolutely no hanging

    @pytest.mark.asyncio
    async def test_rate_limiter_strict_token_conservation(self) -> None:
        """Verifies that token refills match elapsed time accurately."""
        limiter = AsyncRateLimiter(rate=10, per_seconds=1.0)
        # Drain all 10 tokens
        for _ in range(10):
            await limiter.acquire()

        assert limiter.tokens < 1.0

        # Wait 0.5s -> should have refilled ~5 tokens
        await asyncio.sleep(0.5)
        # 5 acquisitions should proceed immediately without sleeping
        t0 = time.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.time() - t0
        assert elapsed < 0.05  # Instantaneous from refilled tokens


# ===========================================================================
# 4. MacroFilter Blackout Window Boundary Precision
# ===========================================================================


class TestMacroFilterBlackoutBoundariesPrecision:
    """Second-by-second boundary verification around the 30-minute macro blackout window."""

    @pytest.mark.parametrize(
        ("offset", "expected_blocked", "expected_reason"),
        [
            (timedelta(minutes=30, seconds=1), False, "OK"),                    # -30m 1s -> outside window
            (timedelta(minutes=30), True, "MACRO_WINDOW_HIGH"),                 # -30m 0s -> exact boundary (BLOCKED)
            (timedelta(minutes=29, seconds=59), True, "MACRO_WINDOW_HIGH"),     # -29m 59s -> 1s inside window (BLOCKED)
            (timedelta(seconds=0), True, "MACRO_WINDOW_HIGH"),                  # 0s -> event time (BLOCKED)
            (-timedelta(minutes=29, seconds=59), True, "MACRO_WINDOW_HIGH"),    # +29m 59s -> 1s inside window (BLOCKED)
            (-timedelta(minutes=30), True, "MACRO_WINDOW_HIGH"),                # +30m 0s -> exact boundary (BLOCKED)
            (-timedelta(minutes=30, seconds=1), False, "OK"),                   # +30m 1s -> outside window
        ],
    )
    def test_blackout_boundary_seconds_precision(
        self,
        sim_clock: SimulatedClock,
        offset: timedelta,
        expected_blocked: bool,
        expected_reason: str,
    ) -> None:
        """Tests boundaries: -30m-1s, -30m, -29m59s, event time, +29m59s, +30m, +30m+1s."""
        mf = MacroFilter(clock=sim_clock, yaml_path=None, macro_block_minutes=30)
        event_time = datetime(2026, 9, 15, 12, 30, 0, tzinfo=UTC)
        mf.load_events([
            {"name": "CPI Inflation", "type": "CPI", "timestamp": event_time, "impact": "high"}
        ])

        query_time = event_time - offset
        blocked, reason = mf.is_macro_window_active(query_time, strategy_type="intraday")

        assert blocked == expected_blocked
        assert reason == expected_reason


# ===========================================================================
# 5. FOMC All-Day Swing Lockout vs Intraday Window
# ===========================================================================


class TestFOMCLockoutSwingVsIntraday:
    """Full-day timeline test: FOMC day blocks swing strategy all day, intraday only during 30m window."""

    def test_fomc_full_day_timeline(self, sim_clock: SimulatedClock) -> None:
        mf = MacroFilter(clock=sim_clock, yaml_path=None, macro_block_minutes=30)
        # FOMC meeting at 14:00 ET (18:00 UTC) on 2026-09-16
        fomc_utc = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)
        mf.load_events([
            {
                "name": "FOMC Rate Decision and Press Conference",
                "type": "FOMC",
                "timestamp": fomc_utc,
                "impact": "critical",
            }
        ])

        timeline = [
            ("00:01 ET (midnight start)", datetime(2026, 9, 16, 0, 1, 0, tzinfo=ET_TIMEZONE), True, False),
            ("08:00 ET (pre-market)", datetime(2026, 9, 16, 8, 0, 0, tzinfo=ET_TIMEZONE), True, False),
            ("09:30 ET (market open)", datetime(2026, 9, 16, 9, 30, 0, tzinfo=ET_TIMEZONE), True, False),
            ("12:00 ET (midday)", datetime(2026, 9, 16, 12, 0, 0, tzinfo=ET_TIMEZONE), True, False),
            ("13:29:59 ET (1s before 30m window)", datetime(2026, 9, 16, 13, 29, 59, tzinfo=ET_TIMEZONE), True, False),
            ("13:30:00 ET (start of 30m window)", datetime(2026, 9, 16, 13, 30, 0, tzinfo=ET_TIMEZONE), True, True),
            ("14:00:00 ET (FOMC announcement)", datetime(2026, 9, 16, 14, 0, 0, tzinfo=ET_TIMEZONE), True, True),
            ("14:30:00 ET (end of 30m window)", datetime(2026, 9, 16, 14, 30, 0, tzinfo=ET_TIMEZONE), True, True),
            ("14:30:01 ET (1s after 30m window)", datetime(2026, 9, 16, 14, 30, 1, tzinfo=ET_TIMEZONE), True, False),
            ("15:50:00 ET (late afternoon)", datetime(2026, 9, 16, 15, 50, 0, tzinfo=ET_TIMEZONE), True, False),
            ("16:00:00 ET (market close)", datetime(2026, 9, 16, 16, 0, 0, tzinfo=ET_TIMEZONE), True, False),
            ("23:59:00 ET (end of FOMC day)", datetime(2026, 9, 16, 23, 59, 0, tzinfo=ET_TIMEZONE), True, False),
        ]

        for desc, dt_et, exp_swing_blocked, exp_intra_blocked in timeline:
            dt_utc = dt_et.astimezone(UTC)
            s_blocked, s_reason = mf.is_macro_window_active(dt_utc, strategy_type="swing")
            i_blocked, i_reason = mf.is_macro_window_active(dt_utc, strategy_type="intraday")

            assert s_blocked == exp_swing_blocked, f"Swing failed at {desc}"
            if exp_swing_blocked:
                assert s_reason == "FOMC_ALL_DAY_SWING_BLOCK", f"Swing reason mismatch at {desc}: {s_reason}"

            assert i_blocked == exp_intra_blocked, f"Intraday failed at {desc}"
            if exp_intra_blocked:
                assert s_reason == "FOMC_ALL_DAY_SWING_BLOCK"
                assert i_reason == "MACRO_WINDOW_CRITICAL"
            else:
                assert i_reason == "OK"

    def test_fomc_adjacent_days_unaffected(self, sim_clock: SimulatedClock) -> None:
        """The day before and day after FOMC are unaffected for swing strategies."""
        mf = MacroFilter(clock=sim_clock, yaml_path=None, macro_block_minutes=30)
        fomc_utc = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)
        mf.load_events([
            {"name": "FOMC", "type": "FOMC", "timestamp": fomc_utc, "impact": "critical"}
        ])

        # Day before at 14:00 ET
        day_before = datetime(2026, 9, 15, 14, 0, 0, tzinfo=ET_TIMEZONE).astimezone(UTC)
        b_prev, r_prev = mf.is_macro_window_active(day_before, strategy_type="swing")
        assert b_prev is False
        assert r_prev == "OK"

        # Day after at 14:00 ET
        day_after = datetime(2026, 9, 17, 14, 0, 0, tzinfo=ET_TIMEZONE).astimezone(UTC)
        b_next, r_next = mf.is_macro_window_active(day_after, strategy_type="swing")
        assert b_next is False
        assert r_next == "OK"
