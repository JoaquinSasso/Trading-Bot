"""Rate Limiter asíncrono con algoritmo Token Bucket y política de reintentos para APIs de mercado."""

from __future__ import annotations

import asyncio
import inspect
import random
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import structlog

from tbot.common.clock import Clock, SystemClock
from tbot.common.errors import MarketDataError, ProviderUnavailableError, RateLimitExceededError

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class AsyncTokenBucket:
    """Token Bucket rate limiter asíncrono y concurrente.

    Regula el consumo de peticiones hacia APIs externas. Para Alpaca Free Tier (200 req/min),
    se aplica un límite conservador de <= 180 req/min (10% de margen de seguridad).
    """

    def __init__(
        self,
        rate_limit_per_minute: float = 180.0,
        capacity: float | None = None,
        clock: Clock | None = None,
        time_func: Callable[[], float] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
        window_seconds: float = 60.0,
    ) -> None:
        self._rate_limit = rate_limit_per_minute
        self._window_seconds = window_seconds
        self._rate = rate_limit_per_minute / window_seconds  # 3.0 tokens/s por defecto
        self._capacity = float(capacity if capacity is not None else rate_limit_per_minute)
        self._tokens = self._capacity
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._time_func = time_func or self._default_time
        self._sleep_func = sleep_func or asyncio.sleep
        self._last_time = self._time_func()

        self._async_lock: asyncio.Lock | None = None
        self._thread_lock = threading.Lock()

        # Telemetría y compatibilidad con tests E2E
        self.timestamps: list[datetime] = []

    @property
    def lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    @property
    def available_tokens(self) -> float:
        with self._thread_lock:
            self._replenish(self._time_func())
            return self._tokens

    @property
    def capacity(self) -> float:
        return self._capacity

    @property
    def rate(self) -> float:
        return self._rate

    def _default_time(self) -> float:
        return self._clock.now().timestamp()

    def _replenish(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_time)
        self._last_time = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

    def _record_timestamp(self, current_time: datetime | None = None) -> None:
        now_dt = current_time if current_time is not None else self._clock.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        cutoff = now_dt - timedelta(seconds=self._window_seconds)
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        self.timestamps.append(now_dt)

    def acquire_now(self, current_time: datetime | None = None, tokens: float = 1.0) -> bool:
        """Intento no bloqueante de consumo de tokens. Retorna True si tuvo éxito."""
        with self._thread_lock:
            now = current_time.timestamp() if current_time is not None else self._time_func()
            self._replenish(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._record_timestamp(current_time)
                return True
            return False

    def acquire_sync(self, current_time: datetime | None = None, tokens: float = 1.0) -> bool:
        return self.acquire_now(current_time=current_time, tokens=tokens)

    async def acquire(
        self,
        tokens: float | datetime = 1.0,
        current_time: datetime | None = None,
        blocking: bool = True,
        max_wait_seconds: float | None = None,
    ) -> float:
        """Adquiere tokens esperando asíncronamente si es necesario.

        Retorna el tiempo esperado en segundos (0.0 si había tokens disponibles).
        """
        if isinstance(tokens, datetime):
            current_time = tokens
            token_count = 1.0
        else:
            token_count = float(tokens)

        if token_count < 0:
            raise ValueError("tokens must be non-negative")

        async with self.lock:
            with self._thread_lock:
                now = current_time.timestamp() if current_time is not None else self._time_func()
                self._replenish(now)

                if self._tokens >= token_count:
                    self._tokens -= token_count
                    self._record_timestamp(current_time)
                    return 0.0

                deficit = token_count - self._tokens
                wait_seconds = deficit / self._rate

                if not blocking or (
                    max_wait_seconds is not None and wait_seconds > max_wait_seconds
                ):
                    raise RateLimitExceededError(
                        f"Rate limit exceeded ({self._rate_limit:.0f} req/min budget). "
                        f"Wait time required: {wait_seconds:.2f}s",
                        retry_after=wait_seconds,
                        limit=int(self._rate_limit),
                    )

            await self._sleep_func(wait_seconds)

            with self._thread_lock:
                now_after = (
                    current_time.timestamp() if current_time is not None else self._time_func()
                )
                self._replenish(now_after)
                self._tokens = max(0.0, self._tokens - token_count)
                self._record_timestamp(current_time)
                return wait_seconds


class RateLimiter:
    """Sliding-window rate limiter compatible con llamadas síncronas simples."""

    def __init__(
        self,
        max_requests: int = 180,
        window_seconds: float = 60.0,
        clock: Clock | None = None,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()
        self.timestamps: list[datetime] = []
        self._lock = threading.Lock()

    def acquire(self, current_time: datetime | None = None) -> bool:
        now_dt = current_time if current_time is not None else self._clock.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        with self._lock:
            cutoff = now_dt - timedelta(seconds=self.window_seconds)
            self.timestamps = [t for t in self.timestamps if t > cutoff]
            if len(self.timestamps) >= self.max_requests:
                return False
            self.timestamps.append(now_dt)
            return True


@dataclass(frozen=True)
class BackoffPolicy:
    """Configuración inmutable de la política de backoff exponencial."""

    initial_delay: float = 1.0
    multiplier: float = 2.0
    max_retries: int = 3
    max_delay: float = 10.0
    jitter: bool = True
    retryable_status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def compute_delay(
        self,
        attempt: int,
        retry_after: float | None = None,
        random_func: Callable[[float, float], float] = random.uniform,
    ) -> float:
        """Calcula el retardo en segundos para un reintento (0-indexed)."""
        base = min(self.max_delay, self.initial_delay * (self.multiplier**attempt))
        delay = random_func(0.5 * base, base) if self.jitter else base

        if retry_after is not None and retry_after > 0:
            delay = max(delay, retry_after)

        return min(self.max_delay, delay)

    def extract_status_code(self, exc: BaseException) -> int | None:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status

        resp = getattr(exc, "response", None)
        if resp is not None:
            resp_status = getattr(resp, "status_code", None)
            if isinstance(resp_status, int):
                return resp_status

        http_err = getattr(exc, "_http_error", None)
        if http_err is not None:
            resp = getattr(http_err, "response", None)
            if resp is not None:
                resp_status = getattr(resp, "status_code", None)
                if isinstance(resp_status, int):
                    return resp_status

        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code

        msg = str(exc)
        for c in (400, 401, 403, 404, 429, 500, 502, 503, 504):
            if (
                f"HTTP {c}" in msg
                or f" {c} " in msg
                or f"status={c}" in msg.lower()
                or f"status_code={c}" in msg.lower()
            ):
                return c

        return None

    def extract_retry_after(self, exc: BaseException) -> float | None:
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, (int, float)):
            return float(retry_after)

        resp = getattr(exc, "response", None)
        if resp is None:
            http_err = getattr(exc, "_http_error", None)
            if http_err is not None:
                resp = getattr(http_err, "response", None)

        if resp is not None:
            headers = getattr(resp, "headers", None)
            if headers and "retry-after" in headers:
                try:
                    return float(headers["retry-after"])
                except (ValueError, TypeError):
                    pass
        return None

    def is_retryable(self, exc: BaseException) -> bool:
        status = self.extract_status_code(exc)
        if status is not None:
            return status in self.retryable_status_codes

        if isinstance(exc, RateLimitExceededError):
            return True

        exc_type = type(exc).__name__.lower()
        if any(term in exc_type for term in ("connect", "timeout", "network", "remote")):
            return True

        msg = str(exc).lower()
        if "429" in msg or "rate limit" in msg or "too many requests" in msg:
            return True
        return any(
            code in msg for code in ("500", "502", "503", "504", "gateway", "service unavailable")
        )

    def wrap_error(self, exc: BaseException, attempts: int = 1) -> MarketDataError:
        status = self.extract_status_code(exc)
        if (
            status == 429
            or isinstance(exc, RateLimitExceededError)
            or "rate limit" in str(exc).lower()
        ):
            retry_after = self.extract_retry_after(exc)
            return RateLimitExceededError(
                f"Rate limit exceeded (HTTP 429): {exc}",
                retry_after=retry_after,
                details={"attempts": attempts, "original_error": str(exc)},
            )
        if (
            status in (500, 502, 503, 504)
            or isinstance(exc, ProviderUnavailableError)
            or any(
                term in str(exc).lower()
                for term in (
                    "500",
                    "502",
                    "503",
                    "504",
                    "service unavailable",
                    "gateway timeout",
                    "bad gateway",
                    "internal server error",
                )
            )
        ):
            return ProviderUnavailableError(
                f"Market data provider unavailable (HTTP {status}): {exc}",
                details={"status_code": status, "attempts": attempts, "original_error": str(exc)},
            )
        return MarketDataError(
            f"Market data request failed (HTTP {status}): {exc}",
            details={"status_code": status, "attempts": attempts, "original_error": str(exc)},
        )


DEFAULT_BACKOFF_POLICY = BackoffPolicy()


async def execute_with_retry(
    func: Callable[..., Awaitable[T] | T],
    *args: Any,
    policy: BackoffPolicy = DEFAULT_BACKOFF_POLICY,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    **kwargs: Any,
) -> T:
    """Ejecuta una función síncrona (vía asyncio.to_thread) o asíncrona con backoff exponencial."""
    for attempt in range(policy.max_retries + 1):
        try:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:
            if not policy.is_retryable(exc):
                raise policy.wrap_error(exc, attempts=attempt + 1) from exc

            if attempt >= policy.max_retries:
                logger.error(
                    "Retries exhausted for market data request",
                    attempts=attempt + 1,
                    max_retries=policy.max_retries,
                    error=str(exc),
                )
                raise policy.wrap_error(exc, attempts=attempt + 1) from exc

            retry_after = policy.extract_retry_after(exc)
            delay = policy.compute_delay(attempt, retry_after=retry_after)
            logger.warning(
                "Transient error in market data call, backing off",
                attempt=attempt + 1,
                max_retries=policy.max_retries,
                delay_seconds=delay,
                error=str(exc),
            )
            await sleep_func(delay)

    raise MarketDataError("Unexpected exhaustion in execute_with_retry")
