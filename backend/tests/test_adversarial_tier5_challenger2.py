"""White-Box Adversarial Stress Test Suite for Tier 5 Data Layer Hardening.

Conducted by Challenger 2 (teamwork_preview_challenger).
Covers:
1. Provider Facade (`backend/tbot/data/provider.py`)
2. Rate Limiter & Token Bucket (`backend/tbot/data/rate_limiter.py`)
3. Database Cache (`backend/tbot/data/cache.py`)
4. Proxy Mapping (`backend/tbot/data/proxies.py`)
5. Resampler (`backend/tbot/data/resampler.py`)
6. Staleness Guard & Anomaly (`backend/tbot/data/staleness.py`)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError, ProviderUnavailableError, RateLimitExceededError
from tbot.data.cache import DailyBarCache, find_missing_date_ranges
from tbot.data.provider import AlpacaProvider
from tbot.data.proxies import (
    ProxyMapper,
    get_proxy_pair,
    is_canonical_symbol,
    is_proxy_symbol,
    resolve_data_proxy,
    resolve_trading_proxy,
)
from tbot.data.rate_limiter import (
    AsyncTokenBucket,
    BackoffPolicy,
    RateLimiter,
    execute_with_retry,
)
from tbot.data.resampler import resample_1m_to_5m
from tbot.data.staleness import (
    AnomalyDetector,
    EntryPriceMode,
    FeedHealthState,
    GlobalFeedMonitor,
    StalenessGuard,
    calibrate_freshness,
)
from tbot.data.types import PriceQuote, TradeQuote
from tbot.db.base import Base

# ============================================================================
# 1. PROVIDER FACADE ADVERSARIAL TESTS
# ============================================================================


class TestProviderFacadeAdversarial:
    """Stress tests for AlpacaProvider facade."""

    def test_clamp_sip_end_cross_timezone(self) -> None:
        """Verifies clamping with various timezone configurations (aware, naive, cross-tz)."""
        now_utc = datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC)
        clock = SimulatedClock(now_utc)
        provider = AlpacaProvider(clock=clock)

        # 1. End is naive: should assume clock's timezone
        end_naive = datetime(2026, 9, 21, 14, 25, 0)
        clamped = provider.clamp_sip_end(end_naive)
        # Expected max: 14:30 - 16m = 14:14
        assert clamped == datetime(2026, 9, 21, 14, 14, 0, tzinfo=UTC)

        # 2. End is in a different timezone (e.g. UTC-5, US/Eastern)
        eastern = timezone(timedelta(hours=-5))
        # 10:20 Eastern == 15:20 UTC (future compared to 14:14 UTC)
        end_eastern = datetime(2026, 9, 21, 10, 20, 0, tzinfo=eastern)
        clamped_eastern = provider.clamp_sip_end(end_eastern)
        assert clamped_eastern == datetime(2026, 9, 21, 14, 14, 0, tzinfo=UTC)

        # 3. End is past embargo (e.g. 13:00 UTC)
        end_past = datetime(2026, 9, 21, 13, 0, 0, tzinfo=UTC)
        assert provider.clamp_sip_end(end_past) == end_past

    @pytest.mark.asyncio
    async def test_get_daily_bars_empty_and_inverted_dates(self) -> None:
        """Verifies safe empty DataFrame return on invalid ranges and empty symbol lists."""
        provider = AlpacaProvider()

        # Empty symbols
        df_empty_sym = await provider.get_daily_bars([], date(2026, 1, 1), date(2026, 1, 10))
        assert df_empty_sym.empty
        assert "symbol" in df_empty_sym.columns
        assert "close" in df_empty_sym.columns

        # Inverted dates
        df_inverted = await provider.get_daily_bars(["SPY"], date(2026, 1, 10), date(2026, 1, 1))
        assert df_inverted.empty

    @pytest.mark.asyncio
    async def test_get_intraday_bars_embargo_fast_path(self) -> None:
        """Intraday SIP requests completely inside the 16-minute embargo return empty immediately."""
        now_utc = datetime(2026, 9, 21, 15, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now_utc)
        mock_client = MagicMock()
        provider = AlpacaProvider(clock=clock, historical_client=mock_client)

        # Window entirely within last 16m: 14:50 to 15:00 UTC
        start = datetime(2026, 9, 21, 14, 50, 0, tzinfo=UTC)
        end = datetime(2026, 9, 21, 15, 0, 0, tzinfo=UTC)

        df = await provider.get_intraday_bars(["SPY"], "1m", start, end, feed="sip_delayed")
        assert df.empty
        # Client should not have been called at all!
        mock_client.get_stock_bars.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_latest_quote_missing_symbol_raises_market_data_error(self) -> None:
        """Provider raises MarketDataError if Alpaca returns empty response for a quote."""
        mock_client = MagicMock()
        # Mock empty dict returned
        mock_client.get_stock_latest_quote.return_value = {}
        provider = AlpacaProvider(historical_client=mock_client)

        with pytest.raises(MarketDataError, match="No quote returned for symbol"):
            await provider.get_latest_quote("UNKNOWN")

    @pytest.mark.asyncio
    async def test_rate_limiter_rejection_in_provider(self) -> None:
        """Provider surfaces RateLimitExceededError when sync rate limiter denies request."""
        sync_limiter = RateLimiter(max_requests=0)  # Always rejects
        provider = AlpacaProvider(rate_limiter=sync_limiter)

        with pytest.raises(RateLimitExceededError):
            await provider.get_latest_quote("SPY")


# ============================================================================
# 2. RATE LIMITER & TOKEN BUCKET ADVERSARIAL TESTS
# ============================================================================


class TestRateLimiterAdversarial:
    """Stress testing of Token Bucket and sliding-window rate limiters."""

    @pytest.mark.asyncio
    async def test_async_token_bucket_negative_tokens_rejected(self) -> None:
        """Negative token request raises ValueError."""
        bucket = AsyncTokenBucket(rate_limit_per_minute=180.0)
        with pytest.raises(ValueError, match="tokens must be non-negative"):
            await bucket.acquire(-1.0)

    @pytest.mark.asyncio
    async def test_async_token_bucket_non_blocking_exhaustion(self) -> None:
        """Non-blocking acquire raises RateLimitExceededError when budget is empty."""
        sim_time = 1000.0

        def mock_time() -> float:
            return sim_time

        bucket = AsyncTokenBucket(
            rate_limit_per_minute=60.0,
            capacity=1.0,
            time_func=mock_time,
        )

        # Consume only available token
        assert bucket.acquire_now() is True
        assert bucket.acquire_now() is False

        # acquire(blocking=False) must raise RateLimitExceededError
        with pytest.raises(RateLimitExceededError) as exc_info:
            await bucket.acquire(1.0, blocking=False)
        assert exc_info.value.retry_after > 0

    @pytest.mark.asyncio
    async def test_async_token_bucket_concurrent_acquisition_stress(self) -> None:
        """Spawns 50 concurrent tasks acquiring tokens; verifies strict rate conservation."""
        sim_time = 0.0

        def mock_time() -> float:
            return sim_time

        async def mock_sleep(seconds: float) -> None:
            nonlocal sim_time
            sim_time += seconds

        # Rate: 60 tokens/min = 1.0 token/sec, capacity: 5 tokens
        bucket = AsyncTokenBucket(
            rate_limit_per_minute=60.0,
            capacity=5.0,
            time_func=mock_time,
            sleep_func=mock_sleep,
        )

        num_tasks = 25
        delays = await asyncio.gather(*[bucket.acquire(1.0) for _ in range(num_tasks)])

        # First 5 were free (capacity=5), remaining 20 waited 1.0s each
        assert len(delays) == num_tasks
        # Total simulated time should equal 20 seconds
        assert sim_time == pytest.approx(20.0, abs=1e-3)

    def test_backoff_policy_delay_bounds_and_jitter(self) -> None:
        """Validates backoff delay calculation, exponential doubling, and max delay cap."""
        policy = BackoffPolicy(
            initial_delay=1.0,
            multiplier=2.0,
            max_delay=8.0,
            jitter=False,
        )

        # Attempt 0: 1.0
        assert policy.compute_delay(0) == 1.0
        # Attempt 1: 2.0
        assert policy.compute_delay(1) == 2.0
        # Attempt 2: 4.0
        assert policy.compute_delay(2) == 4.0
        # Attempt 3: 8.0 (capped)
        assert policy.compute_delay(3) == 8.0
        # Attempt 4: 8.0 (capped)
        assert policy.compute_delay(4) == 8.0

        # With retry_after larger than delay but smaller than max_delay
        assert policy.compute_delay(0, retry_after=5.5) == 5.5
        # With retry_after larger than max_delay
        assert policy.compute_delay(0, retry_after=25.0) == 8.0

    @pytest.mark.asyncio
    async def test_execute_with_retry_exhaustion_wrapping(self) -> None:
        """Exhausting retries wraps into ProviderUnavailableError for 503."""
        policy = BackoffPolicy(max_retries=2, initial_delay=0.01, jitter=False)

        mock_call = MagicMock(side_effect=Exception("HTTP 503 Service Unavailable"))
        sleep_mock = AsyncMock()

        with pytest.raises(ProviderUnavailableError) as exc_info:
            await execute_with_retry(
                mock_call,
                policy=policy,
                sleep_func=sleep_mock,
            )

        assert mock_call.call_count == 3  # initial + 2 retries
        assert sleep_mock.call_count == 2
        assert "503" in str(exc_info.value)


# ============================================================================
# 3. DATABASE CACHE ADVERSARIAL TESTS
# ============================================================================


class TestDailyBarCacheAdversarial:
    """Stress tests for DailyBarCache missing ranges and idempotency."""

    def test_find_missing_date_ranges_boundary_and_gaps(self) -> None:
        """Finds gaps, bridges weekends, and separates wide holes."""
        # Empty
        assert find_missing_date_ranges([]) == []

        # Single date
        d1 = date(2026, 9, 1)
        assert find_missing_date_ranges([d1]) == [(d1, d1)]

        # Continuous dates
        dates_contig = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
        assert find_missing_date_ranges(dates_contig) == [(date(2026, 9, 1), date(2026, 9, 3))]

        # Weekend gap (Fri Sep 4 to Mon Sep 7: 3 days gap <= 4 max_gap)
        dates_weekend = [date(2026, 9, 4), date(2026, 9, 7)]
        assert find_missing_date_ranges(dates_weekend, max_gap_days=4) == [(date(2026, 9, 4), date(2026, 9, 7))]

        # Wide gap (Sep 1 and Sep 15: 14 days gap > 4)
        dates_wide = [date(2026, 9, 1), date(2026, 9, 15)]
        assert find_missing_date_ranges(dates_wide, max_gap_days=4) == [
            (date(2026, 9, 1), date(2026, 9, 1)),
            (date(2026, 9, 15), date(2026, 9, 15)),
        ]

    @pytest.mark.asyncio
    async def test_cache_fallback_on_db_read_failure(self) -> None:
        """When DB read raises SQLAlchemyError, cache gracefully falls back to fetch_fn."""
        cache = DailyBarCache()
        mock_session = AsyncMock()
        mock_session.scalars.side_effect = OperationalError("SELECT 1", {}, Exception("DB down"))

        async def mock_fetch(syms: list[str], s: date, e: date, feed: str, adj: bool) -> list[dict[str, Any]]:
            return [{
                "symbol": syms[0],
                "date": s,
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 1000.0,
                "adjusted": adj,
                "feed": feed,
            }]

        df = await cache.get_or_fetch_daily_bars(
            symbols=["SPY"],
            start=date(2026, 9, 1),
            end=date(2026, 9, 1),
            fetch_fn=mock_fetch,
            session=mock_session,
        )

        assert not df.empty
        assert df.iloc[0]["symbol"] == "SPY"
        assert df.iloc[0]["close"] == 104.0


# ============================================================================
# 4. PROXY MAPPING ADVERSARIAL TESTS
# ============================================================================


class TestProxyMappingAdversarial:
    """Stress tests for canonical proxy mappings and custom mapper."""

    def test_canonical_proxies_invariants(self) -> None:
        """Checks all canonical mappings, bi-directionality, and unmapped handling."""
        # Canonical signals -> exec
        assert resolve_trading_proxy("SPY") == "SPYM"
        assert resolve_trading_proxy("QQQ") == "QQQM"
        assert resolve_trading_proxy("GLD") == "GLDM"

        # Reversed exec -> signal
        assert resolve_data_proxy("SPYM") == "SPY"
        assert resolve_data_proxy("QQQM") == "QQQ"
        assert resolve_data_proxy("GLDM") == "GLD"

        # Case and whitespace tolerance
        assert resolve_trading_proxy("  spy\t") == "SPYM"
        assert resolve_data_proxy("\nqqqm  ") == "QQQ"

        # Unmapped
        assert resolve_trading_proxy("AAPL") == "AAPL"
        assert resolve_data_proxy("AAPL") == "AAPL"

        # Predicates
        assert is_canonical_symbol("SPY") is True
        assert is_proxy_symbol("SPYM") is True
        assert is_canonical_symbol("AAPL") is False
        assert is_proxy_symbol("AAPL") is False

        # Proxy pairs
        assert get_proxy_pair("SPY") == ("SPY", "SPYM")
        assert get_proxy_pair("SPYM") == ("SPY", "SPYM")
        assert get_proxy_pair("AAPL") == ("AAPL", "AAPL")

    def test_proxy_mapper_custom_overrides(self) -> None:
        """Custom mappings extend and override defaults."""
        mapper = ProxyMapper(initial_mappings={"DIA": "DIAM"})
        assert mapper.resolve_trading("DIA") == "DIAM"
        assert mapper.resolve_data("DIAM") == "DIA"
        # Original canonicals still work
        assert mapper.resolve_trading("SPY") == "SPYM"


# ============================================================================
# 5. BAR RESAMPLER ADVERSARIAL TESTS
# ============================================================================


class TestBarResamplerAdversarial:
    """Stress tests for 1m to 5m bar resampling and VWAP."""

    def test_resample_zero_volume_vwap_fallback(self) -> None:
        """When volume is 0, VWAP falls back to close price without division by zero."""
        base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            {
                "timestamp": base_time + timedelta(minutes=i),
                "symbol": "SPY",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0 + i,
                "volume": 0.0,
            }
            for i in range(5)
        ]
        df_1m = pd.DataFrame(bars)
        df_5m = resample_1m_to_5m(df_1m)

        assert len(df_5m) == 1
        row = df_5m.iloc[0]
        assert row["volume"] == 0.0
        # VWAP must equal the final close price (105.0)
        assert row["close"] == 105.0
        assert row["vwap"] == 105.0
        assert not np.isnan(row["vwap"])

    def test_resample_synthetic_fill_missing_intervals(self) -> None:
        """Missing intervals in timeseries are forward-filled and marked synthetic."""
        base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        # Bar 1 at 09:30-09:35 (label 09:35)
        # Gap at 09:35-09:40 (missing)
        # Bar 2 at 09:40-09:45 (label 09:45)
        bars = [
            {
                "timestamp": base_time,
                "symbol": "SPY",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 500.0,
            },
            {
                "timestamp": base_time + timedelta(minutes=10),
                "symbol": "SPY",
                "open": 102.0,
                "high": 103.0,
                "low": 101.5,
                "close": 102.5,
                "volume": 800.0,
            },
        ]
        df_1m = pd.DataFrame(bars)
        df_5m = resample_1m_to_5m(df_1m, fill_missing=True)

        # Expected 3 bars: 09:35 (real), 09:40 (synthetic), 09:45 (real)
        assert len(df_5m) == 3
        # Synthetic bar at index 1
        synth = df_5m.iloc[1]
        assert synth["is_synthetic"] is True or synth["is_synthetic"] == 1
        assert synth["volume"] == 0.0
        assert synth["close"] == 100.5  # Forward filled from 09:35


# ============================================================================
# 6. STALENESS GUARD & ANOMALY ADVERSARIAL TESTS
# ============================================================================


class TestStalenessGuardAdversarial:
    """Stress tests for StalenessGuard, GlobalFeedMonitor, and AnomalyDetector."""

    def test_calibrate_freshness_boundary_limits(self) -> None:
        """Calibrated tau is strictly clamped between 180s and 600s."""
        # Ultra fast updates (0.1s to 1s) -> clamped to MIN_TAU (180s)
        fast_intervals = [0.5] * 100
        assert calibrate_freshness(fast_intervals) == 180.0

        # Ultra slow updates (1000s) -> clamped to MAX_TAU (600s)
        slow_intervals = [1000.0] * 100
        assert calibrate_freshness(slow_intervals) == 600.0

        # Empty or degenerate inputs -> default 300s
        assert calibrate_freshness([]) == 300.0
        assert calibrate_freshness(None) == 300.0
        assert calibrate_freshness([-10.0, float("nan"), float("inf")]) == 300.0

    def test_global_feed_state_transitions(self) -> None:
        """Verifies state machine: HEALTHY (<=60s) -> DEGRADED (<=300s) -> STALE (>300s)."""
        now = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now)
        monitor = GlobalFeedMonitor(clock=clock, enforce_market_hours=False)

        # Record message at T=0
        monitor.record_message(now)
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False

        # Advance 30s: still healthy
        clock.advance(timedelta(seconds=30))
        assert monitor.state == FeedHealthState.HEALTHY

        # Advance to 65s: degraded
        clock.advance(timedelta(seconds=35))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_fallback_active is True

        # Advance to 305s: stale
        clock.advance(timedelta(seconds=240))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_fallback_active is True

        # New message arrives: recovers to healthy
        monitor.record_message(clock.now())
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False

    def test_entry_price_crossed_book_rejection(self) -> None:
        """A crossed book (bid > ask) is invalid and rejected, falling back to trade."""
        now = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        # Crossed quote: bid 105.00 > ask 100.00
        crossed_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("105.00"),
            ask=Decimal("100.00"),
            timestamp=now,
        )
        fresh_trade = TradeQuote(
            symbol="SPY",
            price=Decimal("101.50"),
            timestamp=now,
        )

        price, mode = guard.get_entry_price(crossed_quote, fresh_trade)
        # Crossed quote must be skipped, using fresh trade instead!
        assert mode == EntryPriceMode("LAST_TRADE")
        assert price == Decimal("101.50")

    def test_anomaly_detector_exact_boundary(self) -> None:
        """Discrepancy exactly equal to 2*ATR is acceptable; strictly greater is anomaly."""
        detector = AnomalyDetector(multiplier=Decimal("2"))

        # C_SIP = 100.0, ATR = 1.0 -> 2*ATR = 2.0
        # IEX = 102.0: diff = 2.0 (diff == threshold -> not an anomaly)
        is_ok, reason = detector.check_price_anomaly(
            iex_price=Decimal("102.00"),
            sip_close=Decimal("100.00"),
            atr_5m=Decimal("1.00"),
        )
        assert is_ok is True
        assert reason == "OK"

        # IEX = 102.01: diff = 2.01 > 2.0 -> PRICE_ANOMALY
        is_ok_exceed, reason_exceed = detector.check_price_anomaly(
            iex_price=Decimal("102.01"),
            sip_close=Decimal("100.00"),
            atr_5m=Decimal("1.00"),
        )
        assert is_ok_exceed is False
        assert reason_exceed == "PRICE_ANOMALY"

    def test_entry_price_wide_spread_fallback_to_trade(self) -> None:
        """Spread > 10 bps bypasses midpoint and uses last trade if fresh."""
        now = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        # Bid 100.00, Ask 100.50 -> midpoint 100.25, spread = 0.50 / 100.25 = 49.8 bps (> 10 bps)
        wide_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("100.00"),
            ask=Decimal("100.50"),
            timestamp=now,
        )
        fresh_trade = TradeQuote(
            symbol="SPY",
            price=Decimal("100.20"),
            timestamp=now,
        )

        price, mode = guard.get_entry_price(wide_quote, fresh_trade)
        assert mode == EntryPriceMode("LAST_TRADE")
        assert price == Decimal("100.20")

    def test_future_timestamp_tolerance_and_rejection(self) -> None:
        """Timestamps within 1.0s in the future are tolerated; > 1.0s are rejected."""
        now = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        # 0.5s in future: tolerated
        fresh_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("100.00"),
            ask=Decimal("100.05"),
            timestamp=now + timedelta(milliseconds=500),
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("100.02"), timestamp=now)
        price, mode = guard.get_entry_price(fresh_quote, trade)
        assert mode == EntryPriceMode("MIDPOINT")

        # 2.0s in future: rejected as invalid/stale
        future_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("100.00"),
            ask=Decimal("100.05"),
            timestamp=now + timedelta(seconds=2),
        )
        # With stale trade too
        stale_trade = TradeQuote(symbol="SPY", price=Decimal("100.02"), timestamp=now - timedelta(seconds=500))
        price_rej, mode_rej = guard.get_entry_price(future_quote, stale_trade)
        assert mode_rej == EntryPriceMode("STALE_PRICE")
        assert price_rej is None

    def test_validate_entry_success_and_exception_raising(self) -> None:
        """Verifies full validation pipeline and error raising."""
        now = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)
        guard.record_message(now)

        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("100.00"),
            ask=Decimal("100.05"),
            timestamp=now,
        )
        trade = TradeQuote(
            symbol="SPY",
            price=Decimal("100.02"),
            timestamp=now,
        )

        # 1. Success path
        price, is_valid, reason = guard.validate_entry(
            symbol="SPY",
            quote=quote,
            trade=trade,
            sip_close=Decimal("100.00"),
            atr_5m=Decimal("0.50"),
        )
        assert is_valid is True
        assert reason == "OK"
        assert price == Decimal("100.025")

        # validate_entry_or_raise returns price
        assert guard.validate_entry_or_raise(
            symbol="SPY",
            quote=quote,
            trade=trade,
            sip_close=Decimal("100.00"),
            atr_5m=Decimal("0.50"),
        ) == Decimal("100.025")

        # 2. Price anomaly raises PriceAnomalyError
        from tbot.common.errors import PriceAnomalyError, StaleDataError

        with pytest.raises(PriceAnomalyError, match="discrepancy exceeds 2\\*ATR"):
            guard.validate_entry_or_raise(
                symbol="SPY",
                quote=quote,
                trade=trade,
                sip_close=Decimal("90.00"),  # 10.025 vs 90 -> huge anomaly
                atr_5m=Decimal("0.50"),
            )

        # 3. Disconnected feed raises StaleDataError
        guard.record_disconnect("Adversarial test")
        with pytest.raises(StaleDataError, match="Stale market data blocked entry"):
            guard.validate_entry_or_raise(
                symbol="SPY",
                quote=quote,
                trade=trade,
                sip_close=Decimal("100.00"),
                atr_5m=Decimal("0.50"),
            )


# ============================================================================
# 7. MULTI-MODULE INTEGRATED STRESS TESTS
# ============================================================================


class TestMultiModuleIntegratedAdversarial:
    """End-to-end white-box stress testing across interacting data components."""

    @pytest.mark.asyncio
    async def test_sqlite_in_memory_daily_bar_cache_concurrency(self) -> None:
        """Tests concurrent idempotent writes with SQLite in-memory engine."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        cache = DailyBarCache(session_factory=session_factory)

        sample_bars = [
            {
                "symbol": "SPY",
                "date": date(2026, 9, 1) + timedelta(days=i),
                "open": 100.0 + i,
                "high": 102.0 + i,
                "low": 99.0 + i,
                "close": 101.0 + i,
                "volume": 1000.0 * (i + 1),
                "adjusted": True,
                "feed": "sip_delayed",
            }
            for i in range(10)
        ]

        # Concurrently save the exact same bars from 10 tasks
        async def save_worker() -> int:
            return await cache.store_bars(sample_bars)

        results = await asyncio.gather(*[save_worker() for _ in range(10)])
        assert len(results) == 10

        # Retrieve and verify exactly 10 distinct records
        df = await cache.get_bars(
            symbols=["SPY"],
            start=date(2026, 9, 1),
            end=date(2026, 9, 10),
            feed="sip_delayed",
            adjusted=True,
        )
        assert len(df) == 10
        assert df["symbol"].tolist() == ["SPY"] * 10
        await engine.dispose()

    def test_resample_out_of_order_1m_bars(self) -> None:
        """Resampler correctly sorts out-of-order 1m bars before aggregating."""
        base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            {"timestamp": base_time + timedelta(minutes=3), "symbol": "SPY", "open": 103.0, "high": 104.0, "low": 102.5, "close": 103.5, "volume": 100.0},
            {"timestamp": base_time + timedelta(minutes=0), "symbol": "SPY", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 100.0},
            {"timestamp": base_time + timedelta(minutes=4), "symbol": "SPY", "open": 104.0, "high": 105.0, "low": 103.5, "close": 104.5, "volume": 100.0},
            {"timestamp": base_time + timedelta(minutes=1), "symbol": "SPY", "open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5, "volume": 100.0},
            {"timestamp": base_time + timedelta(minutes=2), "symbol": "SPY", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.5, "volume": 100.0},
        ]
        df_1m = pd.DataFrame(bars)
        df_5m = resample_1m_to_5m(df_1m)

        assert len(df_5m) == 1
        row = df_5m.iloc[0]
        # Open should be from minute 0 (100.0)
        assert row["open"] == 100.0
        # High should be max (105.0)
        assert row["high"] == 105.0
        # Low should be min (99.5)
        assert row["low"] == 99.5
        # Close should be from minute 4 (104.5)
        assert row["close"] == 104.5
        assert row["volume"] == 500.0

    def test_resample_multi_symbol_interleaved(self) -> None:
        """Interleaved timeseries for multiple symbols are processed independently."""
        base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = []
        for i in range(5):
            t = base_time + timedelta(minutes=i)
            bars.append({"timestamp": t, "symbol": "SPY", "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "volume": 100.0})
            bars.append({"timestamp": t, "symbol": "QQQ", "open": 200.0 + i, "high": 201.0 + i, "low": 199.0 + i, "close": 200.5 + i, "volume": 200.0})

        df_1m = pd.DataFrame(bars)
        df_5m = resample_1m_to_5m(df_1m)

        assert len(df_5m) == 2
        symbols = df_5m["symbol"].tolist()
        assert "QQQ" in symbols
        assert "SPY" in symbols

        spy_row = df_5m[df_5m["symbol"] == "SPY"].iloc[0]
        qqq_row = df_5m[df_5m["symbol"] == "QQQ"].iloc[0]

        assert spy_row["open"] == 100.0
        assert spy_row["close"] == 104.5
        assert spy_row["volume"] == 500.0

        assert qqq_row["open"] == 200.0
        assert qqq_row["close"] == 204.5
        assert qqq_row["volume"] == 1000.0

    @pytest.mark.asyncio
    async def test_provider_iex_5m_local_aggregation(self) -> None:
        """AlpacaProvider with 5m IEX feed aggregates 1m bars into 5m bars locally."""
        now = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(now)
        mock_client = MagicMock()

        # Mock Alpaca returning 5 x 1m bars for SPY
        base_t = datetime(2026, 9, 21, 13, 30, 0, tzinfo=UTC)
        mock_1m_bars = []
        for i in range(5):
            bar = MagicMock()
            bar.timestamp = base_t + timedelta(minutes=i)
            bar.open = 100.0 + i
            bar.high = 101.0 + i
            bar.low = 99.0 + i
            bar.close = 100.5 + i
            bar.volume = 100.0
            bar.vwap = 100.2 + i
            bar.trade_count = 10
            mock_1m_bars.append(bar)

        bar_set = MagicMock()
        bar_set.df = None
        bar_set.data = {"SPY": mock_1m_bars}
        mock_client.get_stock_bars.return_value = bar_set

        provider = AlpacaProvider(clock=clock, historical_client=mock_client)

        start = base_t
        end = base_t + timedelta(minutes=5)
        df = await provider.get_intraday_bars(["SPY"], timeframe="5m", start=start, end=end, feed="iex")

        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "SPY"
        assert df.iloc[0]["open"] == 100.0
        assert df.iloc[0]["close"] == 104.5
        assert df.iloc[0]["volume"] == 500.0
        assert df.iloc[0]["feed"] == "iex"
