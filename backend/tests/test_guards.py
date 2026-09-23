"""Unit tests for holdout seal execution guards and interval logic (R0 / Milestone M0)."""

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.guards import (
    DEV_DAILY_END,
    DEV_DAILY_START,
    DEV_HOURLY_END,
    DEV_HOURLY_START,
    SAMPLE_5M_END,
    SAMPLE_5M_START,
    HoldoutViolationError,
    assert_not_holdout,
    holdout_bypass_context,
    normalize_resolution,
)


class TestHoldoutViolationErrorHierarchy:
    def test_subclass_relationships(self):
        assert issubclass(HoldoutViolationError, PermissionError)
        assert issubclass(HoldoutViolationError, ValueError)

    def test_caught_by_permission_error(self):
        with pytest.raises(PermissionError):
            raise HoldoutViolationError("test permission")

    def test_caught_by_value_error(self):
        with pytest.raises(ValueError):
            raise HoldoutViolationError("test value")


class TestResolutionNormalization:
    def test_daily_aliases(self):
        for alias in ["daily", "DAILY", "1d", "1D", "d", "day"]:
            assert normalize_resolution(alias) == "daily"

    def test_hourly_aliases(self):
        for alias in ["hourly", "HOURLY", "1h", "1H", "h", "hour"]:
            assert normalize_resolution(alias) == "hourly"

    def test_5m_aliases(self):
        for alias in ["5m", "5M", "5min", "minute", "intraday_5m"]:
            assert normalize_resolution(alias) == "5m"

    def test_unknown_resolution_raises(self):
        with pytest.raises(ValueError, match="Unknown resolution"):
            normalize_resolution("weekly")


class TestDailyHoldoutGuards:
    def test_dev_window_cleanly_passes(self):
        assert_not_holdout("2010-01-01", "2022-12-31", resolution="daily")
        assert_not_holdout(DEV_DAILY_START, DEV_DAILY_END, resolution="daily")
        assert_not_holdout("2015-06-01", "2018-06-01", resolution="daily")

    def test_full_holdout_violation_raises(self):
        with pytest.raises(HoldoutViolationError) as exc_info:
            assert_not_holdout("2023-01-01", "2026-02-27", resolution="daily")
        assert "intersects sealed holdout" in str(exc_info.value)
        assert "2023-01-01" in str(exc_info.value)
        assert "2026-02-27" in str(exc_info.value)

    def test_holdout_start_boundary_overlap(self):
        # Starts in dev, touches holdout day 1
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2022-12-01", "2023-01-01", resolution="daily")

    def test_holdout_end_boundary_overlap(self):
        # Starts on last day of holdout, ends after
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2026-02-27", "2026-04-01", resolution="daily")

    def test_super_interval_overlap(self):
        # Spans before holdout to after holdout
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2022-01-01", "2026-05-01", resolution="daily")

    def test_sub_interval_interior_overlap(self):
        # Deep interior of holdout
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2024-03-01", "2024-09-01", resolution="daily")

    def test_post_holdout_daily_allowed(self):
        # 2026-02-28 onwards is past the daily holdout end
        assert_not_holdout("2026-02-28", "2026-06-01", resolution="daily")


class TestHourlyHoldoutGuards:
    def test_hourly_dev_window_passes(self):
        assert_not_holdout("2023-10-23", "2025-09-21", resolution="hourly")
        assert_not_holdout(DEV_HOURLY_START, DEV_HOURLY_END, resolution="hourly")

    def test_hourly_holdout_raises(self):
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2025-09-22", "2026-09-21", resolution="hourly")

    def test_hourly_boundary_touch_raises(self):
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2025-09-01", "2025-09-22", resolution="hourly")

    def test_hourly_post_holdout_allowed(self):
        assert_not_holdout("2026-09-22", "2026-10-01", resolution="hourly")


class TestResolutionDiscrimination:
    def test_2024_range_fails_daily_passes_hourly(self):
        # 2024 is in Daily holdout, but in Hourly dev window
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2024-01-01", "2024-12-31", resolution="daily")

        # Must pass cleanly when resolution is hourly
        assert_not_holdout("2024-01-01", "2024-12-31", resolution="hourly")

    def test_5m_sample_passes(self):
        # 5m data is a designated non-holdout sample
        assert_not_holdout("2026-06-26", "2026-09-21", resolution="5m")


class TestInputTypeHandling:
    def test_date_types(self):
        assert_not_holdout(date(2020, 1, 1), date(2020, 12, 31))

    def test_datetime_types(self):
        assert_not_holdout(datetime(2020, 1, 1, 9, 30), datetime(2020, 12, 31, 16, 0))

    def test_pandas_timestamp_types(self):
        assert_not_holdout(pd.Timestamp("2020-01-01"), pd.Timestamp("2020-12-31"))

    def test_str_iso_types(self):
        assert_not_holdout("2020-01-01", "2020-12-31")

    def test_mixed_types(self):
        assert_not_holdout("2020-01-01", date(2020, 12, 31))
        assert_not_holdout(datetime(2020, 1, 1), "2020-12-31")


class TestInvalidInputs:
    def test_start_after_end_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid range: start"):
            assert_not_holdout("2022-01-01", "2021-01-01")

    def test_invalid_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Expected date, datetime, or str"):
            assert_not_holdout(12345, "2022-01-01")  # type: ignore


class TestSyntheticDataAndBypass:
    def test_allow_synthetic_argument(self):
        # In holdout, but allow_synthetic=True prevents raise
        assert_not_holdout("2025-01-01", "2025-12-31", resolution="daily", allow_synthetic=True)

    def test_bypass_context_manager(self):
        # Outside context: raises
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2025-01-01", "2025-12-31", resolution="daily")

        # Inside context: passes
        with holdout_bypass_context():
            assert_not_holdout("2025-01-01", "2025-12-31", resolution="daily")

        # Outside context again: raises
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2025-01-01", "2025-12-31", resolution="daily")

    def test_unseal_env_var(self, monkeypatch):
        monkeypatch.setenv("TBOT_UNSEAL_HOLDOUT", "1")
        # Now holdout passes because environment explicitly unsealed it for Phase 6
        assert_not_holdout("2025-01-01", "2025-12-31", resolution="daily")


class TestIntraday5mHoldoutGuards:
    """Dedicated regression tests for Vulnerability 1 (5m bypass in assert_not_holdout)."""

    def test_5m_within_sanctioned_window_passes(self):
        """Requests within [SAMPLE_5M_START, SAMPLE_5M_END] pass cleanly."""
        assert_not_holdout("2026-06-26", "2026-09-21", resolution="5m")
        assert_not_holdout(SAMPLE_5M_START, SAMPLE_5M_END, resolution="5m")
        assert_not_holdout("2026-07-01", "2026-08-31", resolution="5m")
        assert_not_holdout("2026-08-15", "2026-08-15", resolution="5m")

    def test_5m_daily_holdout_violation_raises(self):
        """assert_not_holdout('2025-01-01', '2025-12-31', resolution='5m') must raise HoldoutViolationError."""
        with pytest.raises(HoldoutViolationError) as exc_info:
            assert_not_holdout("2025-01-01", "2025-12-31", resolution="5m")
        assert "intersects sealed daily holdout" in str(exc_info.value)
        assert "2025-01-01" in str(exc_info.value)
        assert "2025-12-31" in str(exc_info.value)

    def test_5m_daily_holdout_boundaries_raise(self):
        """Boundary and spanning holdout ranges with resolution='5m' must raise HoldoutViolationError."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2022-12-01", "2023-01-01", resolution="5m")

        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2023-01-01", "2023-12-31", resolution="5m")

        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2026-02-27", "2026-03-31", resolution="5m")

        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2020-01-01", "2026-05-01", resolution="5m")

    def test_5m_resolution_aliases_enforce_guard(self):
        """All canonical 5m aliases must enforce holdout."""
        for alias in ["5m", "5M", "5min", "minute", "intraday_5m"]:
            with pytest.raises(HoldoutViolationError):
                assert_not_holdout("2025-01-01", "2025-12-31", resolution=alias)

    def test_5m_dev_window_passes(self):
        """5m requests wholly within daily development window (2010-01-01 to 2022-12-31) pass."""
        assert_not_holdout("2010-01-01", "2022-12-31", resolution="5m")
        assert_not_holdout("2020-01-01", "2021-12-31", resolution="5m")

    def test_5m_outside_sample_hourly_holdout_raises(self):
        """Requests outside the sanctioned 5m sample that intersect hourly holdout must raise."""
        with pytest.raises(HoldoutViolationError):
            assert_not_holdout("2026-06-25", "2026-06-25", resolution="5m")

    def test_5m_inverted_range_raises_value_error(self):
        """Inverted dates with resolution='5m' must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid range: start.*must be <= end"):
            assert_not_holdout("2026-09-21", "2026-06-26", resolution="5m")


class TestDataLoaderResolutionDerivation:
    """Regression tests for file-level resolution derivation."""

    @pytest.fixture
    def loader(self):
        return HistoricalDataLoader()

    def test_detect_file_resolution_suffixes(self, loader):
        assert loader.detect_file_resolution("SPY_daily.csv") == "daily"
        assert loader.detect_file_resolution("SPY_1d.csv") == "daily"
        assert loader.detect_file_resolution("SPY_day.csv") == "daily"
        assert loader.detect_file_resolution("SPY_1h.csv") == "hourly"
        assert loader.detect_file_resolution("SPY_1hour.csv") == "hourly"
        assert loader.detect_file_resolution("SPY_hourly.csv") == "hourly"
        assert loader.detect_file_resolution("SPY_5m.csv") == "5m"
        assert loader.detect_file_resolution("SPY_5min.csv") == "5m"

    def test_detect_file_resolution_directory_context(self, loader):
        assert loader.detect_file_resolution("data/intraday_1h/AAPL.csv") == "hourly"
        assert loader.detect_file_resolution("data/intraday_5m/AAPL.csv") == "5m"
        assert loader.detect_file_resolution("data/universe_a/BIL.csv") == "daily"
        assert loader.detect_file_resolution("data/historical_2010_2026/SPY.csv") == "daily"
        assert loader.detect_file_resolution("temp/custom_data.csv") is None


class TestDataLoaderResolutionSpoofingPrevention:
    """Regression tests for resolution spoofing prevention in HistoricalDataLoader."""

    @pytest.fixture
    def loader(self):
        return HistoricalDataLoader()

    def test_daily_file_with_hourly_resolution_raises(self, loader):
        with pytest.raises(ValueError, match="Resolution mismatch.*inherent file resolution is 'daily'"):
            loader.load_from_csv(
                "data/historical_2010_2026/SPY_daily.csv",
                symbol="SPY",
                start_date="2023-01-01",
                end_date="2023-06-01",
                resolution="hourly",
            )

    def test_daily_file_with_5m_resolution_raises(self, loader):
        with pytest.raises(ValueError, match="Resolution mismatch.*inherent file resolution is 'daily'"):
            loader.load_from_csv(
                "data/historical_2010_2026/SPY_daily.csv",
                symbol="SPY",
                start_date="2025-01-01",
                end_date="2025-12-31",
                resolution="5m",
            )

    def test_daily_file_no_dates_with_hourly_resolution_raises(self, loader):
        with pytest.raises(ValueError, match="Resolution mismatch"):
            loader.load_from_csv(
                "data/historical_2010_2026/SPY_daily.csv",
                symbol="SPY",
                resolution="hourly",
            )

    def test_daily_file_no_dates_with_5m_resolution_raises(self, loader):
        with pytest.raises(ValueError, match="Resolution mismatch"):
            loader.load_from_csv(
                "data/historical_2010_2026/SPY_daily.csv",
                symbol="SPY",
                resolution="5m",
            )

    def test_hourly_file_with_daily_resolution_raises(self, loader):
        with pytest.raises(ValueError, match="Resolution mismatch.*inherent file resolution is 'hourly'"):
            loader.load_from_csv(
                "data/intraday_1h/SPY_1h.csv",
                symbol="SPY",
                resolution="daily",
            )

    def test_hourly_file_with_5m_resolution_raises(self, loader):
        with pytest.raises(ValueError, match="Resolution mismatch.*inherent file resolution is 'hourly'"):
            loader.load_from_csv(
                "data/intraday_1h/SPY_1h.csv",
                symbol="SPY",
                resolution="5m",
            )

    def test_hourly_file_auto_detects_and_clamps_to_dev_window(self, loader):
        df = loader.load_from_csv("data/intraday_1h/SPY_1h.csv", symbol="SPY")
        assert len(df) > 0
        assert pd.to_datetime(df["timestamp"]).max().date() <= date(2025, 9, 21)

    def test_daily_file_auto_detects_and_clamps_to_dev_window(self, loader):
        df = loader.load_from_csv("data/historical_2010_2026/SPY_daily.csv", symbol="SPY")
        assert len(df) > 0
        assert pd.to_datetime(df["date"]).max().date() <= date(2022, 12, 31)

    def test_aliased_matching_resolutions_pass(self, loader):
        df_d = loader.load_from_csv("data/historical_2020_2022/SPY_daily.csv", symbol="SPY", resolution="1d")
        assert len(df_d) > 0

        df_h = loader.load_from_csv("data/intraday_1h/SPY_1h.csv", symbol="SPY", resolution="1h")
        assert len(df_h) > 0

    def test_unmarked_file_data_frequency_mismatch_raises(self, loader, tmp_path):
        unmarked = tmp_path / "custom_data.csv"
        df_sample = pd.DataFrame({
            "date": pd.date_range("2021-01-01", periods=10, freq="B").strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 104.0,
            "volume": 10000,
            "symbol": "MOCK",
        })
        df_sample.to_csv(unmarked, index=False)

        with pytest.raises(ValueError, match="Data frequency mismatch.*daily frequency.*hourly"):
            loader.load_from_csv(unmarked, symbol="MOCK", resolution="hourly")


class TestHistoricalDirectoryQuarantine:
    """Regression tests for Vulnerability 3: unconditional quarantine of data/historical."""

    @pytest.fixture
    def loader(self):
        return HistoricalDataLoader()

    def test_historical_5m_spy_unconditional_block_without_dates(self, loader):
        """SPY_5m.csv in data/historical must raise HoldoutViolationError when loaded without dates."""
        with pytest.raises(HoldoutViolationError, match="data/historical"):
            loader.load_from_csv("data/historical/SPY_5m.csv", symbol="SPY")

    def test_historical_5m_qqq_unconditional_block_without_dates(self, loader):
        """QQQ_5m.csv in data/historical must raise HoldoutViolationError when loaded without dates."""
        with pytest.raises(HoldoutViolationError, match="data/historical"):
            loader.load_from_csv("data/historical/QQQ_5m.csv", symbol="QQQ")

    def test_historical_5m_blocked_with_dev_dates(self, loader):
        """Even with explicit development window dates, files in data/historical must fail closed."""
        with pytest.raises(HoldoutViolationError, match="data/historical"):
            loader.load_from_csv(
                "data/historical/SPY_5m.csv",
                symbol="SPY",
                start_date="2020-01-01",
                end_date="2022-12-31",
                resolution="5m",
            )

    def test_historical_5m_blocked_with_sample_dates(self, loader):
        """Even with valid 5m sample dates, data/historical files are rejected."""
        with pytest.raises(HoldoutViolationError, match="data/historical"):
            loader.load_from_csv(
                "data/historical/SPY_5m.csv",
                symbol="SPY",
                start_date="2026-06-26",
                end_date="2026-09-21",
                resolution="5m",
            )

    def test_historical_daily_files_unconditional_block(self, loader):
        """Daily files in data/historical must be blocked by directory guard."""
        for sym in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]:
            p = Path(f"data/historical/{sym}_daily.csv")
            if p.exists():
                with pytest.raises(HoldoutViolationError, match="data/historical"):
                    loader.load_from_csv(p, symbol=sym)

    def test_path_representation_variants_blocked(self, loader):
        """Relative paths, Windows backslashes, and absolute resolved paths must all trigger quarantine."""
        rel_path = "data/historical/SPY_5m.csv"
        abs_path = str(Path(rel_path).resolve())
        win_path = rel_path.replace("/", "\\")

        for p in [rel_path, abs_path, win_path]:
            with pytest.raises(HoldoutViolationError, match="data/historical"):
                loader.load_from_csv(p, symbol="SPY")

    def test_legitimate_directories_not_blocked(self, loader):
        """Development and spanning directories must NOT be blocked by the historical directory guard."""
        df_dev = loader.load_from_csv("data/historical_2020_2022/SPY_daily.csv", symbol="SPY")
        assert len(df_dev) > 0
        assert df_dev["date"].max().isoformat() <= "2022-12-31"

        df_span = loader.load_from_csv("data/historical_2010_2026/SPY_daily.csv", symbol="SPY")
        assert len(df_span) > 0
        assert df_span["date"].max().isoformat() <= "2022-12-31"

        df_14 = loader.load_from_csv("data/historical_14/AAPL_daily.csv", symbol="AAPL")
        assert len(df_14) > 0
        assert df_14["date"].max().isoformat() <= "2022-12-31"

        df_5m = loader.load_from_csv("data/intraday_5m/SPY_5m.csv", symbol="SPY")
        assert len(df_5m) > 0

    def test_phase6_unseal_permits_historical_access(self, monkeypatch, loader):
        """When Phase 6 unseal flag is explicitly active, data/historical access is allowed."""
        monkeypatch.setenv("TBOT_UNSEAL_HOLDOUT", "1")
        df = loader.load_from_csv("data/historical/SPY_5m.csv", symbol="SPY")
        assert len(df) == 4681
