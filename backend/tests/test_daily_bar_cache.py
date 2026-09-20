"""Unit tests for DailyBarCache, missing range detection, and idempotent upserts."""

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from tbot.common.clock import SimulatedClock
from tbot.data.cache import DailyBarCache, find_missing_date_ranges
from tbot.db.models import DailyBar


def test_missing_date_ranges_grouping() -> None:
    assert find_missing_date_ranges([]) == []

    # Single date
    d1 = date(2026, 1, 5)
    assert find_missing_date_ranges([d1]) == [(d1, d1)]

    # Contiguous days
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    assert find_missing_date_ranges([d1, d2, d3]) == [(d1, d3)]

    # Weekend bridge (Friday to Monday is 3 days <= 4)
    d_fri = date(2026, 1, 9)
    d_mon = date(2026, 1, 12)
    assert find_missing_date_ranges([d_fri, d_mon]) == [(d_fri, d_mon)]

    # Long gap (> 4 days)
    d_far = date(2026, 2, 5)
    assert find_missing_date_ranges([d1, d_far]) == [(d1, d1), (d_far, d_far)]


@pytest.mark.asyncio
async def test_cache_lifecycle_miss_then_hit(test_session: AsyncSession) -> None:
    clock = SimulatedClock(datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC))
    cache = DailyBarCache(clock=clock)

    fetch_call_count = 0

    async def mock_fetch(
        symbols: list[str], start: date, end: date, feed: str, adjusted: bool
    ) -> list[dict[str, Any]]:
        nonlocal fetch_call_count
        fetch_call_count += 1
        return [
            {
                "symbol": "SPY",
                "date": date(2026, 1, 5),
                "open": 500.0,
                "high": 505.0,
                "low": 498.0,
                "close": 502.0,
                "volume": 1000000,
                "adjusted": adjusted,
                "feed": feed,
            },
            {
                "symbol": "SPY",
                "date": date(2026, 1, 6),
                "open": 502.0,
                "high": 507.0,
                "low": 501.0,
                "close": 506.0,
                "volume": 1100000,
                "adjusted": adjusted,
                "feed": feed,
            },
        ]

    # First call: cache miss, triggers mock_fetch
    df1 = await cache.get_or_fetch_daily_bars(
        symbols=["SPY"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        feed="sip_delayed",
        adjusted=True,
        fetch_fn=mock_fetch,
        session=test_session,
    )
    assert len(df1) == 2
    assert fetch_call_count == 1

    # Second call for same range: 100% cache hit, fetch_fn not called
    df2 = await cache.get_or_fetch_daily_bars(
        symbols=["SPY"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        feed="sip_delayed",
        adjusted=True,
        fetch_fn=mock_fetch,
        session=test_session,
    )
    assert len(df2) == 2
    assert fetch_call_count == 1  # Unchanged
    assert df1["close"].tolist() == df2["close"].tolist()


@pytest.mark.asyncio
async def test_idempotent_duplicate_writes(test_session: AsyncSession) -> None:
    clock = SimulatedClock(datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC))
    cache = DailyBarCache(clock=clock)

    bars = [
        {
            "symbol": "QQQ",
            "date": date(2026, 1, 5),
            "open": 400.0,
            "high": 405.0,
            "low": 398.0,
            "close": 402.0,
            "volume": 500000,
            "adjusted": True,
            "feed": "sip_delayed",
        }
    ]

    # First write
    await cache.save_bars(test_session, bars)

    # Second identical write must succeed via ON CONFLICT DO NOTHING
    await cache.save_bars(test_session, bars)

    # Confirm only 1 row exists in DB
    stmt = select(DailyBar).where(DailyBar.symbol == "QQQ", DailyBar.date == date(2026, 1, 5))
    rows = (await test_session.scalars(stmt)).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_defensive_fallback_when_session_none() -> None:
    clock = SimulatedClock(datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC))
    cache = DailyBarCache(clock=clock, session_factory=None)

    async def mock_fetch(
        symbols: list[str], start: date, end: date, feed: str, adjusted: bool
    ) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "GLD",
                "date": date(2026, 1, 5),
                "open": 180.0,
                "high": 182.0,
                "low": 179.0,
                "close": 181.0,
                "volume": 200000,
                "adjusted": adjusted,
                "feed": feed,
            }
        ]

    # Must not crash; fetches directly and returns DataFrame
    df = await cache.get_or_fetch_daily_bars(
        symbols=["GLD"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        fetch_fn=mock_fetch,
        session=None,
    )
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "GLD"
    assert df.iloc[0]["close"] == 181.0


@pytest.mark.asyncio
async def test_defensive_fallback_on_db_exception() -> None:
    clock = SimulatedClock(datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC))
    cache = DailyBarCache(clock=clock)

    broken_session = MagicMock(spec=AsyncSession)
    broken_session.scalars = AsyncMock(
        side_effect=OperationalError("DB down", params={}, orig=Exception())
    )
    broken_session.rollback = AsyncMock()

    async def mock_fetch(
        symbols: list[str], start: date, end: date, feed: str, adjusted: bool
    ) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "SPY",
                "date": date(2026, 1, 5),
                "open": 500.0,
                "high": 505.0,
                "low": 498.0,
                "close": 502.0,
                "volume": 1000000,
                "adjusted": adjusted,
                "feed": feed,
            }
        ]

    df = await cache.get_or_fetch_daily_bars(
        symbols=["SPY"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        fetch_fn=mock_fetch,
        session=broken_session,
    )
    assert len(df) == 1
    assert broken_session.rollback.called


@pytest.mark.asyncio
async def test_empty_range_handling() -> None:
    clock = SimulatedClock(datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC))
    cache = DailyBarCache(clock=clock)

    # start > end
    df = await cache.get_or_fetch_daily_bars(
        symbols=["SPY"],
        start=date(2026, 1, 10),
        end=date(2026, 1, 5),
    )
    assert df.empty


@pytest.mark.asyncio
async def test_store_and_get_bars_direct(test_session: AsyncSession) -> None:
    clock = SimulatedClock(datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC))
    cache = DailyBarCache(clock=clock)

    df_to_store = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "date": date(2026, 1, 5),
                "open": 150.0,
                "high": 155.0,
                "low": 149.0,
                "close": 153.0,
                "volume": 3000000,
                "adjusted": True,
                "feed": "sip_delayed",
            }
        ]
    )

    saved_count = await cache.store_bars(df_to_store, session=test_session)
    assert saved_count >= 1

    df_retrieved = await cache.get_bars(
        symbols=["AAPL"],
        start=date(2026, 1, 1),
        end=date(2026, 1, 10),
        session=test_session,
    )
    assert len(df_retrieved) == 1
    assert df_retrieved.iloc[0]["symbol"] == "AAPL"
    assert df_retrieved.iloc[0]["close"] == 153.0
