"""Execution guards and holdout seal enforcement for backtesting.

Enforces strict holdout boundaries (Rule 0 / Milestone M0):
- Daily Holdout: 2023-01-01 to 2026-02-27 (Strictly sealed until Phase 6).
- Hourly Holdout: 2025-09-22 to 2026-09-21.
- Daily Dev Window: 2010-01-01 to 2022-12-31.
- Hourly Dev Window: 2023-10-23 to 2025-09-21.
- Intraday 5m Sample: 2026-06-26 to 2026-09-21 (Single sample, non-holdout, ineligible for model selection).
"""

from __future__ import annotations

import contextlib
import os
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Holdout and Development Window Boundaries
HOLDOUT_DAILY_START = date(2023, 1, 1)
HOLDOUT_DAILY_END = date(2026, 2, 27)

DEV_DAILY_START = date(2010, 1, 1)
DEV_DAILY_END = date(2022, 12, 31)

HOLDOUT_HOURLY_START = date(2025, 9, 22)
HOLDOUT_HOURLY_END = date(2026, 9, 21)

DEV_HOURLY_START = date(2023, 10, 23)
DEV_HOURLY_END = date(2025, 9, 21)

SAMPLE_5M_START = date(2026, 6, 26)
SAMPLE_5M_END = date(2026, 9, 21)

# Canonical resolution aliases
RESOLUTION_DAILY_ALIASES = {"daily", "1d", "d", "day"}
RESOLUTION_HOURLY_ALIASES = {"hourly", "1h", "h", "hour"}
RESOLUTION_5M_ALIASES = {"5m", "5min", "minute", "intraday_5m"}

_HOLDOUT_BYPASS: ContextVar[bool] = ContextVar("holdout_bypass", default=False)


class HoldoutViolationError(PermissionError, ValueError):
    """Raised when a backtest or data load attempts to access sealed holdout data.

    Subclasses both PermissionError and ValueError to ensure callers catching
    either standard permission/access errors or domain parameter validation errors
    intercept holdout violations.
    """
    pass


@contextlib.contextmanager
def holdout_bypass_context(enabled: bool = True):
    """Context manager to temporarily bypass holdout guards in synthetic tests.

    Thread-safe and async-safe via ContextVar.
    """
    token = _HOLDOUT_BYPASS.set(enabled)
    try:
        yield
    finally:
        _HOLDOUT_BYPASS.reset(token)


def is_holdout_unsealed() -> bool:
    """Returns True if Phase 6 holdout unseal environment flag is explicitly active."""
    return os.environ.get("TBOT_UNSEAL_HOLDOUT", "0").strip() in {"1", "true", "TRUE", "yes"}


def is_synthetic_bypassed() -> bool:
    """Returns True if holdout check is bypassed for synthetic testing in current context."""
    return _HOLDOUT_BYPASS.get()


def _to_date(val: date | datetime | str | Any) -> date:
    """Converts date, datetime, str, or pandas.Timestamp to datetime.date."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        val_clean = val.strip()
        if len(val_clean) >= 10 and val_clean[4] == "-" and val_clean[7] == "-":
            try:
                return date.fromisoformat(val_clean[:10])
            except ValueError:
                pass
        import pandas as pd
        return pd.to_datetime(val_clean).date()
    if hasattr(val, "date") and callable(val.date):
        return val.date()
    raise TypeError(f"Expected date, datetime, or str, got {type(val).__name__}: {val!r}")


def normalize_resolution(resolution: str) -> str:
    """Normalizes resolution string to canonical names: 'daily', 'hourly', '5m'."""
    res = resolution.lower().strip()
    if res in RESOLUTION_DAILY_ALIASES:
        return "daily"
    if res in RESOLUTION_HOURLY_ALIASES:
        return "hourly"
    if res in RESOLUTION_5M_ALIASES:
        return "5m"
    valid = sorted(RESOLUTION_DAILY_ALIASES | RESOLUTION_HOURLY_ALIASES | RESOLUTION_5M_ALIASES)
    raise ValueError(f"Unknown resolution '{resolution}'. Supported resolutions: {valid}")


def assert_not_holdout(
    start: date | datetime | str,
    end: date | datetime | str,
    resolution: str = "daily",
    allow_synthetic: bool = False,
) -> None:
    """Asserts that the requested range [start, end] does not intersect sealed holdout data.

    Args:
        start: Start date, datetime, or ISO string.
        end: End date, datetime, or ISO string.
        resolution: Temporal resolution ('daily', 'hourly', '5m'). Defaults to 'daily'.
        allow_synthetic: If True, bypasses holdout check (used exclusively for synthetic mock data).

    Raises:
        ValueError: If start > end, invalid types, or unrecognized resolution.
        HoldoutViolationError: If [start, end] intersects sealed holdout range for resolution.
    """
    if allow_synthetic or is_synthetic_bypassed():
        return

    if is_holdout_unsealed():
        logger.warning(
            "AUDIT_HOLDOUT_UNSEALED: Accessing holdout under Phase 6 authorization",
            start=str(start),
            end=str(end),
            resolution=resolution,
        )
        return

    norm_res = normalize_resolution(resolution)
    req_start = _to_date(start)
    req_end = _to_date(end)

    if req_start > req_end:
        raise ValueError(f"Invalid range: start ({req_start}) must be <= end ({req_end})")

    if norm_res == "5m":
        if req_start >= SAMPLE_5M_START and req_end <= SAMPLE_5M_END:
            # 5m dataset (2026-06-26 to 2026-09-21) is a single non-holdout sample;
            # ineligible for parameter selection, but allowed through execution guard.
            return

        # Requested range falls outside sanctioned 5m diagnostic window.
        # Check intersection with sealed daily holdout (Rule 0).
        intersection_start = max(req_start, HOLDOUT_DAILY_START)
        intersection_end = min(req_end, HOLDOUT_DAILY_END)
        if intersection_start <= intersection_end:
            msg = (
                f"Holdout Violation: Requested range [{req_start} -> {req_end}] (resolution='{norm_res}') "
                f"falls outside sanctioned 5m sample [{SAMPLE_5M_START} -> {SAMPLE_5M_END}] and "
                f"intersects sealed daily holdout [{HOLDOUT_DAILY_START} -> {HOLDOUT_DAILY_END}] at "
                f"[{intersection_start} -> {intersection_end}]. "
                f"Access to holdout data is strictly forbidden prior to Phase 6 unsealing. "
                f"See data/HOLDOUT_LOCK.md for unsealing governance and development window constraints."
            )
            logger.error(
                "HOLDOUT_VIOLATION_TRIGGERED",
                start=str(req_start),
                end=str(req_end),
                resolution=norm_res,
                intersection=f"{intersection_start} -> {intersection_end}",
            )
            raise HoldoutViolationError(msg)

        # Defense-in-depth: check intersection with sealed hourly holdout
        h_inter_start = max(req_start, HOLDOUT_HOURLY_START)
        h_inter_end = min(req_end, HOLDOUT_HOURLY_END)
        if h_inter_start <= h_inter_end:
            msg = (
                f"Holdout Violation: Requested range [{req_start} -> {req_end}] (resolution='{norm_res}') "
                f"falls outside sanctioned 5m sample [{SAMPLE_5M_START} -> {SAMPLE_5M_END}] and "
                f"intersects sealed hourly holdout [{HOLDOUT_HOURLY_START} -> {HOLDOUT_HOURLY_END}] at "
                f"[{h_inter_start} -> {h_inter_end}]. "
                f"Access to holdout data is strictly forbidden prior to Phase 6 unsealing. "
                f"See data/HOLDOUT_LOCK.md for unsealing governance and development window constraints."
            )
            logger.error(
                "HOLDOUT_VIOLATION_TRIGGERED",
                start=str(req_start),
                end=str(req_end),
                resolution=norm_res,
                intersection=f"{h_inter_start} -> {h_inter_end}",
            )
            raise HoldoutViolationError(msg)

        return

    if norm_res == "daily":
        h_start, h_end = HOLDOUT_DAILY_START, HOLDOUT_DAILY_END
    elif norm_res == "hourly":
        h_start, h_end = HOLDOUT_HOURLY_START, HOLDOUT_HOURLY_END
    else:
        raise ValueError(f"Unsupported resolution: {norm_res}")

    # Closed interval intersection: max(A, C) <= min(B, D)
    intersection_start = max(req_start, h_start)
    intersection_end = min(req_end, h_end)

    if intersection_start <= intersection_end:
        msg = (
            f"Holdout Violation: Requested range [{req_start} -> {req_end}] (resolution='{norm_res}') "
            f"intersects sealed holdout [{h_start} -> {h_end}] at [{intersection_start} -> {intersection_end}]. "
            f"Access to holdout data is strictly forbidden prior to Phase 6 unsealing. "
            f"See data/HOLDOUT_LOCK.md for unsealing governance and development window constraints."
        )
        logger.error(
            "HOLDOUT_VIOLATION_TRIGGERED",
            start=str(req_start),
            end=str(req_end),
            resolution=norm_res,
            intersection=f"{intersection_start} -> {intersection_end}",
        )
        raise HoldoutViolationError(msg)
