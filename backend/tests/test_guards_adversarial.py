"""Empirical adversarial stress tests against backend/tbot/backtest/guards.py.

Designed by m0_challenger_1 (teamwork_preview_challenger) to exhaustively probe:
1. Inverted date intervals (start > end) across types and boundary conditions.
2. Mixed type inputs (date, datetime, pd.Timestamp, str, ISO offsets, invalid types).
3. Exact boundary touching (start/end on exact holdout limits, 1-day offsets, single-day windows).
4. Datetime inputs crossing UTC midnight vs ET midnight and timezone-aware objects.
5. All 7 topological interval relationships (disjoint left/right, partial overlaps, sub/super/exact).
6. Concurrency and ContextVar leak safety across OS threads, asyncio tasks, and nested contexts.
7. Fuzzing, resolution alias spoofing, and environment unseal integrity.
"""

from __future__ import annotations

import asyncio
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
import pytz

from tbot.backtest.guards import (
    SAMPLE_5M_END,
    SAMPLE_5M_START,
    HoldoutViolationError,
    assert_not_holdout,
    holdout_bypass_context,
    is_holdout_unsealed,
    is_synthetic_bypassed,
    normalize_resolution,
)


# =====================================================================
# 1. Inverted Date Intervals (start > end)
# =====================================================================
class TestAdversarialInvertedIntervals:
    """Stress test inverted interval rejection across types and boundary contexts."""

    def test_inverted_strings(self):
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout("2022-06-01", "2021-06-01")

    def test_inverted_date_objects(self):
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout(date(2022, 6, 1), date(2021, 6, 1))

    def test_inverted_datetimes(self):
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout(datetime(2022, 6, 2, 10, 0), datetime(2022, 6, 1, 9, 0))

    def test_same_day_intraday_datetimes_evaluated_at_date_granularity(self):
        """Document that guards evaluate at date granularity:
        datetime(2022, 6, 1, 16, 0) and datetime(2022, 6, 1, 9, 0) both convert to date(2022, 6, 1).
        In the dev window, this is treated as a valid single-day range date(2022, 6, 1) -> date(2022, 6, 1).
        In the holdout window, this triggers HoldoutViolationError fail-closed."""
        # Dev window: passes as date(2022, 6, 1)
        assert_not_holdout(datetime(2022, 6, 1, 16, 0), datetime(2022, 6, 1, 9, 0))

        # Holdout window: triggers HoldoutViolationError fail-closed
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(datetime(2024, 6, 1, 16, 0), datetime(2024, 6, 1, 9, 0))

    def test_inverted_pandas_timestamps(self):
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout(pd.Timestamp("2022-06-01"), pd.Timestamp("2021-06-01"))

    def test_inverted_mixed_types(self):
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout(pd.Timestamp("2022-06-01"), date(2021, 6, 1))
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout(date(2022, 6, 1), "2021-06-01")

    def test_inverted_within_sealed_holdout_prioritizes_value_error(self):
        """Even if both dates fall squarely inside the sealed holdout, inverted start > end
        must fail fast with ValueError rather than misleadingly reporting a valid holdout intersection."""
        with pytest.raises(ValueError, match="Invalid range: start"):
            assert_not_holdout("2025-06-01", "2024-06-01", resolution="daily")

    def test_inverted_spanning_across_holdout_and_dev(self):
        with pytest.raises(ValueError, match="Invalid range: start"):
            assert_not_holdout("2026-05-01", "2020-01-01", resolution="daily")

    def test_same_day_different_times_does_not_invert_date(self):
        """Intraday datetimes on the same calendar day normalize to start == end (valid 1-day span)."""
        # Dev window: 9:30 to 16:00 on same day
        assert_not_holdout(
            datetime(2021, 5, 10, 9, 30),
            datetime(2021, 5, 10, 16, 0),
            resolution="daily",
        )


# =====================================================================
# 2. Mixed Types and Robustness Against Malicious/Exotic Inputs
# =====================================================================
class TestAdversarialMixedTypesAndFuzzing:
    """Stress test combinations of data types, exotic date strings, and malicious inputs."""

    @pytest.mark.parametrize(
        "start_gen",
        [
            lambda: date(2021, 3, 1),
            lambda: datetime(2021, 3, 1, 9, 30),
            lambda: pd.Timestamp("2021-03-01"),
            lambda: "2021-03-01",
            lambda: "  2021-03-01  ",
            lambda: "2021/03/01",
        ],
    )
    @pytest.mark.parametrize(
        "end_gen",
        [
            lambda: date(2021, 8, 1),
            lambda: datetime(2021, 8, 1, 16, 0),
            lambda: pd.Timestamp("2021-08-01"),
            lambda: "2021-08-01",
            lambda: "  2021-08-01  ",
            lambda: "2021/08/01",
        ],
    )
    def test_full_cross_type_matrix_dev_window_passes(self, start_gen, end_gen):
        """All valid representations of 2021-03-01 to 2021-08-01 must pass cleanly."""
        assert_not_holdout(start_gen(), end_gen(), resolution="daily")

    @pytest.mark.parametrize(
        "start_gen",
        [
            lambda: date(2024, 3, 1),
            lambda: datetime(2024, 3, 1, 9, 30),
            lambda: pd.Timestamp("2024-03-01"),
            lambda: "2024-03-01",
            lambda: "2024/03/01",
        ],
    )
    @pytest.mark.parametrize(
        "end_gen",
        [
            lambda: date(2024, 8, 1),
            lambda: datetime(2024, 8, 1, 16, 0),
            lambda: pd.Timestamp("2024-08-01"),
            lambda: "2024-08-01",
            lambda: "2024/08/01",
        ],
    )
    def test_full_cross_type_matrix_holdout_window_raises(self, start_gen, end_gen):
        """All representations of 2024-03-01 to 2024-08-01 must trigger HoldoutViolationError."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(start_gen(), end_gen(), resolution="daily")

    def test_numpy_datetime64_raises_type_error_fail_closed(self):
        """np.datetime64 lacks .date() and must be rejected fail-closed with TypeError."""
        with pytest.raises(TypeError, match="Expected date, datetime, or str"):
            assert_not_holdout(np.datetime64("2024-01-01"), "2024-06-01")

    @pytest.mark.parametrize(
        "bad_input",
        [
            None,
            12345,
            12345.67,
            True,
            False,
            [],
            {},
            object(),
        ],
    )
    def test_unsupported_types_rejected_with_type_error(self, bad_input):
        with pytest.raises(TypeError):
            assert_not_holdout(bad_input, "2021-01-01")
        with pytest.raises(TypeError):
            assert_not_holdout("2021-01-01", bad_input)

    @pytest.mark.parametrize(
        "malicious_str",
        [
            "",
            "   ",
            "not-a-date",
            "2023-02-29",  # Not a leap year!
            "2024-13-01",  # Invalid month
            "2024-04-31",  # April has 30 days
            "2024-01-01; DROP TABLE data;--",
            "<script>alert(1)</script>",
        ],
    )
    def test_malformed_and_malicious_strings_rejected(self, malicious_str):
        with pytest.raises((ValueError, TypeError)):
            assert_not_holdout(malicious_str, "2024-01-01")


# =====================================================================
# 3. Exact Boundary Touching & Single-Day Tests
# =====================================================================
class TestAdversarialExactBoundaries:
    """Stress test exact boundary coordinates and 1-day offsets."""

    # Daily Holdout: [2023-01-01, 2026-02-27]
    def test_daily_exact_start_single_day(self):
        """Exact first day of daily holdout must trigger violation."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2023-01-01", "2023-01-01", resolution="daily")
        assert "intersects sealed holdout" in str(exc.value)
        assert "[2023-01-01 -> 2023-01-01]" in str(exc.value)

    def test_daily_exact_end_single_day(self):
        """Exact last day of daily holdout must trigger violation."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2026-02-27", "2026-02-27", resolution="daily")
        assert "intersects sealed holdout" in str(exc.value)
        assert "[2026-02-27 -> 2026-02-27]" in str(exc.value)

    def test_daily_single_day_one_before_start_passes(self):
        """Day immediately preceding daily holdout (2022-12-31) must pass."""
        assert_not_holdout("2022-12-31", "2022-12-31", resolution="daily")

    def test_daily_single_day_one_after_end_passes(self):
        """Day immediately following daily holdout (2026-02-28) must pass."""
        assert_not_holdout("2026-02-28", "2026-02-28", resolution="daily")

    def test_daily_left_boundary_exact_touch_raises(self):
        """Range ending exactly on first holdout day."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2022-12-01", "2023-01-01", resolution="daily")
        assert "at [2023-01-01 -> 2023-01-01]" in str(exc.value)

    def test_daily_right_boundary_exact_touch_raises(self):
        """Range starting exactly on last holdout day."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2026-02-27", "2026-03-31", resolution="daily")
        assert "at [2026-02-27 -> 2026-02-27]" in str(exc.value)

    def test_daily_leap_year_day_inside_holdout(self):
        """2024-02-29 is a valid leap day inside daily holdout and must raise."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2024-02-29", "2024-02-29", resolution="daily")

    # Hourly Holdout: [2025-09-22, 2026-09-21]
    def test_hourly_exact_start_single_day(self):
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2025-09-22", "2025-09-22", resolution="hourly")
        assert "[2025-09-22 -> 2025-09-22]" in str(exc.value)

    def test_hourly_exact_end_single_day(self):
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2026-09-21", "2026-09-21", resolution="hourly")
        assert "[2026-09-21 -> 2026-09-21]" in str(exc.value)

    def test_hourly_single_day_one_before_start_passes(self):
        """2025-09-21 is the final day of hourly dev window and must pass."""
        assert_not_holdout("2025-09-21", "2025-09-21", resolution="hourly")

    def test_hourly_single_day_one_after_end_passes(self):
        """2026-09-22 is post-hourly-holdout and must pass."""
        assert_not_holdout("2026-09-22", "2026-09-22", resolution="hourly")

    def test_hourly_left_touch_raises(self):
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2025-09-15", "2025-09-22", resolution="hourly")

    def test_hourly_right_touch_raises(self):
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2026-09-21", "2026-09-30", resolution="hourly")


# =====================================================================
# 4. Timezone Aware Datetimes and Midnight Crossing
# =====================================================================
class TestAdversarialTimezonesAndMidnight:
    """Stress test timezone offsets, UTC vs ET midnight crossings, and aware datetimes."""

    def test_utc_datetime_crossing_midnight_fails_closed(self):
        """A UTC datetime at 2023-01-01 02:00:00 UTC (which corresponds to 2022-12-31 21:00:00 EST)
        normalizes by date() to 2023-01-01 and is blocked fail-closed."""
        dt_utc = datetime(2023, 1, 1, 2, 0, tzinfo=UTC)
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(dt_utc, "2023-06-01", resolution="daily")

    def test_et_aware_datetime_in_dev_window_passes(self):
        """America/New_York aware datetime on 2022-12-31 passes dev window."""
        eastern = pytz.timezone("America/New_York")
        dt_et = eastern.localize(datetime(2022, 12, 31, 23, 59))
        assert_not_holdout("2022-01-01", dt_et, resolution="daily")

    def test_zoneinfo_utc_datetime(self):
        """Python standard library zoneinfo.ZoneInfo support."""
        zi_utc = zoneinfo.ZoneInfo("UTC")
        dt_zi = datetime(2024, 6, 15, 12, 0, tzinfo=zi_utc)
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(dt_zi, dt_zi, resolution="daily")

    def test_iso_string_with_explicit_timezone_offsets(self):
        # 2023-01-01T00:00:00Z -> Holdout start
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2023-01-01T00:00:00Z", "2023-01-05T00:00:00Z", resolution="daily")

        # 2022-12-31T23:59:59-05:00 -> Dev end
        assert_not_holdout(
            "2022-01-01T00:00:00-05:00",
            "2022-12-31T23:59:59-05:00",
            resolution="daily",
        )

    def test_holdout_end_last_second_boundary(self):
        """2026-02-27 23:59:59 is inside holdout; 2026-02-28 00:00:00 is outside."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout(
                datetime(2026, 2, 27, 23, 59, 59),
                datetime(2026, 2, 27, 23, 59, 59),
                resolution="daily",
            )
        assert_not_holdout(
            datetime(2026, 2, 28, 0, 0, 0),
            datetime(2026, 3, 1, 0, 0, 0),
            resolution="daily",
        )


# =====================================================================
# 5. Exhaustive Interval Topology Verification
# =====================================================================
class TestAdversarialIntervalTopologies:
    """Verify all 7 topological interval relationships between [S, E] and holdout [H_start, H_end]."""

    # Holdout Daily: [2023-01-01, 2026-02-27]
    def test_topology_1_disjoint_left(self):
        """[S, E] < [H_start, H_end]: Completely before holdout."""
        assert_not_holdout("2020-01-01", "2022-12-31", resolution="daily")

    def test_topology_2_partial_left_overlap(self):
        """S < H_start <= E < H_end: Starts before, penetrates holdout."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2022-06-01", "2024-06-01", resolution="daily")
        assert "at [2023-01-01 -> 2024-06-01]" in str(exc.value)

    def test_topology_3_exact_match(self):
        """S == H_start and E == H_end: Exactly matches holdout."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2023-01-01", "2026-02-27", resolution="daily")
        assert "at [2023-01-01 -> 2026-02-27]" in str(exc.value)

    def test_topology_4_sub_interval_interior(self):
        """H_start < S <= E < H_end: Deep interior subset of holdout."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2024-01-01", "2025-01-01", resolution="daily")
        assert "at [2024-01-01 -> 2025-01-01]" in str(exc.value)

    def test_topology_5_super_interval(self):
        """S < H_start and E > H_end: Spans across entire holdout and beyond."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2020-01-01", "2026-05-01", resolution="daily")
        assert "at [2023-01-01 -> 2026-02-27]" in str(exc.value)

    def test_topology_6_partial_right_overlap(self):
        """H_start < S <= H_end < E: Starts inside, extends beyond holdout."""
        with pytest.raises(HoldoutViolationError) as exc:
            assert_not_holdout("2025-01-01", "2026-05-01", resolution="daily")
        assert "at [2025-01-01 -> 2026-02-27]" in str(exc.value)

    def test_topology_7_disjoint_right(self):
        """[H_start, H_end] < [S, E]: Completely after holdout."""
        assert_not_holdout("2026-02-28", "2026-12-31", resolution="daily")


# =====================================================================
# 6. Concurrency, Multi-Threading, Async, and ContextVar Isolation
# =====================================================================
class TestAdversarialConcurrencyAndContextVarSafety:
    """Stress test thread-safety, task-safety, nested contexts, and exception recovery."""

    def test_multi_threaded_contextvar_isolation_stress(self):
        """Concurrent threads: 5 bypassing and 5 non-bypassing running concurrently.
        Bypassing threads MUST NOT leak bypass to non-bypassing threads.
        Non-bypassing threads MUST NOT affect bypassing threads."""
        errors: list[str] = []

        def worker_bypassed(worker_id: int):
            for _ in range(50):
                with holdout_bypass_context(True):
                    try:
                        assert_not_holdout("2024-01-01", "2024-06-01", resolution="daily")
                    except Exception as e:
                        errors.append(f"Worker bypassed {worker_id} failed: {e}")

        def worker_strict(worker_id: int):
            for _ in range(50):
                try:
                    assert_not_holdout("2024-01-01", "2024-06-01", resolution="daily")
                    errors.append(f"Worker strict {worker_id} LEAKED holdout bypass!")
                except HoldoutViolationError:
                    pass  # Expected
                except Exception as e:
                    errors.append(f"Worker strict {worker_id} got unexpected error: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for i in range(5):
                futures.append(executor.submit(worker_bypassed, i))
                futures.append(executor.submit(worker_strict, i))
            for f in futures:
                f.result()

        assert not errors, f"Concurrency isolation failures detected: {errors}"
        # Ensure main thread state remained uncontaminated
        assert not is_synthetic_bypassed()

    @pytest.mark.asyncio
    async def test_asyncio_task_isolation(self):
        """Async tasks running in parallel event loop must isolate ContextVar."""
        results: dict[str, bool] = {}

        async def task_bypassed():
            with holdout_bypass_context(True):
                await asyncio.sleep(0.01)
                assert_not_holdout("2024-01-01", "2024-06-01", resolution="daily")
                results["task_bypassed"] = True

        async def task_strict():
            await asyncio.sleep(0.005)
            try:
                assert_not_holdout("2024-01-01", "2024-06-01", resolution="daily")
                results["task_strict"] = False  # Should NOT succeed
            except HoldoutViolationError:
                results["task_strict"] = True  # Correctly rejected

        await asyncio.gather(task_bypassed(), task_strict())
        assert results.get("task_bypassed") is True
        assert results.get("task_strict") is True
        assert not is_synthetic_bypassed()

    def test_nested_context_manager_token_restoration(self):
        """ContextVar tokens must cleanly restore across arbitrary nesting depths."""
        assert not is_synthetic_bypassed()

        with holdout_bypass_context(True):
            assert is_synthetic_bypassed()
            assert_not_holdout("2024-01-01", "2024-06-01")

            # Nested re-enable
            with holdout_bypass_context(False):
                assert not is_synthetic_bypassed()
                with pytest.raises(HoldoutViolationError):
                    assert_not_holdout("2024-01-01", "2024-06-01")

            # Restored to True
            assert is_synthetic_bypassed()
            assert_not_holdout("2024-01-01", "2024-06-01")

        # Restored to False
        assert not is_synthetic_bypassed()
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2024-01-01", "2024-06-01")

    def test_exception_inside_context_does_not_leak_bypass(self):
        """If an exception crashes out of the bypass context, finally clause resets token."""
        assert not is_synthetic_bypassed()

        try:
            with holdout_bypass_context(True):
                assert is_synthetic_bypassed()
                raise RuntimeError("Simulated test explosion")
        except RuntimeError:
            pass

        assert not is_synthetic_bypassed()
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2024-01-01", "2024-06-01")


# =====================================================================
# 7. Resolution Normalization Spoofing & Fuzzing
# =====================================================================
class TestAdversarialResolutionAliasing:
    """Stress test resolution canonicalization and spoofing attempts."""

    @pytest.mark.parametrize(
        "res_input,expected",
        [
            ("DAILY", "daily"),
            ("  daily  ", "daily"),
            ("1d", "daily"),
            ("1D", "daily"),
            ("  1D  ", "daily"),
            ("day", "daily"),
            ("DAY", "daily"),
            ("HOURLY", "hourly"),
            ("  hourly  ", "hourly"),
            ("1h", "hourly"),
            ("1H", "hourly"),
            ("hour", "hourly"),
            ("5m", "5m"),
            ("5M", "5m"),
            ("5min", "5m"),
            ("minute", "5m"),
            ("intraday_5m", "5m"),
        ],
    )
    def test_valid_resolution_aliases(self, res_input, expected):
        assert normalize_resolution(res_input) == expected

    @pytest.mark.parametrize(
        "invalid_res",
        [
            "",
            "   ",
            "weekly",
            "monthly",
            "quarterly",
            "yearly",
            "1s",
            "tick",
            "random_string",
        ],
    )
    def test_invalid_resolutions_raise_value_error(self, invalid_res):
        with pytest.raises(ValueError, match="Unknown resolution"):
            normalize_resolution(invalid_res)


# =====================================================================
# 8. Unseal Flag Auditing and Security
# =====================================================================
class TestAdversarialUnsealSecurity:
    """Stress test the TBOT_UNSEAL_HOLDOUT environment mechanism."""

    @pytest.mark.parametrize("falsy_val", ["", "0", "false", "FALSE", "no", "NO", "unseal", "2"])
    def test_falsy_unseal_env_keeps_guards_sealed(self, monkeypatch, falsy_val):
        monkeypatch.setenv("TBOT_UNSEAL_HOLDOUT", falsy_val)
        assert not is_holdout_unsealed()
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2024-01-01", "2024-06-01", resolution="daily")

    @pytest.mark.parametrize("truthy_val", ["1", "true", "TRUE", "yes"])
    def test_truthy_unseal_env_permits_access(self, monkeypatch, truthy_val):
        monkeypatch.setenv("TBOT_UNSEAL_HOLDOUT", truthy_val)
        assert is_holdout_unsealed()
        # Should pass without raising
        assert_not_holdout("2024-01-01", "2024-06-01", resolution="daily")


# =====================================================================
# 9. Adversarial 5m Resolution Holdout Probes & Spoofing
# =====================================================================
class TestAdversarial5mResolutionGuards:
    """Adversarial stress testing of 5m resolution boundary enforcement."""

    @pytest.mark.parametrize(
        "holdout_range",
        [
            ("2023-01-01", "2026-02-27"),  # Exact daily holdout
            ("2024-01-01", "2024-12-31"),  # Interior daily holdout
            ("2025-01-01", "2025-06-01"),  # 2025 holdout
            ("2022-12-15", "2023-01-15"),  # Left boundary overlap
            ("2026-02-20", "2026-03-10"),  # Right boundary overlap
            ("2020-01-01", "2026-05-01"),  # Super-interval
        ],
    )
    def test_5m_cannot_penetrate_daily_holdout(self, holdout_range):
        start_d, end_d = holdout_range
        with pytest.raises(HoldoutViolationError, match="intersects sealed daily holdout"):
            assert_not_holdout(start_d, end_d, resolution="5m")

    @pytest.mark.parametrize(
        "res_alias",
        ["5m", "5M", "5min", "minute", "intraday_5m"],
    )
    def test_5m_aliases_all_enforce_daily_holdout(self, res_alias):
        with pytest.raises(HoldoutViolationError, match="intersects sealed daily holdout"):
            assert_not_holdout("2025-01-01", "2025-12-31", resolution=res_alias)

    def test_5m_sample_subintervals_pass(self):
        # Exact bounds
        assert_not_holdout(SAMPLE_5M_START, SAMPLE_5M_END, resolution="5m")
        # Sub-intervals
        assert_not_holdout("2026-07-01", "2026-07-31", resolution="5m")
        assert_not_holdout("2026-08-01", "2026-08-01", resolution="5m")
        assert_not_holdout("2026-09-01", "2026-09-21", resolution="5m")

    def test_5m_outside_sample_hourly_holdout_boundary_touch(self):
        # 2026-06-25 is 1 day before sample start and inside hourly holdout
        with pytest.raises(HoldoutViolationError, match="intersects sealed hourly holdout"):
            assert_not_holdout("2026-06-25", "2026-06-25", resolution="5m")

