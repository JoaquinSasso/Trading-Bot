"""Empirical stress-testing harness for Milestone M0 Holdout Seal & Execution Guards.

Tests all real data files in:
- data/historical_14/
- data/historical_2010_2026/
- data/intraday_1h/
- data/universe_a/
- data/historical/
- data/daily_2026/

Also tests:
- Runner CLI with holdout dates and boundary dates
- Compare module with holdout dates and boundary dates
- Live market data fetching restriction
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.backtest.guards import (
    DEV_DAILY_END,
    DEV_HOURLY_END,
    HOLDOUT_DAILY_END,
    HOLDOUT_DAILY_START,
    HOLDOUT_HOURLY_END,
    HOLDOUT_HOURLY_START,
    HoldoutViolationError,
    assert_not_holdout,
)


def test_real_data_files_no_dates() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 1: Load Real Data Files WITHOUT Dates (Holdout Truncation Check)")
    print("=" * 80)

    loader = HistoricalDataLoader()
    passed = 0
    total = 0

    dirs_to_test = [
        ("data/historical_14", "daily", DEV_DAILY_END, HOLDOUT_DAILY_START, HOLDOUT_DAILY_END),
        ("data/historical_2010_2026", "daily", DEV_DAILY_END, HOLDOUT_DAILY_START, HOLDOUT_DAILY_END),
        ("data/intraday_1h", "hourly", DEV_HOURLY_END, HOLDOUT_HOURLY_START, HOLDOUT_HOURLY_END),
        ("data/universe_a", "daily", DEV_DAILY_END, HOLDOUT_DAILY_START, HOLDOUT_DAILY_END),
    ]

    for dir_path_str, resolution, max_allowed_date, holdout_s, holdout_e in dirs_to_test:
        dir_p = Path(dir_path_str)
        csv_files = sorted(dir_p.glob("*.csv"))
        print(f"\n--- Checking {dir_path_str} ({len(csv_files)} files, res={resolution}) ---")

        for f in csv_files:
            total += 1
            sym = f.stem.replace("_daily", "").replace("_1h", "")
            try:
                df = loader.load_from_csv(f, symbol=sym)
                time_col = "timestamp" if "timestamp" in df.columns else "date"
                dates = pd.to_datetime(df[time_col]).dt.date
                min_d = dates.min()
                max_d = dates.max()
                count = len(df)

                # Check 1: Max date <= dev_end
                assert max_d <= max_allowed_date, (
                    f"LEAK DETECTED in {f}: max date {max_d} > allowed {max_allowed_date}"
                )

                # Check 2: Zero rows in holdout range
                holdout_rows = df[(dates >= holdout_s) & (dates <= holdout_e)]
                assert len(holdout_rows) == 0, (
                    f"HOLDOUT LEAK DETECTED in {f}: {len(holdout_rows)} rows in [{holdout_s} -> {holdout_e}]"
                )

                print(f"  [PASS] {f.name:<25}: {count:>6} bars | {min_d} -> {max_d} (<= {max_allowed_date})")
                passed += 1
            except Exception as e:
                print(f"  [FAIL] {f.name:<25}: {type(e).__name__}: {e}")

    print(f"\nSuite 1 Summary: {passed}/{total} files passed holdout truncation check.")
    return passed, total


def test_real_data_files_explicit_holdout_dates() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 2: Request Explicit Holdout Dates on Real Data (Hard-Fail Check)")
    print("=" * 80)

    loader = HistoricalDataLoader()
    passed = 0
    total = 0

    # Daily checks
    daily_dirs = ["data/historical_14", "data/historical_2010_2026", "data/universe_a"]
    daily_test_ranges = [
        ("2023-01-01", "2023-01-10", "Holdout start"),
        ("2025-01-01", "2025-12-31", "Holdout interior"),
        ("2022-12-01", "2023-01-01", "Spanning boundary into holdout"),
        ("2026-02-01", "2026-02-27", "Holdout end"),
    ]

    for dir_str in daily_dirs:
        dir_p = Path(dir_str)
        csv_files = sorted(dir_p.glob("*.csv"))
        print(f"\n--- Checking {dir_str} ({len(csv_files)} files) with explicit holdout dates ---")

        for f in csv_files:
            sym = f.stem.replace("_daily", "")
            for start_s, end_s, desc in daily_test_ranges:
                total += 1
                try:
                    loader.load_from_csv(f, symbol=sym, start_date=start_s, end_date=end_s, resolution="daily")
                    print(f"  [FAIL LEAK] {f.name} did NOT raise HoldoutViolationError for [{start_s} -> {end_s}] ({desc})")
                except HoldoutViolationError:
                    passed += 1
                except Exception as e:
                    print(f"  [UNEXPECTED ERROR] {f.name} raised {type(e).__name__} instead of HoldoutViolationError: {e}")

    # Hourly checks
    hourly_p = Path("data/intraday_1h")
    hourly_files = sorted(hourly_p.glob("*.csv"))
    hourly_test_ranges = [
        ("2025-09-22", "2025-09-30", "Hourly holdout start"),
        ("2026-01-01", "2026-06-01", "Hourly holdout interior"),
        ("2025-09-21", "2025-09-22", "Hourly boundary into holdout"),
    ]

    print(f"\n--- Checking data/intraday_1h ({len(hourly_files)} files) with explicit hourly holdout dates ---")
    for f in hourly_files:
        sym = f.stem.replace("_1h", "")
        for start_s, end_s, desc in hourly_test_ranges:
            total += 1
            try:
                loader.load_from_csv(f, symbol=sym, start_date=start_s, end_date=end_s, resolution="hourly")
                print(f"  [FAIL LEAK] {f.name} did NOT raise HoldoutViolationError for [{start_s} -> {end_s}] ({desc})")
            except HoldoutViolationError:
                passed += 1
            except Exception as e:
                print(f"  [UNEXPECTED ERROR] {f.name} raised {type(e).__name__} instead of HoldoutViolationError: {e}")

    print(f"\nSuite 2 Summary: {passed}/{total} explicit holdout requests were correctly BLOCKED.")
    return passed, total


def test_100pct_holdout_directories() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 3: Failure on 100% Holdout Files (data/historical/ & daily_2026/)")
    print("=" * 80)

    loader = HistoricalDataLoader()
    passed = 0
    total = 0

    holdout_dirs = ["data/historical", "data/daily_2026"]

    for dir_str in holdout_dirs:
        dir_p = Path(dir_str)
        if not dir_p.exists():
            continue
        csv_files = sorted(dir_p.glob("*.csv"))
        print(f"\n--- Checking directory {dir_str} ({len(csv_files)} files) ---")

        for f in csv_files:
            sym = f.stem.replace("_daily", "").replace("_5m", "")
            total += 1
            # 1. Load without dates: Must fail because file is holdout
            try:
                df = loader.load_from_csv(f, symbol=sym)
                time_col = "timestamp" if "timestamp" in df.columns else "date"
                min_d = pd.to_datetime(df[time_col]).min()
                max_d = pd.to_datetime(df[time_col]).max()
                print(f"  [FAIL LEAK] {f.name} in {dir_str} loaded successfully without dates! ({min_d} to {max_d}, {len(df)} rows)")
            except HoldoutViolationError as e:
                passed += 1
                print(f"  [PASS BLOCKED] {f.name:<25}: Blocked with HoldoutViolationError")
            except Exception as e:
                print(f"  [UNEXPECTED ERROR] {f.name} in {dir_str} raised {type(e).__name__}: {e}")

    print(f"\nSuite 3 Summary: {passed}/{total} files in holdout directories were BLOCKED.")
    return passed, total


def test_runner_cli_holdout_rejection() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 4: CLI Runner (backend/tbot/backtest/runner.py) Holdout Rejection")
    print("=" * 80)

    test_commands = [
        # (args, expected_to_fail, desc)
        (["--strategy", "s1", "--start", "2025-01-01", "--end", "2025-12-31"], True, "Full 2025 holdout year"),
        (["--strategy", "s1", "--start", "2023-01-01", "--end", "2023-01-10"], True, "Holdout daily start (2023-01-01)"),
        (["--strategy", "s1", "--start", "2022-12-01", "--end", "2023-01-01"], True, "Spanning boundary 2022-12-01 -> 2023-01-01"),
        (["--strategy", "s1", "--start", "2026-02-01", "--end", "2026-02-27"], True, "Holdout daily end (2026-02-27)"),
        (["--strategy", "s1", "--start", "2020-01-01", "--end", "2022-12-31"], False, "Development window 2020-01-01 -> 2022-12-31"),
        (["--strategy", "s1", "--data-dir", "data/historical"], True, "Runner with data-dir=data/historical (holdout dir)"),
    ]

    passed = 0
    for args, expect_fail, desc in test_commands:
        cmd = [sys.executable, "-m", "tbot.backtest.runner"] + args
        print(f"Running: {' '.join(cmd[1:])} ({desc})")
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path.cwd()))

        if expect_fail:
            if res.returncode != 0 and "HoldoutViolationError" in (res.stderr + res.stdout):
                print(f"  [PASS] CLI rejected holdout run as expected (exit code {res.returncode})")
                passed += 1
            else:
                print(f"  [FAIL] CLI did not properly reject holdout! exit={res.returncode}, out={res.stdout[:200]}, err={res.stderr[:200]}")
        else:
            if res.returncode == 0:
                print(f"  [PASS] CLI allowed development run (exit code 0)")
                passed += 1
            else:
                print(f"  [FAIL] CLI failed on valid development window! exit={res.returncode}, err={res.stderr[:200]}")

    print(f"\nSuite 4 Summary: {passed}/{len(test_commands)} CLI runner scenarios passed.")
    return passed, len(test_commands)


def test_compare_module_holdout_rejection() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 5: Compare Module (backend/tbot/backtest/compare.py) Holdout Rejection")
    print("=" * 80)

    from tbot.backtest.compare import run_comparison

    passed = 0
    total = 0

    holdout_scenarios = [
        (date(2025, 1, 1), date(2025, 12, 31), "2025 full year holdout"),
        (date(2023, 1, 1), date(2023, 1, 10), "2023-01-01 holdout start"),
        (date(2022, 12, 1), date(2023, 1, 1), "2022-12-01 -> 2023-01-01 boundary"),
    ]

    for start_d, end_d, desc in holdout_scenarios:
        total += 1
        try:
            asyncio.run(run_comparison(start_date=start_d, end_date=end_d))
            print(f"  [FAIL LEAK] run_comparison did not reject holdout range [{start_d} -> {end_d}] ({desc})")
        except HoldoutViolationError as e:
            print(f"  [PASS BLOCKED] run_comparison blocked [{start_d} -> {end_d}]")
            passed += 1
        except Exception as e:
            print(f"  [UNEXPECTED ERROR] {type(e).__name__}: {e}")

    print(f"\nSuite 5 Summary: {passed}/{total} compare scenarios blocked.")
    return passed, total


def test_live_data_fetch_blocked() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 6: Live Market Data Fetching Blocked (fetch_and_save_real_data)")
    print("=" * 80)

    loader = HistoricalDataLoader()
    passed = 0
    total = 1
    try:
        loader.fetch_and_save_real_data("SPY")
        print("  [FAIL LEAK] fetch_and_save_real_data succeeded without Phase 6 unseal!")
    except HoldoutViolationError as e:
        print(f"  [PASS BLOCKED] fetch_and_save_real_data blocked: {e}")
        passed += 1
    except Exception as e:
        print(f"  [UNEXPECTED ERROR] {type(e).__name__}: {e}")
    return passed, total


def test_adversarial_bypass_isolation() -> tuple[int, int]:
    print("\n" + "=" * 80)
    print("TEST SUITE 7: Adversarial Bypass Isolation (Synthetic Bypass Leak Check)")
    print("=" * 80)

    from tbot.backtest.guards import holdout_bypass_context, is_synthetic_bypassed

    passed = 0
    total = 3

    # Check 1: initially false
    if not is_synthetic_bypassed():
        passed += 1

    # Check 2: Enter bypass context
    with holdout_bypass_context(True):
        if is_synthetic_bypassed():
            try:
                assert_not_holdout("2025-01-01", "2025-12-31")
                passed += 1
            except Exception:
                pass

    # Check 3: Outside bypass context, must immediately revert to sealed
    if not is_synthetic_bypassed():
        try:
            assert_not_holdout("2025-01-01", "2025-12-31")
        except HoldoutViolationError:
            passed += 1

    print(f"\nSuite 7 Summary: {passed}/{total} bypass isolation checks passed.")
    return passed, total


if __name__ == "__main__":
    print("STARTING EMPIRICAL VERIFICATION HARNESS")
    s1_p, s1_t = test_real_data_files_no_dates()
    s2_p, s2_t = test_real_data_files_explicit_holdout_dates()
    s3_p, s3_t = test_100pct_holdout_directories()
    s4_p, s4_t = test_runner_cli_holdout_rejection()
    s5_p, s5_t = test_compare_module_holdout_rejection()
    s6_p, s6_t = test_live_data_fetch_blocked()
    s7_p, s7_t = test_adversarial_bypass_isolation()

    print("\n" + "=" * 80)
    print(f"TOTAL RESULTS: {s1_p+s2_p+s3_p+s4_p+s5_p+s6_p+s7_p} passed out of {s1_t+s2_t+s3_t+s4_t+s5_t+s6_t+s7_t} total checks")
    print(f"Suite 1 (Real files no dates): {s1_p}/{s1_t}")
    print(f"Suite 2 (Explicit holdout dates): {s2_p}/{s2_t}")
    print(f"Suite 3 (100% holdout dirs): {s3_p}/{s3_t}")
    print(f"Suite 4 (CLI runner): {s4_p}/{s4_t}")
    print(f"Suite 5 (Compare module): {s5_p}/{s5_t}")
    print(f"Suite 6 (Live fetch blocked): {s6_p}/{s6_t}")
    print(f"Suite 7 (Bypass isolation): {s7_p}/{s7_t}")
    print("=" * 80)
