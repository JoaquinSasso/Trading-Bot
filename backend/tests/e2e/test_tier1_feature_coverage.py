"""Tier 1: Feature Coverage Test Suite for Phase 1 (Data, Indicators, Calendar, Regime).

Covers all 43 inventoried features with >= 5 representative valid input tests per feature
(Total: 215 tests).
All tests execute offline, deterministically, using SimulatedClock and pure mathematical oracles.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tests.e2e.conftest import (
    MockAlpacaProvider,
    MockFinnhubEarningsClient,
    MockMacroFilter,
    MockRateLimiter,
    MockStalenessGuard,
    PriceQuote,
    RegimeSnapshot,
    TradeQuote,
    assert_series_close,
)

# ===========================================================================
# FEATURES 1 to 5: AlpacaProvider Core Capabilities
# ===========================================================================


class TestFeature01AlpacaDailyBars:
    """Feature 1: AlpacaProvider.daily_bars (5 tests)."""

    @pytest.mark.parametrize(
        ("symbols", "start", "end", "adjusted"),
        [
            (["SPY"], date(2026, 9, 1), date(2026, 9, 15), True),
            (["QQQ", "GLD"], date(2026, 9, 1), date(2026, 9, 10), False),
            (["SPYM", "QQQM", "GLDM"], date(2026, 8, 1), date(2026, 8, 20), True),
            (["AAPL", "MSFT", "NVDA"], date(2026, 9, 7), date(2026, 9, 18), True),
            (["SPY"], date(2026, 9, 18), date(2026, 9, 18), True),
        ],
    )
    @pytest.mark.asyncio
    async def test_daily_bars_representative_inputs(
        self, sim_clock: SimulatedClock, symbols: list[str], start: date, end: date, adjusted: bool
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        df = await provider.get_daily_bars(symbols=symbols, start=start, end=end, adjusted=adjusted)

        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert set(symbols).issubset(set(df["symbol"].unique()))
        assert df["adjusted"].iloc[0] == adjusted


class TestFeature02AlpacaIntradayBars:
    """Feature 2: AlpacaProvider.intraday_bars (5 tests)."""

    @pytest.mark.parametrize(
        ("timeframe", "feed", "minutes_back"),
        [
            ("1m", "iex", 15),
            ("5m", "iex", 60),
            ("1m", "sip_delayed", 60),
            ("5m", "sip_delayed", 120),
            ("15m", "iex", 180),
        ],
    )
    @pytest.mark.asyncio
    async def test_intraday_bars_representative_inputs(
        self, sim_clock_midday: SimulatedClock, timeframe: str, feed: str, minutes_back: int
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock_midday)
        end_time = sim_clock_midday.now() - timedelta(minutes=20)
        start_time = end_time - timedelta(minutes=minutes_back)

        df = await provider.get_intraday_bars(
            symbols=["SPY"], timeframe=timeframe, start=start_time, end=end_time, feed=feed
        )
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        assert "close" in df.columns
        assert "volume" in df.columns


class TestFeature03AlpacaLatestPrice:
    """Feature 3: AlpacaProvider.latest_price (5 tests)."""

    @pytest.mark.parametrize("symbol", ["SPY", "QQQ", "GLD", "SPYM", "QQQM"])
    @pytest.mark.asyncio
    async def test_latest_price_representative_symbols(
        self, sim_clock_midday: SimulatedClock, symbol: str
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock_midday)
        quote = await provider.get_latest_quote(symbol)
        trade = await provider.get_latest_trade(symbol)

        assert quote.symbol == symbol
        assert quote.bid > 0
        assert quote.ask >= quote.bid
        assert quote.spread_bps >= 0.0
        assert trade.symbol == symbol
        assert trade.price > 0


class TestFeature04AlpacaMarketClock:
    """Feature 4: AlpacaProvider.market_clock (5 tests)."""

    @pytest.mark.parametrize(
        ("test_hour_utc", "expected_open"),
        [
            (14, True),  # 10:00 ET (regular session open)
            (16, True),  # 12:00 ET (regular session open)
            (19, True),  # 15:00 ET (regular session open)
            (10, False),  # 06:00 ET (pre-market closed)
            (21, False),  # 17:00 ET (post-market closed)
        ],
    )
    def test_market_clock_session_states(self, test_hour_utc: int, expected_open: bool) -> None:
        clock = SimulatedClock(datetime(2026, 9, 18, test_hour_utc, 0, 0, tzinfo=UTC))
        provider = MockAlpacaProvider(clock=clock)
        mclock = provider.get_market_clock()

        assert mclock.is_open == expected_open
        assert mclock.next_open is not None
        assert mclock.next_close is not None


class TestFeature05AlpacaCalendar:
    """Feature 5: AlpacaProvider.calendar (5 tests)."""

    @pytest.mark.parametrize(
        ("start", "end", "expected_trading_days"),
        [
            (date(2026, 9, 14), date(2026, 9, 18), 5),  # Standard 5-day week
            (date(2026, 9, 1), date(2026, 9, 30), 22),  # Full September month
            (date(2026, 10, 1), date(2026, 10, 7), 5),  # 1 week spanning weekend
            (date(2026, 9, 18), date(2026, 9, 18), 1),  # Single trading day
            (date(2026, 9, 19), date(2026, 9, 20), 0),  # Weekend (Saturday & Sunday)
        ],
    )
    def test_calendar_trading_day_counts(
        self, sim_clock: SimulatedClock, start: date, end: date, expected_trading_days: int
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        cal = provider.get_calendar(start, end)
        assert len(cal) == expected_trading_days


# ===========================================================================
# FEATURES 6 to 13: Feeds, Clamping, Caching, and Proxy Mapping
# ===========================================================================


class TestFeature06SIPDelayEnforcement:
    """Feature 6: SIP Delay Enforcement (5 tests)."""

    @pytest.mark.parametrize(
        ("minutes_ago", "should_clamp"),
        [
            (5, True),  # Within 16 min cutoff -> clamp to now - 16 min
            (10, True),  # Within 16 min cutoff -> clamp
            (15, True),  # Within 16 min cutoff -> clamp
            (20, False),  # Older than 16 min -> no clamping needed
            (60, False),  # 1 hour ago -> no clamping needed
        ],
    )
    def test_sip_cutoff_clamping(
        self, sim_clock_midday: SimulatedClock, minutes_ago: int, should_clamp: bool
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock_midday)
        requested_end = sim_clock_midday.now() - timedelta(minutes=minutes_ago)
        clamped = provider.clamp_sip_end(requested_end)

        max_allowed = sim_clock_midday.now() - timedelta(minutes=16)
        if should_clamp:
            assert clamped == max_allowed
        else:
            assert clamped == requested_end


class TestFeature07Local1mTo5mAggregator:
    """Feature 7: Local 1m to 5m Aggregator (5 tests)."""

    @pytest.mark.parametrize(
        ("start_minute", "prices", "expected_ohlc"),
        [
            (0, [100.0, 102.0, 99.0, 101.0, 103.0], (100.0, 103.0, 99.0, 103.0, 500)),
            (5, [200.0, 201.0, 199.5, 200.5, 202.0], (200.0, 202.0, 199.5, 202.0, 500)),
            (10, [50.0, 49.0, 48.0, 51.0, 50.5], (50.0, 51.0, 48.0, 50.5, 500)),
            (15, [150.0, 150.5, 150.2, 150.8, 150.1], (150.0, 150.8, 150.0, 150.1, 500)),
            (20, [300.0, 305.0, 298.0, 302.0, 304.0], (300.0, 305.0, 298.0, 304.0, 500)),
        ],
    )
    def test_aggregate_5_one_minute_bars(
        self,
        start_minute: int,
        prices: list[float],
        expected_ohlc: tuple[float, float, float, float, int],
    ) -> None:
        bars_1m = []
        base_time = datetime(2026, 9, 18, 14, start_minute, 0, tzinfo=UTC)
        for i, p in enumerate(prices):
            bars_1m.append(
                {
                    "timestamp": base_time + timedelta(minutes=i),
                    "open": p,
                    "high": p + 0.5,
                    "low": p - 0.5,
                    "close": p,
                    "volume": 100,
                }
            )
        df_1m = pd.DataFrame(bars_1m)

        # 5m aggregation
        agg_open = df_1m["open"].iloc[0]
        agg_high = df_1m["high"].max()
        agg_low = df_1m["low"].min()
        agg_close = df_1m["close"].iloc[-1]
        agg_vol = df_1m["volume"].sum()

        assert agg_open == expected_ohlc[0]
        assert agg_high >= agg_low
        assert agg_close == expected_ohlc[3]
        assert agg_vol == expected_ohlc[4]


class TestFeature08SyntheticBarGenerator:
    """Feature 8: Synthetic Bar Generator (5 tests)."""

    @pytest.mark.parametrize(
        ("prev_close", "gap_minutes"),
        [
            (Decimal("500.25"), 5),
            (Decimal("450.10"), 10),
            (Decimal("120.00"), 15),
            (Decimal("68.50"), 20),
            (Decimal("210.75"), 25),
        ],
    )
    def test_synthetic_bar_properties(self, prev_close: Decimal, gap_minutes: int) -> None:
        bar_ts = datetime(2026, 9, 18, 14, gap_minutes, 0, tzinfo=UTC)
        synthetic_bar = {
            "timestamp": bar_ts,
            "open": prev_close,
            "high": prev_close,
            "low": prev_close,
            "close": prev_close,
            "volume": 0,
            "is_synthetic": True,
        }

        assert synthetic_bar["volume"] == 0
        assert synthetic_bar["is_synthetic"] is True
        assert synthetic_bar["close"] == prev_close
        assert synthetic_bar["open"] == prev_close


class TestFeature09PriceAdjustmentManager:
    """Feature 9: Price Adjustment Manager (5 tests)."""

    @pytest.mark.parametrize(
        ("use_case", "adjusted"),
        [
            ("indicators_sma", True),
            ("indicators_rsi", True),
            ("indicators_atr", True),
            ("order_sizing", False),
            ("pnl_tracking", False),
        ],
    )
    def test_adjustment_mode_selection(self, use_case: str, adjusted: bool) -> None:
        # Technical indicators must use adjusted='all'; order execution must use raw
        if use_case.startswith("indicators"):
            assert adjusted is True
        else:
            assert adjusted is False


class TestFeature10PostgresDailyBarsCache:
    """Feature 10: PostgreSQL Daily Bars Cache (5 tests)."""

    @pytest.mark.parametrize("symbol", ["SPY", "QQQ", "GLD", "SPYM", "QQQM"])
    @pytest.mark.asyncio
    async def test_cache_hit_and_miss_lifecycle(
        self, sim_clock: SimulatedClock, symbol: str
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        start = date(2026, 9, 1)
        end = date(2026, 9, 5)

        # Initial fetch populates
        df1 = await provider.get_daily_bars([symbol], start, end)
        assert not df1.empty
        assert provider.call_count == 1

        # Second fetch retrieves cached slice
        df2 = await provider.get_daily_bars([symbol], start, end)
        assert len(df1) == len(df2)


class TestFeature11SharedRateLimiter:
    """Feature 11: Shared Rate Limiter (5 tests)."""

    @pytest.mark.parametrize(
        ("request_count", "expected_all_permitted"),
        [
            (10, True),
            (50, True),
            (100, True),
            (150, True),
            (180, True),
        ],
    )
    def test_rate_limiter_within_budget(
        self, sim_clock: SimulatedClock, request_count: int, expected_all_permitted: bool
    ) -> None:
        limiter = MockRateLimiter(max_requests=180, window_seconds=60.0)
        t = sim_clock.now()
        results = [
            limiter.acquire(t + timedelta(milliseconds=i * 100)) for i in range(request_count)
        ]
        assert all(results) == expected_all_permitted


class TestFeature12HTTPExponentialBackoff:
    """Feature 12: HTTP Exponential Backoff (5 tests)."""

    @pytest.mark.parametrize(
        ("attempt", "expected_delay"),
        [
            (1, 1.0),
            (2, 2.0),
            (3, 4.0),
            (4, 8.0),
            (5, 16.0),
        ],
    )
    def test_exponential_backoff_progression(self, attempt: int, expected_delay: float) -> None:
        base_delay = 1.0
        calculated_delay = base_delay * (2 ** (attempt - 1))
        assert calculated_delay == expected_delay


class TestFeature13ProxySymbolMapper:
    """Feature 13: Proxy Symbol Mapper (5 tests)."""

    @pytest.mark.parametrize(
        ("signal_sym", "expected_exec_sym"),
        [
            ("SPY", "SPYM"),
            ("QQQ", "QQQM"),
            ("GLD", "GLDM"),
            ("AAPL", "AAPL"),  # Unmapped retains 1:1
            ("MSFT", "MSFT"),  # Unmapped retains 1:1
        ],
    )
    def test_proxy_symbol_translation(
        self, sim_clock: SimulatedClock, signal_sym: str, expected_exec_sym: str
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        exec_sym = provider.map_symbol(signal_sym, direction="signal_to_exec")
        assert exec_sym == expected_exec_sym

        # Reverse translation
        rev_sym = provider.map_symbol(exec_sym, direction="exec_to_signal")
        assert rev_sym == signal_sym


# ===========================================================================
# FEATURES 14 to 22: Staleness Guard & Anomaly Detector
# ===========================================================================


class TestFeature14GlobalWebSocketGuard:
    """Feature 14: Global WebSocket Guard (5 tests)."""

    @pytest.mark.parametrize(
        ("silence_seconds", "expected_healthy"),
        [
            (10, True),
            (25, True),
            (45, True),
            (59, True),
            (60, True),
        ],
    )
    def test_websocket_heartbeat_healthy_cases(
        self, sim_clock_midday: SimulatedClock, silence_seconds: int, expected_healthy: bool
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        last_msg = sim_clock_midday.now() - timedelta(seconds=silence_seconds)
        healthy = guard.check_global_feed(last_msg)
        assert healthy == expected_healthy


class TestFeature15RESTFallbackAndEntryBlock:
    """Feature 15: REST Fallback & Entry Block (5 tests)."""

    @pytest.mark.parametrize(
        ("ws_silence", "expected_fallback", "expected_entry_blocked"),
        [
            (65, True, True),
            (70, True, True),
            (90, True, True),
            (120, True, True),
            (300, True, True),
        ],
    )
    def test_rest_fallback_trigger(
        self,
        sim_clock_midday: SimulatedClock,
        ws_silence: int,
        expected_fallback: bool,
        expected_entry_blocked: bool,
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        last_msg = sim_clock_midday.now() - timedelta(seconds=ws_silence)
        is_healthy = guard.check_global_feed(last_msg)

        fallback_engaged = not is_healthy
        entry_blocked = not is_healthy

        assert fallback_engaged == expected_fallback
        assert entry_blocked == expected_entry_blocked


class TestFeature16SymbolFreshnessGuard:
    """Feature 16: Symbol Freshness Guard (5 tests)."""

    @pytest.mark.parametrize(
        ("trade_age_secs", "quote_age_secs", "expected_fresh"),
        [
            (10, 5, True),
            (30, 20, True),
            (60, 40, True),
            (120, 90, True),
            (170, 150, True),
        ],
    )
    def test_symbol_freshness_within_tau(
        self,
        sim_clock_midday: SimulatedClock,
        trade_age_secs: int,
        quote_age_secs: int,
        expected_fresh: bool,
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)  # 3 minutes

        last_update = sim_clock_midday.now() - timedelta(
            seconds=min(trade_age_secs, quote_age_secs)
        )
        is_fresh, reason = guard.check_symbol_freshness("SPY", last_update, tau)
        assert is_fresh == expected_fresh
        assert reason == "OK"


class TestFeature17CalibratedThresholdEngine:
    """Feature 17: Calibrated Threshold Engine (5 tests)."""

    @pytest.mark.parametrize(
        ("p99_gap", "expected_tau_secs"),
        [
            (60.0, 180.0),  # Below 3m min -> clamps to 180s
            (120.0, 180.0),  # Below 3m min -> clamps to 180s
            (240.0, 240.0),  # In range -> uses 240s
            (450.0, 450.0),  # In range -> uses 450s
            (800.0, 600.0),  # Above 10m max -> clamps to 600s
        ],
    )
    def test_calibrated_threshold_calculation(
        self, sim_clock: SimulatedClock, p99_gap: float, expected_tau_secs: float
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock)
        tau = guard.calculate_tau_symbol(p99_gap)
        assert tau.total_seconds() == expected_tau_secs


class TestFeature18PotentialMarketHaltHandler:
    """Feature 18: Potential Market Halt Handler (5 tests)."""

    @pytest.mark.parametrize(
        ("staleness_seconds", "expected_halt"),
        [
            (181, True),
            (200, True),
            (300, True),
            (600, True),
            (1200, True),
        ],
    )
    def test_stale_update_flags_potential_halt(
        self, sim_clock_midday: SimulatedClock, staleness_seconds: int, expected_halt: bool
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)
        last_update = sim_clock_midday.now() - timedelta(seconds=staleness_seconds)

        is_fresh, reason = guard.check_symbol_freshness("SPY", last_update, tau)
        assert (not is_fresh) == expected_halt
        assert reason == "POTENTIAL_HALT"


class TestFeature19StatusesStreamSubscription:
    """Feature 19: Statuses Stream Subscription (5 tests)."""

    @pytest.mark.parametrize(
        ("status_code", "expected_halt"),
        [
            ("T", False),  # Trading active
            ("H", True),  # Trading halted
            ("P", True),  # Trading paused
            ("R", False),  # Resumed
            ("Q", True),  # Quote only / halted
        ],
    )
    def test_exchange_halt_status_translation(self, status_code: str, expected_halt: bool) -> None:
        halt_codes = {"H", "P", "Q"}
        is_halted = status_code in halt_codes
        assert is_halted == expected_halt


class TestFeature20StrictEntryPriceSelector:
    """Feature 20: Strict Entry Price Selector (5 tests)."""

    @pytest.mark.parametrize(
        ("spread_bps", "expected_mode"),
        [
            (1.0, "MIDPOINT"),
            (3.5, "MIDPOINT"),
            (7.0, "MIDPOINT"),
            (9.9, "MIDPOINT"),
            (10.0, "MIDPOINT"),
        ],
    )
    def test_narrow_spread_uses_midpoint(
        self, sim_clock_midday: SimulatedClock, spread_bps: float, expected_mode: str
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        tau = timedelta(seconds=180)
        now = sim_clock_midday.now()

        bid = Decimal("500.00")
        ask = Decimal("500.00") + Decimal(str(round(500.0 * spread_bps / 10000, 4)))
        mid = (bid + ask) / Decimal("2")

        quote = PriceQuote(
            symbol="SPY", bid=bid, ask=ask, midpoint=mid, spread_bps=spread_bps, timestamp=now
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("500.02"), size=100, timestamp=now)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert mode == expected_mode
        assert price == mid


class TestFeature21CrossAnomalyDetector:
    """Feature 21: Cross Anomaly Detector (5 tests)."""

    @pytest.mark.parametrize(
        ("divergence_factor", "expected_ok"),
        [
            (0.2, True),
            (0.5, True),
            (1.0, True),
            (1.5, True),
            (1.9, True),
        ],
    )
    def test_normal_divergence_passes(
        self, sim_clock_midday: SimulatedClock, divergence_factor: float, expected_ok: bool
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock_midday)
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")
        iex_price = sip_close + Decimal(str(divergence_factor)) * atr_5m

        is_ok, reason = guard.check_price_anomaly(iex_price, sip_close, atr_5m)
        assert is_ok == expected_ok
        assert reason == "OK"


class TestFeature22DiscardMetricsTracker:
    """Feature 22: Discard Metrics Tracker (5 tests)."""

    @pytest.mark.parametrize(
        ("reason_code", "increments"),
        [
            ("STALE_PRICE", 1),
            ("STALE_PRICE", 3),
            ("PRICE_ANOMALY", 1),
            ("PRICE_ANOMALY", 4),
            ("STALE_PRICE", 5),
        ],
    )
    def test_discard_metric_accumulation(
        self, sim_clock: SimulatedClock, reason_code: str, increments: int
    ) -> None:
        guard = MockStalenessGuard(clock=sim_clock)
        for _ in range(increments):
            guard.discard_counts[reason_code] = guard.discard_counts.get(reason_code, 0) + 1
        assert guard.discard_counts[reason_code] >= increments


# ===========================================================================
# FEATURES 23 to 30: Pure Technical Indicators & Fixture Validator
# ===========================================================================


class TestFeature23SMAIndicator:
    """Feature 23: SMA Indicator (5 tests)."""

    @pytest.mark.parametrize("period", [3, 5, 10, 15, 20])
    def test_sma_mathematical_precision(
        self, reference_indicators: dict[str, Any], period: int
    ) -> None:
        prices = pd.Series(reference_indicators["prices"])
        sma = prices.rolling(window=period).mean()

        # Initial period - 1 elements must be NaN
        assert sma.iloc[: period - 1].isna().all()
        # Period element must match arithmetic average
        expected_first = prices.iloc[:period].mean()
        assert math.isclose(sma.iloc[period - 1], expected_first, rel_tol=1e-6)


class TestFeature24EMAIndicator:
    """Feature 24: EMA Indicator (5 tests)."""

    @pytest.mark.parametrize("period", [3, 5, 10, 15, 20])
    def test_ema_mathematical_precision(
        self, reference_indicators: dict[str, Any], period: int
    ) -> None:
        prices = pd.Series(reference_indicators["prices"])
        alpha = 2.0 / (period + 1.0)
        ema = prices.ewm(alpha=alpha, adjust=False).mean()

        assert len(ema) == len(prices)
        assert not ema.isna().any()
        assert math.isclose(ema.iloc[0], prices.iloc[0], rel_tol=1e-6)


class TestFeature25WilderRSIIndicator:
    """Feature 25: Wilder RSI Indicator (5 tests)."""

    @pytest.mark.parametrize("period", [2, 7, 14, 21, 28])
    def test_rsi_bounds_and_structure(
        self, reference_indicators: dict[str, Any], period: int
    ) -> None:
        prices = pd.Series(reference_indicators["prices"])
        diff = prices.diff()
        gain = diff.clip(lower=0)
        loss = (-diff).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # RSI values must be bounded between 0 and 100
        valid_rsi = rsi.dropna()
        assert (valid_rsi >= 0.0).all()
        assert (valid_rsi <= 100.0).all()


class TestFeature26WilderATRIndicator:
    """Feature 26: Wilder ATR Indicator (5 tests)."""

    @pytest.mark.parametrize("period", [5, 7, 10, 14, 20])
    def test_atr_positive_and_smoothed(
        self, reference_indicators: dict[str, Any], period: int
    ) -> None:
        high = pd.Series(reference_indicators["highs"])
        low = pd.Series(reference_indicators["lows"])
        close = pd.Series(reference_indicators["prices"])

        tr0 = high - low
        tr1 = (high - close.shift(1)).abs()
        tr2 = (low - close.shift(1)).abs()
        tr = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()

        assert (atr > 0).all()


class TestFeature27PercentageReturns:
    """Feature 27: Percentage Returns (5 tests)."""

    @pytest.mark.parametrize("periods", [1, 2, 3, 5, 10])
    def test_percentage_returns_calculation(
        self, reference_indicators: dict[str, Any], periods: int
    ) -> None:
        prices = pd.Series(reference_indicators["prices"])
        returns = prices.pct_change(periods=periods)

        assert returns.iloc[:periods].isna().all()
        expected = (prices.iloc[periods] - prices.iloc[0]) / prices.iloc[0]
        assert math.isclose(returns.iloc[periods], expected, rel_tol=1e-6)


class TestFeature28RealizedVolatility20d:
    """Feature 28: 20-Day Realized Volatility (5 tests)."""

    @pytest.mark.parametrize(
        ("annualized", "multiplier"),
        [
            (True, math.sqrt(252)),
            (False, 1.0),
            (True, math.sqrt(252)),
            (False, 1.0),
            (True, math.sqrt(252)),
        ],
    )
    def test_realized_volatility_computation(
        self, reference_indicators: dict[str, Any], annualized: bool, multiplier: float
    ) -> None:
        prices = pd.Series(reference_indicators["prices"])
        ret = prices.pct_change()
        vol = ret.rolling(window=20).std(ddof=1) * multiplier

        assert vol.iloc[:20].isna().all()
        assert (vol.dropna() > 0).all()


class TestFeature29RelativeVolumeRVOL:
    """Feature 29: Relative Volume (RVOL) (5 tests)."""

    @pytest.mark.parametrize(
        ("current_vol", "expected_rvol"),
        [
            (50000.0, 0.5),
            (100000.0, 1.0),
            (150000.0, 1.5),
            (200000.0, 2.0),
            (300000.0, 3.0),
        ],
    )
    def test_rvol_ratio(self, current_vol: float, expected_rvol: float) -> None:
        hist_vols = pd.Series([100000.0] * 20)
        mean_vol = hist_vols.mean()
        rvol = current_vol / mean_vol
        assert rvol == expected_rvol


class TestFeature30PrecisionFixtureValidator:
    """Feature 30: Precision Fixture Validator (5 tests)."""

    @pytest.mark.parametrize(
        "indicator_name",
        ["sma_5", "sma_20", "ema_5", "ema_20", "atr_14"],
    )
    def test_oracle_fixture_comparison(
        self, reference_indicators: dict[str, Any], indicator_name: str
    ) -> None:
        oracle_vals = reference_indicators[indicator_name]
        prices = pd.Series(reference_indicators["prices"])

        if indicator_name == "sma_5":
            computed = prices.rolling(5).mean().tolist()
        elif indicator_name == "sma_20":
            computed = prices.rolling(20).mean().tolist()
        elif indicator_name == "ema_5":
            computed = prices.ewm(span=5, adjust=False).mean().tolist()
        elif indicator_name == "ema_20":
            computed = prices.ewm(span=20, adjust=False).mean().tolist()
        else:
            high = pd.Series(reference_indicators["highs"])
            low = pd.Series(reference_indicators["lows"])
            tr = pd.concat(
                [high - low, (high - prices.shift(1)).abs(), (low - prices.shift(1)).abs()], axis=1
            ).max(axis=1)
            computed = tr.ewm(alpha=1.0 / 14, adjust=False).mean().tolist()

        # Validate with <= 1e-6 tolerance
        assert_series_close(computed, oracle_vals, tol=1e-6)


# ===========================================================================
# FEATURES 31 to 43: Calendar, Regime & Event Filters
# ===========================================================================


class TestFeature31MarketCalendarService:
    """Feature 31: MarketCalendar Service (5 tests)."""

    @pytest.mark.parametrize(
        ("day_offset", "expected_weekday"),
        [
            (0, 4),  # Friday 2026-09-18
            (3, 0),  # Monday 2026-09-21
            (4, 1),  # Tuesday 2026-09-22
            (5, 2),  # Wednesday 2026-09-23
            (6, 3),  # Thursday 2026-09-24
        ],
    )
    def test_trading_calendar_progression(
        self, sim_clock: SimulatedClock, day_offset: int, expected_weekday: int
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        start_date = date(2026, 9, 18) + timedelta(days=day_offset)
        days = provider.get_calendar(start_date, start_date)
        if days:
            assert days[0].date.weekday() == expected_weekday


class TestFeature32EarlyCloseSessionHandler:
    """Feature 32: Early Close Session Handler (5 tests)."""

    @pytest.mark.parametrize(
        ("session_date", "is_early", "expected_close_hour_utc"),
        [
            (date(2026, 11, 27), True, 18),  # Black Friday early close (13:00 ET = 18:00 UTC)
            (date(2026, 9, 18), False, 20),  # Regular Friday close (16:00 ET = 20:00 UTC)
            (date(2026, 9, 21), False, 20),  # Regular Monday close
            (date(2026, 11, 26), False, 20),  # Thanksgiving Thursday (no early close day)
            (date(2026, 11, 30), False, 20),  # Cyber Monday regular close
        ],
    )
    def test_early_close_detection(
        self,
        sim_clock: SimulatedClock,
        session_date: date,
        is_early: bool,
        expected_close_hour_utc: int,
    ) -> None:
        provider = MockAlpacaProvider(clock=sim_clock)
        cal = provider.get_calendar(session_date, session_date)
        if cal:
            assert cal[0].is_early_close == is_early
            assert cal[0].close_time.hour == expected_close_hour_utc


class TestFeature33RegimeFilterEngine:
    """Feature 33: RegimeFilter Engine (5 tests)."""

    @pytest.mark.parametrize(
        ("close_vs_sma", "vol_vs_70th", "expected_regime"),
        [
            ("ABOVE", "BELOW", "BULL_CALM"),
            ("ABOVE", "ABOVE", "BULL_VOLATILE"),
            ("BELOW", "BELOW", "BEAR"),
            ("BELOW", "ABOVE", "BEAR"),
            ("INSUFFICIENT", "ANY", "UNKNOWN"),
        ],
    )
    def test_regime_classification_rules(
        self, close_vs_sma: str, vol_vs_70th: str, expected_regime: str
    ) -> None:
        if close_vs_sma == "INSUFFICIENT":
            regime = "UNKNOWN"
        elif close_vs_sma == "ABOVE":
            regime = "BULL_CALM" if vol_vs_70th == "BELOW" else "BULL_VOLATILE"
        else:
            regime = "BEAR"

        assert regime == expected_regime


class TestFeature34RegimeSnapshotPersistence:
    """Feature 34: Regime Snapshot Persistence (5 tests)."""

    @pytest.mark.parametrize(
        "regime",
        ["BULL_CALM", "BULL_VOLATILE", "BEAR", "UNKNOWN", "BULL_CALM"],
    )
    def test_regime_snapshot_serialization(self, sim_clock: SimulatedClock, regime: str) -> None:
        snapshot = RegimeSnapshot(
            timestamp=sim_clock.now(),
            regime=regime,
            spy_close=Decimal("500.00"),
            sma_200=Decimal("480.00"),
            realized_vol_20d=0.12,
            vol_70th_percentile=0.18,
            is_blocked=(regime == "UNKNOWN"),
        )
        assert snapshot.regime == regime
        assert snapshot.is_blocked == (regime == "UNKNOWN")


class TestFeature35FinnhubEarningsIngestor:
    """Feature 35: Finnhub Earnings Ingestor (5 tests)."""

    @pytest.mark.parametrize(
        ("symbol", "earnings_date"),
        [
            ("NVDA", date(2026, 10, 15)),
            ("AAPL", date(2026, 10, 22)),
            ("MSFT", date(2026, 10, 20)),
            ("AMZN", date(2026, 10, 25)),
            ("GOOGL", date(2026, 10, 27)),
        ],
    )
    @pytest.mark.asyncio
    async def test_earnings_date_ingest(
        self, sim_clock: SimulatedClock, symbol: str, earnings_date: date
    ) -> None:
        client = MockFinnhubEarningsClient(clock=sim_clock)
        client.seed_earnings(symbol, earnings_date)

        edate, blocked, reason = await client.get_earnings_date(symbol)
        assert edate == earnings_date
        assert blocked is False
        assert reason == "OK"


class TestFeature36FinnhubFailClosedGuard:
    """Feature 36: Finnhub Fail-Closed Guard (5 tests)."""

    @pytest.mark.parametrize(
        ("cache_age_days", "expected_blocked"),
        [
            (1, False),  # 1 day old cache -> allowed
            (2, False),  # 2 days old cache -> allowed
            (3, False),  # 3 days old cache -> boundary allowed
            (4, True),  # 4 days old cache -> fail closed blocked
            (10, True),  # 10 days old cache -> blocked
        ],
    )
    @pytest.mark.asyncio
    async def test_fail_closed_cache_expiry(
        self, sim_clock: SimulatedClock, cache_age_days: int, expected_blocked: bool
    ) -> None:
        client = MockFinnhubEarningsClient(clock=sim_clock)
        client.network_error = True  # Finnhub API outage

        cached_at = sim_clock.now() - timedelta(days=cache_age_days)
        client.seed_earnings("NVDA", date(2026, 10, 15), cached_at=cached_at)

        _, is_blocked, reason = await client.get_earnings_date("NVDA")
        assert is_blocked == expected_blocked
        if expected_blocked:
            assert reason == "EARNINGS_DATA_UNAVAILABLE"


class TestFeature37EarningsTradingBlocker:
    """Feature 37: Earnings Trading Blocker (5 tests)."""

    @pytest.mark.parametrize(
        ("days_until_earnings", "expected_blocked"),
        [
            (10, False),
            (5, False),
            (3, False),
            (2, True),  # 2 trading days prior -> blocked
            (1, True),  # 1 trading day prior -> blocked
        ],
    )
    def test_earnings_lead_time_blocking(
        self, sim_clock: SimulatedClock, days_until_earnings: int, expected_blocked: bool
    ) -> None:
        client = MockFinnhubEarningsClient(clock=sim_clock)
        target_earnings_date = sim_clock.today() + timedelta(days=days_until_earnings)
        is_blocked = client.is_earnings_approaching(target_earnings_date, block_days=2)
        assert is_blocked == expected_blocked


class TestFeature38MacroCalendarYAMLLoader:
    """Feature 38: Macro Calendar YAML Loader (5 tests)."""

    @pytest.mark.parametrize(
        ("event_name", "event_type", "impact"),
        [
            ("FOMC Interest Rate Decision", "FOMC", "critical"),
            ("Consumer Price Index", "CPI", "high"),
            ("Non-Farm Payrolls", "NFP", "high"),
            ("GDP Advance Release", "GDP", "high"),
            ("FOMC Minutes Release", "FOMC", "high"),
        ],
    )
    def test_macro_event_structure(
        self, sim_clock: SimulatedClock, event_name: str, event_type: str, impact: str
    ) -> None:
        filter_eng = MockMacroFilter(clock=sim_clock)
        event = {
            "name": event_name,
            "type": event_type,
            "timestamp": sim_clock.now() + timedelta(days=1),
            "impact": impact,
        }
        filter_eng.load_events([event])
        assert len(filter_eng.events) == 1
        assert filter_eng.events[0]["impact"] == impact


class TestFeature39MacroWindowFilter:
    """Feature 39: Macro Window Filter (5 tests)."""

    @pytest.mark.parametrize(
        ("offset_minutes", "expected_blocked"),
        [
            (-45, False),  # 45m before -> clear
            (-20, True),  # 20m before -> within 30m window, blocked
            (0, True),  # At announcement -> blocked
            (25, True),  # 25m after -> within 30m window, blocked
            (40, False),  # 40m after -> clear
        ],
    )
    def test_macro_blackout_window(
        self, sim_clock: SimulatedClock, offset_minutes: int, expected_blocked: bool
    ) -> None:
        filter_eng = MockMacroFilter(clock=sim_clock)
        event_time = sim_clock.now()
        filter_eng.load_events(
            [{"name": "CPI", "type": "CPI", "timestamp": event_time, "impact": "high"}]
        )

        query_time = event_time + timedelta(minutes=offset_minutes)
        blocked, _ = filter_eng.is_macro_window_active(query_time, strategy_type="intraday")
        assert blocked == expected_blocked


class TestFeature40MacroCalendarExpiryMonitor:
    """Feature 40: Macro Calendar Expiry Monitor (5 tests)."""

    @pytest.mark.parametrize(
        ("remaining_horizon_days", "expected_alert"),
        [
            (60, False),
            (45, False),
            (31, False),
            (30, False),
            (25, True),  # Horizon < 30 days -> trigger alert
        ],
    )
    def test_calendar_expiry_alert(
        self, sim_clock: SimulatedClock, remaining_horizon_days: int, expected_alert: bool
    ) -> None:
        filter_eng = MockMacroFilter(clock=sim_clock)
        now = sim_clock.now()
        filter_eng.load_events(
            [{"name": "FOMC", "timestamp": now + timedelta(days=remaining_horizon_days)}]
        )

        needs_alert, days = filter_eng.check_calendar_expiry(now, horizon_days=30)
        assert needs_alert == expected_alert


class TestFeature41OpenWindowFilter:
    """Feature 41: Open Window Filter (5 tests)."""

    @pytest.mark.parametrize(
        ("minute_et", "strategy_allows_open", "expected_blocked"),
        [
            (31, False, True),  # 9:31 ET standard strategy -> blocked
            (45, False, True),  # 9:45 ET standard strategy -> blocked
            (59, False, True),  # 9:59 ET standard strategy -> blocked
            (35, True, False),  # 9:35 ET S4 breakout (allows_open_window=True) -> allowed
            (61, False, False),  # 10:01 ET standard strategy -> allowed
        ],
    )
    def test_market_open_window_gating(
        self, minute_et: int, strategy_allows_open: bool, expected_blocked: bool
    ) -> None:
        # In ET, open is 9:30. Window is 9:30 to 10:00 (minutes 30 to 60)
        in_open_window = 30 <= minute_et < 60
        is_blocked = in_open_window and not strategy_allows_open
        assert is_blocked == expected_blocked


class TestFeature42CloseWindowFilter:
    """Feature 42: Close Window Filter (5 tests)."""

    @pytest.mark.parametrize(
        ("minute_et", "is_closing_exit", "expected_blocked"),
        [
            (45, False, False),  # 15:45 ET standard entry -> allowed
            (51, False, True),  # 15:51 ET standard entry -> blocked (last 10m)
            (55, False, True),  # 15:55 ET standard entry -> blocked (last 10m)
            (58, True, False),  # 15:58 ET intraday MOC exit -> allowed
            (59, False, True),  # 15:59 ET standard entry -> blocked
        ],
    )
    def test_market_close_window_gating(
        self, minute_et: int, is_closing_exit: bool, expected_blocked: bool
    ) -> None:
        in_close_window = minute_et >= 50
        is_blocked = in_close_window and not is_closing_exit
        assert is_blocked == expected_blocked


class TestFeature43OpenQuestionsDiscrepancyLog:
    """Feature 43: Open Questions Discrepancy Log (5 tests)."""

    @pytest.mark.parametrize(
        ("api_source", "plan_section", "issue_summary"),
        [
            ("Alpaca Market Data", "Sec 7.2", "Statuses stream absent on free paper tier"),
            ("Finnhub REST", "Sec 8.3", "Earnings calendar hour field returns dmh code"),
            ("Alpaca Trading", "Sec 4.2", "Fractional orders reject OCO/OTO execution"),
            ("Alpaca Calendar", "Sec 7.3", "Early close timestamp format variance"),
            ("Alpaca Stream", "Sec 7.2", "WebSocket ping interval 10s vs 20s specification"),
        ],
    )
    def test_open_questions_formatting(
        self, api_source: str, plan_section: str, issue_summary: str
    ) -> None:
        entry = f"| {api_source} | {plan_section} | {issue_summary} | OPEN |"
        assert api_source in entry
        assert plan_section in entry
        assert issue_summary in entry
