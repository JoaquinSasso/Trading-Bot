"""Unit tests for AsyncTokenBucket, BackoffPolicy, and execute_with_retry."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError, ProviderUnavailableError, RateLimitExceededError
from tbot.data.rate_limiter import (
    AsyncTokenBucket,
    BackoffPolicy,
    RateLimiter,
    execute_with_retry,
)


class MockAPIError(Exception):
    def __init__(
        self, status_code: int, message: str = "API Error", retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class TestAsyncTokenBucket:
    @pytest.mark.asyncio
    async def test_initial_capacity_and_burst(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        assert limiter.capacity == 180.0
        assert limiter.rate == 3.0
        assert limiter.available_tokens == 180.0

        # Burst acquire all 180 tokens
        for _ in range(180):
            assert limiter.acquire_now() is True

        assert limiter.available_tokens == 0.0
        # 181st attempt without advancing clock fails
        assert limiter.acquire_now() is False

    @pytest.mark.asyncio
    async def test_replenishment_with_simulated_clock(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        # Drain all tokens
        for _ in range(180):
            limiter.acquire_now()
        assert limiter.available_tokens == 0.0

        # Advance clock by 1.0 second -> exactly 3 tokens replenished
        clock.advance(timedelta(seconds=1))
        assert abs(limiter.available_tokens - 3.0) < 1e-6

        # Advance by 10 seconds -> 30 tokens
        clock.advance(timedelta(seconds=9))
        assert abs(limiter.available_tokens - 30.0) < 1e-6

        # Advance past capacity -> capped at 180
        clock.advance(timedelta(seconds=100))
        assert limiter.available_tokens == 180.0

    @pytest.mark.asyncio
    async def test_async_acquire_blocking_with_mock_sleep(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        sleep_calls: list[float] = []

        async def mock_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            clock.advance(timedelta(seconds=seconds))

        limiter = AsyncTokenBucket(
            rate_limit_per_minute=180.0,
            clock=clock,
            sleep_func=mock_sleep,
        )

        # Drain tokens
        for _ in range(180):
            limiter.acquire_now()

        # Request 3 tokens. Rate is 3 tokens/s, deficit = 3.0, expected wait = 1.0s
        wait_time = await limiter.acquire(tokens=3.0)
        assert abs(wait_time - 1.0) < 1e-6
        assert len(sleep_calls) == 1
        assert abs(sleep_calls[0] - 1.0) < 1e-6

    @pytest.mark.asyncio
    async def test_acquire_non_blocking_raises_rate_limit_exceeded(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        for _ in range(180):
            limiter.acquire_now()

        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.acquire(tokens=1.0, blocking=False)
        assert exc_info.value.limit == 180
        assert exc_info.value.retry_after is not None

    @pytest.mark.asyncio
    async def test_max_wait_seconds_rejection(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, clock=clock)

        for _ in range(180):
            limiter.acquire_now()

        # Deficit of 60 tokens requires 20 seconds. If max_wait_seconds=5.0, must reject.
        with pytest.raises(RateLimitExceededError):
            await limiter.acquire(tokens=60.0, blocking=True, max_wait_seconds=5.0)

    @pytest.mark.asyncio
    async def test_concurrent_token_acquisition(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        limiter = AsyncTokenBucket(rate_limit_per_minute=180.0, capacity=100.0, clock=clock)

        results = await asyncio.gather(*[limiter.acquire(tokens=1.0) for _ in range(50)])
        assert len(results) == 50
        assert all(w == 0.0 for w in results)
        assert limiter.available_tokens == 50.0

    def test_rate_limiter_sliding_window(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 21, 10, 0, 0, tzinfo=UTC))
        rl = RateLimiter(max_requests=5, window_seconds=60.0, clock=clock)

        for _ in range(5):
            assert rl.acquire() is True
        assert rl.acquire() is False

        # Advance past window
        clock.advance(timedelta(seconds=61))
        assert rl.acquire() is True


class TestBackoffPolicy:
    def test_progression_without_jitter(self) -> None:
        policy = BackoffPolicy(
            initial_delay=1.0, multiplier=2.0, max_retries=3, max_delay=10.0, jitter=False
        )
        assert policy.compute_delay(0) == 1.0
        assert policy.compute_delay(1) == 2.0
        assert policy.compute_delay(2) == 4.0
        assert policy.compute_delay(3) == 8.0
        assert policy.compute_delay(4) == 10.0  # Capped at 10.0

    def test_retry_after_header_respected(self) -> None:
        policy = BackoffPolicy(initial_delay=1.0, multiplier=2.0, max_delay=10.0, jitter=False)
        assert policy.compute_delay(0, retry_after=5.0) == 5.0
        assert policy.compute_delay(0, retry_after=15.0) == 10.0  # Capped at max_delay

    def test_status_code_retryable(self) -> None:
        policy = BackoffPolicy()
        assert policy.is_retryable(MockAPIError(429)) is True
        assert policy.is_retryable(MockAPIError(500)) is True
        assert policy.is_retryable(MockAPIError(502)) is True
        assert policy.is_retryable(MockAPIError(503)) is True
        assert policy.is_retryable(MockAPIError(504)) is True

        assert policy.is_retryable(MockAPIError(400)) is False
        assert policy.is_retryable(MockAPIError(401)) is False
        assert policy.is_retryable(MockAPIError(403)) is False
        assert policy.is_retryable(MockAPIError(404)) is False

    def test_error_wrapping(self) -> None:
        policy = BackoffPolicy()
        err_429 = policy.wrap_error(MockAPIError(429, retry_after=3.5))
        assert isinstance(err_429, RateLimitExceededError)
        assert err_429.retry_after == 3.5

        err_500 = policy.wrap_error(MockAPIError(500))
        assert isinstance(err_500, ProviderUnavailableError)

        err_400 = policy.wrap_error(MockAPIError(400))
        assert isinstance(err_400, MarketDataError)
        assert not isinstance(err_400, RateLimitExceededError)


class TestExecuteWithRetry:
    @pytest.mark.asyncio
    async def test_successful_first_try(self) -> None:
        call_count = 0

        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        res = await execute_with_retry(succeed)
        assert res == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_transient_retry_success(self) -> None:
        call_count = 0
        sleep_durations: list[float] = []

        async def mock_sleep(d: float) -> None:
            sleep_durations.append(d)

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise MockAPIError(503, "Service Unavailable")
            return "recovered"

        policy = BackoffPolicy(jitter=False)
        res = await execute_with_retry(flaky, policy=policy, sleep_func=mock_sleep)
        assert res == "recovered"
        assert call_count == 3
        assert len(sleep_durations) == 2
        assert sleep_durations[0] == 1.0
        assert sleep_durations[1] == 2.0

    @pytest.mark.asyncio
    async def test_exhaustion_raises_wrapped_error(self) -> None:
        call_count = 0

        async def always_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise MockAPIError(429, "Rate Limit", retry_after=2.0)

        policy = BackoffPolicy(max_retries=2, jitter=False)
        with pytest.raises(RateLimitExceededError) as exc_info:
            await execute_with_retry(
                always_fails, policy=policy, sleep_func=lambda _: asyncio.sleep(0)
            )
        assert call_count == 3  # 1 initial + 2 retries
        assert exc_info.value.retry_after == 2.0

    @pytest.mark.asyncio
    async def test_non_retryable_fails_immediately(self) -> None:
        call_count = 0

        async def bad_request() -> str:
            nonlocal call_count
            call_count += 1
            raise MockAPIError(400, "Bad Request")

        with pytest.raises(MarketDataError) as exc_info:
            await execute_with_retry(bad_request)
        assert call_count == 1
        assert not isinstance(exc_info.value, RateLimitExceededError)

    @pytest.mark.asyncio
    async def test_sync_function_execution_in_thread(self) -> None:
        def sync_fetch(a: int, b: int) -> int:
            return a + b

        res = await execute_with_retry(sync_fetch, 10, 20)
        assert res == 30
