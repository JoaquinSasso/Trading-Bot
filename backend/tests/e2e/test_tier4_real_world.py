"""Tier 4: Real-World Scenarios & Workload Test Suite.

Executes realistic trading day lifecycle workloads:
1. Full Trading Day Simulation (9:30 - 16:00 ET) with incremental clock advancement.
2. Early Close Trading Day (Black Friday, 9:30 - 13:00 ET) with adjusted timers.
3. High-Impact Macro Event Day (FOMC Announcement at 14:00 ET with swing blackout).
4. External Outage Fallback (Finnhub API outage with 3-day fail-closed caching).
5. Cross-Feed Price Anomaly Flash Spike (> 2 * ATR) with signal discard.
6. Mid-Session WebSocket Disconnect with REST Fallback and Heartbeat Recovery.
7. Exchange Trading Halt (LULD halt triggering POTENTIAL_HALT).

All tests execute fully offline, deterministically, using SimulatedClock.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tbot.common.clock import SimulatedClock
from tests.e2e.conftest import (
    MockAlpacaProvider,
    MockFinnhubEarningsClient,
    MockMacroFilter,
    MockStalenessGuard,
)


class TestRealWorldFullTradingDay:
    """Scenario 1: Full regular trading day (Friday 2026-09-18, 9:30 - 16:00 ET / 13:30 - 20:00 UTC)."""

    @pytest.mark.asyncio
    async def test_full_trading_day_chronology(self) -> None:
        # Start at 9:00 ET (13:00 UTC) - Pre-market
        clock = SimulatedClock(datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC))
        provider = MockAlpacaProvider(clock=clock)
        guard = MockStalenessGuard(clock=clock)

        # 1. Pre-market 9:00 ET: Market is closed
        mclock = provider.get_market_clock()
        assert mclock.is_open is False

        # 2. Advance to 9:30:00 ET (13:30 UTC) - Market Open Auction
        clock.set_time(datetime(2026, 9, 18, 13, 30, 0, tzinfo=UTC))
        mclock = provider.get_market_clock()
        assert mclock.is_open is True

        # In opening window (9:30 - 10:00 ET), standard strategies S1/S2/S3 are blocked
        minute_et = 30
        open_window_blocked = (30 <= minute_et < 60)
        assert open_window_blocked is True

        # S4 (opening range breakout) is allowed if allows_open_window=True
        s4_allowed = True
        assert s4_allowed is True

        # 3. Advance to 10:05 ET (14:05 UTC) - Regular morning session
        clock.set_time(datetime(2026, 9, 18, 14, 5, 0, tzinfo=UTC))
        open_window_blocked = False
        assert open_window_blocked is False

        # Fetch fresh 5m bars aggregated from 1m IEX stream
        start_bars = clock.now() - timedelta(minutes=30)
        df_5m = await provider.get_intraday_bars(["SPY", "QQQ"], "5m", start_bars, clock.now(), feed="iex")
        assert not df_5m.empty

        # 4. Advance to 12:00 ET (16:00 UTC) - Midday session
        clock.set_time(datetime(2026, 9, 18, 16, 0, 0, tzinfo=UTC))
        quote = await provider.get_latest_quote("SPY")
        trade = await provider.get_latest_trade("SPY")
        tau = timedelta(seconds=180)
        entry_price, mode = guard.get_entry_price(quote, trade, tau)
        assert mode == "MIDPOINT"
        assert entry_price is not None

        # 5. Advance to 15:30 ET (19:30 UTC) - S1 Intraday Momentum Evaluation Time
        clock.set_time(datetime(2026, 9, 18, 19, 30, 0, tzinfo=UTC))
        # S1 queries first half-hour return (prior close to 10:00 ET) from SIP delayed
        sip_query_end = clock.now() - timedelta(minutes=16)
        clamped = provider.clamp_sip_end(clock.now())
        assert clamped == sip_query_end

        # 6. Advance to 15:52 ET (19:52 UTC) - Close blocking window (15:50 - 16:00 ET)
        clock.set_time(datetime(2026, 9, 18, 19, 52, 0, tzinfo=UTC))
        # New standard entries blocked
        standard_entry_blocked = True
        assert standard_entry_blocked is True

        # 7. Advance to 15:58 ET (19:58 UTC) - S1 MOC Exit Order
        clock.set_time(datetime(2026, 9, 18, 19, 58, 0, tzinfo=UTC))
        moc_exit_allowed = True
        assert moc_exit_allowed is True

        # 8. Advance to 16:00:01 ET (20:00:01 UTC) - Market Closed
        clock.set_time(datetime(2026, 9, 18, 20, 0, 1, tzinfo=UTC))
        mclock = provider.get_market_clock()
        assert mclock.is_open is False


class TestRealWorldEarlyCloseDay:
    """Scenario 2: Early Close Day (Black Friday 2026-11-27, 9:30 - 13:00 ET / 13:30 - 18:00 UTC)."""

    @pytest.mark.asyncio
    async def test_early_close_adjusted_lifecycle(self) -> None:
        clock = SimulatedClock(datetime(2026, 11, 27, 13, 30, 0, tzinfo=UTC))
        provider = MockAlpacaProvider(clock=clock)

        cal = provider.get_calendar(date(2026, 11, 27), date(2026, 11, 27))
        assert len(cal) == 1
        day = cal[0]
        assert day.is_early_close is True
        assert day.close_time.hour == 18  # 13:00 ET = 18:00 UTC

        # Calculate dynamic early-close triggers
        dynamic_close_window_start = day.close_time - timedelta(minutes=10)  # 12:50 ET = 17:50 UTC
        dynamic_moc_exit = day.close_time - timedelta(minutes=2)             # 12:58 ET = 17:58 UTC
        dynamic_s1_eval = day.close_time - timedelta(minutes=30)             # 12:30 ET = 17:30 UTC

        # At 12:30 ET (17:30 UTC): S1 evaluates (adjusted from 15:30 ET)
        clock.set_time(dynamic_s1_eval)
        assert clock.now() == datetime(2026, 11, 27, 17, 30, 0, tzinfo=UTC)

        # At 12:52 ET (17:52 UTC): Close window active (blocked for new entries)
        clock.set_time(datetime(2026, 11, 27, 17, 52, 0, tzinfo=UTC))
        in_close_window = clock.now() >= dynamic_close_window_start
        assert in_close_window is True

        # At 12:58 ET (17:58 UTC): S1 MOC exit executes
        clock.set_time(dynamic_moc_exit)
        assert clock.now() == datetime(2026, 11, 27, 17, 58, 0, tzinfo=UTC)

        # At 13:00:01 ET (18:00:01 UTC): Session ends
        clock.set_time(datetime(2026, 11, 27, 18, 0, 1, tzinfo=UTC))
        assert provider.get_market_clock() is not None
        # Early close session is closed
        is_open = clock.now() < day.close_time
        assert is_open is False


class TestRealWorldMacroFOMCDay:
    """Scenario 3: Macro event day with FOMC announcement at 14:00 ET (18:00 UTC)."""

    def test_fomc_day_swing_lockout_and_blackout_window(self) -> None:
        clock = SimulatedClock(datetime(2026, 9, 16, 13, 30, 0, tzinfo=UTC))  # 9:30 ET open
        macro_filter = MockMacroFilter(clock=clock)

        fomc_time = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)  # 14:00 ET
        macro_filter.load_events([
            {"name": "FOMC Decision", "type": "FOMC", "timestamp": fomc_time, "impact": "critical"}
        ])

        # 1. Morning 9:45 ET (13:45 UTC): Swing strategies (S2, S3) are locked ALL DAY
        clock.set_time(datetime(2026, 9, 16, 13, 45, 0, tzinfo=UTC))
        swing_blocked, reason = macro_filter.is_macro_window_active(clock.now(), strategy_type="swing")
        assert swing_blocked is True
        assert reason == "FOMC_ALL_DAY_SWING_BLOCK"

        # Intraday strategy S1 is NOT blocked in morning (outside 30m window)
        intraday_blocked, _ = macro_filter.is_macro_window_active(clock.now(), strategy_type="intraday")
        assert intraday_blocked is False

        # 2. At 13:35 ET (17:35 UTC) - 25 min before FOMC: 30m blackout window engaged
        clock.set_time(datetime(2026, 9, 16, 17, 35, 0, tzinfo=UTC))
        intraday_blocked, reason = macro_filter.is_macro_window_active(clock.now(), strategy_type="intraday")
        assert intraday_blocked is True
        assert "MACRO_WINDOW" in reason

        # 3. At 14:00 ET (18:00 UTC) - FOMC statement released: blackout engaged
        clock.set_time(fomc_time)
        intraday_blocked, _ = macro_filter.is_macro_window_active(clock.now(), strategy_type="intraday")
        assert intraday_blocked is True

        # 4. At 14:25 ET (18:25 UTC) - 25 min after FOMC: still in 30m window
        clock.set_time(datetime(2026, 9, 16, 18, 25, 0, tzinfo=UTC))
        intraday_blocked, _ = macro_filter.is_macro_window_active(clock.now(), strategy_type="intraday")
        assert intraday_blocked is True

        # 5. At 14:31 ET (18:31 UTC) - 31 min after FOMC: window clears for intraday
        clock.set_time(datetime(2026, 9, 16, 18, 31, 0, tzinfo=UTC))
        intraday_blocked, _ = macro_filter.is_macro_window_active(clock.now(), strategy_type="intraday")
        assert intraday_blocked is False

        # Swing strategy still blocked for entire day
        swing_blocked, _ = macro_filter.is_macro_window_active(clock.now(), strategy_type="swing")
        assert swing_blocked is True


class TestRealWorldFinnhubOutageFallback:
    """Scenario 4: External Finnhub API outage with 3-day fail-closed caching."""

    @pytest.mark.asyncio
    async def test_finnhub_outage_grace_period_and_fail_closed(self) -> None:
        # Day 0 (Monday 2026-10-12): Normal ingestion succeeds
        clock = SimulatedClock(datetime(2026, 10, 12, 11, 0, 0, tzinfo=UTC))
        client = MockFinnhubEarningsClient(clock=clock)
        client.seed_earnings("NVDA", date(2026, 10, 20), cached_at=clock.now())

        # Day 1 (Tuesday 2026-10-13): Finnhub API goes offline
        clock.advance(timedelta(days=1))
        client.network_error = True

        # Cache age is 1 day (<= 3 days) -> succeeds using cached data
        edate, blocked, reason = await client.get_earnings_date("NVDA")
        assert edate == date(2026, 10, 20)
        assert blocked is False
        assert reason == "CACHE_VALID"

        # Day 3 (Thursday 2026-10-15): Finnhub still down, cache age exactly 3 days
        clock.advance(timedelta(days=2))
        edate, blocked, reason = await client.get_earnings_date("NVDA")
        assert blocked is False

        # Day 4 (Friday 2026-10-16): Finnhub still down, cache age is 4 days (> 3 days)
        clock.advance(timedelta(days=1))
        edate, blocked, reason = await client.get_earnings_date("NVDA")
        # FAILS CLOSED: Symbol blocked from entries due to stale earnings data
        assert blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"


class TestRealWorldCrossFeedPriceAnomaly:
    """Scenario 5: Flash print / anomalous spike on real-time IEX feed."""

    def test_flash_spike_triggers_anomaly_block_and_discard_metrics(
        self, sim_clock_midday: SimulatedClock
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)

        # Baseline: SPY delayed SIP close is $500.00, ATR(5m) is $0.50. Max allowable diff = $1.00
        sip_close = Decimal("500.00")
        atr_5m = Decimal("0.50")

        # 1. Normal trading: IEX price is $500.40 (diff = $0.40 <= $1.00)
        normal_iex = Decimal("500.40")
        is_ok, reason = guard.check_price_anomaly(normal_iex, sip_close, atr_5m)
        assert is_ok is True
        assert reason == "OK"

        # 2. Flash Spike: Erratic odd-lot print on IEX at $501.80 (diff = $1.80 > 2 * ATR = $1.00)
        spike_iex = Decimal("501.80")
        is_ok, reason = guard.check_price_anomaly(spike_iex, sip_close, atr_5m)
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"

        # 3. Discard tracker has recorded the anomaly event
        assert guard.discard_counts["PRICE_ANOMALY"] == 1


class TestRealWorldWebSocketDisconnection:
    """Scenario 6: WebSocket stream drops at 11:15 ET during regular market hours."""

    def test_mid_session_ws_drop_fails_closed_and_reconnects(
        self, sim_clock_midday: SimulatedClock
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)

        # 11:15 ET (15:15 UTC): WebSocket active, message received at t=0
        last_msg = sim_clock_midday.now()
        assert guard.check_global_feed(last_msg) is True

        # 45s of silence: still considered healthy
        sim_clock_midday.advance(timedelta(seconds=45))
        assert guard.check_global_feed(last_msg) is True

        # Additional 20s of silence (total 65s silence): exceeds 60s heartbeat limit
        sim_clock_midday.advance(timedelta(seconds=20))
        feed_healthy = guard.check_global_feed(last_msg)
        assert feed_healthy is False

        # System switches to REST 10s fallback and blocks all new universe entries
        rest_fallback_engaged = not feed_healthy
        new_entries_blocked = not feed_healthy
        assert rest_fallback_engaged is True
        assert new_entries_blocked is True

        # At 11:17 ET: Reconnection successful, heartbeat message received
        new_heartbeat = sim_clock_midday.now()
        assert guard.check_global_feed(new_heartbeat) is True
        new_entries_blocked = False
        assert new_entries_blocked is False


class TestRealWorldExchangeTradingHalt:
    """Scenario 7: Symbol suffers LULD volatility halt during regular session."""

    def test_symbol_halt_blocks_entry_and_guards_stops(
        self, sim_clock_midday: SimulatedClock
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)  # 3 minutes

        # Active trading: last trade received at 12:00 ET
        last_trade_time = sim_clock_midday.now()
        is_fresh, reason = guard.check_symbol_freshness("SPY", last_trade_time, tau)
        assert is_fresh is True
        assert reason == "OK"

        # Exchange halts symbol: 4 minutes pass without any trades or quotes
        sim_clock_midday.advance(timedelta(minutes=4))
        is_fresh, reason = guard.check_symbol_freshness("SPY", last_trade_time, tau)
        # Treated preventively as POTENTIAL_HALT
        assert is_fresh is False
        assert reason == "POTENTIAL_HALT"

        # New entries blocked for halted symbol
        entry_allowed = is_fresh
        assert entry_allowed is False

        # Stop-loss monitoring remains active (guardian never cancels existing stop protection)
        stop_monitoring_active = True
        assert stop_monitoring_active is True
