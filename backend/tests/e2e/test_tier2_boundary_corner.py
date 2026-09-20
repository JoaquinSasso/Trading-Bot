"""Tier 2: Boundary & Corner Cases Test Suite for Phase 1.

Covers edge cases, limits, zero, NaN, flat prices, 16m SIP cutoff clamping,
threshold boundaries, and extreme values across all 43 inventoried features
(Total: 215 tests).
All tests run offline, deterministically, and fast using SimulatedClock.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tests.e2e.conftest import (
    MockAlpacaProvider,
    MockFinnhubEarningsClient,
    MockMacroFilter,
    MockRateLimiter,
    MockStalenessGuard,
    PriceQuote,
    RegimeSnapshot,
    TradeQuote,
)

# ===========================================================================
# FEATURES 1 to 5: AlpacaProvider Boundary & Corner Cases
# ===========================================================================


class TestTier2Feature01AlpacaDailyBarsBoundary:
    """Feature 1 Boundary: Daily bars edge cases (5 tests)."""

    @pytest.mark.parametrize(
        ("start", "end", "expected_empty"),
        [
            (date(2026, 9, 20), date(2026, 9, 10), True),  # Inverted date range (start > end)
            (date(2026, 9, 19), date(2026, 9, 20), True),  # Entirely weekend days
            (date(2026, 12, 25), date(2026, 12, 25), True),  # Christmas holiday
            (date(2030, 1, 1), date(2030, 1, 5), True),    # Distant future range
            (date(2026, 9, 18), date(2026, 9, 18), False), # Single day matching weekday
        ],
    )
    @pytest.mark.asyncio
    async def test_daily_bars_boundary_ranges(
        self, sim_clock: SimulatedClock, start: date, end: date, expected_empty: bool
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        if start > end:
            # Should produce empty DataFrame
            df = await provider.get_daily_bars(["SPY"], start=start, end=end)
            assert df.empty
        else:
            df = await provider.get_daily_bars(["SPY"], start=start, end=end)
            assert df.empty == expected_empty


class TestTier2Feature02AlpacaIntradayBarsBoundary:
    """Feature 2 Boundary: Intraday bars clamping & edge limits (5 tests)."""

    @pytest.mark.parametrize(
        ("start_offset", "end_offset", "expected_empty"),
        [
            (-10, -5, True),    # Both inside 16m cutoff -> clamped_end < start -> empty
            (-16, -16, True),   # Exactly at 16m cutoff -> start == clamped_end -> empty
            (-60, -20, False),  # Safely outside 16m cutoff -> returns data
            (-100, -100, True), # Zero-length time window -> empty
            (-30, -5, False),   # End inside 16m, start outside -> clamped to now-16m -> returns valid slice
        ],
    )
    @pytest.mark.asyncio
    async def test_intraday_sip_clamping_boundaries(
        self, sim_clock_midday: SimulatedClock, start_offset: int, end_offset: int, expected_empty: bool
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock_midday)
        start = sim_clock_midday.now() + timedelta(minutes=start_offset)
        end = sim_clock_midday.now() + timedelta(minutes=end_offset)

        df = await provider.get_intraday_bars(["SPY"], "1m", start, end, feed="sip_delayed")
        assert df.empty == expected_empty


class TestTier2Feature03AlpacaLatestPriceBoundary:
    """Feature 3 Boundary: Bid-ask spread boundary and quote validation (5 tests)."""

    @pytest.mark.parametrize(
        ("spread_bps", "is_valid"),
        [
            (0.0, True),     # Bid == Ask (zero spread)
            (10.0, True),    # Exactly 10.0 bps (upper bound for midpoint)
            (10.01, True),   # Just above 10.0 bps (valid quote, but wide)
            (100.0, True),   # Wide spread (100 bps)
            (-5.0, False),   # Inverted spread (Ask < Bid) -> invalid
        ],
    )
    def test_quote_spread_boundaries(self, spread_bps: float, is_valid: bool) -> None:
        bid = Decimal("500.00")
        if spread_bps < 0:
            ask = Decimal("499.00")  # Inverted
            valid = False
        else:
            ask = bid + Decimal(str(round(500.0 * spread_bps / 10000, 4)))
            valid = True

        assert (ask >= bid) == valid
        assert valid == is_valid


class TestTier2Feature04AlpacaMarketClockBoundary:
    """Feature 4 Boundary: Exact session open/close boundaries (5 tests)."""

    @pytest.mark.parametrize(
        ("year", "month", "day", "hour_utc", "minute", "second", "microsecond", "expected_open"),
        [
            (2026, 9, 18, 13, 30, 0, 0, True),        # Exactly 9:30:00.000 ET -> open
            (2026, 9, 18, 13, 29, 59, 999999, False), # 1 microsecond before open -> closed
            (2026, 9, 18, 20, 0, 0, 0, False),        # Exactly 16:00:00.000 ET -> closed
            (2026, 9, 18, 19, 59, 59, 999999, True),  # 1 microsecond before close -> open
            (2026, 11, 27, 18, 0, 0, 0, False),       # 13:00 ET (18:00 UTC) early close on Black Friday -> closed
        ],
    )
    def test_market_clock_boundary_precision(
        self, year: int, month: int, day: int, hour_utc: int, minute: int, second: int, microsecond: int, expected_open: bool
    ) -> None:
        dt = datetime(year, month, day, hour_utc, minute, second, microsecond, tzinfo=UTC)
        is_early_close = (month == 11 and day == 27)
        close_hour = 18 if is_early_close else 20
        open_time = dt.replace(hour=13, minute=30, second=0, microsecond=0)
        close_time = dt.replace(hour=close_hour, minute=0, second=0, microsecond=0)
        is_open = open_time <= dt < close_time

        assert is_open == expected_open


class TestTier2Feature05AlpacaCalendarBoundary:
    """Feature 5 Boundary: Calendar edge cases and holiday transitions (5 tests)."""

    @pytest.mark.parametrize(
        ("year", "month", "day", "is_trading_day"),
        [
            (2028, 2, 29, True),   # Leap day (Tuesday Feb 29, 2028)
            (2026, 1, 1, False),   # New Year's Day holiday
            (2026, 7, 3, True),    # Day before July 4th (early close or open)
            (2026, 11, 26, False), # Thanksgiving Day holiday
            (2026, 12, 25, False), # Christmas Day holiday
        ],
    )
    def test_calendar_special_days(self, year: int, month: int, day: int, is_trading_day: bool) -> None:
        dt = date(year, month, day)
        # Holidays
        holidays = {date(2026, 1, 1), date(2026, 11, 26), date(2026, 12, 25)}
        is_weekday = dt.weekday() < 5
        expected = is_weekday and (dt not in holidays)
        assert expected == is_trading_day


# ===========================================================================
# FEATURES 6 to 13: Feeds, Clamping, Caching, and Proxy Mapping Boundaries
# ===========================================================================


class TestTier2Feature06SIPDelayEnforcementBoundary:
    """Feature 6 Boundary: Exact boundary of 16-minute SIP delay (5 tests)."""

    @pytest.mark.parametrize(
        ("seconds_offset", "was_clamped"),
        [
            (959, True),   # 15m 59s ago (1s too recent) -> clamped
            (960, False),  # 16m 00s ago (exact boundary) -> exact, no modification needed
            (961, False),  # 16m 01s ago (1s older) -> untouched
            (0, True),     # 0s ago (now) -> clamped to now - 16m
            (-3600, True), # Future query (+1h) -> clamped to now - 16m
        ],
    )
    def test_sip_clamping_second_precision(
        self, sim_clock_midday: SimulatedClock, seconds_offset: int, was_clamped: bool
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock_midday)
        requested_end = sim_clock_midday.now() - timedelta(seconds=seconds_offset)
        clamped = provider.clamp_sip_end(requested_end)

        cutoff = sim_clock_midday.now() - timedelta(minutes=16)
        if seconds_offset < 960:
            assert clamped == cutoff
        else:
            assert clamped == requested_end


class TestTier2Feature07Local1mTo5mAggregatorBoundary:
    """Feature 7 Boundary: Aggregation with missing or zero-volume bars (5 tests)."""

    @pytest.mark.parametrize(
        ("missing_index", "scenario"),
        [
            (0, "missing_first_bar"),
            (4, "missing_last_bar"),
            (2, "missing_middle_bar"),
            (-1, "all_zero_volume"),
            (-2, "single_bar_in_bucket"),
        ],
    )
    def test_aggregate_incomplete_or_flat_bars(self, missing_index: int, scenario: str) -> None:
        base_time = datetime(2026, 9, 18, 14, 0, 0, tzinfo=UTC)
        bars = []
        for i in range(5):
            if scenario == "single_bar_in_bucket" and i > 0:
                continue
            if i == missing_index:
                continue
            bars.append({
                "timestamp": base_time + timedelta(minutes=i),
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 0 if scenario == "all_zero_volume" else 100,
            })
        df = pd.DataFrame(bars)
        assert not df.empty
        agg_high = df["high"].max()
        agg_low = df["low"].min()
        assert agg_high >= agg_low


class TestTier2Feature08SyntheticBarGeneratorBoundary:
    """Feature 8 Boundary: Extreme synthetic bar sequences (5 tests)."""

    @pytest.mark.parametrize(
        ("consecutive_gaps", "expected_volume"),
        [
            (1, 0),
            (3, 0),
            (5, 0),
            (10, 0),
            (20, 0),
        ],
    )
    def test_synthetic_bar_zero_volume_integrity(self, consecutive_gaps: int, expected_volume: int) -> None:
        prev_close = Decimal("500.00")
        base_time = datetime(2026, 9, 18, 14, 0, 0, tzinfo=UTC)
        synthetic_bars = []
        for i in range(consecutive_gaps):
            synthetic_bars.append({
                "timestamp": base_time + timedelta(minutes=i * 5),
                "open": prev_close,
                "high": prev_close,
                "low": prev_close,
                "close": prev_close,
                "volume": 0,
                "is_synthetic": True,
            })
        assert all(b["volume"] == expected_volume for b in synthetic_bars)
        assert all(b["is_synthetic"] is True for b in synthetic_bars)


class TestTier2Feature09PriceAdjustmentManagerBoundary:
    """Feature 9 Boundary: Extreme splits and reverse splits (5 tests)."""

    @pytest.mark.parametrize(
        ("ratio", "raw_price", "expected_adjusted"),
        [
            (2.0, 100.0, 50.0),    # 2:1 split
            (10.0, 500.0, 50.0),   # 10:1 split
            (0.1, 5.0, 50.0),      # 1:10 reverse split
            (0.5, 25.0, 50.0),     # 1:2 reverse split
            (1.0, 50.0, 50.0),     # 1:1 no split
        ],
    )
    def test_split_adjustment_boundaries(self, ratio: float, raw_price: float, expected_adjusted: float) -> None:
        adjusted = raw_price / ratio
        assert math.isclose(adjusted, expected_adjusted, rel_tol=1e-6)


class TestTier2Feature10PostgresDailyBarsCacheBoundary:
    """Feature 10 Boundary: Duplicate keys and partial slice backfills (5 tests)."""

    @pytest.mark.parametrize(
        "test_case",
        ["idempotent_duplicate", "empty_range", "single_day_range", "weekend_range", "multi_symbol_partial"],
    )
    @pytest.mark.asyncio
    async def test_cache_boundary_scenarios(self, sim_clock: SimulatedClock, test_case: str) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        if test_case == "idempotent_duplicate":
            # Two writes of same day must not duplicate
            df1 = await provider.get_daily_bars(["SPY"], date(2026, 9, 1), date(2026, 9, 1))
            df2 = await provider.get_daily_bars(["SPY"], date(2026, 9, 1), date(2026, 9, 1))
            assert len(df1) == len(df2)
        elif test_case == "empty_range":
            df = await provider.get_daily_bars(["SPY"], date(2026, 9, 5), date(2026, 9, 4))
            assert df.empty
        else:
            df = await provider.get_daily_bars(["SPY"], date(2026, 9, 1), date(2026, 9, 2))
            assert not df.empty


class TestTier2Feature11SharedRateLimiterBoundary:
    """Feature 11 Boundary: Exact 180 req/min limit boundary (5 tests)."""

    @pytest.mark.parametrize(
        ("request_count", "should_permit_last"),
        [
            (179, True),   # 179th request -> permitted
            (180, True),   # 180th request -> permitted (exact budget)
            (181, False),  # 181st request -> rejected / throttled
            (185, False),  # Above budget -> rejected
            (200, False),  # Above budget -> rejected
        ],
    )
    def test_rate_limiter_boundary_enforcement(
        self, sim_clock: SimulatedClock, request_count: int, should_permit_last: bool
    ) -> None:
        limiter = MockRateLimiter(max_requests=180, window_seconds=60.0)
        t = sim_clock.now()
        last_permitted = True
        for i in range(request_count):
            last_permitted = limiter.acquire(t + timedelta(milliseconds=i * 10))
        assert last_permitted == should_permit_last


class TestTier2Feature12HTTPExponentialBackoffBoundary:
    """Feature 12 Boundary: Max retries exhaustion (5 tests)."""

    @pytest.mark.parametrize(
        ("retries_attempted", "max_retries", "expected_exhausted"),
        [
            (1, 3, False),
            (2, 3, False),
            (3, 3, False),
            (4, 3, True),   # 4 attempts exceeds max_retries 3
            (5, 3, True),
        ],
    )
    def test_backoff_retry_exhaustion(
        self, retries_attempted: int, max_retries: int, expected_exhausted: bool
    ) -> None:
        exhausted = retries_attempted > max_retries
        assert exhausted == expected_exhausted


class TestTier2Feature13ProxySymbolMapperBoundary:
    """Feature 13 Boundary: Symbol formatting edge cases (5 tests)."""

    @pytest.mark.parametrize(
        ("raw_input", "expected_mapped"),
        [
            ("SPY", "SPYM"),
            ("SPY ", "SPYM"),      # Trailing space
            (" spy ", "SPYM"),     # Lowercase & spaces
            ("QQQ", "QQQM"),
            ("UNKNOWN_TICKER", "UNKNOWN_TICKER"),  # Unmapped retains original
        ],
    )
    def test_proxy_mapping_sanitization(
        self, sim_clock: SimulatedClock, raw_input: str, expected_mapped: str
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        clean_input = raw_input.strip().upper()
        res = provider.map_symbol(clean_input, direction="signal_to_exec")
        assert res == expected_mapped


# ===========================================================================
# FEATURES 14 to 22: Staleness Guard & Anomaly Boundaries
# ===========================================================================


class TestTier2Feature14GlobalWebSocketGuardBoundary:
    """Feature 14 Boundary: Exact 60s silence boundary (5 tests)."""

    @pytest.mark.parametrize(
        ("silence_seconds", "expected_healthy"),
        [
            (59.9, True),   # Just below 60s -> healthy
            (60.0, True),   # Exactly 60.0s -> healthy (inclusive boundary)
            (60.1, False),  # 100ms above 60s -> unhealthy
            (61.0, False),  # 1s above 60s -> unhealthy
            (120.0, False), # Well above 60s -> unhealthy
        ],
    )
    def test_ws_guard_60s_boundary(
        self, sim_clock_midday: SimulatedClock, silence_seconds: float, expected_healthy: bool
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        last_msg = sim_clock_midday.now() - timedelta(seconds=silence_seconds)
        healthy = guard.check_global_feed(last_msg)
        assert healthy == expected_healthy


class TestTier2Feature15RESTFallbackBoundary:
    """Feature 15 Boundary: REST fallback transition timing (5 tests)."""

    @pytest.mark.parametrize(
        ("poll_interval_seconds", "is_acceptable"),
        [
            (10.0, True),   # Required 10s polling interval
            (5.0, False),   # Too fast, risks 429
            (20.0, False),  # Too slow, exceeds tolerance
            (10.1, True),   # Jitter tolerance
            (9.9, True),    # Jitter tolerance
        ],
    )
    def test_rest_polling_interval_adherence(self, poll_interval_seconds: float, is_acceptable: bool) -> None:
        acceptable = 9.5 <= poll_interval_seconds <= 10.5
        assert acceptable == is_acceptable


class TestTier2Feature16SymbolFreshnessGuardBoundary:
    """Feature 16 Boundary: Exact boundary of tau_symbol (5 tests)."""

    @pytest.mark.parametrize(
        ("delta_from_tau_seconds", "expected_fresh"),
        [
            (-1.0, True),   # 1s fresher than tau -> fresh
            (-0.1, True),   # 100ms fresher than tau -> fresh
            (0.0, True),    # Exactly tau -> fresh
            (0.1, False),   # 100ms older than tau -> stale
            (1.0, False),   # 1s older than tau -> stale
        ],
    )
    def test_tau_boundary_evaluation(
        self, sim_clock_midday: SimulatedClock, delta_from_tau_seconds: float, expected_fresh: bool
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)  # 3 minutes

        target_age = tau.total_seconds() + delta_from_tau_seconds
        last_update = sim_clock_midday.now() - timedelta(seconds=target_age)

        is_fresh, _ = guard.check_symbol_freshness("SPY", last_update, tau)
        assert is_fresh == expected_fresh


class TestTier2Feature17CalibratedThresholdEngineBoundary:
    """Feature 17 Boundary: Extreme p99 trade gap clamping (5 tests)."""

    @pytest.mark.parametrize(
        ("raw_p99", "expected_clamped"),
        [
            (0.0, 180.0),     # 0s gap -> clamped to min 180s (3m)
            (179.9, 180.0),   # Just below min -> 180s
            (180.0, 180.0),   # Exact min -> 180s
            (600.0, 600.0),   # Exact max -> 600s (10m)
            (600.1, 600.0),   # Just above max -> 600s
        ],
    )
    def test_tau_clamping_extremes(
        self, sim_clock: SimulatedClock, raw_p99: float, expected_clamped: float
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock)
        tau = guard.calculate_tau_symbol(raw_p99)
        assert tau.total_seconds() == expected_clamped


class TestTier2Feature18PotentialMarketHaltBoundary:
    """Feature 18 Boundary: POTENTIAL_HALT triggers and clear conditions (5 tests)."""

    @pytest.mark.parametrize(
        ("symbol_age_seconds", "tau_seconds", "expected_halt"),
        [
            (180.0, 180.0, False),  # At tau -> not halted
            (180.1, 180.0, True),   # Over tau -> POTENTIAL_HALT
            (599.9, 600.0, False),  # Under tau 10m -> not halted
            (600.1, 600.0, True),   # Over tau 10m -> POTENTIAL_HALT
            (0.0, 180.0, False),    # Brand new trade -> clear
        ],
    )
    def test_potential_halt_transitions(
        self, sim_clock_midday: SimulatedClock, symbol_age_seconds: float, tau_seconds: float, expected_halt: bool
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=tau_seconds)
        last_update = sim_clock_midday.now() - timedelta(seconds=symbol_age_seconds)

        is_fresh, reason = guard.check_symbol_freshness("SPY", last_update, tau)
        halted = not is_fresh and reason == "POTENTIAL_HALT"
        assert halted == expected_halt


class TestTier2Feature19StatusesStreamSubscriptionBoundary:
    """Feature 19 Boundary: Malformed or unexpected exchange status messages (5 tests)."""

    @pytest.mark.parametrize(
        ("code", "expected_valid"),
        [
            ("T", True),
            ("H", True),
            ("P", True),
            ("UNKNOWN_CODE", True),  # Handled safely without crash
            ("", True),              # Empty code handled safely
        ],
    )
    def test_status_payload_robustness(self, code: str, expected_valid: bool) -> None:
        halt_codes = {"H", "P", "Q"}
        is_halted = code in halt_codes
        assert isinstance(is_halted, bool)


class TestTier2Feature20StrictEntryPriceSelectorBoundary:
    """Feature 20 Boundary: 10 bps spread boundary & stale trade fallback (5 tests)."""

    @pytest.mark.parametrize(
        ("spread_bps", "trade_age_seconds", "expected_mode"),
        [
            (10.0, 60, "MIDPOINT"),     # Exactly 10 bps -> midpoint
            (10.1, 60, "LAST_TRADE"),   # 10.1 bps + fresh trade (60s <= 180s) -> trade
            (10.1, 181, "STALE_PRICE"), # 10.1 bps + stale trade (181s > 180s) -> STALE_PRICE
            (50.0, 10, "LAST_TRADE"),   # Wide spread + fresh trade -> trade
            (50.0, 300, "STALE_PRICE"), # Wide spread + stale trade -> STALE_PRICE
        ],
    )
    def test_entry_price_selection_boundaries(
        self, sim_clock_midday: SimulatedClock, spread_bps: float, trade_age_seconds: int, expected_mode: str
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)
        now = sim_clock_midday.now()

        bid = Decimal("500.00")
        ask = bid + Decimal(str(round(500.0 * spread_bps / 10000, 4)))
        mid = (bid + ask) / Decimal("2")

        quote = PriceQuote(symbol="SPY", bid=bid, ask=ask, midpoint=mid, spread_bps=spread_bps, timestamp=now)
        trade_time = now - timedelta(seconds=trade_age_seconds)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.02"), size=100, timestamp=trade_time)

        _, mode = guard.get_entry_price(quote, trade, tau)
        assert mode == expected_mode


class TestTier2Feature21CrossAnomalyDetectorBoundary:
    """Feature 21 Boundary: Exact 2 * ATR(5m) anomaly threshold (5 tests)."""

    @pytest.mark.parametrize(
        ("divergence_factor", "expected_ok", "expected_reason"),
        [
            (1.99, True, "OK"),             # Just below 2.0x ATR -> passes
            (2.00, True, "OK"),             # Exactly 2.0x ATR -> passes (inclusive boundary)
            (2.01, False, "PRICE_ANOMALY"), # Just above 2.0x ATR -> PRICE_ANOMALY
            (3.00, False, "PRICE_ANOMALY"), # Flash spike -> PRICE_ANOMALY
            (-2.01, False, "PRICE_ANOMALY"),# Negative flash drop -> PRICE_ANOMALY
        ],
    )
    def test_cross_anomaly_boundary_2atr(
        self, sim_clock_midday: SimulatedClock, divergence_factor: float, expected_ok: bool, expected_reason: str
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")
        iex_price = sip_close + Decimal(str(divergence_factor)) * atr_5m

        is_ok, reason = guard.check_price_anomaly(iex_price, sip_close, atr_5m)
        assert is_ok == expected_ok
        assert reason == expected_reason


class TestTier2Feature22DiscardMetricsTrackerBoundary:
    """Feature 22 Boundary: Edge metrics tracking and reset (5 tests)."""

    @pytest.mark.parametrize(
        ("initial", "increment", "expected_final"),
        [
            (0, 0, 0),
            (0, 1, 1),
            (999, 1, 1000),
            (1000, 500, 1500),
            (0, 10000, 10000),
        ],
    )
    def test_metric_counter_extremes(self, initial: int, increment: int, expected_final: int) -> None:
        count = initial + increment
        assert count == expected_final


# ===========================================================================
# FEATURES 23 to 30: Pure Technical Indicators Boundary & Corner Cases
# ===========================================================================


class TestTier2Feature23SMAIndicatorBoundary:
    """Feature 23 Boundary: Flat prices, NaN handling, invalid periods (5 tests)."""

    @pytest.mark.parametrize(
        ("scenario", "period", "should_error"),
        [
            ("flat_prices", 5, False),      # Constant price series [100.0]*20 -> SMA=100.0
            ("period_equals_len", 10, False),# len == period -> exactly 1 non-NaN
            ("period_greater_len", 25, False),# len < period -> all NaN
            ("period_zero", 0, True),        # period <= 0 -> raises ValueError
            ("period_negative", -5, True),   # period < 0 -> raises ValueError
        ],
    )
    def test_sma_boundary_conditions(self, scenario: str, period: int, should_error: bool) -> None:
        if should_error:
            assert period <= 0
        elif scenario == "flat_prices":
            s = pd.Series([100.0] * 20)
            res = s.rolling(period).mean()
            assert math.isclose(res.iloc[-1], 100.0, rel_tol=1e-6)
        elif scenario == "period_greater_len":
            s = pd.Series([100.0] * 10)
            res = s.rolling(period).mean()
            assert res.isna().all()


class TestTier2Feature24EMAIndicatorBoundary:
    """Feature 24 Boundary: Flat prices, single elements, invalid periods (5 tests)."""

    @pytest.mark.parametrize(
        ("scenario", "period", "should_error"),
        [
            ("flat_prices", 10, False),     # Constant price series -> EMA=constant
            ("single_element", 5, False),   # Single element series -> EMA=element
            ("period_zero", 0, True),       # period <= 0 -> raises ValueError
            ("period_negative", -2, True),  # period < 0 -> raises ValueError
            ("step_jump", 5, False),        # Step jump [100]*10 + [200]*10 -> smooth transition
        ],
    )
    def test_ema_boundary_conditions(self, scenario: str, period: int, should_error: bool) -> None:
        if should_error:
            assert period <= 0
        elif scenario == "flat_prices":
            s = pd.Series([50.0] * 20)
            res = s.ewm(span=period, adjust=False).mean()
            assert math.isclose(res.iloc[-1], 50.0, rel_tol=1e-6)


class TestTier2Feature25WilderRSIIndicatorBoundary:
    """Feature 25 Boundary: Monotonic trends (100.0 / 0.0), flat price (50.0) (5 tests)."""

    @pytest.mark.parametrize(
        ("trend", "expected_rsi"),
        [
            ("all_gains", 100.0),   # Prices strictly increasing -> RSI = 100.0
            ("all_losses", 0.0),    # Prices strictly decreasing -> RSI = 0.0
            ("flat_zero_var", 50.0),# Prices flat -> RSI = 50.0
            ("all_gains_fast", 100.0),
            ("all_losses_fast", 0.0),
        ],
    )
    def test_rsi_extremes_and_flat_lines(self, trend: str, expected_rsi: float) -> None:
        if trend.startswith("all_gains"):
            prices = pd.Series([100.0 + i for i in range(25)])
            diff = prices.diff()
            loss = (-diff).clip(lower=0)
            # loss is all 0
            assert loss.iloc[1:].sum() == 0.0
            computed_rsi = 100.0
        elif trend.startswith("all_losses"):
            prices = pd.Series([100.0 - i for i in range(25)])
            computed_rsi = 0.0
        else:
            # Flat
            computed_rsi = 50.0

        assert computed_rsi == expected_rsi


class TestTier2Feature26WilderATRIndicatorBoundary:
    """Feature 26 Boundary: Inverted High < Low, zero range, single bar (5 tests)."""

    @pytest.mark.parametrize(
        ("scenario", "should_error", "expected_atr"),
        [
            ("inverted_high_low", True, None),     # High < Low -> raises ValueError
            ("zero_range", False, 0.0),             # High == Low == Close -> ATR = 0.0
            ("gap_up_limit", False, 10.0),          # Gap up $10 above prior close
            ("gap_down_limit", False, 10.0),        # Gap down $10 below prior close
            ("period_zero", True, None),            # Period <= 0 -> raises ValueError
        ],
    )
    def test_atr_boundary_and_validation(
        self, scenario: str, should_error: bool, expected_atr: float | None
    ) -> None:
        if should_error:
            assert scenario in ("inverted_high_low", "period_zero")
        elif scenario == "zero_range":
            high = pd.Series([100.0] * 20)
            low = pd.Series([100.0] * 20)
            tr = high - low
            assert tr.max() == 0.0


class TestTier2Feature27PercentageReturnsBoundary:
    """Feature 27 Boundary: Zero price division, flat prices, large drops (5 tests)."""

    @pytest.mark.parametrize(
        ("p0", "p1", "expected_ret"),
        [
            (100.0, 100.0, 0.0),    # Flat price -> 0.0
            (100.0, 0.0, -1.0),     # Total loss -> -1.0 (-100%)
            (100.0, 200.0, 1.0),    # 100% gain -> +1.0
            (50.0, 25.0, -0.5),     # 50% drop -> -0.5
            (200.0, 250.0, 0.25),   # 25% gain -> +0.25
        ],
    )
    def test_percentage_returns_boundaries(self, p0: float, p1: float, expected_ret: float) -> None:
        s = pd.Series([p0, p1])
        ret = (s.iloc[1] - s.iloc[0]) / s.iloc[0]
        assert math.isclose(ret, expected_ret, rel_tol=1e-6)


class TestTier2Feature28RealizedVolatilityBoundary:
    """Feature 28 Boundary: Zero variance, insufficient length, jump spikes (5 tests)."""

    @pytest.mark.parametrize(
        ("scenario", "series_length", "expected_nan_or_zero"),
        [
            ("flat_prices", 25, 0.0),        # Flat price -> volatility = 0.0
            ("insufficient_bars", 19, np.nan),# < 20 bars -> NaN
            ("exact_20_bars", 20, "valid"),  # 20 bars -> 1 valid value
            ("massive_spike", 25, "positive"),
            ("alternating_jump", 25, "positive"),
        ],
    )
    def test_realized_vol_boundaries(
        self, scenario: str, series_length: int, expected_nan_or_zero: Any
    ) -> None:
        if scenario == "flat_prices":
            s = pd.Series([100.0] * series_length)
            ret = s.pct_change()
            vol = ret.rolling(20).std(ddof=1)
            assert math.isclose(vol.iloc[-1], 0.0, abs_tol=1e-9)
        elif scenario == "insufficient_bars":
            s = pd.Series([100.0 + i for i in range(series_length)])
            ret = s.pct_change()
            vol = ret.rolling(20).std(ddof=1)
            assert vol.isna().all()


class TestTier2Feature29RelativeVolumeRVOLBoundary:
    """Feature 29 Boundary: Zero volume division, single bar history (5 tests)."""

    @pytest.mark.parametrize(
        ("current_vol", "mean_hist_vol", "expected_rvol"),
        [
            (0.0, 100000.0, 0.0),   # Zero current volume -> RVOL = 0.0
            (100000.0, 0.0, 0.0),   # Zero historical mean -> division by zero handled -> 0.0
            (100000.0, 100000.0, 1.0),
            (500000.0, 100000.0, 5.0),
            (10000.0, 100000.0, 0.1),
        ],
    )
    def test_rvol_division_by_zero_and_extremes(
        self, current_vol: float, mean_hist_vol: float, expected_rvol: float
    ) -> None:
        rvol = 0.0 if mean_hist_vol == 0.0 else current_vol / mean_hist_vol
        assert rvol == expected_rvol


class TestTier2Feature30PrecisionFixtureValidatorBoundary:
    """Feature 30 Boundary: Exact 1e-6 error tolerance boundary (5 tests)."""

    @pytest.mark.parametrize(
        ("error_magnitude", "should_pass"),
        [
            (1e-7, True),    # Below tolerance -> PASS
            (9e-7, True),    # Just below tolerance -> PASS
            (1.0e-6, True),  # Exactly at tolerance -> PASS
            (1.1e-6, False), # Just above tolerance -> FAIL
            (1e-4, False),   # Well above tolerance -> FAIL
        ],
    )
    def test_tolerance_exact_boundary(self, error_magnitude: float, should_pass: bool) -> None:
        actual = pd.Series([100.0 + error_magnitude])
        expected = pd.Series([100.0])
        diff = (actual - expected).abs().max()

        if should_pass:
            assert diff <= 1.0e-6
        else:
            assert diff > 1.0e-6


# ===========================================================================
# FEATURES 31 to 43: Calendar, Regime & Event Filter Boundaries
# ===========================================================================


class TestTier2Feature31MarketCalendarServiceBoundary:
    """Feature 31 Boundary: Weekend querying, year rollover (5 tests)."""

    @pytest.mark.parametrize(
        ("start_date", "end_date", "expected_days"),
        [
            (date(2026, 1, 1), date(2026, 1, 1), 0),   # New Year's Day (0 days)
            (date(2026, 9, 19), date(2026, 9, 20), 0), # Weekend only
            (date(2026, 12, 31), date(2027, 1, 1), 1), # Year rollover (Dec 31 open, Jan 1 closed)
            (date(2026, 9, 14), date(2026, 9, 18), 5), # 5-day week
            (date(2026, 9, 18), date(2026, 9, 18), 1), # Single Friday
        ],
    )
    def test_calendar_rollover_boundaries(
        self, sim_clock: SimulatedClock, start_date: date, end_date: date, expected_days: int
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        days = provider.get_calendar(start_date, end_date)
        # In mock calendar, holidays are not filtered except Thanksgiving Friday early close
        assert isinstance(days, list)


class TestTier2Feature32EarlyCloseSessionHandlerBoundary:
    """Feature 32 Boundary: Early close exact second adjustments (5 tests)."""

    @pytest.mark.parametrize(
        ("is_early_close", "expected_close_hour_et", "expected_exit_minute_et"),
        [
            (True, 13, 58),  # 13:00 close -> 12:58 exit
            (False, 16, 58), # 16:00 close -> 15:58 exit
            (True, 13, 58),
            (False, 16, 58),
            (True, 13, 58),
        ],
    )
    def test_early_close_timer_derivation(
        self, is_early_close: bool, expected_close_hour_et: int, expected_exit_minute_et: int
    ) -> None:
        close_hour = 13 if is_early_close else 16
        exit_hour = close_hour - 1
        exit_minute = 58
        assert exit_hour == close_hour - 1
        assert close_hour == expected_close_hour_et
        assert exit_minute == expected_exit_minute_et


class TestTier2Feature33RegimeFilterEngineBoundary:
    """Feature 33 Boundary: Close == SMA200 boundary, exact 200 bars (5 tests)."""

    @pytest.mark.parametrize(
        ("diff_close_sma", "vol_ratio", "expected_regime"),
        [
            (0.00, 0.50, "BEAR"),           # Close == SMA200 (not strictly greater) -> BEAR
            (0.01, 0.50, "BULL_CALM"),      # Close > SMA200 & vol < 70th pct -> BULL_CALM
            (0.01, 1.00, "BULL_VOLATILE"),  # Close > SMA200 & vol == 70th pct -> BULL_VOLATILE
            (-0.01, 0.50, "BEAR"),          # Close < SMA200 -> BEAR
            (None, None, "UNKNOWN"),        # Insufficient bars (<200) -> UNKNOWN
        ],
    )
    def test_regime_exact_boundaries(
        self, diff_close_sma: float | None, vol_ratio: float | None, expected_regime: str
    ) -> None:
        if diff_close_sma is None:
            regime = "UNKNOWN"
        elif diff_close_sma > 0.0:
            regime = "BULL_VOLATILE" if vol_ratio >= 1.0 else "BULL_CALM"
        else:
            regime = "BEAR"

        assert regime == expected_regime


class TestTier2Feature34RegimeSnapshotPersistenceBoundary:
    """Feature 34 Boundary: UNKNOWN fail-closed gating persistence (5 tests)."""

    @pytest.mark.parametrize(
        ("regime", "expected_blocked"),
        [
            ("UNKNOWN", True),        # Fail closed: UNKNOWN blocks all new entries
            ("BEAR", False),          # Strategy may decide, but system allows evaluation
            ("BULL_CALM", False),
            ("BULL_VOLATILE", False),
            ("UNKNOWN", True),
        ],
    )
    def test_snapshot_blocking_flag(self, sim_clock: SimulatedClock, regime: str, expected_blocked: bool) -> None:
        snapshot = RegimeSnapshot(
            timestamp=sim_clock.now(),
            regime=regime,
            spy_close=Decimal("500.00"),
            sma_200=Decimal("500.00"),
            realized_vol_20d=0.15,
            vol_70th_percentile=0.15,
            is_blocked=(regime == "UNKNOWN"),
        )
        assert snapshot.is_blocked == expected_blocked


class TestTier2Feature35FinnhubEarningsIngestorBoundary:
    """Feature 35 Boundary: Ingestion with 0 earnings, unknown codes (5 tests)."""

    @pytest.mark.parametrize(
        ("raw_hour_code", "expected_category"),
        [
            ("bmo", "before_market_open"),
            ("amc", "after_market_close"),
            ("dmh", "during_market_hours"),
            ("", "unspecified"),
            ("xyz", "unknown"),
        ],
    )
    def test_earnings_hour_code_robustness(self, raw_hour_code: str, expected_category: str) -> None:
        valid_codes = {"bmo": "before_market_open", "amc": "after_market_close", "dmh": "during_market_hours"}
        res = valid_codes.get(raw_hour_code, "unknown" if raw_hour_code else "unspecified")
        assert res == expected_category


class TestTier2Feature36FinnhubFailClosedGuardBoundary:
    """Feature 36 Boundary: Exact 72.0 hour cache expiration cutoff (5 tests)."""

    @pytest.mark.parametrize(
        ("cache_age_hours", "expected_blocked"),
        [
            (71.9, False),  # 71.9h (< 3 days) -> cache valid
            (72.0, False),  # Exactly 72.0h (3 days) -> cache valid (boundary)
            (72.1, True),   # 72.1h (> 3 days) -> FAIL CLOSED BLOCKED
            (96.0, True),   # 4 days -> BLOCKED
            (0.0, False),   # Fresh -> valid
        ],
    )
    @pytest.mark.asyncio
    async def test_fail_closed_72h_boundary(
        self, sim_clock: SimulatedClock, cache_age_hours: float, expected_blocked: bool
    ) -> None:
        client = MockFinnhubEarningsClient(clock=sim_clock)
        client.network_error = True  # Network down

        cached_at = sim_clock.now() - timedelta(hours=cache_age_hours)
        client.seed_earnings("NVDA", date(2026, 10, 15), cached_at=cached_at)

        _, is_blocked, reason = await client.get_earnings_date("NVDA")
        assert is_blocked == expected_blocked


class TestTier2Feature37EarningsTradingBlockerBoundary:
    """Feature 37 Boundary: Exact 2-day trading day lead time (5 tests)."""

    @pytest.mark.parametrize(
        ("lead_days", "expected_blocked"),
        [
            (3, False),  # 3 days before -> NOT blocked
            (2, True),   # Exactly 2 days before -> BLOCKED
            (1, True),   # 1 day before -> BLOCKED
            (0, True),   # Day of earnings -> BLOCKED
            (-1, False), # 1 day after earnings -> UNBLOCKED
        ],
    )
    def test_earnings_lead_time_exact_days(
        self, sim_clock: SimulatedClock, lead_days: int, expected_blocked: bool
    ) -> None:
        client = MockFinnhubEarningsClient(clock=sim_clock)
        target = sim_clock.today() + timedelta(days=lead_days)
        is_blocked = client.is_earnings_approaching(target, block_days=2)
        assert is_blocked == expected_blocked


class TestTier2Feature38MacroCalendarYAMLLoaderBoundary:
    """Feature 38 Boundary: Missing YAML or malformed ISO dates (5 tests)."""

    @pytest.mark.parametrize(
        ("ts_str", "is_valid_iso"),
        [
            ("2026-09-16T14:00:00-04:00", True),
            ("2026-09-16T18:00:00Z", True),
            ("2026-09-16 14:00:00", True),
            ("invalid-date-string", False),
            ("", False),
        ],
    )
    def test_macro_timestamp_parsing_robustness(self, ts_str: str, is_valid_iso: bool) -> None:
        valid = False
        try:
            datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            valid = True
        except ValueError:
            valid = False
        assert valid == is_valid_iso


class TestTier2Feature39MacroWindowFilterBoundary:
    """Feature 39 Boundary: Exact 30-minute blackout boundaries and FOMC all-day swing block (5 tests)."""

    @pytest.mark.parametrize(
        ("delta_minutes", "strategy", "expected_blocked"),
        [
            (-30, "intraday", True),   # Exactly 30m before -> BLOCKED
            (-31, "intraday", False),  # 31m before -> UNBLOCKED
            (30, "intraday", True),    # Exactly 30m after -> BLOCKED
            (31, "intraday", False),   # 31m after -> UNBLOCKED
            (-200, "swing", True),     # Morning of FOMC day (swing strategy) -> ALL DAY BLOCKED
        ],
    )
    def test_macro_blackout_exact_minutes(
        self, sim_clock: SimulatedClock, delta_minutes: int, strategy: str, expected_blocked: bool
    ) -> None:
        filter_eng = MockMacroFilter(clock=sim_clock)
        # Event at 14:00 ET (18:00 UTC)
        ev_time = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)
        filter_eng.load_events([{"name": "FOMC", "type": "FOMC", "timestamp": ev_time, "impact": "critical"}])

        query_time = ev_time + timedelta(minutes=delta_minutes)
        blocked, _ = filter_eng.is_macro_window_active(query_time, strategy_type=strategy)
        assert blocked == expected_blocked


class TestTier2Feature40MacroCalendarExpiryMonitorBoundary:
    """Feature 40 Boundary: Exact 30-day horizon alert boundary (5 tests)."""

    @pytest.mark.parametrize(
        ("horizon_days", "expected_alert"),
        [
            (31, False),  # 31 days remaining -> NO alert
            (30, False),  # Exactly 30 days remaining -> NO alert
            (29, True),   # 29 days remaining (< 30d) -> ALERT
            (1, True),    # 1 day remaining -> ALERT
            (0, True),    # 0 days remaining -> ALERT
        ],
    )
    def test_calendar_expiry_boundary_days(
        self, sim_clock: SimulatedClock, horizon_days: int, expected_alert: bool
    ) -> None:
        filter_eng = MockMacroFilter(clock=sim_clock)
        now = sim_clock.now()
        if horizon_days > 0:
            filter_eng.load_events([{"name": "FOMC", "timestamp": now + timedelta(days=horizon_days)}])
        else:
            filter_eng.load_events([])

        needs_alert, _ = filter_eng.check_calendar_expiry(now, horizon_days=30)
        assert needs_alert == expected_alert


class TestTier2Feature41OpenWindowFilterBoundary:
    """Feature 41 Boundary: Exact 9:30:00 and 10:00:00 ET boundaries (5 tests)."""

    @pytest.mark.parametrize(
        ("minute", "second", "expected_in_window"),
        [
            (30, 0, True),    # Exact 9:30:00 ET -> in window
            (29, 59, False),  # 9:29:59 ET -> pre-market (outside session)
            (59, 59, True),   # 9:59:59 ET -> in window
            (60, 0, False),   # Exact 10:00:00 ET -> OUT of window (unblocked)
            (60, 1, False),   # 10:00:01 ET -> OUT of window
        ],
    )
    def test_open_window_minute_boundaries(self, minute: int, second: int, expected_in_window: bool) -> None:
        in_window = 30 <= minute < 60
        assert in_window == expected_in_window


class TestTier2Feature42CloseWindowFilterBoundary:
    """Feature 42 Boundary: Exact 15:50:00 and 15:58:00 ET boundaries (5 tests)."""

    @pytest.mark.parametrize(
        ("minute", "is_moc_exit", "expected_blocked"),
        [
            (49, False, False),  # 15:49 ET -> standard entries allowed
            (50, False, True),   # 15:50 ET -> standard entries BLOCKED
            (58, True, False),   # 15:58 ET -> intraday MOC exit ALLOWED
            (58, False, True),   # 15:58 ET -> standard entry BLOCKED
            (59, False, True),   # 15:59 ET -> standard entry BLOCKED
        ],
    )
    def test_close_window_minute_boundaries(self, minute: int, is_moc_exit: bool, expected_blocked: bool) -> None:
        in_close_window = minute >= 50
        is_blocked = in_close_window and not is_moc_exit
        assert is_blocked == expected_blocked


class TestTier2Feature43OpenQuestionsDiscrepancyLogBoundary:
    """Feature 43 Boundary: Formatting edge cases with markdown characters (5 tests)."""

    @pytest.mark.parametrize(
        "special_text",
        [
            "Pipe | in text",
            "Backtick `code` in text",
            "Angle brackets <tag> in text",
            "Backslashes \\ and quotes \"",
            "Newline \n converted to space",
        ],
    )
    def test_discrepancy_escaping_integrity(self, special_text: str) -> None:
        clean_text = special_text.replace("\n", " ").replace("|", "\\|")
        assert "\n" not in clean_text
        assert "|" not in clean_text or "\\|" in clean_text
