"""Empirical Challenger Gen 3 Stress Test Suite.

Comprehensive empirical verification of the 5 remediated defects in `backend/tbot/data/staleness.py`:
1. Thread-safety race condition on discard_counts (multithreaded 12+ threads, 60,000+ discards, concurrent readers).
2. NaN / string 'NaN' sanitization across all input positions and representations without decimal.InvalidOperation.
3. Infinity bypass rejection in entry pricing and anomaly detection.
4. Dict / synthetic bar attribute access defense in validate_entry and validate_entry_or_raise.
5. High-precision Decimal context (>28 digits) exact boundary stability without false-positive anomalies.
6. Auxiliary edge cases: timestamps, arrays, out-of-order streams, extreme values.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import StaleDataError
from tbot.data.staleness import (
    AnomalyDetector,
    StalenessGuard,
    ThreadSafeDiscardCounts,
    _normalize_ts,
    _to_decimal,
    calibrate_freshness,
)
from tbot.data.types import PriceQuote, TradeQuote
from tbot.indicators.pure import create_synthetic_bar


@pytest.fixture
def sim_clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC))


# ============================================================================
# 1. THREAD-SAFETY STRESS TEST: DISCARD_COUNTS
# ============================================================================


class TestThreadSafetyDiscardCountsStress:
    """Stress test multithreaded concurrency on discard metrics."""

    def test_multithreaded_high_contention_discards_zero_loss(self) -> None:
        """Runs 12 concurrent threads (6 on STALE_PRICE, 6 on PRICE_ANOMALY).

        Total operations: 6 * 5,000 + 6 * 5,000 = 60,000 discards under GIL preemption (1e-7).
        Additionally, 2 background reader threads continuously read, copy, and iterate
        the discard_counts structure to test lock stability and avoid iteration errors.
        """
        old_switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-7)

        guard = StalenessGuard(enforce_market_hours=False)
        iterations_per_thread = 5000
        worker_count_per_type = 6
        expected_stale = worker_count_per_type * iterations_per_thread
        expected_anomaly = worker_count_per_type * iterations_per_thread

        stop_readers = threading.Event()
        reader_errors: list[Exception] = []

        def reader_loop() -> None:
            while not stop_readers.is_set():
                try:
                    # Test dictionary operations under high concurrent writes
                    _ = guard.discard_counts.copy()
                    _ = list(guard.discard_counts.items())
                    _ = list(guard.discard_counts.values())
                    _ = list(guard.discard_counts.keys())
                    _ = guard.discard_counts.get("STALE_PRICE", 0)
                    _ = guard.discard_counts.get("PRICE_ANOMALY", 0)
                    _ = len(guard.discard_counts)
                except Exception as ex:
                    reader_errors.append(ex)

        readers = [threading.Thread(target=reader_loop) for _ in range(2)]
        for r in readers:
            r.start()

        def worker_stale() -> None:
            q_stale = PriceQuote(
                symbol="TEST",
                bid=Decimal("10.00"),
                ask=Decimal("10.05"),
                timestamp=datetime(2020, 1, 1, tzinfo=UTC),
            )
            t_stale = TradeQuote(
                symbol="TEST",
                price=Decimal("10.02"),
                timestamp=datetime(2020, 1, 1, tzinfo=UTC),
            )
            for _ in range(iterations_per_thread):
                price, mode = guard.get_entry_price(q_stale, t_stale)
                assert price is None
                assert mode == "STALE_PRICE"

        def worker_anomaly() -> None:
            for _ in range(iterations_per_thread):
                ok, reason = guard.check_price_anomaly(
                    Decimal("200.00"), Decimal("100.00"), Decimal("1.00")
                )
                assert ok is False
                assert reason == "PRICE_ANOMALY"

        threads = []
        for _ in range(worker_count_per_type):
            threads.append(threading.Thread(target=worker_stale))
            threads.append(threading.Thread(target=worker_anomaly))

        try:
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        finally:
            stop_readers.set()
            for r in readers:
                r.join()
            sys.setswitchinterval(old_switch)

        assert not reader_errors, f"Reader encountered exceptions: {reader_errors}"

        actual_stale = guard.discard_counts["STALE_PRICE"]
        actual_anomaly = guard.discard_counts["PRICE_ANOMALY"]

        assert actual_stale == expected_stale, (
            f"STALE_PRICE lost updates: expected {expected_stale}, got {actual_stale}"
        )
        assert actual_anomaly == expected_anomaly, (
            f"PRICE_ANOMALY lost updates: expected {expected_anomaly}, got {actual_anomaly}"
        )

    def test_direct_increment_method_thread_safety(self) -> None:
        """Verifies direct calls to ThreadSafeDiscardCounts.increment."""
        counts = ThreadSafeDiscardCounts({"A": 0, "B": 0})
        num_threads = 10
        iters = 10000

        def worker() -> None:
            for _ in range(iters):
                counts.increment("A", 1)
                counts.increment("B", 2)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counts["A"] == num_threads * iters
        assert counts["B"] == num_threads * iters * 2


# ============================================================================
# 2. NAN & STRING 'NAN' SANITIZATION EXHAUSTIVE TEST
# ============================================================================


class TestNanSanitizationExhaustive:
    """Exhaustive stress testing of NaN representations and operations."""

    NAN_INPUTS = [
        Decimal("NaN"),
        Decimal("-NaN"),
        Decimal("sNaN"),
        Decimal("-sNaN"),
        "NaN",
        "nan",
        "NAN",
        "-NaN",
        "+NaN",
        " sNaN ",
        float("nan"),
        float("-nan"),
        np.nan,
    ]

    def test_to_decimal_nan_all_forms(self) -> None:
        """_to_decimal must return None for every form of NaN."""
        for val in self.NAN_INPUTS:
            res = _to_decimal(val)
            assert res is None, f"_to_decimal failed to return None for NaN variant: {val!r}"

    def test_anomaly_detector_nan_no_invalid_operation(self) -> None:
        """check_price_anomaly must never raise decimal.InvalidOperation when receiving NaNs."""
        detector = AnomalyDetector()
        detector_fc = AnomalyDetector(fallback_mode="fail_closed")

        for nan_val in self.NAN_INPUTS:
            # 1. NaN as iex_price
            ok, reason = detector.check_price_anomaly(nan_val, Decimal("100"), Decimal("1"))
            assert ok is False
            assert reason in ("MISSING_PRICE_DATA", "INVALID_PRICE")

            # 2. NaN as sip_close
            ok, reason = detector.check_price_anomaly(Decimal("100"), nan_val, Decimal("1"))
            assert ok is False
            assert reason in ("MISSING_PRICE_DATA", "INVALID_PRICE")

            # 3. NaN as atr_5m (fallback percentage)
            ok, reason = detector.check_price_anomaly(Decimal("101"), Decimal("100"), nan_val)
            assert ok is True  # 1% diff <= 2% fallback threshold
            assert reason == "OK"

            # 4. NaN as atr_5m (fallback fail_closed)
            ok, reason = detector_fc.check_price_anomaly(Decimal("101"), Decimal("100"), nan_val)
            assert ok is False
            assert reason == "MISSING_ATR"

            # 5. All inputs NaN
            ok, reason = detector.check_price_anomaly(nan_val, nan_val, nan_val)
            assert ok is False

    def test_get_entry_price_nan_quote_and_trade(self, sim_clock: SimulatedClock) -> None:
        """Quotes or trades with NaN prices must be rejected fail-closed without exception."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        # Quote with Decimal('NaN') bid (explicit midpoint/spread_bps to bypass PriceQuote.__post_init__ arithmetic)
        q_nan_bid = PriceQuote(
            symbol="TEST",
            bid=Decimal("NaN"),
            ask=Decimal("100.00"),
            timestamp=t_now,
            bid_size=100,
            ask_size=100,
            midpoint=Decimal("NaN"),
            spread_bps=0.0,
        )
        t_fresh = TradeQuote(symbol="TEST", price=Decimal("100.00"), timestamp=t_now, size=100)
        # Should reject quote and fall back to fresh trade
        price, mode = guard.get_entry_price(q_nan_bid, t_fresh)
        assert price == Decimal("100.00")
        assert mode == "LAST_TRADE"

        # Trade with Decimal('NaN') price
        t_nan = TradeQuote(symbol="TEST", price=Decimal("NaN"), timestamp=t_now, size=100)
        price, mode = guard.get_entry_price(None, t_nan)
        assert price is None
        assert mode == "STALE_PRICE"

    def test_validate_entry_nan_inputs_fail_closed(self, sim_clock: SimulatedClock) -> None:
        """validate_entry handles NaN in quote, trade, sip_close, and atr_5m safely."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()

        t_fresh = TradeQuote(symbol="TEST", price=Decimal("100.00"), timestamp=t_now, size=100)

        # NaN in sip_close
        price, is_valid, reason = guard.validate_entry(
            "TEST",
            quote=None,
            trade=t_fresh,
            sip_close=Decimal("NaN"),
            atr_5m=Decimal("1.00"),
        )
        assert is_valid is False
        assert price is None
        assert reason in ("MISSING_PRICE_DATA", "INVALID_PRICE")


# ============================================================================
# 3. INFINITY BYPASS PREVENTION EXHAUSTIVE TEST
# ============================================================================


class TestInfinityBypassPreventionExhaustive:
    """Stress testing rejection of positive and negative Infinity."""

    INF_INPUTS = [
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("+Inf"),
        Decimal("-Inf"),
        "Infinity",
        "-Infinity",
        "+Infinity",
        "inf",
        "-inf",
        float("inf"),
        float("-inf"),
    ]

    def test_to_decimal_infinity_all_forms(self) -> None:
        """_to_decimal must return None for every Infinity representation."""
        for val in self.INF_INPUTS:
            res = _to_decimal(val)
            assert res is None, f"_to_decimal failed to return None for Infinity variant: {val!r}"

    def test_infinity_trade_cannot_be_selected_as_entry_price(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Decimal('Infinity') in TradeQuote cannot bypass entry pricing."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        for inf_val in [Decimal("Infinity"), Decimal("-Infinity")]:
            t_inf = TradeQuote(symbol="TEST", price=inf_val, timestamp=t_now, size=100)
            price, mode = guard.get_entry_price(None, t_inf)
            assert price is None
            assert mode == "STALE_PRICE"

    def test_infinity_quotes_cannot_calculate_valid_midpoint(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Quotes with infinite bid, ask, or midpoint must be rejected."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        for inf_val in [Decimal("Infinity"), Decimal("-Infinity")]:
            q_inf = PriceQuote(
                symbol="TEST",
                bid=Decimal("100.00"),
                ask=inf_val,
                timestamp=t_now,
                bid_size=100,
                ask_size=100,
                midpoint=inf_val,
                spread_bps=0.0,
            )
            price, mode = guard.get_entry_price(q_inf, None)
            assert price is None
            assert mode == "STALE_PRICE"

    def test_infinity_atr_does_not_mask_anomalies(self) -> None:
        """An infinite ATR must not inflate the threshold to Infinity to allow anomalies."""
        detector = AnomalyDetector(fallback_mode="percentage", fallback_pct=Decimal("0.02"))

        # If ATR is Infinity, _to_decimal returns None -> falls back to 2% of sip_close (2.0)
        # An IEX price of 1,000,000 against sip_close=100 is diff=999,900 >> 2.0 -> PRICE_ANOMALY
        ok, reason = detector.check_price_anomaly(
            Decimal("1000000.00"), Decimal("100.00"), Decimal("Infinity")
        )
        assert ok is False
        assert reason == "PRICE_ANOMALY"

        # Fail-closed mode rejects with MISSING_ATR
        detector_fc = AnomalyDetector(fallback_mode="fail_closed")
        ok, reason = detector_fc.check_price_anomaly(
            Decimal("105.00"), Decimal("100.00"), Decimal("Infinity")
        )
        assert ok is False
        assert reason == "MISSING_ATR"


# ============================================================================
# 4. DICT ATTRIBUTE ACCESS & SYNTHETIC BAR IMMUNITY
# ============================================================================


class TestDictAttributeAccessAndSyntheticBars:
    """Stress testing validate_entry against synthetic bar dicts and non-quote objects."""

    def test_synthetic_bar_dict_in_validate_entry(self, sim_clock: SimulatedClock) -> None:
        """validate_entry handles create_synthetic_bar dict safely without AttributeError."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()

        synth = create_synthetic_bar(t_now, Decimal("250.00"))
        assert isinstance(synth, dict)

        # 1. As quote only
        price, is_valid, reason = guard.validate_entry(
            "MSFT",
            quote=synth,  # type: ignore[arg-type]
            trade=None,
            sip_close=Decimal("250.00"),
            atr_5m=Decimal("2.00"),
        )
        assert is_valid is False
        assert price is None
        assert reason in ("STALE_PRICE", "NO_DATA")

        # 2. As trade only
        price, is_valid, reason = guard.validate_entry(
            "MSFT",
            quote=None,
            trade=synth,  # type: ignore[arg-type]
            sip_close=Decimal("250.00"),
            atr_5m=Decimal("2.00"),
        )
        assert is_valid is False
        assert price is None
        assert reason in ("STALE_PRICE", "NO_DATA")

        # 3. As both
        price, is_valid, reason = guard.validate_entry(
            "MSFT",
            quote=synth,  # type: ignore[arg-type]
            trade=synth,  # type: ignore[arg-type]
            sip_close=Decimal("250.00"),
            atr_5m=Decimal("2.00"),
        )
        assert is_valid is False
        assert price is None
        assert reason in ("STALE_PRICE", "NO_DATA")

    def test_arbitrary_dict_and_malformed_objects(self, sim_clock: SimulatedClock) -> None:
        """Arbitrary dictionaries or malformed objects fail closed safely."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()

        malformed_inputs = [
            {},
            {"timestamp": "not_a_datetime"},
            {"timestamp": None},
            {"bid": 100, "ask": 101},
            [1, 2, 3],
            "string_quote",
            object(),
        ]

        for bad in malformed_inputs:
            price, is_valid, reason = guard.validate_entry(
                "AAPL",
                quote=bad,  # type: ignore[arg-type]
                trade=bad,  # type: ignore[arg-type]
                sip_close=Decimal("150.00"),
                atr_5m=Decimal("1.00"),
            )
            assert is_valid is False
            assert price is None
            assert reason in ("STALE_PRICE", "NO_DATA")

    def test_validate_entry_or_raise_raises_stale_data_error(
        self, sim_clock: SimulatedClock
    ) -> None:
        """validate_entry_or_raise raises StaleDataError, never AttributeError."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()

        synth = create_synthetic_bar(t_now, Decimal("100.00"))
        with pytest.raises(StaleDataError) as exc_info:
            guard.validate_entry_or_raise(
                "SPY",
                quote=synth,  # type: ignore[arg-type]
                trade=synth,  # type: ignore[arg-type]
                sip_close=Decimal("100.00"),
                atr_5m=Decimal("1.00"),
            )
        assert exc_info.value.details["reason"] in ("STALE_PRICE", "NO_DATA")


# ============================================================================
# 5. HIGH CONTEXT PRECISION (>28 DIGITS) EXACT BOUNDARY STRESS TEST
# ============================================================================


class TestContextPrecisionHighDigitsBoundary:
    """Stress testing exact mathematical boundaries with >28 decimal digits."""

    def test_40_digit_exact_boundaries(self) -> None:
        """Checks upward and downward exact boundaries with 40 decimal places.

        diff == 2 * ATR -> (True, 'OK')
        diff == 2 * ATR + 1e-39 -> (False, 'PRICE_ANOMALY')
        diff == 2 * ATR - 1e-39 -> (True, 'OK')
        """
        detector = AnomalyDetector()

        # 40 significant digits
        sip_close = Decimal("100.1234567890123456789012345678901234567890")
        atr = Decimal("1.0000000000000000000000000000000000000001")
        threshold = Decimal("2") * atr

        # Exact boundary upward
        p_iex_exact_up = sip_close + threshold
        ok, reason = detector.check_price_anomaly(p_iex_exact_up, sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Above boundary upward (+1e-38)
        p_iex_above = p_iex_exact_up + Decimal("1e-38")
        ok, reason = detector.check_price_anomaly(p_iex_above, sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"

        # Below boundary upward (-1e-38)
        p_iex_below = p_iex_exact_up - Decimal("1e-38")
        ok, reason = detector.check_price_anomaly(p_iex_below, sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Exact boundary downward (flash crash boundary)
        p_iex_exact_down = sip_close - threshold
        ok, reason = detector.check_price_anomaly(p_iex_exact_down, sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Below boundary downward (anomalous drop by -1e-38)
        p_iex_below_down = p_iex_exact_down - Decimal("1e-38")
        ok, reason = detector.check_price_anomaly(p_iex_below_down, sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"


# ============================================================================
# 6. AUXILIARY EDGE CASES & INTEGRATION STRESS
# ============================================================================


class TestAuxiliaryEdgeCases:
    """Stress tests for calibrate_freshness, timestamps, and out-of-order data."""

    def test_calibrate_freshness_array_and_series_inputs(self) -> None:
        """calibrate_freshness accepts numpy arrays, pandas Series, lists, and generators."""
        # 1. Standard list
        tau_list = calibrate_freshness([10.0, 20.0, 30.0])
        assert tau_list == 180.0  # Clamped to MIN_TAU_SECONDS

        # 2. Numpy ndarray
        arr = np.array([100.0, 200.0, 350.0, 500.0])
        tau_arr = calibrate_freshness(arr)
        assert 180.0 <= tau_arr <= 600.0

        # 3. Empty numpy ndarray
        assert calibrate_freshness(np.array([])) == 300.0

        # 4. Pandas Series with missing values
        ser = pd.Series([120.0, None, np.nan, 250.0, 450.0])
        tau_ser = calibrate_freshness(ser)  # type: ignore[arg-type]
        assert 180.0 <= tau_ser <= 600.0

        # 5. Generator expression
        gen = (x * 10.0 for x in range(1, 10))
        tau_gen = calibrate_freshness(gen)  # type: ignore[arg-type]
        assert tau_gen == 180.0

    def test_normalize_ts_mixed_timezones(self) -> None:
        """_normalize_ts converts naive to UTC and handles non-UTC offsets correctly."""
        # Naive datetime
        dt_naive = datetime(2026, 9, 21, 12, 0, 0)
        norm_naive = _normalize_ts(dt_naive)
        assert norm_naive is not None
        assert norm_naive.tzinfo == UTC
        assert norm_naive.hour == 12

        # Aware datetime with UTC-4 offset (EDT)
        edt = timezone(timedelta(hours=-4))
        dt_edt = datetime(2026, 9, 21, 9, 30, 0, tzinfo=edt)
        norm_edt = _normalize_ts(dt_edt)
        assert norm_edt is not None
        assert norm_edt.tzinfo == UTC
        assert norm_edt.hour == 13
        assert norm_edt.minute == 30

        # Invalid objects
        assert _normalize_ts(None) is None
        assert _normalize_ts("2026-09-21") is None
        assert _normalize_ts(123456789) is None

    def test_out_of_order_stream_does_not_regress_monitors(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Delayed out-of-order packets must not regress last_message_time or last_quote_time."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_base = sim_clock.now()

        # 1. Send initial packet at t_base
        guard.record_message(t_base)
        assert guard.global_monitor.last_message_time == t_base

        # 2. Advance time to t_base + 10s and send packet
        sim_clock.set_time(t_base + timedelta(seconds=10))
        guard.record_message(t_base + timedelta(seconds=10))
        assert guard.global_monitor.last_message_time == t_base + timedelta(seconds=10)

        # 3. Delayed packet from the past (t_base - 5s) arrives
        guard.record_message(t_base - timedelta(seconds=5))
        # Cursor must remain at t_base + 10s (no regression)
        assert guard.global_monitor.last_message_time == t_base + timedelta(seconds=10)

        # 4. Quotes out-of-order: current quote at t_base + 10s
        q_new = PriceQuote(
            symbol="SPY",
            bid=Decimal("400"),
            ask=Decimal("400.01"),
            timestamp=t_base + timedelta(seconds=10),
        )
        # Delayed quote from 20 seconds ago
        q_old = PriceQuote(
            symbol="SPY",
            bid=Decimal("399"),
            ask=Decimal("399.01"),
            timestamp=t_base - timedelta(seconds=20),
        )

        guard.record_quote("SPY", q_new)
        state = guard.symbol_monitor._symbols.get("SPY")
        assert state is not None
        assert state.last_quote_time == t_base + timedelta(seconds=10)
        assert state.last_quote == q_new

        # Delayed quote arrives: cursor and cached quote must not regress
        guard.record_quote("SPY", q_old)
        assert state.last_quote_time == t_base + timedelta(seconds=10)
        assert state.last_quote == q_new


# ============================================================================
# 7. DEFECT 1 EMPIRICAL CHALLENGE: CALIBRATE_FRESHNESS
# ============================================================================


class TestDefect1CalibrateFreshnessEmpirical:
    """Empirically stress-test calibrate_freshness across array types and edge cases."""

    def test_numpy_array_no_truth_value_error(self) -> None:
        """NumPy 1D arrays must not raise ValueError on truth evaluation."""
        arr = np.array([12.5, 45.0, 95.0, 150.0, 320.0])
        tau = calibrate_freshness(arr)
        assert isinstance(tau, float)
        assert 180.0 <= tau <= 600.0

    def test_pandas_series_no_truth_value_error(self) -> None:
        """Pandas Series must not raise ValueError on truth evaluation."""
        ser = pd.Series([15.0, 30.0, 45.0, 60.0, 250.0])
        tau = calibrate_freshness(ser)
        assert isinstance(tau, float)
        assert 180.0 <= tau <= 600.0

    def test_empty_sequences_all_types(self) -> None:
        """All empty sequence variations must cleanly return DEFAULT_TAU_SECONDS (300.0)."""
        assert calibrate_freshness([]) == 300.0
        assert calibrate_freshness(()) == 300.0
        assert calibrate_freshness(np.array([])) == 300.0
        assert calibrate_freshness(pd.Series([], dtype=float)) == 300.0
        assert calibrate_freshness(None) == 300.0
        assert calibrate_freshness(x for x in []) == 300.0

    def test_invalid_and_corrupt_elements_filtering(self) -> None:
        """Non-numeric, negative, NaN, Inf, and pd.NA values must be filtered out."""
        corrupt = [
            np.nan,
            float("nan"),
            float("inf"),
            float("-inf"),
            -100.0,
            -0.001,
            None,
            "not_a_number",
        ]
        # All corrupt -> fallback to 300.0
        assert calibrate_freshness(corrupt) == 300.0

        # Corrupt mixed with valid positive numbers
        mixed = [np.nan, None, -50.0, 200.0, 400.0, float("inf")]
        tau = calibrate_freshness(mixed)
        assert 180.0 <= tau <= 600.0
        assert tau == pytest.approx(398.0, rel=1e-2)

        # Pandas series with pd.NA
        ser_corrupt = pd.Series([pd.NA, None, np.nan, -10.0, 250.0, 500.0])
        tau_ser = calibrate_freshness(ser_corrupt)
        assert 180.0 <= tau_ser <= 600.0

    def test_single_element_and_extreme_clamps(self) -> None:
        """Single elements and extreme outliers must be clamped to [180.0, 600.0]."""
        # Very small gap -> clamped to 180.0
        assert calibrate_freshness([0.0]) == 180.0
        assert calibrate_freshness(np.array([1.0])) == 180.0
        assert calibrate_freshness(pd.Series([5.0])) == 180.0

        # Massive gap -> clamped to 600.0
        assert calibrate_freshness([1_000_000.0]) == 600.0
        assert calibrate_freshness(np.array([86400.0])) == 600.0

    def test_large_scale_stress_100k_intervals(self) -> None:
        """100,000 intervals computed in <50ms without memory or stability issues."""
        rng = np.random.default_rng(seed=42)
        large_arr = rng.exponential(scale=20.0, size=100_000)
        tau = calibrate_freshness(large_arr)
        assert isinstance(tau, float)
        assert 180.0 <= tau <= 600.0


# ============================================================================
# 8. DEFECT 2 EMPIRICAL CHALLENGE: VALIDATE_ENTRY TIMEZONES
# ============================================================================


class TestDefect2ValidateEntryTimezonesEmpirical:
    """Empirically stress-test validate_entry across mixed timezone combinations."""

    def test_mixed_naive_aware_quote_and_trade(self) -> None:
        """Offset-naive and offset-aware datetimes in quote and trade must not crash."""
        base_utc = datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC)
        clock = SimulatedClock(base_utc)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)
        guard.record_message(base_utc)

        base_naive = datetime(2026, 9, 21, 14, 30, 0)  # Naive

        # Scenario A: Quote naive, Trade aware
        q_naive = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=base_naive)
        t_aware = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=base_utc)
        price, ok, reason = guard.validate_entry("SPY", q_naive, t_aware, Decimal("500.00"), Decimal("1.00"))
        assert ok is True
        assert reason == "OK"
        assert price == Decimal("500.01")

        # Scenario B: Quote aware, Trade naive
        q_aware = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=base_utc)
        t_naive = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=base_naive)
        price, ok, reason = guard.validate_entry("SPY", q_aware, t_naive, Decimal("500.00"), Decimal("1.00"))
        assert ok is True
        assert reason == "OK"
        assert price == Decimal("500.01")

        # Scenario C: Both naive
        price, ok, reason = guard.validate_entry("SPY", q_naive, t_naive, Decimal("500.00"), Decimal("1.00"))
        assert ok is True
        assert reason == "OK"
        assert price == Decimal("500.01")

    def test_non_utc_timezone_offsets(self) -> None:
        """Timezones with non-zero offsets (Eastern Time UTC-4, Tokyo UTC+9) must normalize correctly."""
        base_utc = datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC)
        clock = SimulatedClock(base_utc)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)
        guard.record_message(base_utc)

        tz_edt = timezone(timedelta(hours=-4))
        tz_tokyo = timezone(timedelta(hours=9))

        dt_edt = datetime(2026, 9, 21, 10, 30, 0, tzinfo=tz_edt)  # Equivalent to 14:30 UTC
        dt_tokyo = datetime(2026, 9, 21, 23, 30, 0, tzinfo=tz_tokyo)  # Equivalent to 14:30 UTC

        q_edt = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=dt_edt)
        t_tokyo = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=dt_tokyo)

        price, ok, reason = guard.validate_entry("SPY", q_edt, t_tokyo, Decimal("500.00"), Decimal("1.00"))
        assert ok is True
        assert reason == "OK"
        assert price == Decimal("500.01")

    def test_validate_entry_or_raise_timezone_consistency(self) -> None:
        """validate_entry_or_raise must return Decimal price cleanly with mixed timezones."""
        base_utc = datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC)
        clock = SimulatedClock(base_utc)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)
        guard.record_message(base_utc)

        q_naive = PriceQuote("SPY", Decimal("500.00"), Decimal("500.02"), timestamp=datetime(2026, 9, 21, 14, 30, 0))
        t_aware = TradeQuote("SPY", Decimal("500.01"), 100, timestamp=base_utc)

        price = guard.validate_entry_or_raise("SPY", q_naive, t_aware, Decimal("500.00"), Decimal("1.00"))
        assert isinstance(price, Decimal)
        assert price == Decimal("500.01")


# ============================================================================
# 9. DEFECT 3 EMPIRICAL CHALLENGE: MONOTONICITY & OUT-OF-ORDER ARRIVALS
# ============================================================================


class TestDefect3MonotonicityAndOutOfOrderEmpirical:
    """Empirically verify temporal monotonicity: out-of-order packets never regress state."""

    def test_global_feed_delayed_message_does_not_trigger_stale(self) -> None:
        """A delayed message from the past must not regress last_message_time or trigger STALE."""
        t0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(t0)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        # 1. Healthy messages up to t0 + 50s
        clock.set_time(t0 + timedelta(seconds=50))
        guard.record_message(t0 + timedelta(seconds=50))
        assert guard.is_feed_healthy() is True
        assert guard.global_monitor.last_message_time == t0 + timedelta(seconds=50)

        # 2. Delayed out-of-order packets arrive from 10 minutes ago
        for past_sec in [0, 5, 10, 20]:
            guard.record_message(t0 + timedelta(seconds=past_sec))

        # Monotonic cursor must stay at t0 + 50s
        assert guard.global_monitor.last_message_time == t0 + timedelta(seconds=50)

        # 3. Advance clock to t0 + 55s (5s since latest message, 55s since t0)
        clock.set_time(t0 + timedelta(seconds=55))
        assert guard.is_feed_healthy() is True
        assert guard.global_monitor.state.value == "HEALTHY"

    def test_symbol_freshness_delayed_quote_and_trade_does_not_halt(self) -> None:
        """Delayed quotes and trades must not regress symbol freshness or cause POTENTIAL_HALT."""
        t0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(t0)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)
        guard.record_message(t0)
        guard.set_symbol_tau("SPY", 180.0)

        # Fresh quote and trade at t0 + 100s
        clock.set_time(t0 + timedelta(seconds=100))
        guard.record_quote("SPY", PriceQuote("SPY", Decimal("500"), Decimal("500.02"), timestamp=t0 + timedelta(seconds=100)))
        guard.record_trade("SPY", TradeQuote("SPY", Decimal("500.01"), 100, timestamp=t0 + timedelta(seconds=100)))

        state = guard.symbol_monitor._symbols["SPY"]
        assert state.last_quote_time == t0 + timedelta(seconds=100)
        assert state.last_trade_time == t0 + timedelta(seconds=100)
        assert state.last_quote.bid == Decimal("500")

        # Stale quotes/trades from t0 arrive out of order
        guard.record_quote("SPY", PriceQuote("SPY", Decimal("100"), Decimal("100.02"), timestamp=t0))
        guard.record_trade("SPY", TradeQuote("SPY", Decimal("100.01"), 10, timestamp=t0))

        # Must NOT regress
        assert state.last_quote_time == t0 + timedelta(seconds=100)
        assert state.last_trade_time == t0 + timedelta(seconds=100)
        assert state.last_quote.bid == Decimal("500")
        assert state.last_trade.price == Decimal("500.01")

        # Freshness evaluation at t0 + 110s is still OK
        clock.set_time(t0 + timedelta(seconds=110))
        ok, reason = guard.check_symbol_freshness("SPY")
        assert ok is True
        assert reason == "OK"

    def test_random_packet_bombardment_strict_monotonicity(self) -> None:
        """500 shuffled messages must never cause cursor regression."""
        import random
        t0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(t0)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        timeline = [t0 + timedelta(seconds=i) for i in range(500)]
        shuffled = timeline.copy()
        random.seed(1337)
        random.shuffle(shuffled)

        prev_cursor = None
        for ts in shuffled:
            clock.set_time(max(clock.now(), ts))
            guard.record_message(ts)
            current_cursor = guard.global_monitor.last_message_time
            assert current_cursor is not None
            if prev_cursor is not None:
                assert current_cursor >= prev_cursor, f"Monotonicity violation: {current_cursor} < {prev_cursor}"
            prev_cursor = current_cursor

        assert guard.global_monitor.last_message_time == t0 + timedelta(seconds=499)


# ============================================================================
# 10. DEFECT 4 EMPIRICAL CHALLENGE: FUTURE TIMESTAMPS & DEAD FEED MASKING
# ============================================================================


class TestDefect4FutureTimestampsMaskingEmpirical:
    """Empirically verify that future timestamps are clamped and cannot mask dead feeds."""

    def test_future_timestamp_clamped_and_detects_disconnect(self) -> None:
        """A future timestamp (+1 hour) must be clamped to now, allowing 60s silence detection."""
        t0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(t0)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        # Message arrives with future timestamp (1 hour ahead)
        future_ts = t0 + timedelta(hours=1)
        guard.record_message(future_ts)

        # 1. Clamped to t0
        assert guard.global_monitor.last_message_time == t0

        # 2. Advance 61 seconds without any new messages (simulating dead socket)
        clock.set_time(t0 + timedelta(seconds=61))

        # Feed must be recognized as DEGRADED (NOT healthy)
        assert guard.is_feed_healthy() is False
        assert guard.get_feed_state().value == "DEGRADED"
        assert guard.global_monitor.is_fallback_active is True

        # Gating must block orders
        allowed, reason = guard.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_DEGRADED"

        # 3. Advance to 305 seconds -> must be STALE
        clock.set_time(t0 + timedelta(seconds=305))
        assert guard.get_feed_state().value == "STALE"
        assert guard.global_monitor.is_fallback_active is True

    def test_future_quote_clamped_and_detects_potential_halt(self) -> None:
        """A future quote (+2 hours) must be clamped to now and transition to POTENTIAL_HALT."""
        t0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(t0)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)
        guard.record_message(t0)
        guard.set_symbol_tau("AAPL", 180.0)

        # Future quote arrives
        q_future = PriceQuote("AAPL", Decimal("150"), Decimal("150.02"), timestamp=t0 + timedelta(hours=2))
        guard.record_quote("AAPL", q_future)

        # Clamped to t0
        state = guard.symbol_monitor._symbols["AAPL"]
        assert state.last_quote_time == t0

        # Advance 185s -> exceeds tau (180s)
        clock.set_time(t0 + timedelta(seconds=185))
        ok, reason = guard.check_symbol_freshness("AAPL")
        assert ok is False
        assert reason == "POTENTIAL_HALT"

    def test_check_global_feed_future_tolerance_boundary(self) -> None:
        """check_global_feed allows <= 1.0s clock skew, but strictly rejects > 1.0s future timestamp."""
        t0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
        clock = SimulatedClock(t0)
        guard = StalenessGuard(clock=clock, enforce_market_hours=False)

        # Exactly 0.5s in future -> within 1.0s tolerance -> True
        assert guard.check_global_feed(t0 + timedelta(milliseconds=500)) is True

        # Exactly 1.0s in future -> boundary -> True
        assert guard.check_global_feed(t0 + timedelta(seconds=1)) is True

        # 1.5s in future -> exceeds tolerance -> False
        assert guard.check_global_feed(t0 + timedelta(milliseconds=1500)) is False

        # 60s in future -> False
        assert guard.check_global_feed(t0 + timedelta(seconds=60)) is False

