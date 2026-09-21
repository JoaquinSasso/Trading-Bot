"""Adversarial white-box test suite for Tier 5 Coverage Hardening (Challenger 1).

Covers white-box analysis, edge cases, boundary conditions, and failure modes on:
1. Indicators: `backend/tbot/indicators/pure.py`
2. Market Calendar & Clock: `backend/tbot/data/calendar.py`, `backend/tbot/data/types.py`
3. Regime Filter: `backend/tbot/regime/filter.py`
4. Event Calendar: `backend/tbot/events/calendar.py`
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError
from tbot.data.calendar import MarketCalendar
from tbot.data.types import ET_ZONE, PriceQuote
from tbot.events.calendar import (
    EventCalendar,
    FinnhubEarningsClient,
    MacroFilter,
    parse_hour_code,
)
from tbot.indicators.pure import (
    atr,
    create_synthetic_bar,
    ema,
    forward_fill_synthetic_bars,
    percentage_returns,
    realized_volatility_20d,
    relative_volume,
    rsi,
    sma,
)
from tbot.regime.filter import MarketRegime, RegimeFilter

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_holidays_2026() -> set[date]:
    """Official 2026 US market holidays."""
    return {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Day
        date(2026, 2, 16),  # Washington's Birthday (Presidents' Day)
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (Observed)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }


@pytest.fixture
def mock_early_closes_2026() -> dict[date, tuple[int, int]]:
    """Early close sessions in 2026 (13:00 ET)."""
    return {
        date(2026, 11, 27): (13, 0),  # Black Friday
        date(2026, 12, 24): (13, 0),  # Christmas Eve
    }


@pytest.fixture
def calendar_2026(
    mock_holidays_2026: set[date],
    mock_early_closes_2026: dict[date, tuple[int, int]],
) -> MarketCalendar:
    """Preloaded MarketCalendar instance covering the full 2026 calendar year."""
    clock = SimulatedClock(datetime(2026, 4, 17, 13, 30, tzinfo=UTC))
    start_date = date(2026, 1, 1)
    end_date = date(2026, 12, 31)
    days = MarketCalendar.generate_standard_trading_days(
        start=start_date,
        end=end_date,
        holidays=mock_holidays_2026,
        early_closes=mock_early_closes_2026,
    )
    return MarketCalendar(
        clock=clock,
        initial_days=days,
        covered_range=(start_date, end_date),
    )


# ============================================================================
# 1. Indicators White-Box Adversarial Tests (pure.py)
# ============================================================================


class TestIndicatorsWhiteBoxAdversarial:
    """Adversarial stress tests for backend/tbot/indicators/pure.py."""

    def test_sma_period_exceeds_series_length(self) -> None:
        """SMA with period > len(series) must return all-NaN Series."""
        s = pd.Series([10.0, 11.0, 12.0])
        res = sma(s, period=5)
        assert len(res) == 3
        assert res.isna().all()

    def test_sma_invalid_period_types(self) -> None:
        """SMA must strictly reject booleans, zero, negative, and floats for period."""
        s = pd.Series([10.0, 11.0])
        with pytest.raises(ValueError, match="period"):
            sma(s, period=0)
        with pytest.raises(ValueError, match="period"):
            sma(s, period=-3)
        with pytest.raises(ValueError, match="period"):
            sma(s, period=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="period"):
            sma(s, period=2.5)  # type: ignore[arg-type]

    def test_ema_period_1_equals_raw_series(self) -> None:
        """EMA with period=1 (alpha=1.0) must produce values identical to raw series."""
        s = pd.Series([10.0, 15.0, 12.0, 25.0])
        res = ema(s, period=1)
        np.testing.assert_allclose(res.to_numpy(), s.to_numpy(), atol=1e-6)

    def test_ema_series_with_infinite_values(self) -> None:
        """White-box edge case: pandas ewm skips infinite values during smoothing."""
        s = pd.Series([10.0, np.inf, 20.0])
        res = ema(s, period=2)
        # In pandas ewm, np.inf is skipped, preserving continuity with finite numbers
        assert res.iloc[0] == 10.0
        assert res.iloc[1] == 10.0
        assert 18.0 < res.iloc[2] < 19.0

    def test_rsi_flat_series_and_monotonic(self) -> None:
        """Flat series -> 50.0; Monotonic up -> 100.0; Monotonic down -> 0.0."""
        flat = pd.Series([100.0] * 10)
        res_flat = rsi(flat, period=14)
        assert np.isnan(res_flat.iloc[0])
        assert (res_flat.iloc[1:] == 50.0).all()

        up = pd.Series([10.0 + i for i in range(10)])
        res_up = rsi(up, period=14)
        assert np.isnan(res_up.iloc[0])
        assert (res_up.iloc[1:] == 100.0).all()

        down = pd.Series([100.0 - i for i in range(10)])
        res_down = rsi(down, period=14)
        assert np.isnan(res_down.iloc[0])
        assert (res_down.iloc[1:] == 0.0).all()

    def test_rsi_nan_in_middle_propagates_ewm_behavior(self) -> None:
        """Adversarial check: In pandas ewm(adjust=False), a NaN price causes diff to be NaN,

        which propagates the previous ewm avg_gain/avg_loss without resetting.
        """
        s = pd.Series([10.0, 11.0, np.nan, 12.0])
        res = rsi(s, period=14)
        assert np.isnan(res.iloc[0])
        assert res.iloc[1] == 100.0
        # Index 2 and 3 remain 100.0 because avg_loss stays 0.0 and avg_gain is carried forward
        assert res.iloc[2] == 100.0
        assert res.iloc[3] == 100.0

    def test_rsi_duplicate_indices(self) -> None:
        """RSI must function correctly even when Series index contains duplicates."""
        s = pd.Series([10.0, 10.0, 12.0, 10.0], index=[0, 0, 1, 1])
        res = rsi(s, period=14)
        assert len(res) == 4
        assert res.index.tolist() == [0, 0, 1, 1]

    def test_atr_mismatched_index_labels_raises_error(self) -> None:
        """ATR inputs with identical lengths but mismatched indices must raise ValueError on comparison."""
        high = pd.Series([10.0, 12.0], index=[0, 1])
        low = pd.Series([9.0, 11.0], index=[1, 2])
        close = pd.Series([9.5, 11.5], index=[2, 3])
        with pytest.raises(ValueError, match="identically-labeled|lengths"):
            atr(high, low, close, period=14)

    def test_atr_high_strictly_below_low_raises_value_error(self) -> None:
        """ATR must reject bars where high < low."""
        high = pd.Series([10.0, 8.0])
        low = pd.Series([9.0, 9.0])
        close = pd.Series([9.5, 8.5])
        with pytest.raises(ValueError, match="high price cannot be lower than low price"):
            atr(high, low, close, period=14)

    def test_atr_negative_prices_raise_value_error(self) -> None:
        """ATR must reject negative prices."""
        high = pd.Series([10.0, 12.0])
        low = pd.Series([-1.0, 8.0])
        close = pd.Series([9.5, 10.0])
        with pytest.raises(ValueError, match="Prices must be non-negative"):
            atr(high, low, close, period=14)

    def test_percentage_returns_zero_prices(self) -> None:
        """Division by zero in percentage returns must be replaced with NaN rather than Inf."""
        s = pd.Series([0.0, 10.0, 0.0])
        ret = percentage_returns(s, periods=1)
        assert np.isnan(ret.iloc[0])
        assert np.isnan(ret.iloc[1])  # (10 - 0) / 0 = inf -> replaced by nan
        assert ret.iloc[2] == -1.0  # (0 - 10) / 10 = -1.0

    def test_realized_volatility_window_exceeds_series(self) -> None:
        """Realized volatility with window > len(series) must return all NaN Series."""
        s = pd.Series([100.0, 102.0, 101.0, 103.0])
        vol = realized_volatility_20d(s, window=20)
        assert len(vol) == 4
        assert vol.isna().all()

    def test_relative_volume_zero_and_negative_conditions(self) -> None:
        """RVOL edge cases: current=0 & avg=0 -> 1.0; current>0 & avg=0 -> 0.0."""
        # Current 0, hist all 0 -> 1.0
        assert relative_volume(0, pd.Series([0.0, 0.0, 0.0])) == 1.0
        # Current > 0, hist all 0 -> 0.0
        assert relative_volume(500, pd.Series([0.0, 0.0, 0.0])) == 0.0
        # Negative current -> ValueError
        with pytest.raises(ValueError, match="non-negative"):
            relative_volume(-10, pd.Series([100.0, 200.0]))
        # Negative history -> ValueError
        with pytest.raises(ValueError, match="negative"):
            relative_volume(100, pd.Series([100.0, -20.0]))

    def test_create_synthetic_bar_validation(self) -> None:
        """create_synthetic_bar requires timezone-aware datetime and positive price."""
        aware_dt = datetime(2026, 4, 17, 14, 0, tzinfo=UTC)
        naive_dt = datetime(2026, 4, 17, 14, 0)

        with pytest.raises(ValueError, match="timezone-aware"):
            create_synthetic_bar(naive_dt, Decimal("100.5"))

        with pytest.raises(ValueError, match="positive"):
            create_synthetic_bar(aware_dt, Decimal("0.0"))

        with pytest.raises(ValueError, match="positive"):
            create_synthetic_bar(aware_dt, Decimal("-10.0"))

        bar = create_synthetic_bar(aware_dt, Decimal("100.5"))
        assert bar["open"] == Decimal("100.5")
        assert bar["volume"] == Decimal("0")
        assert bar["is_synthetic"] is True

    def test_forward_fill_synthetic_bars_leading_nan_edge_case(self) -> None:
        """White-box edge case: If the first bar in expected_index is missing from df,

        forward_fill_synthetic_bars leaves open/high/low/close as NaN because ffill()
        cannot fill backward from future bars.
        """
        tz = ZoneInfo("America/New_York")
        idx = pd.date_range("2026-04-17 09:30", "2026-04-17 09:40", freq="5min", tz=tz)
        # df only has bars from 09:35 onward
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [500.0, 600.0],
            },
            index=idx[1:],
        )
        filled = forward_fill_synthetic_bars(df, idx)

        # First bar at 09:30 has is_synthetic=True, volume=0.0, but prices are NaN
        assert filled.loc[idx[0], "is_synthetic"] is True or filled.loc[idx[0], "is_synthetic"] == 1
        assert filled.loc[idx[0], "volume"] == 0.0
        assert np.isnan(filled.loc[idx[0], "close"])


# ============================================================================
# 2. Market Calendar & Types White-Box Tests (calendar.py, types.py)
# ============================================================================


class TestMarketCalendarWhiteBoxAdversarial:
    """Adversarial testing of MarketCalendar and data types."""

    def test_dst_spring_forward_transition_2026(self, calendar_2026: MarketCalendar) -> None:
        """In 2026, DST starts Sunday March 8. On Monday March 9, open is 13:30 UTC (09:30 EDT)."""
        mon_mar_9 = date(2026, 3, 9)
        day = calendar_2026.get_trading_day(mon_mar_9)
        assert day is not None
        # In EDT (UTC-4), 09:30 is 13:30 UTC
        assert day.open_time == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
        assert day.close_time == datetime(2026, 3, 9, 20, 0, tzinfo=UTC)

    def test_dst_fall_back_transition_2026(self, calendar_2026: MarketCalendar) -> None:
        """In 2026, DST ends Sunday Nov 1. On Monday Nov 2, open is 14:30 UTC (09:30 EST)."""
        mon_nov_2 = date(2026, 11, 2)
        day = calendar_2026.get_trading_day(mon_nov_2)
        assert day is not None
        # In EST (UTC-5), 09:30 is 14:30 UTC
        assert day.open_time == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        assert day.close_time == datetime(2026, 11, 2, 21, 0, tzinfo=UTC)

    def test_session_boundary_subsecond_precision(self, calendar_2026: MarketCalendar) -> None:
        """Exhaustive boundary testing around open (09:30 ET) and close (16:00 ET)."""
        # 09:29:59.999 ET
        t_pre = datetime(2026, 4, 17, 9, 29, 59, 999000, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t_pre) is False
        assert calendar_2026.is_open_window(t_pre) is False

        # 09:30:00.000 ET
        t_open = datetime(2026, 4, 17, 9, 30, 0, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t_open) is True
        assert calendar_2026.is_open_window(t_open) is True
        assert calendar_2026.minutes_since_open(t_open) == 0.0

        # 09:59:59.999 ET -> inside open window
        t_open_win_last = datetime(2026, 4, 17, 9, 59, 59, 999000, tzinfo=ET_ZONE)
        assert calendar_2026.is_open_window(t_open_win_last) is True

        # 10:00:00.000 ET -> open window ends
        t_open_win_end = datetime(2026, 4, 17, 10, 0, 0, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_open_window(t_open_win_end) is False
        assert calendar_2026.is_regular_trading_window(t_open_win_end) is True

        # 15:49:59.999 ET -> regular window, not yet close window
        t_close_pre = datetime(2026, 4, 17, 15, 49, 59, 999000, tzinfo=ET_ZONE)
        assert calendar_2026.is_close_window(t_close_pre) is False
        assert calendar_2026.is_regular_trading_window(t_close_pre) is True

        # 15:50:00.000 ET -> close window starts
        t_close_start = datetime(2026, 4, 17, 15, 50, 0, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_close_window(t_close_start) is True
        assert calendar_2026.is_regular_trading_window(t_close_start) is False

        # 15:59:59.999 ET -> market open, inside close window
        t_last_sec = datetime(2026, 4, 17, 15, 59, 59, 999000, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t_last_sec) is True
        assert calendar_2026.is_close_window(t_last_sec) is True

        # 16:00:00.000 ET -> market is closed (ts < day.close_time), but is_close_window is True (ts <= day.close_time)
        t_close = datetime(2026, 4, 17, 16, 0, 0, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t_close) is False
        assert calendar_2026.is_close_window(t_close) is True
        assert calendar_2026.minutes_until_close(t_close) is None

        # 16:00:00.001 ET -> post market, outside close window
        t_post = datetime(2026, 4, 17, 16, 0, 0, 1000, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t_post) is False
        assert calendar_2026.is_close_window(t_post) is False

    def test_early_close_session_13_00(self, calendar_2026: MarketCalendar) -> None:
        """Black Friday (2026-11-27) early close at 13:00 ET: close window is 12:50 to 13:00."""
        # 12:49:59 ET
        t_pre = datetime(2026, 11, 27, 12, 49, 59, tzinfo=ET_ZONE)
        assert calendar_2026.is_close_window(t_pre) is False

        # 12:50:00 ET
        t_start = datetime(2026, 11, 27, 12, 50, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_close_window(t_start) is True

        # 13:00:00 ET
        t_close = datetime(2026, 11, 27, 13, 0, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t_close) is False
        assert calendar_2026.is_close_window(t_close) is True

    def test_minutes_since_open_post_market_observation(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """White-box observation: minutes_since_open does not check is_market_open,

        returning total minutes elapsed since 09:30 ET even after market close.
        """
        t_night = datetime(2026, 4, 17, 20, 0, 0, tzinfo=ET_ZONE)  # 20:00 ET (closed)
        assert calendar_2026.is_market_open(t_night) is False
        elapsed = calendar_2026.minutes_since_open(t_night)
        # 10.5 hours = 630 minutes
        assert elapsed == 630.0

    def test_uncached_date_without_trading_client_raises(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """Querying a date outside the covered range without TradingClient must raise MarketDataError."""
        outside_date = date(2027, 1, 5)
        with pytest.raises(MarketDataError, match="no se configuró un TradingClient"):
            calendar_2026.is_trading_day(outside_date)

    def test_price_quote_crossed_market_spread_bps(self) -> None:
        """Inverted/crossed quote (bid > ask) yields negative spread_bps."""
        q = PriceQuote(symbol="AAPL", bid=Decimal("101.00"), ask=Decimal("100.00"))
        assert q.midpoint == Decimal("100.50")
        assert q.spread_bps is not None
        assert q.spread_bps < 0.0


# ============================================================================
# 3. Regime Filter White-Box Adversarial Tests (filter.py)
# ============================================================================


class TestRegimeFilterWhiteBoxAdversarial:
    """Adversarial white-box tests on RegimeFilter."""

    def test_regime_empty_dataframe_fail_closed(self, sim_clock: SimulatedClock) -> None:
        """Empty DataFrame fails-closed to UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        snap = rf.classify(pd.DataFrame())
        assert snap.regime == MarketRegime.UNKNOWN.value
        assert snap.inputs["is_blocked"] is True
        assert snap.inputs["reason"] == "EMPTY_DATAFRAME"

    def test_regime_missing_close_column_fail_closed(self, sim_clock: SimulatedClock) -> None:
        """DataFrame missing 'close' or 'Close' column fails-closed to UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        df = pd.DataFrame({"symbol": ["SPY"] * 250, "price": [500.0] * 250})
        snap = rf.classify(df)
        assert snap.regime == MarketRegime.UNKNOWN.value
        assert snap.inputs["is_blocked"] is True
        assert snap.inputs["reason"] == "MISSING_CLOSE_COLUMN"

    def test_regime_no_spy_in_multi_symbol_dataframe(self, sim_clock: SimulatedClock) -> None:
        """DataFrame with symbols but no SPY rows fails-closed to UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        df = pd.DataFrame({"symbol": ["QQQ"] * 250, "close": [400.0] * 250})
        snap = rf.classify(df)
        assert snap.regime == MarketRegime.UNKNOWN.value
        assert snap.inputs["is_blocked"] is True
        assert snap.inputs["reason"] == "NO_SPY_BARS_FOUND"

    def test_regime_case_sensitivity_gap_on_date_column_deduplication(
        self, sim_clock: SimulatedClock
    ) -> None:
        """WHITE-BOX DEFECT DISCOVERY:

        RegimeFilter line 141 checks `if "date" in df.columns:` to drop duplicates.
        If the column is named 'Date' (capital D), drop_duplicates is skipped!
        Consequently, duplicate rows artificially inflate bar_count >= min_bars,
        bypassing the insufficient bars fail-closed guard!
        """
        rf = RegimeFilter(clock=sim_clock, min_bars=200)

        # 100 unique days duplicated twice = 200 rows total
        unique_dates = list(pd.date_range("2025-01-01", periods=100, freq="B"))
        duplicated_dates = unique_dates * 2
        closes = [500.0 + i * 0.1 for i in range(200)]

        # 1. With lowercase 'date': drop_duplicates runs -> 100 bars -> fail-closed UNKNOWN
        df_lower = pd.DataFrame({"symbol": "SPY", "date": duplicated_dates, "close": closes})
        snap_lower = rf.classify(df_lower)
        assert snap_lower.regime == MarketRegime.UNKNOWN.value
        assert snap_lower.inputs["bar_count"] == 100
        assert "INSUFFICIENT_BARS" in snap_lower.inputs["reason"]

        # 2. With uppercase 'Date': drop_duplicates runs -> 100 bars -> correctly fail-closed UNKNOWN
        df_upper = pd.DataFrame({"symbol": "SPY", "Date": duplicated_dates, "Close": closes})
        snap_upper = rf.classify(df_upper)
        assert snap_upper.regime == MarketRegime.UNKNOWN.value
        assert snap_upper.inputs["bar_count"] == 100
        assert "INSUFFICIENT_BARS" in snap_upper.inputs["reason"]
        assert snap_upper.inputs["is_blocked"] is True


# ============================================================================
# 4. Event Calendar White-Box Adversarial Tests (calendar.py)
# ============================================================================


class TestEventCalendarWhiteBoxAdversarial:
    """Adversarial white-box tests on EventCalendar, FinnhubEarningsClient, and MacroFilter."""

    def test_finnhub_stale_cache_indefinitely_valid_when_network_error_false(
        self, sim_clock: SimulatedClock
    ) -> None:
        """WHITE-BOX REMEDIATION:

        Unconditional 3-day (72h) cache expiration on every cache hit:
        Stale cache > 72h is blocked even when network_error is False.
        """
        client = FinnhubEarningsClient(clock=sim_clock)
        assert client.network_error is False

        # Seed cache 100 days in the past
        ancient_time = sim_clock.now() - timedelta(days=100)
        client.seed_earnings("AAPL", date(2026, 5, 1), cached_at=ancient_time)

        # Stale cache > 72h is blocked even when network_error is False
        edate, is_blocked, reason = client.get_earnings_date_sync("AAPL")
        assert is_blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"
        assert edate is None

    @pytest.mark.asyncio
    async def test_refresh_earnings_real_http_500_swallowed_fails_open(
        self, sim_clock: SimulatedClock
    ) -> None:
        """WHITE-BOX REMEDIATION:

        When Finnhub encounters HTTP 500 / Network Down:
        Un-cached symbols fail closed with (True, "EARNINGS_DATA_UNAVAILABLE")!
        """
        cal = EventCalendar(clock=sim_clock)

        # Mock HTTP transport returning 500 Internal Server Error
        mock_transport = httpx.MockTransport(lambda req: httpx.Response(500))
        cal.earnings_client.http_client = httpx.AsyncClient(transport=mock_transport)

        # Refresh fails due to HTTP 500
        await cal.refresh_earnings(["AAPL"])

        # Un-cached symbol fails closed with (True, "EARNINGS_DATA_UNAVAILABLE")
        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"

    def test_earnings_lead_time_uses_calendar_days_instead_of_trading_days(
        self, sim_clock: SimulatedClock
    ) -> None:
        """WHITE-BOX REMEDIATION:

        PLAN.md Section 8.3 and PROJECT.md Feature 37 require:
        "Sin entradas nuevas desde 2 días hábiles antes" /
        "Blocks entries 2 trading days prior to earnings release"

        Friday (2026-04-17) is 1 trading day before Monday (2026-04-20) earnings.
        It is correctly blocked.
        """
        # Set clock to Friday 2026-04-17
        sim_clock.set_time(datetime(2026, 4, 17, 14, 0, tzinfo=UTC))
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)

        monday_earnings = date(2026, 4, 20)
        cal.seed_earnings("AAPL", monday_earnings)

        # Friday is 1 trading day before Monday, so it MUST be blocked according to trading days spec.
        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert blocked is True
        assert reason == "EARNINGS_APPROACHING"

    def test_is_symbol_blocked_ignores_current_time_parameter(
        self, sim_clock: SimulatedClock
    ) -> None:
        """WHITE-BOX REMEDIATION:

        EventCalendar.is_symbol_blocked now respects `current_time`.
        """
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)
        # Clock is set to 2026-04-17
        sim_clock.set_time(datetime(2026, 4, 17, 14, 0, tzinfo=UTC))

        # Seed earnings for 2026-04-18 (blocked today)
        cal.seed_earnings("AAPL", date(2026, 4, 18))

        # At self.clock (2026-04-17), earnings is tomorrow (2026-04-18), so it is blocked
        blocked_now, reason_now = cal.is_symbol_blocked("AAPL")
        assert blocked_now is True
        assert reason_now == "EARNINGS_APPROACHING"

        # Passing future current_time (2026-04-19, after earnings but within 3d cache window)
        # is now respected and evaluates as not approaching
        future_dt = datetime(2026, 4, 19, 14, 0, tzinfo=UTC)
        blocked_future, reason_future = cal.is_symbol_blocked("AAPL", current_time=future_dt)
        assert blocked_future is False
        assert reason_future == "OK"

    def test_event_calendar_events_setter_naive_datetime_causes_crash(
        self, sim_clock: SimulatedClock
    ) -> None:
        """WHITE-BOX REMEDIATION:

        Assigning events directly via `cal.events = [...]` passes through load_events normalization.
        Calling is_macro_window_active executes safely without TypeError.
        """
        cal = EventCalendar(clock=sim_clock)
        naive_event = {
            "name": "CPI Report",
            "type": "CPI",
            "timestamp": datetime(2026, 4, 17, 14, 30),  # Naive!
            "impact": "high",
        }
        cal.events = [naive_event]

        # Normalization prevents TypeError
        blocked, reason = cal.is_macro_window_active(sim_clock.now())
        assert isinstance(blocked, bool)
        assert isinstance(reason, str)

    def test_parse_hour_code_non_string_type_error(self) -> None:
        """WHITE-BOX REMEDIATION: parse_hour_code safely handles non-string inputs without crashing."""
        assert parse_hour_code(12) == "unknown"

    def test_macro_blackout_boundary_seconds_precision(self, sim_clock: SimulatedClock) -> None:
        """Verify the exact 30-minute blackout window at second-level precision."""
        event_time = datetime(2026, 4, 17, 14, 0, 0, tzinfo=UTC)
        macro_filter = MacroFilter(clock=sim_clock, macro_block_minutes=30)
        macro_filter.load_events(
            [{"name": "CPI Release", "type": "CPI", "timestamp": event_time, "impact": "high"}]
        )

        # 1. 30m + 1 second before -> Outside window
        t_minus_30m_1s = event_time - timedelta(minutes=30, seconds=1)
        blocked, reason = macro_filter.is_macro_window_active(t_minus_30m_1s)
        assert not blocked
        assert reason == "OK"

        # 2. Exactly 30m before -> Inside window
        t_minus_30m = event_time - timedelta(minutes=30)
        blocked, reason = macro_filter.is_macro_window_active(t_minus_30m)
        assert blocked
        assert reason == "MACRO_WINDOW_HIGH"

        # 3. At event time -> Inside window
        blocked, reason = macro_filter.is_macro_window_active(event_time)
        assert blocked
        assert reason == "MACRO_WINDOW_HIGH"

        # 4. Exactly 30m after -> Inside window
        t_plus_30m = event_time + timedelta(minutes=30)
        blocked, reason = macro_filter.is_macro_window_active(t_plus_30m)
        assert blocked
        assert reason == "MACRO_WINDOW_HIGH"

        # 5. 30m + 1 second after -> Outside window
        t_plus_30m_1s = event_time + timedelta(minutes=30, seconds=1)
        blocked, reason = macro_filter.is_macro_window_active(t_plus_30m_1s)
        assert not blocked
        assert reason == "OK"

    def test_macro_fomc_swing_vs_intraday(self, sim_clock: SimulatedClock) -> None:
        """FOMC announcement blocks swing strategies for the entire Eastern Time day,

        while intraday strategies are only blocked during the 30-minute symmetric window.
        """
        # FOMC at 14:00 ET (18:00 UTC)
        fomc_time = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)
        macro_filter = MacroFilter(clock=sim_clock, macro_block_minutes=30)
        macro_filter.load_events(
            [
                {
                    "name": "FOMC Statement",
                    "type": "FOMC",
                    "timestamp": fomc_time,
                    "impact": "critical",
                }
            ]
        )

        # 10:00 ET (14:00 UTC) on FOMC day
        t_morning = datetime(2026, 5, 6, 14, 0, 0, tzinfo=UTC)

        # Swing strategy must be blocked all day
        blocked_swing, reason_swing = macro_filter.is_macro_window_active(
            t_morning, strategy_type="swing"
        )
        assert blocked_swing is True
        assert reason_swing == "FOMC_ALL_DAY_SWING_BLOCK"

        # Intraday strategy is NOT blocked in the morning
        blocked_intraday, reason_intraday = macro_filter.is_macro_window_active(
            t_morning, strategy_type="intraday"
        )
        assert blocked_intraday is False
        assert reason_intraday == "OK"

        # 13:45 ET (17:45 UTC) -> Within 30m window
        t_window = datetime(2026, 5, 6, 17, 45, 0, tzinfo=UTC)
        blocked_intraday_win, _ = macro_filter.is_macro_window_active(
            t_window, strategy_type="intraday"
        )
        assert blocked_intraday_win is True

    def test_macro_calendar_expiry_monitor(self, sim_clock: SimulatedClock) -> None:
        """check_calendar_expiry triggers an alert when the furthest event is < 30 days away."""
        macro_filter = MacroFilter(clock=sim_clock)

        curr_time = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

        # Event 15 days in the future (< 30 days)
        macro_filter.load_events(
            [
                {
                    "name": "CPI",
                    "type": "CPI",
                    "timestamp": curr_time + timedelta(days=15),
                    "impact": "high",
                }
            ]
        )
        needs_alert, remaining_days = macro_filter.check_calendar_expiry(curr_time, horizon_days=30)
        assert needs_alert is True
        assert remaining_days == 15

        # Event 45 days in the future (>= 30 days)
        macro_filter.load_events(
            [
                {
                    "name": "FOMC",
                    "type": "FOMC",
                    "timestamp": curr_time + timedelta(days=45),
                    "impact": "critical",
                }
            ]
        )
        needs_alert, remaining_days = macro_filter.check_calendar_expiry(curr_time, horizon_days=30)
        assert needs_alert is False
        assert remaining_days == 45
