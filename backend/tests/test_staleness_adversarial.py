"""Adversarial stress tests and empirical edge case challenges for Level 1/2 staleness.

Covers:
- Clock jumps: 59s, 60s, 61s, 300s, 301s, massive forward jumps, backward jumps.
- Rapid feed flapping, connection drops, reconnect recovery under high frequency.
- Non-monotonic message arrival: out-of-order timestamps, future timestamps, historical timestamps.
- Dynamic threshold calibration: 0 gaps, identical gaps, massive gaps, empty array fallback, numpy/pandas types.
- Exchange halt status transitions: T -> H -> P -> R -> T, Q status, and potential halt detection.
- Cross-feed anomaly stress cases and type safety.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.data.staleness import (
    DEFAULT_TAU_SECONDS,
    EntryPriceMode,
    ExchangeHaltStatus,
    FeedHealthState,
    GlobalFeedMonitor,
    StalenessGuard,
    SymbolFreshnessMonitor,
    calibrate_freshness,
)
from tbot.data.types import PriceQuote, TradeQuote


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Clock set to 14:00:00 UTC (10:00 AM ET regular market session)."""
    return SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))


# ============================================================================
# 1. CLOCK JUMPS & BOUNDARY STRESS TESTS
# ============================================================================


class TestClockJumpsStress:
    """Stress tests clock jumps: 59s, 60s, 61s, 300s, 301s and extreme time shifts."""

    def test_clock_jump_stepping_59_60_61_300_301(self, sim_clock: SimulatedClock) -> None:
        """Stepwise verification of exact threshold transitions:
        t=0s: HEALTHY
        t=59s: HEALTHY
        t=60s: HEALTHY
        t=61s: DEGRADED
        t=300s: DEGRADED
        t=301s: STALE
        """
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False

        # Jump to 59s
        sim_clock.advance(timedelta(seconds=59))
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False

        # Jump to 60s (inclusive boundary)
        sim_clock.advance(timedelta(seconds=1))
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False

        # Jump to 61s (first degraded second)
        sim_clock.advance(timedelta(seconds=1))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_fallback_active is True
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_DEGRADED"

        # Jump to 300s (inclusive boundary of degraded)
        sim_clock.advance(timedelta(seconds=239))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_fallback_active is True

        # Jump to 301s (first stale second)
        sim_clock.advance(timedelta(seconds=1))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_fallback_active is True
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_STALE"

    def test_instant_jump_to_61s_degraded(self, sim_clock: SimulatedClock) -> None:
        """Direct jump from t=0 to t=61s without intermediate evaluation."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        sim_clock.advance(timedelta(seconds=61))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_fallback_active is True

    def test_instant_jump_to_301s_stale(self, sim_clock: SimulatedClock) -> None:
        """Direct jump from t=0 to t=301s without intermediate evaluation."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        sim_clock.advance(timedelta(seconds=301))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_fallback_active is True

    def test_massive_clock_jump_100k_seconds(self, sim_clock: SimulatedClock) -> None:
        """Massive clock jump forward by 100,000 seconds (over 27 hours)."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        sim_clock.advance(timedelta(seconds=100000))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_fallback_active is True

    def test_reconnect_recovery_after_massive_clock_jump(self, sim_clock: SimulatedClock) -> None:
        """Feed cleanly recovers to HEALTHY on new message after massive jump."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        sim_clock.advance(timedelta(seconds=100000))
        assert monitor.state == FeedHealthState.STALE

        # New message arrives at the new current time
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is True
        assert reason == "OK"

    def test_negative_clock_jump_retains_healthy(self, sim_clock: SimulatedClock) -> None:
        """Simulate clock stepping backwards (e.g. NTP time slew or system clock adjustment)."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY

        # Clock steps backwards 30 seconds
        sim_clock.advance(timedelta(seconds=-30))
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False


# ============================================================================
# 2. RAPID FEED FLAPPING & CONNECTION DROPS
# ============================================================================


class TestRapidFeedFlappingStress:
    """Stress tests high-frequency connect/disconnect flapping and recovery."""

    def test_rapid_1000_flaps_synchronization(self, sim_clock: SimulatedClock) -> None:
        """1,000 alternating disconnect and reconnect events maintain strict state integrity."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)

        for i in range(1000):
            # Drop
            monitor.record_disconnect(f"drop_{i}")
            assert monitor.state == FeedHealthState.DISCONNECTED
            assert monitor.is_fallback_active is True
            allowed, reason = monitor.is_entry_allowed()
            assert allowed is False
            assert reason == "FEED_DISCONNECTED"

            # Reconnect
            monitor.record_message()
            assert monitor.state == FeedHealthState.HEALTHY
            assert monitor.is_fallback_active is False
            allowed, reason = monitor.is_entry_allowed()
            assert allowed is True
            assert reason == "OK"

    def test_consecutive_disconnects_idempotent(self, sim_clock: SimulatedClock) -> None:
        """Multiple consecutive disconnect calls remain cleanly DISCONNECTED."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        for _ in range(10):
            monitor.record_disconnect("socket error")
            assert monitor.state == FeedHealthState.DISCONNECTED
            assert monitor.is_fallback_active is True

    def test_consecutive_messages_idempotent(self, sim_clock: SimulatedClock) -> None:
        """Multiple consecutive message receipts remain cleanly HEALTHY."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        for _ in range(10):
            monitor.record_message()
            assert monitor.state == FeedHealthState.HEALTHY
            assert monitor.is_fallback_active is False


# ============================================================================
# 3. DYNAMIC THRESHOLD CALIBRATION EDGE CASES
# ============================================================================


class TestDynamicThresholdCalibrationEdgeCases:
    """Edge case mining for calibrate_freshness: 0 gaps, identical, massive, empty, types."""

    def test_zero_gaps_clamped_to_min_tau(self) -> None:
        """Gaps of 0.0s should be clamped to MIN_TAU_SECONDS (180.0s)."""
        result = calibrate_freshness([0.0, 0.0, 0.0, 0.0])
        assert result == 180.0

    def test_identical_gaps_clamping(self) -> None:
        """Identical gap series test clamping lower, within, and upper bounds."""
        # Lower bound clamping
        assert calibrate_freshness([50.0] * 50) == 180.0
        # Within bound
        assert calibrate_freshness([250.0] * 50) == 250.0
        # Upper bound clamping
        assert calibrate_freshness([800.0] * 50) == 600.0

    def test_massive_gaps_1_million_seconds(self) -> None:
        """Extreme gap sizes (1,000,000s) must clamp strictly to MAX_TAU_SECONDS (600.0s)."""
        result = calibrate_freshness([1_000_000.0, 2_000_000.0, 5_000_000.0])
        assert result == 600.0

    def test_empty_sequence_fallback(self) -> None:
        """Empty sequence falls back to DEFAULT_TAU_SECONDS (300.0s)."""
        assert calibrate_freshness([]) == DEFAULT_TAU_SECONDS
        assert calibrate_freshness(()) == DEFAULT_TAU_SECONDS

    def test_all_invalid_elements_fallback(self) -> None:
        """Sequence containing only NaN, Inf, negative, or None falls back to 300.0s."""
        bad_values = [float("nan"), float("inf"), -100.0, -0.01]
        assert calibrate_freshness(bad_values) == DEFAULT_TAU_SECONDS

    def test_single_element_calibration(self) -> None:
        """Single element sequence calibrates correctly."""
        assert calibrate_freshness([100.0]) == 180.0
        assert calibrate_freshness([400.0]) == 400.0
        assert calibrate_freshness([1000.0]) == 600.0

    def test_numpy_array_input_fails_truth_value_bug(self) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: calibrate_freshness soporta NumPy ndarrays."""
        arr = np.array([10.0, 20.0, 30.0])
        assert calibrate_freshness(arr) == 180.0

    def test_pandas_series_input_fails_truth_value_bug(self) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: calibrate_freshness soporta Pandas Series."""
        series = pd.Series([10.0, 20.0, 30.0])
        assert calibrate_freshness(series) == 180.0


# ============================================================================
# 4. NON-MONOTONIC MESSAGE ARRIVAL & TIMESTAMP EDGE CASES
# ============================================================================


class TestNonMonotonicTimestampsStress:
    """Stress tests out-of-order timestamps, future timestamps, and timezone discrepancies."""

    def test_out_of_order_message_regresses_global_feed(self, sim_clock: SimulatedClock) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: Un mensaje fuera de orden no debe retroceder el feed saludable."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message(sim_clock.now())
        assert monitor.state == FeedHealthState.HEALTHY

        # Delayed message from 10 minutes ago arrives out of order
        delayed_ts = sim_clock.now() - timedelta(minutes=10)
        monitor.record_message(delayed_ts)

        # Monotonía garantizada: el feed permanece HEALTHY y last_message_time no retrocede
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_entry_allowed()[0] is True
        assert monitor.last_message_time == sim_clock.now()

    def test_out_of_order_quote_regresses_symbol_freshness(self, sim_clock: SimulatedClock) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: Cotización fuera de orden no debe sobreescribir la cotización fresca."""
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        fresh_quote = PriceQuote(
            symbol="SPY", bid=Decimal("500.00"), ask=Decimal("500.02"), timestamp=sim_clock.now()
        )
        monitor.record_quote("SPY", fresh_quote)
        is_fresh, reason = monitor.check_symbol_freshness("SPY")
        assert is_fresh is True and reason == "OK"

        # Out-of-order older quote arrives
        old_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.02"),
            timestamp=sim_clock.now() - timedelta(minutes=10),
        )
        monitor.record_quote("SPY", old_quote)

        # Monotonía garantizada: símbolo permanece fresco
        is_fresh_after, reason_after = monitor.check_symbol_freshness("SPY")
        assert is_fresh_after is True
        assert reason_after == "OK"

    def test_future_timestamp_masks_global_feed_silence(self, sim_clock: SimulatedClock) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: Timestamps futuros no deben enmascarar desconexiones reales.

        Al clampear a 'now' cualquier timestamp futuro que exceda 1.0s, el feed detectará
        correctamente el silencio subsiguiente y transicionará a STALE.
        """
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        future_ts = sim_clock.now() + timedelta(hours=1)
        monitor.record_message(future_ts)
        assert monitor.state == FeedHealthState.HEALTHY

        # Advance clock by 30 minutes of complete silence
        sim_clock.advance(timedelta(minutes=30))
        # Debido al clamping a 'now', el silencio de 30m es detectado y el feed pasa a STALE
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_fallback_active is True

    def test_entry_price_rejects_excessive_future_timestamp(
        self, sim_clock: SimulatedClock
    ) -> None:
        """get_entry_price enforces a strict 1s upper bound on future timestamps."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)

        # Quote 2 seconds into future is rejected
        fut_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.02"),
            timestamp=sim_clock.now() + timedelta(seconds=2),
        )
        fut_trade = TradeQuote(
            symbol="SPY",
            price=Decimal("500.01"),
            size=100,
            timestamp=sim_clock.now() + timedelta(seconds=2),
        )
        price, mode = guard.get_entry_price(fut_quote, fut_trade)
        assert price is None
        assert mode == EntryPriceMode.STALE_PRICE

        # Quote within 1s into future is accepted
        slight_fut_quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.02"),
            timestamp=sim_clock.now() + timedelta(milliseconds=500),
        )
        price_ok, mode_ok = guard.get_entry_price(slight_fut_quote, fut_trade)
        assert price_ok == Decimal("500.01")
        assert mode_ok == EntryPriceMode.MIDPOINT

    def test_validate_entry_crashes_on_mixed_naive_aware_timestamps(
        self, sim_clock: SimulatedClock
    ) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: validate_entry normaliza timestamps mixtos (naive vs aware) a UTC."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()

        # Naive quote timestamp
        q_naive = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.02"),
            timestamp=datetime(2026, 9, 21, 14, 0, 0),  # tzinfo is None!
        )
        # Aware trade timestamp
        t_aware = TradeQuote(
            symbol="SPY",
            price=Decimal("500.01"),
            size=100,
            timestamp=datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC),  # tzinfo is UTC!
        )

        price, is_valid, reason = guard.validate_entry("SPY", q_naive, t_aware, Decimal("500.00"))
        assert is_valid is True
        assert reason == "OK"
        assert price == Decimal("500.01")


# ============================================================================
# 5. EXCHANGE HALT TRANSITIONS (T -> H -> P -> R -> T) & DETECTION
# ============================================================================


class TestExchangeHaltTransitionsAndDetection:
    """Adversarial testing of exchange halt status transitions and potential halt detection."""

    def test_full_halt_transition_cycle_t_h_p_r_t(self, sim_clock: SimulatedClock) -> None:
        """Full halt cycle: TRADING -> HALTED -> PAUSED -> RESUMED -> TRADING."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()

        quote = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=sim_clock.now())
        trade = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=sim_clock.now())
        guard.record_quote("SPY", quote)
        guard.record_trade("SPY", trade)

        # 1. State T (Trading): allowed
        guard.record_exchange_status("SPY", ExchangeHaltStatus.TRADING)
        price, ok, reason = guard.validate_entry("SPY", quote, trade, Decimal("500.00"))
        assert ok is True and reason == "OK"

        # 2. State H (Halted): blocked with SYMBOL_HALTED
        guard.record_exchange_status("SPY", ExchangeHaltStatus.HALTED)
        price, ok, reason = guard.validate_entry("SPY", quote, trade, Decimal("500.00"))
        assert ok is False and reason == "SYMBOL_HALTED"
        assert price is None

        # 3. State P (Paused): blocked with SYMBOL_HALTED
        guard.record_exchange_status("SPY", ExchangeHaltStatus.PAUSED)
        price, ok, reason = guard.validate_entry("SPY", quote, trade, Decimal("500.00"))
        assert ok is False and reason == "SYMBOL_HALTED"

        # 4. State R (Resumed): if quotes are stale from the halt duration, blocked with POTENTIAL_HALT
        sim_clock.advance(timedelta(seconds=400))  # 400s > tau_symbol (300s)
        guard.record_message()  # Keep global feed alive
        guard.record_exchange_status("SPY", ExchangeHaltStatus.RESUMED)
        # Entry blocked because quote/trade timestamps are from before the halt
        price, ok, reason = guard.validate_entry("SPY", quote, trade, Decimal("500.00"))
        assert ok is False and reason == "POTENTIAL_HALT"

        # 5. Fresh quotes arrive after resumption
        fresh_q = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=sim_clock.now())
        fresh_t = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=sim_clock.now())
        guard.record_quote("SPY", fresh_q)
        guard.record_trade("SPY", fresh_t)
        price, ok, reason = guard.validate_entry("SPY", fresh_q, fresh_t, Decimal("500.00"))
        assert ok is True and reason == "OK"

        # 6. State T (Normal Trading confirmed)
        guard.record_exchange_status("SPY", ExchangeHaltStatus.TRADING)
        price, ok, reason = guard.validate_entry("SPY", fresh_q, fresh_t, Decimal("500.00"))
        assert ok is True and reason == "OK"

    def test_quote_only_status_blocks_entry(self, sim_clock: SimulatedClock) -> None:
        """QUOTE_ONLY ('Q') exchange status prevents trade entry."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        quote = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=sim_clock.now())
        trade = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=sim_clock.now())

        guard.record_exchange_status("SPY", ExchangeHaltStatus.QUOTE_ONLY)
        price, ok, reason = guard.validate_entry("SPY", quote, trade, Decimal("500.00"))
        assert ok is False
        assert reason == "SYMBOL_HALTED"

    def test_potential_halt_detection_when_feed_exceeds_tau(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Symbol data age exceeding tau_symbol triggers POTENTIAL_HALT even without exchange status."""
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        quote = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=sim_clock.now())
        monitor.record_quote("SPY", quote)

        # Advance beyond 300s default tau
        sim_clock.advance(timedelta(seconds=301))
        is_fresh, reason = monitor.check_symbol_freshness("SPY")
        assert is_fresh is False
        assert reason == "POTENTIAL_HALT"

        allowed, entry_reason = monitor.is_symbol_entry_allowed("SPY")
        assert allowed is False
        assert entry_reason == "STALE_SYMBOL_FEED"

    def test_case_and_whitespace_insensitivity_in_halt_codes(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Halt status codes are stripped and uppercased defensively."""
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        monitor.record_exchange_status("SPY", "  h  ")
        assert monitor._symbols["SPY"].is_halted is True
        assert monitor._symbols["SPY"].halt_code == "H"


# ============================================================================
# 6. CROSS-FEED ANOMALY DETECTOR STRESS & NUMERIC STABILITY
# ============================================================================


class TestCrossFeedAnomalyStress:
    """Adversarial testing of cross-feed anomaly detector with extreme values."""

    def test_extreme_price_spikes_and_crashes(self) -> None:
        """100x price spike and 99% price crash are rejected as PRICE_ANOMALY."""
        guard = StalenessGuard()
        # 100x spike
        ok, reason = guard.check_price_anomaly(
            Decimal("50000.00"), Decimal("500.00"), Decimal("1.00")
        )
        assert ok is False and reason == "PRICE_ANOMALY"

        # 99% crash
        ok, reason = guard.check_price_anomaly(Decimal("5.00"), Decimal("500.00"), Decimal("1.00"))
        assert ok is False and reason == "PRICE_ANOMALY"

    def test_exact_boundary_tolerance_precision(self) -> None:
        """Boundary condition diff == 2 * ATR must pass; diff > 2 * ATR must fail."""
        guard = StalenessGuard()
        sip = Decimal("100.00")
        atr = Decimal("0.50")
        exact_pass = sip + Decimal("1.00")  # exactly 2 * ATR
        just_over = sip + Decimal("1.0000001")  # > 2 * ATR

        ok_pass, _ = guard.check_price_anomaly(exact_pass, sip, atr)
        assert ok_pass is True

        ok_fail, reason = guard.check_price_anomaly(just_over, sip, atr)
        assert ok_fail is False and reason == "PRICE_ANOMALY"

    def test_nan_inf_strings_gracefully_handled(self) -> None:
        """Non-numeric strings, NaN, and Inf safely return MISSING_PRICE_DATA without crash."""
        guard = StalenessGuard()
        ok, reason = guard.check_price_anomaly("invalid", Decimal("100.00"), Decimal("1.00"))
        assert ok is False and reason == "MISSING_PRICE_DATA"

        ok, reason = guard.check_price_anomaly(float("nan"), Decimal("100.00"), Decimal("1.00"))
        assert ok is False and reason == "MISSING_PRICE_DATA"

        ok, reason = guard.check_price_anomaly(float("inf"), Decimal("100.00"), Decimal("1.00"))
        assert ok is False and reason == "MISSING_PRICE_DATA"
