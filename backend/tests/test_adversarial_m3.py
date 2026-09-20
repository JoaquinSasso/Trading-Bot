"""Adversarial stress test suite for Milestone 3: Rate Limiter, Backoff Policy, and SIP feed clamping.

Empirical verification covering:
1. Rate Limiter burst stress:
   - 200+ concurrent requests on AsyncTokenBucket (acquire_now & acquire).
   - Multi-threaded stress testing with OS threads (threading.Lock and thread-safety).
   - Exact capacity constraint (<= 180 req/min) strictly maintained.
   - Smooth token replenishment over time; capping at capacity.
   - Non-blocking acquire rejection and accurate wait_time / retry_after calculation.
   - Negative and zero token edge cases.
2. Backoff retry simulation:
   - 429 burst simulation with transient recovery vs retry exhaustion.
   - Strict exponential delay progression (1.0s, 2.0s, 4.0s, 8.0s, 10.0s max).
   - Jitter randomness distribution within [0.5 * base, min(max_delay, base)].
   - Retry-After header extraction and precedence.
   - Status code classification (retryable 429/5xx vs non-retryable 4xx fast-fail).
   - Avoidance of thundering herd under concurrent 429 bursts.
3. SIP feed clamping edge cases:
   - Exact 16-minute boundary (now - 16m).
   - 15m59s (inside embargo) vs 16m01s (outside embargo).
   - Inverted start/end (start > end).
   - start == clamped_end boundary.
   - start > clamped_end (window entirely inside embargo).
   - Timezone resilience (naive, UTC, America/New_York).
   - Verification of ZERO API calls and ZERO token consumption when clamped or invalid.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError, ProviderUnavailableError, RateLimitExceededError
from tbot.data.provider import AlpacaProvider
from tbot.data.proxies import resolve_data_proxy, resolve_trading_proxy
from tbot.data.rate_limiter import (
    AsyncTokenBucket,
    BackoffPolicy,
    RateLimiter,
    execute_with_retry,
)
from tbot.data.resampler import resample_1m_to_5m


class MockAPIError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str = "API Error",
        retry_after: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.headers = headers or {}
        self.response = MagicMock(status_code=status_code, headers=self.headers)


class MockAlpacaBar:
    def __init__(
        self,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        vwap: float | None = None,
        trade_count: int = 10,
    ) -> None:
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.vwap = vwap if vwap is not None else close
        self.trade_count = trade_count


class MockBarSet:
    def __init__(self, data: dict[str, list[MockAlpacaBar]]) -> None:
        self.data = data
        self.df = None


# ============================================================================
# 1. RATE LIMITER BURST STRESS & CONCURRENCY CHALLENGES
# ============================================================================


class TestRateLimiterBurstStress:
    """Stress testing the Token Bucket and sliding window under adversarial burst traffic."""

    def test_burst_250_concurrent_acquire_now_strict_capacity(self) -> None:
        """Adversarial stress: 250 requests fired simultaneously at t=0.
        
        Strict constraint: exactly 180 must succeed, exactly 70 must be rejected.
        Available tokens must never become negative.
        """
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        results: list[bool] = []
        for _ in range(250):
            results.append(limiter.acquire_now())

        successes = sum(1 for r in results if r is True)
        failures = sum(1 for r in results if r is False)

        assert successes == 180, f"Expected exactly 180 burst grants, got {successes}"
        assert failures == 70, f"Expected exactly 70 burst rejections, got {failures}"
        assert limiter.available_tokens == 0.0, "Available tokens must be exactly 0.0"
        assert len(limiter.timestamps) == 180, "Timestamp log must record exactly 180 entries"

    def test_multithreaded_burst_thread_safety(self) -> None:
        """Adversarial stress: 20 OS threads concurrently making 15 requests each (300 total).
        
        Verifies thread-safety, absence of race conditions, and strict atomicity of counter mutations.
        """
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        def worker_task() -> list[bool]:
            return [limiter.acquire_now() for _ in range(15)]

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker_task) for _ in range(20)]
            all_results: list[bool] = []
            for f in futures:
                all_results.extend(f.result())

        assert len(all_results) == 300
        total_granted = sum(1 for r in all_results if r is True)
        total_rejected = sum(1 for r in all_results if r is False)

        assert total_granted == 180, f"Thread contention allowed {total_granted} grants (exceeded 180 capacity!)"
        assert total_rejected == 120
        assert limiter.available_tokens == 0.0

    @pytest.mark.asyncio
    async def test_burst_250_concurrent_async_acquire_pacing_and_no_deadlock(self) -> None:
        """Adversarial stress: 250 asynchronous tasks requesting tokens concurrently.
        
        Verifies:
        1. No deadlocks occur under lock contention.
        2. First 180 tasks are served immediately (wait_time == 0.0).
        3. Tasks 181..250 wait for smooth token replenishment (rate = 3.0 tokens/s).
        4. Tasks 181..250 receive strictly positive, monotonically increasing wait times.
        """
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))

        # Cooperative sleep advancing simulated clock
        async def mock_sleep(seconds: float) -> None:
            clock.advance(timedelta(seconds=seconds))
            await asyncio.sleep(0)  # Yield to event loop

        limiter = AsyncTokenBucket(
            rate_limit_per_minute=180.0,
            clock=clock,
            sleep_func=mock_sleep,
        )

        completion_times: list[float] = []

        async def worker(task_id: int) -> float:
            w = await limiter.acquire(tokens=1.0)
            completion_times.append(clock.now().timestamp())
            return w

        tasks = [asyncio.create_task(worker(i)) for i in range(250)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 250
        # First 180 had zero wait
        immediate = results[:180]
        assert all(w == 0.0 for w in immediate), "First 180 tasks must experience 0.0s wait time"

        # Remaining 70 had to wait for tokens
        delayed = results[180:]
        assert len(delayed) == 70
        # Each delayed task waited for 1 token replenishment (1/3s = 0.333s)
        expected_sleep = pytest.approx(1.0 / 3.0, rel=1e-3, abs=0.01)
        assert all(w == expected_sleep for w in delayed), "Each queued task must sleep for 1 token replenishment"

        # Verify cumulative completion pacing across time:
        t0 = datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC).timestamp()
        # First 180 completed at t0
        assert all(t == t0 for t in completion_times[:180])
        # Remaining 70 completed at strictly paced intervals: t0 + i * (1/3)
        for i, t in enumerate(completion_times[180:], start=1):
            expected_completion = pytest.approx(t0 + i * (1.0 / 3.0), rel=1e-3, abs=0.01)
            assert t == expected_completion, f"Task {180 + i} completion mismatch: {t} vs {expected_completion}"

    def test_smooth_token_replenishment_and_capping(self) -> None:
        """Adversarial stress: check sub-second fractional replenishment and ceiling capping."""
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        # Drain to 0
        for _ in range(180):
            limiter.acquire_now()
        assert limiter.available_tokens == 0.0

        # Sub-second progression: 0.1s -> 0.3 tokens
        clock.advance(timedelta(milliseconds=100))
        assert limiter.available_tokens == pytest.approx(0.3, abs=1e-6)

        # Another 0.9s (total 1.0s) -> 3.0 tokens
        clock.advance(timedelta(milliseconds=900))
        assert limiter.available_tokens == pytest.approx(3.0, abs=1e-6)

        # Another 9.0s (total 10.0s) -> 30.0 tokens
        clock.advance(timedelta(seconds=9))
        assert limiter.available_tokens == pytest.approx(30.0, abs=1e-6)

        # Advance 10,000 seconds -> tokens must be capped at capacity (180.0), never overflow
        clock.advance(timedelta(seconds=10000))
        assert limiter.available_tokens == 180.0

    @pytest.mark.asyncio
    async def test_non_blocking_acquire_raises_accurate_retry_after(self) -> None:
        """Adversarial check: non-blocking acquire on exhausted bucket raises RateLimitExceededError
        with accurate retry_after value matching the token deficit.
        """
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        for _ in range(180):
            limiter.acquire_now()

        # Deficit of 6 tokens at 3 tokens/s requires exactly 2.0s
        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.acquire(tokens=6.0, blocking=False)

        assert exc_info.value.retry_after == pytest.approx(2.0, abs=1e-5)
        assert exc_info.value.limit == 180

    @pytest.mark.asyncio
    async def test_max_wait_seconds_boundary(self) -> None:
        """Adversarial check: max_wait_seconds rejected when wait > max_wait_seconds."""
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        for _ in range(180):
            limiter.acquire_now()

        # 3 tokens deficit -> wait_seconds = 1.0s. If max_wait_seconds = 0.5s -> rejected
        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.acquire(tokens=3.0, blocking=True, max_wait_seconds=0.5)

        assert exc_info.value.retry_after == pytest.approx(1.0, abs=1e-5)

    @pytest.mark.asyncio
    async def test_negative_tokens_rejected_and_zero_tokens_allowed(self) -> None:
        """Edge case: acquiring negative tokens must raise ValueError; zero tokens succeeds."""
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        with pytest.raises(ValueError, match="tokens must be non-negative"):
            await limiter.acquire(tokens=-1.0)

        # Zero tokens succeeds immediately with 0 wait
        wait = await limiter.acquire(tokens=0.0)
        assert wait == 0.0
        assert limiter.available_tokens == 180.0

    def test_sliding_window_rate_limiter_multithreaded_burst(self) -> None:
        """Adversarial stress: RateLimiter sliding window under 300 concurrent requests across threads."""
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        rl = RateLimiter(max_requests=180, window_seconds=60.0, clock=clock)

        def worker() -> list[bool]:
            return [rl.acquire() for _ in range(15)]

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker) for _ in range(20)]
            results: list[bool] = []
            for f in futures:
                results.extend(f.result())

        assert len(results) == 300
        assert sum(1 for r in results if r is True) == 180
        assert sum(1 for r in results if r is False) == 120

        # At t=30s, still in the 60s window -> all attempts fail
        clock.advance(timedelta(seconds=30))
        assert rl.acquire() is False

        # At t=60.1s, previous 180 timestamps expire -> grants again
        clock.advance(timedelta(seconds=30, milliseconds=100))
        assert rl.acquire() is True


# ============================================================================
# 2. BACKOFF RETRY SIMULATION & 429/5XX ADVERSARIAL CHALLENGES
# ============================================================================


class TestBackoffRetrySimulation:
    """Stress testing the BackoffPolicy and execute_with_retry under adverse HTTP conditions."""

    def test_backoff_progression_deterministic(self) -> None:
        """Verify exact exponential progression capped at max_delay without jitter."""
        policy = BackoffPolicy(initial_delay=1.0, multiplier=2.0, max_retries=5, max_delay=10.0, jitter=False)

        assert policy.compute_delay(0) == 1.0
        assert policy.compute_delay(1) == 2.0
        assert policy.compute_delay(2) == 4.0
        assert policy.compute_delay(3) == 8.0
        assert policy.compute_delay(4) == 10.0  # min(10.0, 16.0)
        assert policy.compute_delay(5) == 10.0  # min(10.0, 32.0)

    def test_jitter_distribution_and_bounds(self) -> None:
        """Adversarial stress: verify jitter is strictly within [0.5 * base, min(max_delay, base)].
        
        Runs 1,000 trials to verify uniform distribution and proper variance (preventing thundering herds).
        """
        policy = BackoffPolicy(initial_delay=2.0, multiplier=2.0, max_delay=10.0, jitter=True)

        # For attempt 1: base = 4.0. Expected jitter range: [2.0, 4.0]
        samples = [policy.compute_delay(1) for _ in range(1000)]

        assert all(2.0 <= s <= 4.0 for s in samples), "All samples must be within [0.5 * base, base]"
        assert min(samples) < 2.1, "Distribution must reach lower bound"
        assert max(samples) > 3.9, "Distribution must reach upper bound"
        assert len(set(samples)) > 800, "Jitter must have high entropy (not constant)"

    def test_retry_after_header_precedence_and_capping(self) -> None:
        """Adversarial test: Retry-After header overrides computed delay but respects max_delay."""
        policy = BackoffPolicy(initial_delay=1.0, multiplier=2.0, max_delay=10.0, jitter=False)

        # Retry-After of 6.5s > base of 1.0s -> delay must be 6.5s
        assert policy.compute_delay(0, retry_after=6.5) == 6.5

        # Retry-After of 15.0s > max_delay of 10.0s -> capped at 10.0s
        assert policy.compute_delay(0, retry_after=15.0) == 10.0

        # Base of 8.0s > Retry-After of 2.0s -> higher base wins
        assert policy.compute_delay(3, retry_after=2.0) == 8.0

    @pytest.mark.asyncio
    async def test_transient_429_burst_recovers_on_third_attempt(self) -> None:
        """Simulate transient 429 burst: fails twice with 429, succeeds on third attempt."""
        attempts = 0
        sleep_records: list[float] = []

        async def mock_sleep(d: float) -> None:
            sleep_records.append(d)

        async def flaky_endpoint() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise MockAPIError(429, "Too Many Requests", retry_after=1.5)
            return "SUCCESS"

        policy = BackoffPolicy(initial_delay=1.0, max_retries=3, jitter=False)
        res = await execute_with_retry(flaky_endpoint, policy=policy, sleep_func=mock_sleep)

        assert res == "SUCCESS"
        assert attempts == 3
        assert len(sleep_records) == 2
        # First retry respected retry_after=1.5
        assert sleep_records[0] == 1.5
        # Second retry used max(base=2.0, retry_after=1.5) = 2.0
        assert sleep_records[1] == 2.0

    @pytest.mark.asyncio
    async def test_permanent_429_burst_exhaustion_raises_rate_limit_exceeded(self) -> None:
        """Simulate permanent 429 burst: always fails with 429, exhausts retries, raises RateLimitExceededError."""
        attempts = 0
        sleep_records: list[float] = []

        async def mock_sleep(d: float) -> None:
            sleep_records.append(d)

        async def permanently_blocked() -> str:
            nonlocal attempts
            attempts += 1
            raise MockAPIError(429, "Rate limit exceeded (HTTP 429)", retry_after=3.0)

        policy = BackoffPolicy(initial_delay=1.0, max_retries=3, jitter=False)
        with pytest.raises(RateLimitExceededError) as exc_info:
            await execute_with_retry(permanently_blocked, policy=policy, sleep_func=mock_sleep)

        assert attempts == 4  # Initial attempt + 3 retries
        assert len(sleep_records) == 3
        assert exc_info.value.retry_after == 3.0
        assert exc_info.value.details["attempts"] == 4

    @pytest.mark.asyncio
    async def test_5xx_server_errors_exhaustion_raises_provider_unavailable(self) -> None:
        """Simulate upstream 503 Service Unavailable: retries and raises ProviderUnavailableError."""
        attempts = 0

        async def failing_server() -> str:
            nonlocal attempts
            attempts += 1
            raise MockAPIError(503, "Service Unavailable")

        policy = BackoffPolicy(initial_delay=0.1, max_retries=2, jitter=False)
        with pytest.raises(ProviderUnavailableError) as exc_info:
            await execute_with_retry(failing_server, policy=policy, sleep_func=lambda _: asyncio.sleep(0))

        assert attempts == 3  # Initial + 2 retries
        assert exc_info.value.details["status_code"] == 503
        assert exc_info.value.details["attempts"] == 3

    @pytest.mark.asyncio
    async def test_4xx_client_errors_fail_fast_without_retrying(self) -> None:
        """Adversarial check: 400 Bad Request, 401 Unauthorized, 403 Forbidden fail FAST with 0 retries."""
        for code in (400, 401, 403, 404):
            attempts = 0
            status = code

            async def client_error(err_status: int = status) -> str:
                nonlocal attempts
                attempts += 1
                raise MockAPIError(err_status, f"Client Error {err_status}")

            policy = BackoffPolicy(max_retries=3)
            with pytest.raises(MarketDataError) as exc_info:
                await execute_with_retry(client_error, policy=policy)

            assert attempts == 1, f"HTTP {code} must abort on first attempt, but made {attempts} calls"
            assert not isinstance(exc_info.value, RateLimitExceededError)
            assert not isinstance(exc_info.value, ProviderUnavailableError)

    @pytest.mark.asyncio
    async def test_concurrent_burst_with_jitter_avoids_thundering_herd(self) -> None:
        """Simulate 30 concurrent tasks hitting a 429 and backing off with jitter.
        
        Verifies that scheduled wakeups are dispersed and do not clump at identical timestamps.
        """
        all_delays: list[float] = []

        async def mock_sleep(d: float) -> None:
            all_delays.append(d)
            await asyncio.sleep(0)

        async def burst_task() -> str:
            count = 0

            async def attempt() -> str:
                nonlocal count
                count += 1
                if count < 2:
                    raise MockAPIError(429, "Too Many Requests")
                return "DONE"

            return await execute_with_retry(
                attempt,
                policy=BackoffPolicy(initial_delay=2.0, jitter=True),
                sleep_func=mock_sleep,
            )

        tasks = [asyncio.create_task(burst_task()) for _ in range(30)]
        results = await asyncio.gather(*tasks)

        assert all(r == "DONE" for r in results)
        assert len(all_delays) == 30
        # Check that delays are well spread across [1.0, 2.0]
        unique_delays = len(set(round(d, 4) for d in all_delays))
        assert unique_delays >= 25, f"Expected high delay dispersion, got only {unique_delays} unique delays"


# ============================================================================
# 3. SIP FEED CLAMPING ADVERSARIAL EDGE CASES & ZERO API CALLS
# ============================================================================


class TestSIPFeedClampingAdversarial:
    """Adversarial stress testing for SIP delay clamping rules (end <= now - 16 min)."""

    @pytest.fixture
    def fixed_now(self) -> datetime:
        return datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)

    @pytest.fixture
    def sim_clock(self, fixed_now: datetime) -> SimulatedClock:
        return SimulatedClock(fixed_now)

    def test_sip_clamping_exact_16m_boundary(self, sim_clock: SimulatedClock, fixed_now: datetime) -> None:
        """Exact 16-minute boundary: end at exactly now - 16 min is untouched."""
        provider = AlpacaProvider(clock=sim_clock)
        exact_16m = fixed_now - timedelta(minutes=16)

        clamped = provider.clamp_sip_end(exact_16m)
        assert clamped == exact_16m

    def test_sip_clamping_15m59s_vs_16m01s(self, sim_clock: SimulatedClock, fixed_now: datetime) -> None:
        """Boundary test:
        - 15m59s ago is inside the 16m embargo window -> clamped to now - 16m.
        - 16m01s ago is outside the 16m embargo window -> untouched (now - 16m01s).
        """
        provider = AlpacaProvider(clock=sim_clock)

        t_15m59s = fixed_now - timedelta(minutes=15, seconds=59)
        t_16m01s = fixed_now - timedelta(minutes=16, seconds=1)

        clamped_59s = provider.clamp_sip_end(t_15m59s)
        clamped_01s = provider.clamp_sip_end(t_16m01s)

        assert clamped_59s == fixed_now - timedelta(minutes=16)
        assert clamped_01s == t_16m01s

    @pytest.mark.asyncio
    async def test_sip_clamped_15m59s_causes_zero_api_calls(
        self, sim_clock: SimulatedClock, fixed_now: datetime
    ) -> None:
        """When end is 15m59s ago and start is at 16m ago, clamped_end becomes 16m ago,
        making start >= clamped_end.
        
        Strict constraint: returns empty DataFrame immediately with ZERO API calls and ZERO tokens consumed.
        """
        mock_client = MagicMock()
        limiter = AsyncTokenBucket(clock=sim_clock)
        provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)

        start = fixed_now - timedelta(minutes=16)
        end = fixed_now - timedelta(minutes=15, seconds=59)

        df = await provider.get_intraday_bars(
            symbols=["SPY"],
            timeframe="1m",
            start=start,
            end=end,
            feed="sip_delayed",
        )

        assert df.empty
        assert mock_client.get_stock_bars.call_count == 0
        assert limiter.available_tokens == 180.0

    @pytest.mark.asyncio
    async def test_inverted_start_end_causes_zero_api_calls(
        self, sim_clock: SimulatedClock, fixed_now: datetime
    ) -> None:
        """Adversarial check: inverted start and end (start > end) returns empty DataFrame
        with ZERO API calls and ZERO tokens consumed.
        """
        mock_client = MagicMock()
        limiter = AsyncTokenBucket(clock=sim_clock)
        provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)

        start = fixed_now - timedelta(minutes=10)
        end = fixed_now - timedelta(minutes=20)  # Inverted!

        df = await provider.get_intraday_bars(
            symbols=["SPY"],
            timeframe="1m",
            start=start,
            end=end,
            feed="sip_delayed",
        )

        assert df.empty
        assert mock_client.get_stock_bars.call_count == 0
        assert limiter.available_tokens == 180.0

    @pytest.mark.asyncio
    async def test_start_equals_clamped_end_boundary_zero_api_calls(
        self, sim_clock: SimulatedClock, fixed_now: datetime
    ) -> None:
        """Boundary check: start == clamped_end (e.g. start is now - 16m, end is now - 5m).
        
        Returns empty DataFrame with ZERO API calls and ZERO tokens consumed.
        """
        mock_client = MagicMock()
        limiter = AsyncTokenBucket(clock=sim_clock)
        provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)

        start = fixed_now - timedelta(minutes=16)
        end = fixed_now - timedelta(minutes=5)

        df = await provider.get_intraday_bars(
            symbols=["SPY"],
            timeframe="1m",
            start=start,
            end=end,
            feed="sip_delayed",
        )

        assert df.empty
        assert mock_client.get_stock_bars.call_count == 0
        assert limiter.available_tokens == 180.0

    @pytest.mark.asyncio
    async def test_empty_symbols_list_zero_api_calls(
        self, sim_clock: SimulatedClock, fixed_now: datetime
    ) -> None:
        """Adversarial check: empty symbols list returns empty DataFrame with zero API calls."""
        mock_client = MagicMock()
        limiter = AsyncTokenBucket(clock=sim_clock)
        provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)

        df = await provider.get_intraday_bars(
            symbols=[],
            timeframe="1m",
            start=fixed_now - timedelta(minutes=30),
            end=fixed_now - timedelta(minutes=20),
            feed="sip_delayed",
        )

        assert df.empty
        assert mock_client.get_stock_bars.call_count == 0
        assert limiter.available_tokens == 180.0

    def test_timezone_naive_and_foreign_tz_clamping(self, sim_clock: SimulatedClock, fixed_now: datetime) -> None:
        """Adversarial check: clamp_sip_end handles naive datetime and non-UTC timezones without error."""
        provider = AlpacaProvider(clock=sim_clock)

        # Naive datetime 10 minutes ago
        naive_end = (fixed_now - timedelta(minutes=10)).replace(tzinfo=None)
        clamped_naive = provider.clamp_sip_end(naive_end)
        assert clamped_naive == fixed_now - timedelta(minutes=16)

        # America/New_York datetime
        et_zone = ZoneInfo("America/New_York")
        et_now = fixed_now.astimezone(et_zone)
        et_end = et_now - timedelta(minutes=10)
        clamped_et = provider.clamp_sip_end(et_end)
        assert clamped_et == et_now - timedelta(minutes=16)

    @pytest.mark.asyncio
    async def test_iex_feed_is_not_clamped_to_16m(
        self, sim_clock: SimulatedClock, fixed_now: datetime
    ) -> None:
        """Contrast check: feed='iex' does NOT clamp to now - 16m; allows querying recent data."""
        mock_client = MagicMock()
        mock_bar = MockAlpacaBar(fixed_now - timedelta(minutes=1), 500.0, 501.0, 499.0, 500.5, 100.0)
        mock_client.get_stock_bars.return_value = MockBarSet({"SPY": [mock_bar]})

        limiter = AsyncTokenBucket(clock=sim_clock)
        provider = AlpacaProvider(clock=sim_clock, historical_client=mock_client, rate_limiter=limiter)

        start = fixed_now - timedelta(minutes=2)
        end = fixed_now

        df = await provider.get_intraday_bars(
            symbols=["SPY"],
            timeframe="1m",
            start=start,
            end=end,
            feed="iex",
        )

        assert not df.empty
        assert mock_client.get_stock_bars.call_count == 1
        assert limiter.available_tokens == 179.0  # Consumed exactly 1 token


# ============================================================================
# 4. CROSS-CUTTING COMPONENT INTEGRITY CHALLENGES
# ============================================================================


class TestCrossCuttingComponentIntegrity:
    """Stress testing proxy mapping, resampling, and daily bar cache."""

    def test_proxy_mapping_bidirectional_and_sanitization(self) -> None:
        """Verify proxy resolution with whitespace, case insensitivity, and unmapped fallbacks."""
        # Signal to Exec
        assert resolve_trading_proxy("SPY") == "SPYM"
        assert resolve_trading_proxy("  spy  ") == "SPYM"
        assert resolve_trading_proxy("QQQ") == "QQQM"
        assert resolve_trading_proxy("GLD") == "GLDM"
        assert resolve_trading_proxy("AAPL") == "AAPL"  # Unmapped

        # Exec to Signal
        assert resolve_data_proxy("SPYM") == "SPY"
        assert resolve_data_proxy("  spym  ") == "SPY"
        assert resolve_data_proxy("QQQM") == "QQQ"
        assert resolve_data_proxy("GLDM") == "GLD"
        assert resolve_data_proxy("MSFT") == "MSFT"

    def test_resample_1m_to_5m_edge_cases(self) -> None:
        """Stress testing 1m to 5m aggregation: right-closed labeling, zero-volume fallback, and sorting."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)

        # Empty DataFrame
        df_empty = pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"])
        res_empty = resample_1m_to_5m(df_empty)
        assert res_empty.empty

        # 5 bars with zero volume
        bars_zero_vol = pd.DataFrame([
            {
                "symbol": "SPY",
                "timestamp": base + timedelta(minutes=i),
                "open": 500.0 + i,
                "high": 501.0 + i,
                "low": 499.0 + i,
                "close": 500.5 + i,
                "volume": 0.0,
                "vwap": 500.5 + i,
            }
            for i in range(5)
        ])

        res_zero = resample_1m_to_5m(bars_zero_vol)
        assert len(res_zero) == 1
        # Label must be right edge (09:35:00)
        assert res_zero.iloc[0]["timestamp"] == pd.Timestamp(base + timedelta(minutes=5))
        assert res_zero.iloc[0]["open"] == 500.0
        assert res_zero.iloc[0]["close"] == 504.5
        assert res_zero.iloc[0]["volume"] == 0.0
        # VWAP on 0 volume must fall back to close (not NaN)
        assert res_zero.iloc[0]["vwap"] == 504.5
        assert not np.isnan(res_zero.iloc[0]["vwap"])

    def test_backoff_status_code_and_message_extraction_formats(self) -> None:
        """Adversarial check: extract_status_code extracts codes from various error patterns."""
        policy = BackoffPolicy()

        # Attribute .status_code
        err1 = Exception("error")
        err1.status_code = 429  # type: ignore[attr-defined]
        assert policy.extract_status_code(err1) == 429

        # Nested .response.status_code
        err2 = Exception("error")
        err2.response = MagicMock(status_code=503)  # type: ignore[attr-defined]
        assert policy.extract_status_code(err2) == 503

        # String patterns
        assert policy.extract_status_code(Exception("HTTP 429 Too Many Requests")) == 429
        assert policy.extract_status_code(Exception("Received 502 Bad Gateway from server")) == 502
        assert policy.extract_status_code(Exception("error with status=504")) == 504
        assert policy.extract_status_code(Exception("Unexpected connection drop")) is None

    def test_backoff_retry_after_extraction_variants(self) -> None:
        """Adversarial check: extract_retry_after handles direct attributes, header dicts, and malformed values."""
        policy = BackoffPolicy()

        # Direct attribute
        err1 = Exception("error")
        err1.retry_after = 4.2  # type: ignore[attr-defined]
        assert policy.extract_retry_after(err1) == 4.2

        # Header in response
        err2 = Exception("error")
        err2.response = MagicMock(headers={"retry-after": "7.5"})  # type: ignore[attr-defined]
        assert policy.extract_retry_after(err2) == 7.5

        # Malformed header value (non-float string) -> safe fallback to None
        err3 = Exception("error")
        err3.response = MagicMock(headers={"retry-after": "not-a-number"})  # type: ignore[attr-defined]
        assert policy.extract_retry_after(err3) is None

        # No header
        err4 = Exception("error")
        err4.response = MagicMock(headers={})  # type: ignore[attr-defined]
        assert policy.extract_retry_after(err4) is None

    @pytest.mark.asyncio
    async def test_provider_shared_rate_limiter_drain(self) -> None:
        """Stress check: shared token bucket limiter strictly limits multiple provider calls."""
        clock = SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))
        shared_limiter = AsyncTokenBucket(rate_limit_per_minute=2.0, capacity=2.0, clock=clock)

        mock_client = MagicMock()
        mock_client.get_stock_latest_trade.return_value = {
            "SPY": MagicMock(price=500.0, size=10, timestamp=clock.now())
        }

        provider = AlpacaProvider(clock=clock, historical_client=mock_client, rate_limiter=shared_limiter)

        # Call 1: succeeds (1 token consumed)
        t1 = await provider.get_latest_trade("SPY")
        assert t1.price == Decimal("500.0")
        assert shared_limiter.available_tokens == 1.0

        # Call 2: succeeds (2nd token consumed)
        t2 = await provider.get_latest_trade("SPY")
        assert t2.price == Decimal("500.0")
        assert shared_limiter.available_tokens == 0.0

        # Call 3: bucket is drained. If non-blocking / sync, raises RateLimitExceededError
        with pytest.raises(RateLimitExceededError):
            await shared_limiter.acquire(tokens=1.0, blocking=False)

    @pytest.mark.asyncio
    async def test_provider_intraday_start_equals_end_zero_api_calls(self) -> None:
        """Edge case: start == end for intraday bars query returns empty DataFrame with zero API calls."""
        clock = SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))
        mock_client = MagicMock()
        limiter = AsyncTokenBucket(clock=clock)
        provider = AlpacaProvider(clock=clock, historical_client=mock_client, rate_limiter=limiter)

        t = clock.now() - timedelta(minutes=30)
        df = await provider.get_intraday_bars(
            symbols=["SPY"],
            timeframe="1m",
            start=t,
            end=t,
            feed="sip_delayed",
        )

        assert df.empty
        assert mock_client.get_stock_bars.call_count == 0
        assert limiter.available_tokens == 180.0

    @pytest.mark.asyncio
    async def test_provider_daily_bars_inverted_date_range_zero_api_calls(self) -> None:
        """Edge case: start > end in get_daily_bars returns empty DataFrame with zero API calls."""
        clock = SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))
        mock_client = MagicMock()
        limiter = AsyncTokenBucket(clock=clock)
        provider = AlpacaProvider(clock=clock, historical_client=mock_client, rate_limiter=limiter)

        df = await provider.get_daily_bars(
            symbols=["SPY"],
            start=date(2026, 9, 25),
            end=date(2026, 9, 20),  # Inverted!
            adjusted=True,
        )

        assert df.empty
        assert mock_client.get_stock_bars.call_count == 0
        assert limiter.available_tokens == 180.0

