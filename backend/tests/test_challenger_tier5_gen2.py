"""Adversarial white-box empirical stress-test suite for Tier 5.1 Gen 2.

Empirically validates:
1. Complete resolution of the 6 defects reported by Challenger 1:
   - Cache > 3d returns (None, True, 'EARNINGS_DATA_UNAVAILABLE') even with network_error=False.
   - Real HTTP 500 error in refresh_earnings fails closed with EARNINGS_DATA_UNAVAILABLE.
   - Friday entry prior to Monday earnings is blocked with EARNINGS_APPROACHING.
   - DataFrames with uppercase "Date" or "timestamp" are deduplicated and trigger INSUFFICIENT_BARS if unique days < 200.
   - Setting naive datetimes via cal.events = [...] no longer crashes with TypeError.
   - parse_hour_code(12) and other non-strings no longer crash with AttributeError.
2. Extensive boundary conditions, edge cases, holiday schedules, and stress scenarios.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.data.calendar import MarketCalendar
from tbot.events.calendar import (
    EventCalendar,
    FinnhubEarningsClient,
    MacroFilter,
    parse_hour_code,
)
from tbot.regime.filter import MarketRegime, RegimeFilter

ET_TIMEZONE = ZoneInfo("America/New_York")


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Fixed clock at 2026-04-17 14:00:00 UTC (Friday trading session)."""
    return SimulatedClock(datetime(2026, 4, 17, 14, 0, 0, tzinfo=UTC))


@pytest.fixture
def official_holidays_2026() -> set[date]:
    """Official 2026 US market holidays."""
    return {
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Washington's Birthday (Presidents' Day)
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day (Observed)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26), # Thanksgiving
        date(2026, 12, 25), # Christmas
    }


@pytest.fixture
def market_calendar_2026(official_holidays_2026: set[date]) -> MarketCalendar:
    """MarketCalendar covering the entire 2026 calendar year."""
    clock = SimulatedClock(datetime(2026, 4, 17, 14, 0, 0, tzinfo=UTC))
    start_d = date(2026, 1, 1)
    end_d = date(2026, 12, 31)
    days = MarketCalendar.generate_standard_trading_days(
        start=start_d,
        end=end_d,
        holidays=official_holidays_2026,
    )
    return MarketCalendar(
        clock=clock,
        initial_days=days,
        covered_range=(start_d, end_d),
    )


# ============================================================================
# 1. Defect 1: Unconditional Cache Expiration (72h / 3 days)
# ============================================================================


class TestDefect1CacheExpiration:
    """Empirical verification of the 72h cache cutoff regardless of network_error."""

    def test_cache_strictly_greater_than_72h_fails_closed(self, sim_clock: SimulatedClock) -> None:
        """Cache age > 72h returns EARNINGS_DATA_UNAVAILABLE even with network_error=False."""
        client = FinnhubEarningsClient(clock=sim_clock)
        assert client.network_error is False

        # Seed cache exactly 72 hours + 1 second ago
        stale_time = sim_clock.now() - timedelta(hours=72, seconds=1)
        client.seed_earnings("AAPL", date(2026, 5, 1), cached_at=stale_time)

        edate, is_blocked, reason = client.get_earnings_date_sync("AAPL")
        assert is_blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"
        assert edate is None

    def test_cache_exactly_72h_is_valid(self, sim_clock: SimulatedClock) -> None:
        """Cache age exactly <= 72h returns CACHE_VALID with network_error=False."""
        client = FinnhubEarningsClient(clock=sim_clock)
        assert client.network_error is False

        # Seed cache exactly 72 hours ago
        boundary_time = sim_clock.now() - timedelta(hours=72)
        client.seed_earnings("AAPL", date(2026, 5, 1), cached_at=boundary_time)

        edate, is_blocked, reason = client.get_earnings_date_sync("AAPL")
        assert is_blocked is False
        assert reason == "CACHE_VALID"
        assert edate == date(2026, 5, 1)

    @pytest.mark.parametrize("cache_age_days", [3.1, 4, 10, 50, 100, 365])
    def test_cache_various_stale_ages_all_fail_closed(
        self, sim_clock: SimulatedClock, cache_age_days: float
    ) -> None:
        """Parametric test across diverse stale cache ages."""
        client = FinnhubEarningsClient(clock=sim_clock)
        client.network_error = False

        past_time = sim_clock.now() - timedelta(days=cache_age_days)
        client.seed_earnings("MSFT", date(2026, 6, 15), cached_at=past_time)

        edate, is_blocked, reason = client.get_earnings_date_sync("MSFT")
        assert is_blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"
        assert edate is None

    @pytest.mark.asyncio
    async def test_async_get_earnings_date_matches_sync_on_stale_cache(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Async get_earnings_date exhibits identical fail-closed behavior."""
        client = FinnhubEarningsClient(clock=sim_clock)
        past_time = sim_clock.now() - timedelta(days=5)
        client.seed_earnings("GOOG", date(2026, 7, 20), cached_at=past_time)

        edate, is_blocked, reason = await client.get_earnings_date("GOOG")
        assert is_blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"
        assert edate is None

    def test_event_calendar_facade_blocks_stale_cache(self, sim_clock: SimulatedClock) -> None:
        """EventCalendar.is_symbol_blocked blocks entry when cache is stale."""
        cal = EventCalendar(clock=sim_clock)
        past_time = sim_clock.now() - timedelta(days=4)
        cal.seed_earnings("NVDA", date(2026, 8, 10), cached_at=past_time)

        blocked, reason = cal.is_symbol_blocked("NVDA")
        assert blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"


# ============================================================================
# 2. Defect 2: HTTP 500 Network Outage and Error Tracking
# ============================================================================


class TestDefect2NetworkOutageFailClosed:
    """Empirical verification that HTTP errors fail closed with EARNINGS_DATA_UNAVAILABLE."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [500, 502, 503, 504, 429, 403, 401])
    async def test_refresh_earnings_http_error_codes_fail_closed(
        self, sim_clock: SimulatedClock, status_code: int
    ) -> None:
        """Various HTTP error status codes fail closed for uncached symbols."""
        cal = EventCalendar(clock=sim_clock)
        mock_transport = httpx.MockTransport(lambda req: httpx.Response(status_code))
        cal.earnings_client.http_client = httpx.AsyncClient(transport=mock_transport)

        await cal.refresh_earnings(["AAPL"])

        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_refresh_earnings_network_timeout_fails_closed(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Connection / read timeouts fail closed."""
        cal = EventCalendar(clock=sim_clock)

        def raise_timeout(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("Connection timed out to Finnhub")

        cal.earnings_client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(raise_timeout))

        await cal.refresh_earnings(["TSLA"])

        blocked, reason = cal.is_symbol_blocked("TSLA")
        assert blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_partial_failure_in_batch_refresh(self, sim_clock: SimulatedClock) -> None:
        """In a multi-symbol refresh, only failed symbols are blocked."""
        cal = EventCalendar(clock=sim_clock)

        def mock_handler(req: httpx.Request) -> httpx.Response:
            if "AAPL" in str(req.url):
                return httpx.Response(
                    200,
                    json={
                        "earningsCalendar": [
                            {"symbol": "AAPL", "date": "2026-05-15", "hour": "amc"}
                        ]
                    },
                )
            return httpx.Response(500, json={"error": "Finnhub server failure"})

        cal.earnings_client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

        await cal.refresh_earnings(["AAPL", "MSFT", "AMZN"])

        # AAPL succeeded -> valid cache -> not approaching
        blocked_aapl, reason_aapl = cal.is_symbol_blocked("AAPL")
        assert blocked_aapl is False
        assert reason_aapl == "OK"

        # MSFT & AMZN failed -> failed_symbols set -> fail-closed
        blocked_msft, reason_msft = cal.is_symbol_blocked("MSFT")
        assert blocked_msft is True
        assert reason_msft == "EARNINGS_DATA_UNAVAILABLE"

        blocked_amzn, reason_amzn = cal.is_symbol_blocked("AMZN")
        assert blocked_amzn is True
        assert reason_amzn == "EARNINGS_DATA_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_recovery_from_network_failure(self, sim_clock: SimulatedClock) -> None:
        """A symbol that previously failed recovers when subsequent refresh succeeds."""
        cal = EventCalendar(clock=sim_clock)

        # Run 1: Outage
        cal.earnings_client.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(500))
        )
        await cal.refresh_earnings(["AAPL"])
        assert cal.is_symbol_blocked("AAPL") == (True, "EARNINGS_DATA_UNAVAILABLE")

        # Run 2: Recovered
        cal.earnings_client.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    json={
                        "earningsCalendar": [
                            {"symbol": "AAPL", "date": "2026-05-20", "hour": "bmo"}
                        ]
                    },
                )
            )
        )
        await cal.refresh_earnings(["AAPL"])
        assert cal.is_symbol_blocked("AAPL") == (False, "OK")


# ============================================================================
# 3. Defect 3: Trading Days Earnings Lead Time Across Weekends & Holidays
# ============================================================================


class TestDefect3TradingDaysEarningsLeadTime:
    """Empirical verification of 2 trading days blackout across weekend and holiday gaps."""

    def test_friday_before_monday_earnings_blocked_without_market_calendar(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Friday 2026-04-17 before Monday 2026-04-20 earnings (1 trading day) is blocked."""
        sim_clock.set_time(datetime(2026, 4, 17, 14, 0, tzinfo=UTC))
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)
        cal.seed_earnings("AAPL", date(2026, 4, 20))

        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_APPROACHING"

    def test_thursday_before_monday_earnings_blocked_without_market_calendar(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Thursday 2026-04-16 before Monday 2026-04-20 earnings (2 trading days) is blocked."""
        sim_clock.set_time(datetime(2026, 4, 16, 14, 0, tzinfo=UTC))
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)
        cal.seed_earnings("AAPL", date(2026, 4, 20))

        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_APPROACHING"

    def test_wednesday_before_monday_earnings_allowed_without_market_calendar(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Wednesday 2026-04-15 before Monday 2026-04-20 earnings (3 trading days) is NOT blocked."""
        sim_clock.set_time(datetime(2026, 4, 15, 14, 0, tzinfo=UTC))
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)
        cal.seed_earnings("AAPL", date(2026, 4, 20))

        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is False
        assert reason == "OK"

    def test_friday_before_monday_earnings_blocked_with_market_calendar(
        self, sim_clock: SimulatedClock, market_calendar_2026: MarketCalendar
    ) -> None:
        """With injected MarketCalendar, Friday before Monday is blocked."""
        sim_clock.set_time(datetime(2026, 4, 17, 14, 0, tzinfo=UTC))
        cal = EventCalendar(
            clock=sim_clock,
            market_calendar=market_calendar_2026,
            earnings_block_days=2,
        )
        cal.seed_earnings("AAPL", date(2026, 4, 20))

        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_APPROACHING"

    def test_holiday_gap_good_friday_trading_days(
        self, sim_clock: SimulatedClock, market_calendar_2026: MarketCalendar
    ) -> None:
        """Good Friday (2026-04-03) is a holiday.

        Thursday 2026-04-02 to Tuesday 2026-04-07 earnings:
        Trading days between Thu and Tue are: Thu, Mon, Tue (3 trading days in inclusive interval).
        Trading days away = 3 - 1 = 2 trading days.
        With block_days=2, Thursday 2026-04-02 MUST be blocked!
        """
        sim_clock.set_time(datetime(2026, 4, 2, 14, 0, tzinfo=UTC))
        cal = EventCalendar(
            clock=sim_clock,
            market_calendar=market_calendar_2026,
            earnings_block_days=2,
        )
        cal.seed_earnings("AAPL", date(2026, 4, 7))

        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_APPROACHING"

        # Wednesday 2026-04-01 is 3 trading days away (Thu, Mon, Tue) -> Allowed!
        sim_clock.set_time(datetime(2026, 4, 1, 14, 0, tzinfo=UTC))
        blocked_wed, reason_wed = cal.is_symbol_blocked("AAPL")
        assert blocked_wed is False
        assert reason_wed == "OK"

    def test_current_time_parameter_propagated_to_lead_time_check(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Passing current_time overrides clock.now() and clock.today()."""
        # Clock set to Thursday April 16, earnings on Monday April 20
        sim_clock.set_time(datetime(2026, 4, 16, 14, 0, tzinfo=UTC))
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)
        # Seed cache on April 16
        cal.seed_earnings("AAPL", date(2026, 4, 20), cached_at=sim_clock.now())

        # At clock time (April 16, 2 trading days before April 20): Blocked
        blocked_clock, reason_clock = cal.is_symbol_blocked("AAPL")
        assert blocked_clock is True
        assert reason_clock == "EARNINGS_APPROACHING"

        # Simulated query at April 17 (Friday, 1 trading day before Monday April 20, cache 1d old <= 3d): Blocked
        friday_dt = datetime(2026, 4, 17, 14, 0, tzinfo=UTC)
        blocked_friday, reason_friday = cal.is_symbol_blocked("AAPL", current_time=friday_dt)
        assert blocked_friday is True
        assert reason_friday == "EARNINGS_APPROACHING"

        # Now test a past earnings date: earnings was on April 15, cached on April 16
        cal.seed_earnings("MSFT", date(2026, 4, 15), cached_at=sim_clock.now())
        # Query at April 16 (after earnings, cache fresh): Allowed
        blocked_past, reason_past = cal.is_symbol_blocked("MSFT")
        assert blocked_past is False
        assert reason_past == "OK"


# ============================================================================
# 4. Defect 4: Case-Insensitive Date Deduplication in RegimeFilter
# ============================================================================


class TestDefect4RegimeFilterDeduplication:
    """Empirical verification that all date column variations are deduplicated."""

    @pytest.mark.parametrize("col_name", ["Date", "date", "Timestamp", "timestamp"])
    def test_regime_deduplication_all_column_variants(
        self, sim_clock: SimulatedClock, col_name: str
    ) -> None:
        """100 unique dates duplicated twice (200 rows) must deduplicate to 100 rows

        and fail closed with INSUFFICIENT_BARS (100 < 200).
        """
        rf = RegimeFilter(clock=sim_clock, min_bars=200)

        dates = list(pd.date_range("2025-01-01", periods=100, freq="B"))
        dup_dates = dates * 2
        closes = [500.0 + i * 0.1 for i in range(200)]

        df = pd.DataFrame({"symbol": "SPY", col_name: dup_dates, "close": closes})
        snap = rf.classify(df)

        assert snap.regime == MarketRegime.UNKNOWN.value
        assert snap.inputs["bar_count"] == 100
        assert snap.inputs["is_blocked"] is True
        assert "INSUFFICIENT_BARS" in snap.inputs["reason"]

    def test_regime_sufficient_bars_after_deduplication(self, sim_clock: SimulatedClock) -> None:
        """205 unique dates duplicated twice (410 rows) deduplicates to 205 rows

        and evaluates regime normally without INSUFFICIENT_BARS.
        """
        rf = RegimeFilter(clock=sim_clock, min_bars=200)

        dates = list(pd.date_range("2024-01-01", periods=205, freq="B"))
        dup_dates = dates * 2
        closes = [400.0 + i * 0.5 for i in range(205)] * 2

        df = pd.DataFrame({"symbol": "SPY", "Date": dup_dates, "Close": closes})
        snap = rf.classify(df)

        assert snap.regime != MarketRegime.UNKNOWN.value
        assert snap.inputs["bar_count"] == 205
        assert snap.inputs["is_blocked"] is False
        assert snap.inputs["reason"] == "OK"

    def test_regime_unsorted_duplicated_date_keeps_last_bar(self, sim_clock: SimulatedClock) -> None:
        """RegimeFilter sorts dates before dropping duplicates with keep='last'."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)

        dates = list(pd.date_range("2024-01-01", periods=200, freq="B"))
        # Add an older duplicated bar at the end with an outdated close price
        df = pd.DataFrame({
            "symbol": "SPY",
            "Date": dates + [dates[0]],
            "Close": [500.0] * 200 + [999.9],  # Outlier close on the duplicated oldest date
        })

        snap = rf.classify(df)
        assert snap.inputs["bar_count"] == 200
        # The latest bar close should be 500.0, not 999.9
        assert snap.inputs["spy_close"] == 500.0


# ============================================================================
# 5. Defect 5: Naive Datetime in EventCalendar.events Setter
# ============================================================================


class TestDefect5EventsSetterNaiveDatetime:
    """Empirical verification that setting naive datetimes does not crash."""

    def test_events_setter_with_naive_datetime(self, sim_clock: SimulatedClock) -> None:
        """Assigning naive datetime events via cal.events = [...] converts to UTC without crash."""
        cal = EventCalendar(clock=sim_clock)
        event_time_naive = datetime(2026, 4, 17, 14, 30)

        cal.events = [
            {
                "name": "CPI Report",
                "type": "CPI",
                "timestamp": event_time_naive,
                "impact": "high",
            }
        ]

        # Stored event timestamp is normalized to UTC aware
        stored_ts = cal.events[0]["timestamp"]
        assert stored_ts.tzinfo is not None
        assert stored_ts == event_time_naive.replace(tzinfo=UTC)

        # Checking macro window executes without TypeError
        query_time = sim_clock.now()  # 14:00 UTC, within 30 min of 14:30
        blocked, reason = cal.is_macro_window_active(query_time)
        assert blocked is True
        assert reason == "MACRO_WINDOW_HIGH"

    def test_events_setter_multiple_mixed_naive_and_aware(self, sim_clock: SimulatedClock) -> None:
        """Assigning a mix of naive and aware events normalizes all timestamps."""
        cal = EventCalendar(clock=sim_clock)
        events = [
            {"name": "NFP", "type": "NFP", "timestamp": datetime(2026, 5, 1, 12, 30), "impact": "high"},
            {
                "name": "FOMC",
                "type": "FOMC",
                "timestamp": datetime(2026, 5, 6, 18, 0, tzinfo=UTC),
                "impact": "critical",
            },
        ]
        cal.events = events

        for ev in cal.events:
            assert ev["timestamp"].tzinfo is not None


# ============================================================================
# 6. Defect 6: Robust parse_hour_code for Non-String Types
# ============================================================================


class TestDefect6ParseHourCodeNonStrings:
    """Empirical verification of parse_hour_code input robustness."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            (12, "unknown"),
            (0, "unknown"),
            (-1, "unknown"),
            (3.14159, "unknown"),
            (True, "unknown"),
            (False, "unknown"),
            (None, "unspecified"),
            ("", "unspecified"),
            ("   ", "unspecified"),
            ("bmo", "before_market_open"),
            ("BMO", "before_market_open"),
            ("  bMo  ", "before_market_open"),
            ("amc", "after_market_close"),
            ("AMC", "after_market_close"),
            ("dmh", "during_market_hours"),
            ("DMH", "during_market_hours"),
            ("random_string", "unknown"),
            (object(), "unknown"),
        ],
    )
    def test_parse_hour_code_various_inputs(self, input_val: Any, expected: str) -> None:
        """parse_hour_code returns expected normalized categories for any input type."""
        assert parse_hour_code(input_val) == expected


# ============================================================================
# 7. Additional Adversarial Stress Tests & Edge Cases
# ============================================================================


class TestAdversarialEdgeCasesAndStress:
    """Aggressive challenge suite testing edge conditions and failure modes."""

    def test_regime_non_positive_price_fail_closed(self, sim_clock: SimulatedClock) -> None:
        """Prices <= 0 fail closed with NON_POSITIVE_PRICE_DETECTED."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        closes = [500.0] * 199 + [0.0]
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": closes})

        snap = rf.classify(df)
        assert snap.regime == MarketRegime.UNKNOWN.value
        assert snap.inputs["reason"] == "NON_POSITIVE_PRICE_DETECTED"
        assert snap.inputs["is_blocked"] is True

    def test_regime_latest_close_nan_fail_closed(self, sim_clock: SimulatedClock) -> None:
        """Latest bar close is NaN fails closed with LATEST_CLOSE_IS_NAN."""
        rf = RegimeFilter(clock=sim_clock, min_bars=200)
        closes = [500.0] * 199 + [np.nan]
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        df = pd.DataFrame({"symbol": "SPY", "date": dates, "close": closes})

        snap = rf.classify(df)
        assert snap.regime == MarketRegime.UNKNOWN.value
        assert snap.inputs["reason"] == "LATEST_CLOSE_IS_NAN"
        assert snap.inputs["is_blocked"] is True

    def test_macro_filter_midnight_timezone_boundary(self, sim_clock: SimulatedClock) -> None:
        """MacroFilter correctly evaluates events near UTC/ET midnight boundaries.

        FOMC at 19:30 ET is 23:30 UTC.
        Query at 20:00 ET is 00:00 UTC (next day in UTC, same day in ET).
        Swing strategy must still be blocked!
        """
        mf = MacroFilter(clock=sim_clock)
        fomc_utc = datetime(2026, 5, 6, 23, 30, tzinfo=UTC)  # 19:30 ET
        mf.load_events([{"name": "FOMC Statement", "type": "FOMC", "timestamp": fomc_utc, "impact": "critical"}])

        # Query at 2026-05-07 00:00:00 UTC -> 2026-05-06 20:00:00 ET (same ET day!)
        query_utc = datetime(2026, 5, 7, 0, 0, tzinfo=UTC)
        blocked_swing, reason_swing = mf.is_macro_window_active(query_utc, strategy_type="swing")
        assert blocked_swing is True
        assert reason_swing == "FOMC_ALL_DAY_SWING_BLOCK"

    @pytest.mark.asyncio
    async def test_concurrent_refresh_earnings_stress(self, sim_clock: SimulatedClock) -> None:
        """Concurrent calls to refresh_earnings handle async locks and execution safely."""
        cal = EventCalendar(clock=sim_clock)

        def mock_handler(req: httpx.Request) -> httpx.Response:
            url_str = str(req.url)
            sym = "AAPL" if "AAPL" in url_str else "MSFT"
            return httpx.Response(
                200,
                json={
                    "earningsCalendar": [
                        {"symbol": sym, "date": "2026-06-01", "hour": "amc"}
                    ]
                },
            )

        cal.earnings_client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

        # Launch 10 concurrent refreshes
        tasks = [cal.refresh_earnings(["AAPL", "MSFT"]) for _ in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            assert not isinstance(res, Exception)

        assert "AAPL" in cal.cache
        assert "MSFT" in cal.cache
        assert cal.is_symbol_blocked("AAPL")[0] is False
        assert cal.is_symbol_blocked("MSFT")[0] is False
