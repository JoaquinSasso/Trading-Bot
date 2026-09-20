"""Fixtures, test harnesses, and mock providers for Phase 1 E2E Test Suite.

Provides:
- SimulatedClock instances at key trading times.
- Authoritative reference fixtures for indicator validation.
- Mock implementations of AlpacaProvider, FinnhubClient, and WebSocket feeds.
- Dynamic module resolution for progressive milestone verification.
- Precision assertion helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import Clock, SimulatedClock
from tbot.common.errors import (
    MarketDataError,
)

# ---------------------------------------------------------------------------
# Core Domain Dataclasses (Interface Contracts as per PROJECT.md)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceQuote:
    symbol: str
    bid: Decimal
    ask: Decimal
    midpoint: Decimal
    spread_bps: float
    timestamp: datetime
    is_stale: bool = False
    reason_code: str | None = None


@dataclass(frozen=True)
class TradeQuote:
    symbol: str
    price: Decimal
    size: int
    timestamp: datetime
    is_stale: bool = False
    reason_code: str | None = None


@dataclass(frozen=True)
class MarketClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class TradingDay:
    date: date
    open_time: datetime
    close_time: datetime
    is_early_close: bool = False


@dataclass(frozen=True)
class RegimeSnapshot:
    timestamp: datetime
    regime: Literal["BULL_CALM", "BULL_VOLATILE", "BEAR", "UNKNOWN"]
    spy_close: Decimal
    sma_200: Decimal
    realized_vol_20d: float
    vol_70th_percentile: float
    is_blocked: bool = False


# ---------------------------------------------------------------------------
# Clock Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Standard regular session open: Friday 2026-09-18 09:30:00 ET (13:30:00 UTC)."""
    # 2026-09-18: ET is EDT (UTC-4)
    return SimulatedClock(datetime(2026, 9, 18, 13, 30, 0, tzinfo=UTC))


@pytest.fixture
def sim_clock_midday() -> SimulatedClock:
    """Midday trading session: Friday 2026-09-18 12:00:00 ET (16:00:00 UTC)."""
    return SimulatedClock(datetime(2026, 9, 18, 16, 0, 0, tzinfo=UTC))


@pytest.fixture
def sim_clock_afternoon() -> SimulatedClock:
    """S1 evaluation window: Friday 2026-09-18 15:30:00 ET (19:30:00 UTC)."""
    return SimulatedClock(datetime(2026, 9, 18, 19, 30, 0, tzinfo=UTC))


@pytest.fixture
def sim_clock_close_window() -> SimulatedClock:
    """Close blocking window: Friday 2026-09-18 15:52:00 ET (19:52:00 UTC)."""
    return SimulatedClock(datetime(2026, 9, 18, 19, 52, 0, tzinfo=UTC))


@pytest.fixture
def sim_clock_fomc() -> SimulatedClock:
    """FOMC announcement window: Wednesday 2026-09-16 13:45:00 ET (17:45:00 UTC)."""
    return SimulatedClock(datetime(2026, 9, 16, 17, 45, 0, tzinfo=UTC))


@pytest.fixture
def sim_clock_early_close() -> SimulatedClock:
    """Black Friday early close session: 2026-11-27 12:52:00 ET (17:52:00 UTC). Market closes at 13:00 ET (18:00 UTC)."""
    return SimulatedClock(datetime(2026, 11, 27, 17, 52, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Reference Fixture Loader
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def reference_indicators() -> dict[str, Any]:
    """Loads authoritative reference indicator fixture generated from mathematical formulas."""
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "reference_indicators.json"
    assert fixture_path.exists(), f"Reference fixture missing: {fixture_path}"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Assertion Helpers
# ---------------------------------------------------------------------------


def assert_series_close(
    actual: pd.Series | np.ndarray | list[float],
    expected: pd.Series | np.ndarray | list[float],
    tol: float = 1e-6,
    check_nan: bool = True,
) -> None:
    """Validates that numerical series match expected oracle values within tolerance <= 1e-6."""
    s_actual = pd.Series(actual)
    s_expected = pd.Series(expected)

    assert len(s_actual) == len(s_expected), (
        f"Length mismatch: {len(s_actual)} != {len(s_expected)}"
    )

    if check_nan:
        actual_nans = s_actual.isna()
        expected_nans = s_expected.isna()
        assert actual_nans.equals(expected_nans), (
            f"NaN mask mismatch:\nActual NaNs at: {np.where(actual_nans)[0]}\nExpected NaNs at: {np.where(expected_nans)[0]}"
        )

    # Compare non-NaN values
    valid_mask = ~s_expected.isna() & ~s_actual.isna()
    if valid_mask.any():
        diff = (s_actual[valid_mask] - s_expected[valid_mask]).abs()
        max_diff = diff.max()
        assert max_diff <= tol, f"Maximum difference {max_diff} exceeds tolerance {tol}"


# ---------------------------------------------------------------------------
# High-Fidelity Mock Engines for Offline E2E Testing
# ---------------------------------------------------------------------------


class MockRateLimiter:
    """Shared sliding token bucket rate limiter enforcing <= 180 req/min."""

    def __init__(self, max_requests: int = 180, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps: list[datetime] = []

    def acquire(self, current_time: datetime) -> bool:
        cutoff = current_time - timedelta(seconds=self.window_seconds)
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        if len(self.timestamps) >= self.max_requests:
            return False
        self.timestamps.append(current_time)
        return True


class MockAlpacaProvider:
    """Mock MarketDataProvider with full offline simulation of hybrid feeds, clamping, and backoff."""

    def __init__(self, clock: Clock, rate_limiter: MockRateLimiter | None = None) -> None:
        self.clock = clock
        self.rate_limiter = rate_limiter or MockRateLimiter()
        self.call_count = 0
        self.error_to_inject: Exception | None = None
        self.consecutive_429_count = 0

        # Proxy map
        self.proxy_map = {
            "SPY": "SPYM",
            "QQQ": "QQQM",
            "GLD": "GLDM",
        }

        # Cached bars store
        self.db_cache: dict[str, list[dict[str, Any]]] = {}

    def map_symbol(
        self, symbol: str, direction: Literal["signal_to_exec", "exec_to_signal"] = "signal_to_exec"
    ) -> str:
        if direction == "signal_to_exec":
            return self.proxy_map.get(symbol, symbol)
        # Reverse map
        rev = {v: k for k, v in self.proxy_map.items()}
        return rev.get(symbol, symbol)

    def clamp_sip_end(self, end: datetime) -> datetime:
        """Enforces SIP delay: end <= now - 16 min."""
        max_allowed = self.clock.now() - timedelta(minutes=16)
        return min(end, max_allowed)

    def get_market_clock(self) -> MarketClock:
        now = self.clock.now()
        # In EDT (UTC-4): 9:30 ET is 13:30 UTC, 16:00 ET is 20:00 UTC
        weekday = now.weekday()
        is_weekday = weekday < 5
        market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=20, minute=0, second=0, microsecond=0)
        is_open = is_weekday and (market_open <= now < market_close)

        next_open = market_open if now < market_open else (market_open + timedelta(days=1))
        next_close = market_close if now < market_close else (market_close + timedelta(days=1))

        return MarketClock(
            timestamp=now, is_open=is_open, next_open=next_open, next_close=next_close
        )

    def get_calendar(self, start: date, end: date) -> list[TradingDay]:
        days: list[TradingDay] = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:  # Weekday
                # Thanksgiving Friday early close example
                is_early = cur.month == 11 and cur.day == 27
                close_hour = 18 if is_early else 20  # 13:00 ET vs 16:00 ET in UTC
                open_dt = datetime(cur.year, cur.month, cur.day, 13, 30, 0, tzinfo=UTC)
                close_dt = datetime(cur.year, cur.month, cur.day, close_hour, 0, 0, tzinfo=UTC)
                days.append(
                    TradingDay(
                        date=cur, open_time=open_dt, close_time=close_dt, is_early_close=is_early
                    )
                )
            cur += timedelta(days=1)
        return days

    async def get_daily_bars(
        self, symbols: list[str], start: date, end: date, adjusted: bool = True
    ) -> pd.DataFrame:
        self.call_count += 1
        if self.error_to_inject:
            err = self.error_to_inject
            self.error_to_inject = None
            raise err

        if not self.rate_limiter.acquire(self.clock.now()):
            raise MarketDataError("Rate limit exceeded (180 req/min budget)")

        holidays = {date(2026, 1, 1), date(2026, 11, 26), date(2026, 12, 25)}
        records = []
        cur = start
        today = self.clock.today()
        while cur <= end:
            if cur.weekday() < 5 and cur not in holidays and cur <= today:
                for sym in symbols:
                    records.append(
                        {
                            "symbol": sym,
                            "date": cur,
                            "open": 500.0,
                            "high": 505.0,
                            "low": 498.0,
                            "close": 502.0,
                            "volume": 1000000,
                            "adjusted": adjusted,
                            "feed": "sip_delayed",
                        }
                    )
            cur += timedelta(days=1)
        return pd.DataFrame(records)

    async def get_intraday_bars(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        feed: str = "iex",
        adjusted: bool = True,
    ) -> pd.DataFrame:
        self.call_count += 1
        if not self.rate_limiter.acquire(self.clock.now()):
            raise MarketDataError("Rate limit exceeded (180 req/min budget)")

        if feed == "sip_delayed":
            clamped_end = self.clamp_sip_end(end)
            if start >= clamped_end:
                return pd.DataFrame(
                    columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"]
                )
            eff_end = clamped_end
        else:
            eff_end = end

        records = []
        step = timedelta(minutes=1 if timeframe == "1m" else 5)
        cur = start
        while cur <= eff_end:
            for sym in symbols:
                records.append(
                    {
                        "timestamp": cur,
                        "symbol": sym,
                        "open": 500.0,
                        "high": 501.0,
                        "low": 499.5,
                        "close": 500.5,
                        "volume": 2000,
                        "is_synthetic": False,
                    }
                )
            cur += step
        return pd.DataFrame(records)

    async def get_latest_quote(self, symbol: str) -> PriceQuote:
        now = self.clock.now()
        bid = Decimal("500.00")
        ask = Decimal("500.04")  # spread: 0.04 / 500.02 = 0.8 bps <= 10 bps
        mid = (bid + ask) / Decimal("2")
        spread_bps = float((ask - bid) / mid * 10000)
        return PriceQuote(
            symbol=symbol, bid=bid, ask=ask, midpoint=mid, spread_bps=spread_bps, timestamp=now
        )

    async def get_latest_trade(self, symbol: str) -> TradeQuote:
        now = self.clock.now()
        return TradeQuote(symbol=symbol, price=Decimal("500.02"), size=100, timestamp=now)


class MockStalenessGuard:
    """Two-level staleness guard implementation for requirement validation."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.discard_counts: dict[str, int] = {"STALE_PRICE": 0, "PRICE_ANOMALY": 0}

    def check_global_feed(self, last_msg_time: datetime) -> bool:
        """Global WebSocket feed healthy if last message received <= 60s ago during regular hours."""
        now = self.clock.now()
        # Only enforce during regular hours 13:30 - 20:00 UTC
        if 13 <= now.hour < 20:
            return (now - last_msg_time).total_seconds() <= 60.0
        return True

    def calculate_tau_symbol(self, p99_trade_gap_seconds: float) -> timedelta:
        """tau_symbol = min(10m, max(3m, p99_trade_gap))."""
        clamped_secs = min(600.0, max(180.0, p99_trade_gap_seconds))
        return timedelta(seconds=clamped_secs)

    def check_symbol_freshness(
        self, symbol: str, last_update: datetime, tau: timedelta
    ) -> tuple[bool, str]:
        now = self.clock.now()
        age = now - last_update
        if age > tau:
            return False, "POTENTIAL_HALT"
        return True, "OK"

    def get_entry_price(
        self, quote: PriceQuote, trade: TradeQuote, tau: timedelta
    ) -> tuple[Decimal | None, str]:
        """Strict entry price selector:
        - Midpoint if spread <= 10 bps.
        - Else last trade if fresh <= tau.
        - Else STALE_PRICE.
        """
        now = self.clock.now()
        if quote.spread_bps <= 10.0 and (now - quote.timestamp) <= tau:
            return quote.midpoint, "MIDPOINT"
        if (now - trade.timestamp) <= tau:
            return trade.price, "LAST_TRADE"
        self.discard_counts["STALE_PRICE"] = self.discard_counts.get("STALE_PRICE", 0) + 1
        return None, "STALE_PRICE"

    def check_price_anomaly(
        self, iex_price: Decimal, sip_close: Decimal, atr_5m: Decimal
    ) -> tuple[bool, str]:
        """Flags PRICE_ANOMALY if |iex_price - sip_close| > 2 * atr_5m."""
        diff = abs(iex_price - sip_close)
        threshold = Decimal("2") * atr_5m
        if diff > threshold:
            self.discard_counts["PRICE_ANOMALY"] = self.discard_counts.get("PRICE_ANOMALY", 0) + 1
            return False, "PRICE_ANOMALY"
        return True, "OK"


class MockFinnhubEarningsClient:
    """Mock Finnhub earnings calendar client with 3-day fail-closed cache policy."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.cache: dict[str, tuple[date, datetime]] = {}  # symbol -> (earnings_date, cached_at)
        self.network_error = False

    def seed_earnings(
        self, symbol: str, earnings_date: date, cached_at: datetime | None = None
    ) -> None:
        ts = cached_at or self.clock.now()
        self.cache[symbol] = (earnings_date, ts)

    async def get_earnings_date(self, symbol: str) -> tuple[date | None, bool, str]:
        """Returns (earnings_date, is_blocked, reason_code).
        Fail-closed: if network error and cache > 3 days old (or missing), blocks symbol.
        """
        now = self.clock.now()
        if self.network_error:
            if symbol in self.cache:
                earnings_date, cached_at = self.cache[symbol]
                cache_age = now - cached_at
                if cache_age <= timedelta(days=3):
                    return earnings_date, False, "CACHE_VALID"
            # Fail closed
            return None, True, "EARNINGS_DATA_UNAVAILABLE"

        if symbol in self.cache:
            return self.cache[symbol][0], False, "OK"
        return None, False, "NO_UPCOMING_EARNINGS"

    def is_earnings_approaching(self, earnings_date: date, block_days: int = 2) -> bool:
        """Blocks entries 2 trading days prior to earnings release."""
        today = self.clock.today()
        # Trading day difference
        day_diff = (earnings_date - today).days
        return 0 <= day_diff <= block_days


class MockMacroFilter:
    """Mock Macro Filter ingesting macro events and enforcing blackout windows."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.events: list[dict[str, Any]] = []

    def load_events(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def is_macro_window_active(
        self, current_time: datetime, strategy_type: str = "intraday"
    ) -> tuple[bool, str]:
        for ev in self.events:
            ev_time = ev["timestamp"]
            impact = ev.get("impact", "high")
            ev_type = ev.get("type", "macro")

            # FOMC all-day swing strategy lockout
            if (
                ev_type == "FOMC"
                and strategy_type == "swing"
                and ev_time.date() == current_time.date()
            ):
                return True, "FOMC_ALL_DAY_SWING_BLOCK"

            # 30m window around event
            window_start = ev_time - timedelta(minutes=30)
            window_end = ev_time + timedelta(minutes=30)
            if window_start <= current_time <= window_end:
                return True, f"MACRO_WINDOW_{impact.upper()}"
        return False, "OK"

    def check_calendar_expiry(
        self, current_time: datetime, horizon_days: int = 30
    ) -> tuple[bool, int]:
        future_events = [ev for ev in self.events if ev["timestamp"] > current_time]
        if not future_events:
            return True, 0  # Alert: 0 days remaining
        max_future_date = max(ev["timestamp"] for ev in future_events)
        remaining_days = (max_future_date.date() - current_time.date()).days
        needs_alert = remaining_days < horizon_days
        return needs_alert, remaining_days


# ---------------------------------------------------------------------------
# Module Loader Helpers for Milestone Progressive Verification
# ---------------------------------------------------------------------------


def get_pure_indicators_module():
    """Dynamically loads tbot.indicators.pure if available, otherwise returns None."""
    try:
        from tbot.indicators import pure

        return pure
    except ImportError:
        return None


def get_data_provider_class():
    """Dynamically loads AlpacaProvider if available, otherwise returns None."""
    try:
        from tbot.data.provider import AlpacaProvider

        return AlpacaProvider
    except ImportError:
        return None


def get_staleness_guard_class():
    """Dynamically loads StalenessGuard if available, otherwise returns None."""
    try:
        from tbot.data.staleness import StalenessGuard

        return StalenessGuard
    except ImportError:
        return None


def get_regime_filter_class():
    """Dynamically loads RegimeFilter if available, otherwise returns None."""
    try:
        from tbot.regime.filter import RegimeFilter

        return RegimeFilter
    except ImportError:
        return None


def get_event_calendar_class():
    """Dynamically loads EventCalendar if available, otherwise returns None."""
    try:
        from tbot.events.calendar import EventCalendar

        return EventCalendar
    except ImportError:
        return None
