"""Adversarial stress test suite for MarketCalendar and TradingDay types.

Empirical verification covering:
1. Daylight Saving Time (DST) transition dates (Spring Forward, Fall Back in America/New_York).
2. Early market close days (13:00 ET close, close window 12:50-13:00 ET).
3. Boundary seconds (09:29:59 vs 09:30:00, 09:59:59 vs 10:00:00, 15:49:59 vs 15:50:00, 15:59:59 vs 16:00:00 ET).
4. Microsecond precision boundaries.
5. All 2026 US market holidays and weekend queries.
6. Calendar date math: add_trading_days (positive, negative, zero, spanning holiday weekends, starting on weekends/holidays),
   trading_days_between, and get_next_trading_day / get_previous_trading_day.
7. Timezone resilience: UTC, naive rejection across all methods, non-UTC tz-aware (Tokyo, Honolulu, London),
   and local date rollover scenarios.
8. Dataclass immutability and property precision (duration_minutes, open_time_et, close_time_et).
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError
from tbot.data.calendar import MarketCalendar
from tbot.data.types import ET_ZONE, MarketClock, TradingDay


@pytest.fixture
def holidays_2026() -> set[date]:
    """Official US equity market holidays for 2026."""
    return {
        date(2026, 1, 1),    # New Year's Day (Thursday)
        date(2026, 1, 19),   # Martin Luther King Jr. Day (Monday)
        date(2026, 2, 16),   # Presidents' Day (Monday)
        date(2026, 4, 3),    # Good Friday (Friday)
        date(2026, 5, 25),   # Memorial Day (Monday)
        date(2026, 6, 19),   # Juneteenth (Friday)
        date(2026, 7, 3),    # Independence Day observed (Friday)
        date(2026, 9, 7),    # Labor Day (Monday)
        date(2026, 11, 26),  # Thanksgiving Day (Thursday)
        date(2026, 12, 25),  # Christmas Day (Friday)
    }


@pytest.fixture
def early_closes_2026() -> dict[date, tuple[int, int]]:
    """Early market close sessions in 2026 (13:00 ET)."""
    return {
        date(2026, 11, 27): (13, 0),  # Black Friday (day after Thanksgiving)
        date(2026, 12, 24): (13, 0),  # Christmas Eve
    }


@pytest.fixture
def calendar_2026(
    holidays_2026: set[date],
    early_closes_2026: dict[date, tuple[int, int]],
) -> MarketCalendar:
    """Full-year 2026 calendar fixture with SimulatedClock."""
    start_date = date(2026, 1, 1)
    end_date = date(2026, 12, 31)
    clock = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))
    days = MarketCalendar.generate_standard_trading_days(
        start=start_date,
        end=end_date,
        holidays=holidays_2026,
        early_closes=early_closes_2026,
    )
    return MarketCalendar(
        clock=clock,
        initial_days=days,
        covered_range=(start_date, end_date),
    )


# ===========================================================================
# 1. Daylight Saving Time (DST) Stress Tests
# ===========================================================================


class TestDSTTransitions:
    """Stress tests for Spring Forward (EST -> EDT) and Fall Back (EDT -> EST)."""

    def test_spring_forward_transition_days(self, calendar_2026: MarketCalendar) -> None:
        """In 2026, DST starts Sunday March 8: Friday March 6 is EST (UTC-5), Monday March 9 is EDT (UTC-4)."""
        # Friday March 6 (EST = UTC-5)
        fri = calendar_2026.get_trading_day(date(2026, 3, 6))
        assert fri is not None
        assert fri.open_time == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
        assert fri.close_time == datetime(2026, 3, 6, 21, 0, tzinfo=UTC)
        assert fri.open_time_et.hour == 9 and fri.open_time_et.minute == 30
        assert fri.close_time_et.hour == 16 and fri.close_time_et.minute == 0
        assert fri.duration_minutes == 390.0

        # Monday March 9 (EDT = UTC-4)
        mon = calendar_2026.get_trading_day(date(2026, 3, 9))
        assert mon is not None
        assert mon.open_time == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
        assert mon.close_time == datetime(2026, 3, 9, 20, 0, tzinfo=UTC)
        assert mon.open_time_et.hour == 9 and mon.open_time_et.minute == 30
        assert mon.close_time_et.hour == 16 and mon.close_time_et.minute == 0
        assert mon.duration_minutes == 390.0

    def test_spring_forward_weekend_clock(self, calendar_2026: MarketCalendar) -> None:
        """During the transition weekend (March 7-8), market clock must correctly project to EDT Monday."""
        # Saturday March 7, 2026
        sat_dt = datetime(2026, 3, 7, 18, 0, tzinfo=UTC)
        clock_sat = calendar_2026.get_market_clock(sat_dt)
        assert not clock_sat.is_open
        assert clock_sat.next_open == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)  # 09:30 EDT

        # Sunday March 8 during the 2:00 -> 3:00 transition
        sun_trans = datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 03:00 EDT
        clock_sun = calendar_2026.get_market_clock(sun_trans)
        assert not clock_sun.is_open
        assert clock_sun.next_open == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)

    def test_fall_back_transition_days(self, calendar_2026: MarketCalendar) -> None:
        """In 2026, DST ends Sunday November 1: Friday Oct 30 is EDT (UTC-4), Monday Nov 2 is EST (UTC-5)."""
        # Friday October 30 (EDT = UTC-4)
        fri = calendar_2026.get_trading_day(date(2026, 10, 30))
        assert fri is not None
        assert fri.open_time == datetime(2026, 10, 30, 13, 30, tzinfo=UTC)
        assert fri.close_time == datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
        assert fri.open_time_et.hour == 9 and fri.open_time_et.minute == 30
        assert fri.close_time_et.hour == 16 and fri.close_time_et.minute == 0
        assert fri.duration_minutes == 390.0

        # Monday November 2 (EST = UTC-5)
        mon = calendar_2026.get_trading_day(date(2026, 11, 2))
        assert mon is not None
        assert mon.open_time == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        assert mon.close_time == datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
        assert mon.open_time_et.hour == 9 and mon.open_time_et.minute == 30
        assert mon.close_time_et.hour == 16 and mon.close_time_et.minute == 0
        assert mon.duration_minutes == 390.0

    def test_fall_back_weekend_clock(self, calendar_2026: MarketCalendar) -> None:
        """During the fall-back weekend (Oct 31 - Nov 1), clock must project to EST Monday."""
        # Sunday November 1, 2026 at 06:00 UTC (01:00 EST fold 1)
        sun_dt = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
        clock_sun = calendar_2026.get_market_clock(sun_dt)
        assert not clock_sun.is_open
        assert clock_sun.next_open == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)  # 09:30 EST
        assert clock_sun.next_close == datetime(2026, 11, 2, 21, 0, tzinfo=UTC)  # 16:00 EST

    def test_alpaca_sync_dst_handling(self) -> None:
        """Verify Alpaca sync parses naive ET across DST boundaries correctly into UTC."""
        mock_client = MagicMock()
        mock_fri = MagicMock()
        mock_fri.date = date(2026, 3, 6)
        mock_fri.open = datetime(2026, 3, 6, 9, 30)  # naive ET
        mock_fri.close = datetime(2026, 3, 6, 16, 0)

        mock_mon = MagicMock()
        mock_mon.date = date(2026, 3, 9)
        mock_mon.open = datetime(2026, 3, 9, 9, 30)  # naive ET
        mock_mon.close = datetime(2026, 3, 9, 16, 0)

        mock_client.get_calendar.return_value = [mock_fri, mock_mon]
        clock = SimulatedClock(datetime(2026, 3, 5, 12, 0, tzinfo=UTC))
        cal = MarketCalendar(clock=clock, trading_client=mock_client)
        days = cal.sync_calendar(date(2026, 3, 6), date(2026, 3, 9))

        assert len(days) == 2
        assert days[0].open_time == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)  # EST
        assert days[1].open_time == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)  # EDT


# ===========================================================================
# 2. Early Market Close Days Stress Tests
# ===========================================================================


class TestEarlyMarketCloseAdversarial:
    """Stress tests for early close days (13:00 ET close, close window 12:50-13:00 ET)."""

    def test_early_close_window_adversarial_boundaries(self, calendar_2026: MarketCalendar) -> None:
        """Verify close window 12:50-13:00 ET down to the second on Black Friday 2026-11-27."""
        # 12:49:59 ET -> NOT close window, IS regular trading window
        t1 = datetime(2026, 11, 27, 12, 49, 59, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t1)
        assert not calendar_2026.is_close_window(t1)
        assert calendar_2026.is_regular_trading_window(t1)
        assert pytest.approx(calendar_2026.minutes_until_close(t1), abs=1e-4) == 10.0 + (1.0 / 60.0)

        # 12:50:00 ET -> IS close window, NOT regular trading window
        t2 = datetime(2026, 11, 27, 12, 50, 0, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t2)
        assert calendar_2026.is_close_window(t2)
        assert not calendar_2026.is_regular_trading_window(t2)
        assert pytest.approx(calendar_2026.minutes_until_close(t2), abs=1e-4) == 10.0

        # 12:59:59 ET -> IS close window, market open, 1 second until close
        t3 = datetime(2026, 11, 27, 12, 59, 59, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(t3)
        assert calendar_2026.is_close_window(t3)
        assert not calendar_2026.is_regular_trading_window(t3)
        assert pytest.approx(calendar_2026.minutes_until_close(t3), abs=1e-4) == 1.0 / 60.0

        # 13:00:00 ET -> Market closed, IS close window (close boundary), NOT regular trading
        t4 = datetime(2026, 11, 27, 13, 0, 0, tzinfo=ET_ZONE)
        assert not calendar_2026.is_market_open(t4)
        assert calendar_2026.is_close_window(t4)
        assert not calendar_2026.is_regular_trading_window(t4)
        assert calendar_2026.minutes_until_close(t4) is None

        # 13:00:01 ET -> Market closed, NOT close window
        t5 = datetime(2026, 11, 27, 13, 0, 1, tzinfo=ET_ZONE)
        assert not calendar_2026.is_market_open(t5)
        assert not calendar_2026.is_close_window(t5)
        assert not calendar_2026.is_regular_trading_window(t5)
        assert calendar_2026.minutes_until_close(t5) is None

    def test_early_close_session_duration_and_times(self, calendar_2026: MarketCalendar) -> None:
        """Verify duration and session times on Christmas Eve (2026-12-24)."""
        xmas_eve = date(2026, 12, 24)
        day = calendar_2026.get_trading_day(xmas_eve)
        assert day is not None
        assert day.is_early_close
        assert day.duration_minutes == 210.0  # 3.5 hours
        assert day.open_time == datetime(2026, 12, 24, 14, 30, tzinfo=UTC)  # 09:30 EST
        assert day.close_time == datetime(2026, 12, 24, 18, 0, tzinfo=UTC)  # 13:00 EST
        assert calendar_2026.is_early_close(xmas_eve)

    def test_is_early_close_without_date_uses_clock(self, calendar_2026: MarketCalendar) -> None:
        """is_early_close() with None date inspects the injected clock's current date."""
        sim_clock = calendar_2026.clock
        assert isinstance(sim_clock, SimulatedClock)

        # Set clock to Black Friday at 11:00 ET
        sim_clock.set_time(datetime(2026, 11, 27, 11, 0, tzinfo=ET_ZONE).astimezone(UTC))
        assert calendar_2026.is_early_close() is True

        # Set clock to normal trading day (2026-09-21)
        sim_clock.set_time(datetime(2026, 9, 21, 11, 0, tzinfo=ET_ZONE).astimezone(UTC))
        assert calendar_2026.is_early_close() is False


# ===========================================================================
# 3. Boundary Seconds Stress Tests
# ===========================================================================


class TestBoundarySecondsAdversarial:
    """Rigorous boundary second and sub-second stress testing for all session transitions."""

    def test_open_boundary_seconds(self, calendar_2026: MarketCalendar) -> None:
        """09:29:59 ET vs 09:30:00 ET on standard day."""
        t_pre = datetime(2026, 9, 21, 9, 29, 59, tzinfo=ET_ZONE)
        t_open = datetime(2026, 9, 21, 9, 30, 0, tzinfo=ET_ZONE)

        # 09:29:59 ET
        assert not calendar_2026.is_market_open(t_pre)
        assert not calendar_2026.is_open_window(t_pre)
        assert not calendar_2026.is_regular_trading_window(t_pre)
        assert calendar_2026.minutes_since_open(t_pre) is None
        assert calendar_2026.minutes_until_close(t_pre) is None

        # 09:30:00 ET
        assert calendar_2026.is_market_open(t_open)
        assert calendar_2026.is_open_window(t_open)
        assert not calendar_2026.is_regular_trading_window(t_open)
        assert calendar_2026.minutes_since_open(t_open) == 0.0
        assert calendar_2026.minutes_until_close(t_open) == 390.0

    def test_open_window_transition_seconds(self, calendar_2026: MarketCalendar) -> None:
        """09:59:59 ET vs 10:00:00 ET (transition from open window to regular window)."""
        t_in_open = datetime(2026, 9, 21, 9, 59, 59, tzinfo=ET_ZONE)
        t_regular = datetime(2026, 9, 21, 10, 0, 0, tzinfo=ET_ZONE)

        # 09:59:59 ET
        assert calendar_2026.is_market_open(t_in_open)
        assert calendar_2026.is_open_window(t_in_open)
        assert not calendar_2026.is_regular_trading_window(t_in_open)

        # 10:00:00 ET
        assert calendar_2026.is_market_open(t_regular)
        assert not calendar_2026.is_open_window(t_regular)
        assert calendar_2026.is_regular_trading_window(t_regular)

    def test_close_window_transition_seconds(self, calendar_2026: MarketCalendar) -> None:
        """15:49:59 ET vs 15:50:00 ET (transition from regular window to close window)."""
        t_before = datetime(2026, 9, 21, 15, 49, 59, tzinfo=ET_ZONE)
        t_at = datetime(2026, 9, 21, 15, 50, 0, tzinfo=ET_ZONE)

        # 15:49:59 ET
        assert calendar_2026.is_market_open(t_before)
        assert not calendar_2026.is_close_window(t_before)
        assert calendar_2026.is_regular_trading_window(t_before)

        # 15:50:00 ET
        assert calendar_2026.is_market_open(t_at)
        assert calendar_2026.is_close_window(t_at)
        assert not calendar_2026.is_regular_trading_window(t_at)

    def test_close_boundary_seconds(self, calendar_2026: MarketCalendar) -> None:
        """15:59:59 ET vs 16:00:00 ET vs 16:00:01 ET (market close)."""
        t_pre = datetime(2026, 9, 21, 15, 59, 59, tzinfo=ET_ZONE)
        t_close = datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET_ZONE)
        t_post = datetime(2026, 9, 21, 16, 0, 1, tzinfo=ET_ZONE)

        # 15:59:59 ET
        assert calendar_2026.is_market_open(t_pre)
        assert calendar_2026.is_close_window(t_pre)
        assert not calendar_2026.is_regular_trading_window(t_pre)
        assert pytest.approx(calendar_2026.minutes_until_close(t_pre), abs=1e-4) == 1.0 / 60.0

        # 16:00:00 ET
        assert not calendar_2026.is_market_open(t_close)
        assert calendar_2026.is_close_window(t_close)
        assert not calendar_2026.is_regular_trading_window(t_close)
        assert calendar_2026.minutes_until_close(t_close) is None

        # 16:00:01 ET
        assert not calendar_2026.is_market_open(t_post)
        assert not calendar_2026.is_close_window(t_post)
        assert not calendar_2026.is_regular_trading_window(t_post)
        assert calendar_2026.minutes_until_close(t_post) is None

    def test_subsecond_microsecond_precision(self, calendar_2026: MarketCalendar) -> None:
        """Verify behavior at microsecond level around 09:30 and 16:00."""
        # 09:29:59.999999 ET -> Closed
        us_pre = datetime(2026, 9, 21, 9, 29, 59, 999999, tzinfo=ET_ZONE)
        assert not calendar_2026.is_market_open(us_pre)

        # 09:30:00.000001 ET -> Open
        us_open = datetime(2026, 9, 21, 9, 30, 0, 1, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(us_open)

        # 15:59:59.999999 ET -> Open
        us_pre_close = datetime(2026, 9, 21, 15, 59, 59, 999999, tzinfo=ET_ZONE)
        assert calendar_2026.is_market_open(us_pre_close)

        # 16:00:00.000001 ET -> Closed, outside close window
        us_post_close = datetime(2026, 9, 21, 16, 0, 0, 1, tzinfo=ET_ZONE)
        assert not calendar_2026.is_market_open(us_post_close)
        assert not calendar_2026.is_close_window(us_post_close)


# ===========================================================================
# 4. Holiday Handling and Weekend Queries
# ===========================================================================


class TestHolidaysAndWeekendsAdversarial:
    """Stress tests for full 2026 calendar holidays and weekend queries."""

    def test_all_2026_holidays_are_non_trading(
        self, calendar_2026: MarketCalendar, holidays_2026: set[date]
    ) -> None:
        """All 10 official holidays must be non-trading days with closed market across all hours."""
        for holiday in holidays_2026:
            assert not calendar_2026.is_trading_day(holiday), f"Failed for {holiday}"
            assert calendar_2026.get_trading_day(holiday) is None
            assert calendar_2026.get_session_times(holiday) is None

            for hour in (0, 9, 10, 12, 16, 20):
                ts = datetime(holiday.year, holiday.month, holiday.day, hour, 0, tzinfo=ET_ZONE)
                assert not calendar_2026.is_market_open(ts)
                assert not calendar_2026.is_open_window(ts)
                assert not calendar_2026.is_close_window(ts)
                assert not calendar_2026.is_regular_trading_window(ts)

    def test_good_friday_long_weekend_projection(self, calendar_2026: MarketCalendar) -> None:
        """Good Friday (Apr 3, 2026): Thursday post-market projects next_open to Monday Apr 6."""
        # Thursday Apr 2 post-market at 17:00 ET
        thu_post = datetime(2026, 4, 2, 17, 0, tzinfo=ET_ZONE)
        clock_thu = calendar_2026.get_market_clock(thu_post)
        assert not clock_thu.is_open
        expected_mon_open = datetime(2026, 4, 6, 13, 30, tzinfo=UTC)  # Monday 09:30 EDT
        assert clock_thu.next_open == expected_mon_open

        # Good Friday Apr 3 at 12:00 ET
        gf_noon = datetime(2026, 4, 3, 12, 0, tzinfo=ET_ZONE)
        clock_gf = calendar_2026.get_market_clock(gf_noon)
        assert not clock_gf.is_open
        assert clock_gf.next_open == expected_mon_open

    def test_mlk_day_long_weekend_projection(self, calendar_2026: MarketCalendar) -> None:
        """MLK Day (Monday Jan 19, 2026): Friday post-market projects next_open to Tuesday Jan 20."""
        fri_post = datetime(2026, 1, 16, 17, 0, tzinfo=ET_ZONE)
        clock_fri = calendar_2026.get_market_clock(fri_post)
        assert not clock_fri.is_open
        expected_tue_open = datetime(2026, 1, 20, 14, 30, tzinfo=UTC)  # Tuesday 09:30 EST
        assert clock_fri.next_open == expected_tue_open

    def test_christmas_long_weekend_projection(self, calendar_2026: MarketCalendar) -> None:
        """Christmas (Friday Dec 25, 2026): Christmas Eve post-13:00 ET projects next_open to Monday Dec 28."""
        eve_post = datetime(2026, 12, 24, 13, 30, tzinfo=ET_ZONE)
        clock_eve = calendar_2026.get_market_clock(eve_post)
        assert not clock_eve.is_open
        expected_mon_open = datetime(2026, 12, 28, 14, 30, tzinfo=UTC)  # Monday 09:30 EST
        assert clock_eve.next_open == expected_mon_open

    def test_weekend_queries_across_all_hours(self, calendar_2026: MarketCalendar) -> None:
        """Saturday and Sunday queries must consistently report market closed and project to Monday."""
        saturday = date(2026, 9, 19)
        sunday = date(2026, 9, 20)
        expected_mon_open = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)

        for d in (saturday, sunday):
            for h in (0, 6, 9, 12, 16, 21, 23):
                ts = datetime(d.year, d.month, d.day, h, 30, tzinfo=ET_ZONE)
                assert not calendar_2026.is_market_open(ts)
                m_clock = calendar_2026.get_market_clock(ts)
                assert not m_clock.is_open
                assert m_clock.next_open == expected_mon_open


# ===========================================================================
# 5. Calendar Date Math Stress Tests
# ===========================================================================


class TestCalendarDateMathAdversarial:
    """Stress tests for add_trading_days, trading_days_between, and boundary traversal."""

    def test_add_trading_days_zero(self, calendar_2026: MarketCalendar) -> None:
        """add_trading_days with n=0 returns identical date."""
        assert calendar_2026.add_trading_days(date(2026, 9, 21), 0) == date(2026, 9, 21)
        assert calendar_2026.add_trading_days(date(2026, 9, 19), 0) == date(2026, 9, 19)  # Saturday
        assert calendar_2026.add_trading_days(date(2026, 4, 3), 0) == date(2026, 4, 3)    # Good Friday

    def test_add_trading_days_positive_and_negative(self, calendar_2026: MarketCalendar) -> None:
        """Positive and negative n roundtrips across regular weeks."""
        start = date(2026, 9, 21)  # Monday
        assert calendar_2026.add_trading_days(start, 4) == date(2026, 9, 25)   # Friday
        assert calendar_2026.add_trading_days(start, 5) == date(2026, 9, 28)   # Next Monday
        assert calendar_2026.add_trading_days(date(2026, 9, 28), -5) == start

    def test_add_trading_days_starting_on_weekend(self, calendar_2026: MarketCalendar) -> None:
        """Adding trading days starting on Saturday or Sunday."""
        sat = date(2026, 9, 19)
        sun = date(2026, 9, 20)

        # Saturday + 1 -> Monday
        assert calendar_2026.add_trading_days(sat, 1) == date(2026, 9, 21)
        # Sunday + 1 -> Monday
        assert calendar_2026.add_trading_days(sun, 1) == date(2026, 9, 21)
        # Saturday - 1 -> Friday
        assert calendar_2026.add_trading_days(sat, -1) == date(2026, 9, 18)
        # Sunday - 1 -> Friday
        assert calendar_2026.add_trading_days(sun, -1) == date(2026, 9, 18)

    def test_add_trading_days_spanning_long_holiday_weekends(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """Test arithmetic across Good Friday, MLK Day, Thanksgiving, and Christmas."""
        # Good Friday: Apr 3 (Fri)
        thu_before_gf = date(2026, 4, 2)
        assert calendar_2026.add_trading_days(thu_before_gf, 1) == date(2026, 4, 6)   # Monday
        assert calendar_2026.add_trading_days(thu_before_gf, 2) == date(2026, 4, 7)   # Tuesday
        assert calendar_2026.add_trading_days(date(2026, 4, 6), -1) == thu_before_gf

        # MLK Day: Jan 19 (Mon)
        fri_before_mlk = date(2026, 1, 16)
        assert calendar_2026.add_trading_days(fri_before_mlk, 1) == date(2026, 1, 20)  # Tuesday
        assert calendar_2026.add_trading_days(date(2026, 1, 20), -1) == fri_before_mlk

        # Thanksgiving: Nov 26 (Thu) is holiday, Nov 27 (Fri) is early close trading day
        wed_before_tg = date(2026, 11, 25)
        assert calendar_2026.add_trading_days(wed_before_tg, 1) == date(2026, 11, 27)  # Black Friday
        assert calendar_2026.add_trading_days(wed_before_tg, 2) == date(2026, 11, 30)  # Next Monday
        assert calendar_2026.add_trading_days(date(2026, 11, 30), -1) == date(2026, 11, 27)
        assert calendar_2026.add_trading_days(date(2026, 11, 30), -2) == wed_before_tg

        # Christmas: Dec 25 (Fri)
        thu_eve = date(2026, 12, 24)
        assert calendar_2026.add_trading_days(thu_eve, 1) == date(2026, 12, 28)  # Next Monday
        assert calendar_2026.add_trading_days(date(2026, 12, 28), -1) == thu_eve

    def test_trading_days_between_adversarial_cases(self, calendar_2026: MarketCalendar) -> None:
        """trading_days_between: start > end, start == end (trading vs holiday), monthly count."""
        # start > end -> 0
        assert calendar_2026.trading_days_between(date(2026, 9, 25), date(2026, 9, 21)) == 0

        # start == end on trading day -> 1
        assert calendar_2026.trading_days_between(date(2026, 9, 21), date(2026, 9, 21)) == 1

        # start == end on weekend -> 0
        assert calendar_2026.trading_days_between(date(2026, 9, 19), date(2026, 9, 19)) == 0

        # start == end on holiday -> 0
        assert calendar_2026.trading_days_between(date(2026, 4, 3), date(2026, 4, 3)) == 0

        # Entire month of September 2026: 30 days total - 8 weekend days - 1 holiday (Labor Day Sep 7) = 21 trading days
        assert calendar_2026.trading_days_between(date(2026, 9, 1), date(2026, 9, 30)) == 21

    def test_get_next_and_previous_trading_day_adversarial(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """get_next_trading_day and get_previous_trading_day skip weekends and holidays."""
        # Next from Thursday before Good Friday -> Monday
        assert calendar_2026.get_next_trading_day(date(2026, 4, 2)).date == date(2026, 4, 6)

        # Previous from Monday after MLK Day -> Friday
        assert calendar_2026.get_previous_trading_day(date(2026, 1, 20)).date == date(2026, 1, 16)

        # Boundary beyond covered cache raises MarketDataError (cache miss without client)
        with pytest.raises(MarketDataError, match="no se encuentra en el cache"):
            calendar_2026.get_next_trading_day(date(2026, 12, 31))

        # When 30 days are covered but no trading days exist (e.g. simulated market shutdown)
        empty_clock = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))
        shutdown_cal = MarketCalendar(
            clock=empty_clock,
            initial_days=[],
            covered_range=(date(2026, 1, 1), date(2026, 2, 15)),
        )
        with pytest.raises(MarketDataError, match="No se encontró un día hábil"):
            shutdown_cal.get_next_trading_day(date(2026, 1, 1))


# ===========================================================================
# 6. Timezone Resilience Stress Tests
# ===========================================================================


class TestTimezoneResilienceAdversarial:
    """Stress tests for naive datetime rejection and non-UTC timezone awareness."""

    def test_naive_datetime_rejection_across_all_methods(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """All methods accepting timestamp MUST reject naive datetimes with ValueError."""
        naive_dt = datetime(2026, 9, 21, 10, 0, 0)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.is_market_open(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.is_open_window(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.is_close_window(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.is_regular_trading_window(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.minutes_until_close(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.minutes_since_open(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            calendar_2026.get_market_clock(naive_dt)

    def test_non_utc_timezone_awareness_tokyo_and_london(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """Timestamps in Tokyo (UTC+9) and London (UTC+1) resolve to correct New York market state."""
        tokyo_tz = ZoneInfo("Asia/Tokyo")
        london_tz = ZoneInfo("Europe/London")

        # 09:30 EDT on 2026-09-21 is 13:30 UTC -> 22:30 JST (Tokyo) and 14:30 BST (London)
        tokyo_open = datetime(2026, 9, 21, 22, 30, tzinfo=tokyo_tz)
        london_open = datetime(2026, 9, 21, 14, 30, tzinfo=london_tz)

        assert calendar_2026.is_market_open(tokyo_open)
        assert calendar_2026.is_open_window(tokyo_open)
        assert calendar_2026.minutes_since_open(tokyo_open) == 0.0

        assert calendar_2026.is_market_open(london_open)
        assert calendar_2026.is_open_window(london_open)
        assert calendar_2026.minutes_since_open(london_open) == 0.0

    def test_non_utc_timezone_date_rollover(self, calendar_2026: MarketCalendar) -> None:
        """When local time is Tuesday in Tokyo, but still Monday in New York."""
        tokyo_tz = ZoneInfo("Asia/Tokyo")

        # 15:55 EDT on Monday 2026-09-21 is 19:55 UTC -> 04:55 JST on Tuesday 2026-09-22
        tokyo_close_win = datetime(2026, 9, 22, 4, 55, tzinfo=tokyo_tz)
        assert calendar_2026.is_market_open(tokyo_close_win)
        assert calendar_2026.is_close_window(tokyo_close_win)
        assert not calendar_2026.is_regular_trading_window(tokyo_close_win)

        # 16:05 EDT on Monday 2026-09-21 is 20:05 UTC -> 05:05 JST on Tuesday 2026-09-22 (closed)
        tokyo_post = datetime(2026, 9, 22, 5, 5, tzinfo=tokyo_tz)
        assert not calendar_2026.is_market_open(tokyo_post)
        assert not calendar_2026.is_close_window(tokyo_post)

    def test_honolulu_timezone_behind_utc(self, calendar_2026: MarketCalendar) -> None:
        """Honolulu (UTC-10): 03:30 HST is 13:30 UTC -> 09:30 EDT."""
        hono_tz = ZoneInfo("Pacific/Honolulu")
        hono_open = datetime(2026, 9, 21, 3, 30, tzinfo=hono_tz)

        assert calendar_2026.is_market_open(hono_open)
        assert calendar_2026.is_open_window(hono_open)


# ===========================================================================
# 7. Dataclass Immutability and Property Verification
# ===========================================================================


class TestDataclassImmutabilityAndProperties:
    """Stress tests verifying frozen dataclass invariants and properties."""

    def test_trading_day_frozen(self) -> None:
        """TradingDay must be immutable and reject attribute mutation."""
        day = TradingDay(
            date=date(2026, 9, 21),
            open_time=datetime(2026, 9, 21, 13, 30, tzinfo=UTC),
            close_time=datetime(2026, 9, 21, 20, 0, tzinfo=UTC),
        )
        with pytest.raises(FrozenInstanceError):
            day.date = date(2026, 9, 22)  # type: ignore[misc]

    def test_market_clock_frozen(self) -> None:
        """MarketClock must be immutable and reject attribute mutation."""
        m_clock = MarketClock(
            timestamp=datetime(2026, 9, 21, 13, 30, tzinfo=UTC),
            is_open=True,
            next_open=datetime(2026, 9, 22, 13, 30, tzinfo=UTC),
            next_close=datetime(2026, 9, 21, 20, 0, tzinfo=UTC),
        )
        with pytest.raises(FrozenInstanceError):
            m_clock.is_open = False  # type: ignore[misc]

    def test_trading_day_duration_minutes_precision(self) -> None:
        """duration_minutes must match exact total seconds / 60."""
        std_day = TradingDay(
            date=date(2026, 9, 21),
            open_time=datetime(2026, 9, 21, 13, 30, tzinfo=UTC),
            close_time=datetime(2026, 9, 21, 20, 0, tzinfo=UTC),
        )
        assert std_day.duration_minutes == 390.0

        early_day = TradingDay(
            date=date(2026, 11, 27),
            open_time=datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
            close_time=datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
            is_early_close=True,
        )
        assert early_day.duration_minutes == 210.0


# ===========================================================================
# 8. Multi-Year, Leap Year & Granular Sweep Stress Tests
# ===========================================================================


class TestGranularSweepAndEdgeCases:
    """Second-by-second continuous sweep and leap-year calendar resilience."""

    def test_continuous_second_by_second_open_sweep(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """Continuous sweep around market open: 09:29:55 to 09:30:05 ET."""
        for sec in range(55, 60):
            ts = datetime(2026, 9, 21, 9, 29, sec, tzinfo=ET_ZONE)
            assert not calendar_2026.is_market_open(ts), f"Failed at 09:29:{sec}"
            assert not calendar_2026.is_open_window(ts)
            assert not calendar_2026.is_regular_trading_window(ts)
            assert calendar_2026.minutes_since_open(ts) is None

        for sec in range(0, 10):
            ts = datetime(2026, 9, 21, 9, 30, sec, tzinfo=ET_ZONE)
            assert calendar_2026.is_market_open(ts), f"Failed at 09:30:{sec:02d}"
            assert calendar_2026.is_open_window(ts)
            assert not calendar_2026.is_regular_trading_window(ts)
            assert pytest.approx(calendar_2026.minutes_since_open(ts), abs=1e-5) == sec / 60.0

    def test_continuous_second_by_second_close_sweep(
        self, calendar_2026: MarketCalendar
    ) -> None:
        """Continuous sweep around market close: 15:59:55 to 16:00:05 ET."""
        for sec in range(55, 60):
            ts = datetime(2026, 9, 21, 15, 59, sec, tzinfo=ET_ZONE)
            assert calendar_2026.is_market_open(ts), f"Failed at 15:59:{sec}"
            assert calendar_2026.is_close_window(ts)
            assert not calendar_2026.is_regular_trading_window(ts)
            rem_sec = 60 - sec
            assert pytest.approx(calendar_2026.minutes_until_close(ts), abs=1e-5) == rem_sec / 60.0

        # At 16:00:00 exact: closed, inside close window
        at_close = datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET_ZONE)
        assert not calendar_2026.is_market_open(at_close)
        assert calendar_2026.is_close_window(at_close)
        assert not calendar_2026.is_regular_trading_window(at_close)

        for sec in range(1, 10):
            ts = datetime(2026, 9, 21, 16, 0, sec, tzinfo=ET_ZONE)
            assert not calendar_2026.is_market_open(ts), f"Failed at 16:00:{sec:02d}"
            assert not calendar_2026.is_close_window(ts)
            assert not calendar_2026.is_regular_trading_window(ts)
            assert calendar_2026.minutes_until_close(ts) is None

    def test_leap_year_2024_february_29(self) -> None:
        """Leap year 2024: Feb 29 was Thursday, standard trading day."""
        clock = SimulatedClock(datetime(2024, 2, 29, 14, 30, tzinfo=UTC))
        days = MarketCalendar.generate_standard_trading_days(
            start=date(2024, 2, 1),
            end=date(2024, 3, 1),
        )
        cal = MarketCalendar(clock=clock, initial_days=days, covered_range=(date(2024, 2, 1), date(2024, 3, 1)))

        feb_29 = date(2024, 2, 29)
        assert cal.is_trading_day(feb_29)
        # EST in February 2024: 09:30 EST is 14:30 UTC
        day = cal.get_trading_day(feb_29)
        assert day is not None
        assert day.open_time == datetime(2024, 2, 29, 14, 30, tzinfo=UTC)
        assert day.close_time == datetime(2024, 2, 29, 21, 0, tzinfo=UTC)

        # Feb 28 (Wed) + 1 trading day -> Feb 29 (Thu)
        assert cal.add_trading_days(date(2024, 2, 28), 1) == feb_29
        # Feb 29 (Thu) + 1 trading day -> Mar 1 (Fri)
        assert cal.add_trading_days(feb_29, 1) == date(2024, 3, 1)

    def test_instances_isolation(self, calendar_2026: MarketCalendar) -> None:
        """Two independent calendar instances do not pollute each other's cache or clock."""
        clock1 = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))
        clock2 = SimulatedClock(datetime(2026, 12, 1, 14, 30, tzinfo=UTC))

        cal1 = MarketCalendar(clock=clock1, initial_days=[], covered_range=(date(2026, 9, 1), date(2026, 9, 30)))
        cal2 = MarketCalendar(clock=clock2, initial_days=[], covered_range=(date(2026, 12, 1), date(2026, 12, 31)))

        # cal1 covers Sep, not Dec
        assert cal1.is_trading_day(date(2026, 9, 21)) is False  # empty days in range
        with pytest.raises(MarketDataError):
            cal1.is_trading_day(date(2026, 12, 1))

        # cal2 covers Dec, not Sep
        assert cal2.is_trading_day(date(2026, 12, 1)) is False
        with pytest.raises(MarketDataError):
            cal2.is_trading_day(date(2026, 9, 21))
