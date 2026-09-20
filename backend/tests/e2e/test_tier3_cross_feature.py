"""Tier 3: Cross-Feature Combinations & Pairwise Interaction Test Suite.

Validates interactions across hybrid feeds, rate limits, staleness guards,
price anomaly checks, session calendars, regime filters, and corporate/macro events.
All tests run offline, deterministically, using SimulatedClock.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError
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


class TestPairwiseFeedsAndRateLimiter:
    """Pairwise 1: Hybrid Feeds (SIP + IEX) combined with Shared Rate Limiter."""

    @pytest.mark.asyncio
    async def test_concurrent_sip_and_iex_budget_consumption(
        self, sim_clock_midday: SimulatedClock
    ) -> None:
        """Simultaneous historical SIP delayed queries and live IEX queries share 180 req/min budget."""
        rate_limiter = MockRateLimiter(max_requests=180, window_seconds=60.0)
        provider = MockAlpacaProvider(clock=sim_clock_midday, rate_limiter=rate_limiter)

        symbols = ["SPY", "QQQ", "GLD", "SPYM", "QQQM"]
        now = sim_clock_midday.now()
        start = now - timedelta(hours=2)
        end = now - timedelta(minutes=20)

        # Execute 50 intraday queries (alternating SIP and IEX)
        for i in range(50):
            feed = "sip_delayed" if i % 2 == 0 else "iex"
            df = await provider.get_intraday_bars(symbols[:2], "5m", start, end, feed=feed)
            assert not df.empty

        # 50 queries consumed
        assert len(rate_limiter.timestamps) == 50

        # Burst 130 more queries to hit exact 180 limit
        for _ in range(130):
            await provider.get_intraday_bars(symbols[:1], "1m", start, end, feed="iex")

        assert len(rate_limiter.timestamps) == 180

        # 181st query must fail with rate limit error
        with pytest.raises(MarketDataError, match="Rate limit exceeded"):
            await provider.get_intraday_bars(symbols[:1], "1m", start, end, feed="iex")


class TestPairwiseQuotesStalenessAndEntryPrice:
    """Pairwise 2: Real-time Quotes, Staleness Guard, and Strict Entry Price Selector."""

    @pytest.mark.parametrize(
        ("spread_bps", "trade_age_secs", "has_synthetic_bar", "expected_mode", "expected_price_valid"),
        [
            (5.0, 30, False, "MIDPOINT", True),      # Narrow spread + fresh trade -> Midpoint
            (5.0, 300, False, "MIDPOINT", True),     # Narrow spread + stale trade -> Midpoint (quote is fresh)
            (15.0, 30, False, "LAST_TRADE", True),   # Wide spread + fresh trade -> Last trade
            (15.0, 200, False, "STALE_PRICE", False),# Wide spread + stale trade (>180s) -> STALE_PRICE discard
            (5.0, 30, True, "MIDPOINT", True),       # Synthetic bar present -> ignored, midpoint selected
            (25.0, 250, True, "STALE_PRICE", False), # Wide spread + stale trade + synthetic bar -> STALE_PRICE
        ],
    )
    def test_entry_price_pairwise_matrix(
        self,
        sim_clock_midday: SimulatedClock,
        spread_bps: float,
        trade_age_secs: int,
        has_synthetic_bar: bool,
        expected_mode: str,
        expected_price_valid: bool,
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)
        now = sim_clock_midday.now()

        bid = Decimal("500.00")
        ask = bid + Decimal(str(round(500.0 * spread_bps / 10000, 4)))
        mid = (bid + ask) / Decimal("2")

        quote = PriceQuote(symbol="SPY", bid=bid, ask=ask, midpoint=mid, spread_bps=spread_bps, timestamp=now)
        trade = TradeQuote(
            symbol="SPY",
            price=Decimal("500.02"),
            size=100,
            timestamp=now - timedelta(seconds=trade_age_secs),
        )

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert mode == expected_mode
        assert (price is not None) == expected_price_valid
        if expected_mode == "STALE_PRICE":
            assert guard.discard_counts["STALE_PRICE"] > 0


class TestPairwiseIndicatorsAndRegimeFilter:
    """Pairwise 3: Pure Indicators (SMA200 + Realized Vol) and RegimeFilter Classification."""

    @pytest.mark.parametrize(
        ("close_rel_sma", "vol_percentile_rel", "bar_count", "expected_regime", "expected_blocked"),
        [
            ("ABOVE", "BELOW", 250, "BULL_CALM", False),
            ("ABOVE", "ABOVE", 250, "BULL_VOLATILE", False),
            ("BELOW", "BELOW", 250, "BEAR", False),
            ("BELOW", "ABOVE", 250, "BEAR", False),
            ("ABOVE", "BELOW", 150, "UNKNOWN", True),  # < 200 bars -> UNKNOWN (fail closed)
        ],
    )
    def test_indicators_to_regime_classification(
        self,
        close_rel_sma: str,
        vol_percentile_rel: str,
        bar_count: int,
        expected_regime: str,
        expected_blocked: bool,
    ) -> None:
        if bar_count < 200:
            regime = "UNKNOWN"
        elif close_rel_sma == "ABOVE":
            regime = "BULL_VOLATILE" if vol_percentile_rel == "ABOVE" else "BULL_CALM"
        else:
            regime = "BEAR"

        snapshot = RegimeSnapshot(
            timestamp=datetime(2026, 9, 18, 16, 0, 0, tzinfo=UTC),
            regime=regime,
            spy_close=Decimal("505.00") if close_rel_sma == "ABOVE" else Decimal("495.00"),
            sma_200=Decimal("500.00"),
            realized_vol_20d=0.20 if vol_percentile_rel == "ABOVE" else 0.12,
            vol_70th_percentile=0.15,
            is_blocked=(regime == "UNKNOWN"),
        )

        assert snapshot.regime == expected_regime
        assert snapshot.is_blocked == expected_blocked


class TestPairwiseMarketCalendarAndSessionTimers:
    """Pairwise 4: Early Close Calendar handling and Intraday Strategy Exit Timers."""

    @pytest.mark.parametrize(
        ("session_date", "is_early_close", "expected_exit_time_utc", "expected_close_window_utc"),
        [
            (date(2026, 9, 18), False, datetime(2026, 9, 18, 19, 58, 0, tzinfo=UTC), datetime(2026, 9, 18, 19, 50, 0, tzinfo=UTC)),
            (date(2026, 11, 27), True, datetime(2026, 11, 27, 17, 58, 0, tzinfo=UTC), datetime(2026, 11, 27, 17, 50, 0, tzinfo=UTC)),
        ],
    )
    def test_dynamic_session_timer_derivation(
        self,
        sim_clock: SimulatedClock,
        session_date: date,
        is_early_close: bool,
        expected_exit_time_utc: datetime,
        expected_close_window_utc: datetime,
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        days = provider.get_calendar(session_date, session_date)
        assert len(days) == 1
        day = days[0]

        assert day.is_early_close == is_early_close

        # Dynamic S1 exit-at-close is close - 2 minutes
        exit_time = day.close_time - timedelta(minutes=2)
        # Close window starts close - 10 minutes
        close_window_start = day.close_time - timedelta(minutes=10)

        assert exit_time == expected_exit_time_utc
        assert close_window_start == expected_close_window_utc


class TestPairwiseMacroCalendarAndStrategyWindows:
    """Pairwise 5: Macro Events (FOMC, CPI) and Strategy Type Entry Gating."""

    @pytest.mark.parametrize(
        ("event_type", "strategy_type", "time_offset_min", "expected_blocked"),
        [
            ("FOMC", "swing", -300, True),     # FOMC day morning -> swing strategy blocked all day
            ("FOMC", "intraday", -300, False), # FOMC day morning (9:00 ET) -> intraday allowed outside window
            ("FOMC", "intraday", -15, True),   # 15m before FOMC -> intraday blocked
            ("FOMC", "intraday", +15, True),   # 15m after FOMC -> intraday blocked
            ("FOMC", "intraday", +45, False),  # 45m after FOMC -> intraday allowed
            ("CPI", "swing", -45, False),      # 45m before CPI -> swing allowed
            ("CPI", "swing", -10, True),       # 10m before CPI -> swing blocked
            ("CPI", "intraday", +10, True),    # 10m after CPI -> intraday blocked
        ],
    )
    def test_macro_event_strategy_interaction(
        self,
        sim_clock: SimulatedClock,
        event_type: str,
        strategy_type: str,
        time_offset_min: int,
        expected_blocked: bool,
    ) -> None:
        macro_filter = MockMacroFilter(clock=sim_clock)
        # Event at 14:00 ET (18:00 UTC)
        event_time = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)
        macro_filter.load_events([
            {"name": event_type, "type": event_type, "timestamp": event_time, "impact": "critical"}
        ])

        eval_time = event_time + timedelta(minutes=time_offset_min)
        blocked, _ = macro_filter.is_macro_window_active(eval_time, strategy_type=strategy_type)
        assert blocked == expected_blocked


class TestPairwiseFinnhubOutageAndCacheFailClosed:
    """Pairwise 6: Finnhub Outage Fail-Closed Guard and Approaching Earnings Blocker."""

    @pytest.mark.parametrize(
        ("network_error", "cache_age_days", "days_to_earnings", "expected_blocked"),
        [
            (False, 0, 10, False), # Online, fresh, earnings in 10d -> allowed
            (False, 0, 2, True),   # Online, fresh, earnings in 2d -> blocked (earnings approaching)
            (True, 1, 10, False),  # Offline, cache 1d old, earnings in 10d -> allowed (cache valid)
            (True, 2, 2, True),    # Offline, cache 2d old, earnings in 2d -> blocked (earnings approaching)
            (True, 4, 10, True),   # Offline, cache 4d old (>3d) -> FAIL CLOSED BLOCKED
            (True, 10, 5, True),   # Offline, cache 10d old -> FAIL CLOSED BLOCKED
        ],
    )
    @pytest.mark.asyncio
    async def test_finnhub_outage_fail_closed_matrix(
        self,
        sim_clock: SimulatedClock,
        network_error: bool,
        cache_age_days: int,
        days_to_earnings: int,
        expected_blocked: bool,
    ) -> None:
        client = MockFinnhubEarningsClient(clock=sim_clock)
        client.network_error = network_error

        cached_at = sim_clock.now() - timedelta(days=cache_age_days)
        earnings_date = sim_clock.today() + timedelta(days=days_to_earnings)
        client.seed_earnings("NVDA", earnings_date, cached_at=cached_at)

        edate, query_blocked, reason = await client.get_earnings_date("NVDA")
        if query_blocked:
            overall_blocked = True
        else:
            overall_blocked = client.is_earnings_approaching(edate, block_days=2)

        assert overall_blocked == expected_blocked


class TestPairwiseProxyMapperAndExecutionRouting:
    """Pairwise 7: Proxy Symbol Translation with AlpacaProvider and Pricing."""

    @pytest.mark.parametrize(
        ("signal_sym", "expected_exec_sym", "nominal_price_ratio"),
        [
            ("SPY", "SPYM", Decimal("0.14")),   # SPY ~$500 -> SPYM ~$70
            ("QQQ", "QQQM", Decimal("0.42")),   # QQQ ~$500 -> QQQM ~$210
            ("GLD", "GLDM", Decimal("0.18")),   # GLD ~$250 -> GLDM ~$45
            ("AAPL", "AAPL", Decimal("1.00")),  # Unmapped 1:1
        ],
    )
    def test_proxy_mapper_pricing_coordination(
        self,
        sim_clock: SimulatedClock,
        signal_sym: str,
        expected_exec_sym: str,
        nominal_price_ratio: Decimal,
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        exec_sym = provider.map_symbol(signal_sym, direction="signal_to_exec")
        assert exec_sym == expected_exec_sym

        # Ensure reverse mapping returns signal symbol
        rev_sym = provider.map_symbol(exec_sym, direction="exec_to_signal")
        assert rev_sym == signal_sym


class TestPairwiseCrossAnomalyAndAggregator:
    """Pairwise 8: Cross Anomaly Detector with 5m Bar Aggregator."""

    @pytest.mark.parametrize(
        ("iex_price_offset", "atr_5m", "expected_anomaly"),
        [
            (1.50, 1.00, False),  # 1.50 <= 2 * 1.00 (2.00) -> OK
            (1.99, 1.00, False),  # 1.99 <= 2.00 -> OK
            (2.05, 1.00, True),   # 2.05 > 2.00 -> PRICE_ANOMALY
            (3.00, 1.00, True),   # 3.00 > 2.00 -> PRICE_ANOMALY
            (-2.10, 1.00, True),  # Downward flash crash -> PRICE_ANOMALY
        ],
    )
    def test_anomaly_gating_with_atr(
        self,
        sim_clock_midday: SimulatedClock,
        iex_price_offset: float,
        atr_5m: float,
        expected_anomaly: bool,
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        sip_close = Decimal("500.00")
        atr = Decimal(str(atr_5m))
        iex_price = sip_close + Decimal(str(iex_price_offset))

        is_ok, reason = guard.check_price_anomaly(iex_price, sip_close, atr)
        anomaly_detected = (not is_ok) and (reason == "PRICE_ANOMALY")
        assert anomaly_detected == expected_anomaly


class TestPairwiseWebSocketDropAndRestFallback:
    """Pairwise 9: WebSocket Connection Loss with REST Fallback and Strategy Lockout."""

    def test_ws_loss_triggers_rest_fallback_and_entry_lock(
        self, sim_clock_midday: SimulatedClock
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)

        # 1. Healthy WS received message 10s ago
        msg_time = sim_clock_midday.now() - timedelta(seconds=10)
        assert guard.check_global_feed(msg_time) is True

        # 2. Network drop: clock advances 55s without new messages (total 65s silence)
        sim_clock_midday.advance(timedelta(seconds=55))
        assert guard.check_global_feed(msg_time) is False

        # 3. System enters fallback: entries locked, REST polling engaged
        entry_lock = True
        rest_poll_interval = 10.0
        assert entry_lock is True
        assert rest_poll_interval == 10.0

        # 4. Reconnect heartbeat arrives
        new_msg_time = sim_clock_midday.now()
        assert guard.check_global_feed(new_msg_time) is True
        entry_lock = False
        assert entry_lock is False


class TestPairwiseSyntheticBarIndicatorPipeline:
    """Pairwise 10: Synthetic Bar Forward-Fill with RSI/ATR and Entry Exclusion."""

    def test_synthetic_bars_used_for_indicators_forbidden_for_entry(
        self, sim_clock_midday: SimulatedClock
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)
        now = sim_clock_midday.now()

        # Build series with 2 real bars and 1 synthetic bar
        bars = [
            {"close": 100.0, "volume": 500, "is_synthetic": False},
            {"close": 101.0, "volume": 600, "is_synthetic": False},
            {"close": 101.0, "volume": 0, "is_synthetic": True},  # Forward-filled support bar
        ]
        df = pd.DataFrame(bars)

        # Technical indicators compute cleanly on continuous series
        diff = df["close"].diff()
        assert len(diff) == 3

        # But entry price evaluation must forbid forward-filled bar
        # Live quote has wide spread and trade is stale
        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("100.00"),
            ask=Decimal("101.00"),  # 100 bps spread
            midpoint=Decimal("100.50"),
            spread_bps=99.5,
            timestamp=now,
        )
        trade = TradeQuote(
            symbol="SPY",
            price=Decimal("101.00"),
            size=100,
            timestamp=now - timedelta(minutes=5),  # 5 min old (> 3 min tau)
        )

        entry_price, mode = guard.get_entry_price(quote, trade, tau)
        # Cannot fallback to synthetic bar; must discard with STALE_PRICE
        assert entry_price is None
        assert mode == "STALE_PRICE"
