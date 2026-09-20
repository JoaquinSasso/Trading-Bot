"""Adversarial stress tests for Milestone 3: Bar Resampler, Database Cache, and Proxy Mapper.

Designed and executed by Challenger 2 (teamwork_preview_challenger).
Empirically tests and challenges:
1. Bar Resampling Edge Cases:
   - Irregular 1m timestamps (non-aligned seconds, sub-minute variations, microseconds, out-of-order)
   - Zero-volume bars (entire bucket vol=0, mixed zero and non-zero vol, VWAP fallback to close)
   - Single 1m bar in 5m period (at minute 0, minute 2, minute 4)
   - 09:30 open boundary separation (09:29:59 premarket vs 09:30:00 open)
   - 15:55-16:00 close boundary separation (15:55-15:59 regular close vs 16:00:00 auction)
   - Multi-bar gaps (active -> halt -> resume, fill_missing=False vs fill_missing=True, synthetic flags, expected_index)
   - Flat price series (constant price, fluctuating volume, zero return consistency)
   - Extreme values (large prices, micro prices, massive trade volumes)
   - Input format tolerance (string timestamps, tz-naive indices, multi-symbol independent processing)
2. DailyBarCache Concurrency & Idempotency:
   - Concurrent duplicate writes (20 concurrent tasks writing identical bar)
   - Concurrent overlapping writes (10 tasks writing overlapping date windows)
   - Multi-week missing range detection and grouping (bridging weekends to minimize network queries)
   - Database failure fallback during read (graceful degradation via fetch_fn without crashing)
   - Database failure fallback during write (in-memory return with rollback)
   - Database session missing bypass
   - Multi-symbol partial cache optimization (only missing dates/symbols fetched)
   - Type polymorphism on date inputs (str, date, pd.Timestamp, datetime)
   - Feed normalization robustness
3. Proxy Mapping Edge Cases & Invariance:
   - Whitespace and line-break resilience (spaces, tabs, newlines, carriage returns, empty strings)
   - Mixed case resilience
   - Non-standard ticker handling (dots, slashes, numbers)
   - Round-trip mapping invariance (S -> P -> S for canonicals)
   - Idempotency of resolve_trading_proxy and resolve_data_proxy
   - Unmapped ticker identity preservation
   - Proxy pair consistency
   - Database sync robustness with unnormalized entries
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tbot.data.cache import DailyBarCache
from tbot.data.proxies import (
    CANONICAL_PROXIES,
    ProxyMapper,
    get_proxy_pair,
    is_canonical_symbol,
    is_proxy_symbol,
    map_symbol,
    resolve_data_proxy,
    resolve_trading_proxy,
)
from tbot.data.resampler import aggregate_1m_to_5m, resample_1m_to_5m
from tbot.db.base import Base
from tbot.db.models import DailyBar, UniverseSymbol

# ===========================================================================
# 1. Bar Resampler Adversarial Stress Tests
# ===========================================================================


class TestBarResamplingAdversarial:
    """Adversarial stress testing of 1m to 5m bar resampling and VWAP semantics."""

    def test_resample_irregular_timestamps_with_seconds(self) -> None:
        """Irregular timestamps with sub-minute seconds must land in the correct 5m bucket."""
        base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            {
                "timestamp": base_time + timedelta(seconds=15),
                "open": 100.0,
                "high": 101.5,
                "low": 99.5,
                "close": 101.0,
                "volume": 300.0,
                "vwap": 100.5,
            },
            {
                "timestamp": base_time + timedelta(minutes=2, seconds=45),
                "open": 101.0,
                "high": 103.0,
                "low": 100.5,
                "close": 102.5,
                "volume": 700.0,
                "vwap": 102.0,
            },
            {
                "timestamp": base_time + timedelta(minutes=4, seconds=59),
                "open": 102.5,
                "high": 104.0,
                "low": 102.0,
                "close": 103.5,
                "volume": 500.0,
                "vwap": 103.0,
            },
        ]
        df_1m = pd.DataFrame(bars)
        res = resample_1m_to_5m(df_1m)

        assert len(res) == 1
        row = res.iloc[0]
        # Must be right-labeled at 09:35:00
        assert row["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
        assert row["open"] == 100.0
        assert row["high"] == 104.0
        assert row["low"] == 99.5
        assert row["close"] == 103.5
        assert row["volume"] == 1500.0
        # VWAP = (300*100.5 + 700*102.0 + 500*103.0) / 1500 = (30150 + 71400 + 51500) / 1500 = 153050 / 1500 = 102.033333...
        expected_vwap = 153050.0 / 1500.0
        assert abs(row["vwap"] - expected_vwap) < 1e-6

    def test_resample_microsecond_precision(self) -> None:
        """Microsecond timestamps must not trigger bucketing anomalies or split boundaries."""
        t_open = datetime(2026, 9, 21, 9, 30, 0, 123456, tzinfo=UTC)
        t_close = datetime(2026, 9, 21, 9, 34, 59, 999999, tzinfo=UTC)
        df_1m = pd.DataFrame([
            {"timestamp": t_open, "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5, "volume": 100.0},
            {"timestamp": t_close, "open": 50.5, "high": 52.0, "low": 50.0, "close": 51.5, "volume": 100.0},
        ])
        res = resample_1m_to_5m(df_1m)
        assert len(res) == 1
        assert res.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
        assert res.iloc[0]["open"] == 50.0
        assert res.iloc[0]["close"] == 51.5

    def test_resample_out_of_order_1m_bars(self) -> None:
        """Chronologically shuffled bars must be sorted internally before computing OHLC."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        # Out-of-order sequence: minute 3, minute 0, minute 4, minute 1, minute 2
        minutes_order = [3, 0, 4, 1, 2]
        bars = [
            {
                "timestamp": base + timedelta(minutes=m),
                "open": 100.0 + m,
                "high": 101.0 + m,
                "low": 99.0 + m,
                "close": 100.5 + m,
                "volume": 100.0,
            }
            for m in minutes_order
        ]
        df_shuffled = pd.DataFrame(bars)
        res = resample_1m_to_5m(df_shuffled)

        assert len(res) == 1
        row = res.iloc[0]
        # Open should come from minute 0 (100.0), close should come from minute 4 (104.5)
        assert row["open"] == 100.0
        assert row["close"] == 104.5
        assert row["high"] == 105.0  # minute 4 high
        assert row["low"] == 99.0   # minute 0 low

    def test_resample_sub_minute_duplicate_timestamps(self) -> None:
        """Multiple trades/quotes at the exact same minute timestamp must be aggregated seamlessly."""
        t = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        df = pd.DataFrame([
            {"timestamp": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 200.0, "vwap": 100.2},
            {"timestamp": t, "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 300.0, "vwap": 101.0},
        ])
        res = resample_1m_to_5m(df)
        assert len(res) == 1
        row = res.iloc[0]
        assert row["open"] == 100.0
        assert row["close"] == 101.5
        assert row["volume"] == 500.0
        # VWAP: (200*100.2 + 300*101.0) / 500 = (20040 + 30300) / 500 = 50340 / 500 = 100.68
        assert abs(row["vwap"] - 100.68) < 1e-6

    def test_resample_zero_volume_entire_bucket_vwap_fallback(self) -> None:
        """When an entire 5m bucket has 0 volume, VWAP must fall back to close without NaN or ZeroDivisionError."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            {
                "timestamp": base + timedelta(minutes=i),
                "open": 200.0,
                "high": 202.0,
                "low": 198.0,
                "close": 201.0 + i,
                "volume": 0.0,
            }
            for i in range(5)
        ]
        res = resample_1m_to_5m(pd.DataFrame(bars))
        assert len(res) == 1
        row = res.iloc[0]
        assert row["volume"] == 0.0
        assert row["close"] == 205.0
        # VWAP fallback to last close
        assert row["vwap"] == 205.0
        assert not np.isnan(row["vwap"])

    def test_resample_zero_volume_mixed_bars_vwap_exact(self) -> None:
        """Zero-volume bars within a mixed bucket must NOT dilute the VWAP of non-zero bars."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            # minute 0: zero volume
            {"timestamp": base, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0, "vwap": 100.0},
            # minute 1: real trade 1000 shares at 110.0
            {"timestamp": base + timedelta(minutes=1), "open": 109.0, "high": 111.0, "low": 109.0, "close": 110.0, "volume": 1000.0, "vwap": 110.0},
            # minute 2: zero volume
            {"timestamp": base + timedelta(minutes=2), "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 0.0, "vwap": 110.0},
            # minute 3: real trade 1000 shares at 120.0
            {"timestamp": base + timedelta(minutes=3), "open": 119.0, "high": 121.0, "low": 119.0, "close": 120.0, "volume": 1000.0, "vwap": 120.0},
            # minute 4: zero volume
            {"timestamp": base + timedelta(minutes=4), "open": 120.0, "high": 120.0, "low": 120.0, "close": 120.0, "volume": 0.0, "vwap": 120.0},
        ]
        res = resample_1m_to_5m(pd.DataFrame(bars))
        assert len(res) == 1
        row = res.iloc[0]
        assert row["volume"] == 2000.0
        # VWAP must be exactly (1000*110 + 1000*120) / 2000 = 115.0
        assert abs(row["vwap"] - 115.0) < 1e-6

    @pytest.mark.parametrize("minute_offset", [0, 2, 4])
    def test_resample_single_1m_bar_in_5m_period(self, minute_offset: int) -> None:
        """A single 1m bar at minute 0, 2, or 4 of a 5m interval must properly label at bucket close."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bar_time = base + timedelta(minutes=minute_offset)
        df = pd.DataFrame([{
            "timestamp": bar_time,
            "symbol": "SPY",
            "open": 500.0,
            "high": 502.0,
            "low": 499.0,
            "close": 501.0,
            "volume": 450.0,
            "vwap": 500.8,
            "trade_count": 15,
        }])
        res = resample_1m_to_5m(df)
        assert len(res) == 1
        row = res.iloc[0]
        assert row["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
        assert row["open"] == 500.0
        assert row["high"] == 502.0
        assert row["low"] == 499.0
        assert row["close"] == 501.0
        assert row["volume"] == 450.0
        assert row["vwap"] == 500.8
        assert row["trade_count"] == 15
        assert bool(row["is_synthetic"]) is False

    def test_resample_0930_open_boundary_separation(self) -> None:
        """Pre-market bar at 09:29:59 must land in 09:30:00 bucket, while 09:30:00 must start 09:35:00 bucket."""
        pre_market_time = datetime(2026, 9, 21, 9, 29, 59, tzinfo=UTC)
        market_open_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        df = pd.DataFrame([
            {"timestamp": pre_market_time, "open": 99.0, "high": 99.5, "low": 98.5, "close": 99.2, "volume": 100.0},
            {"timestamp": market_open_time, "open": 100.0, "high": 102.0, "low": 99.8, "close": 101.5, "volume": 500.0},
        ])
        res = resample_1m_to_5m(df)
        assert len(res) == 2
        # Bar 1: pre-market lands in 09:30:00
        assert res.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:30:00+00:00")
        assert res.iloc[0]["close"] == 99.2
        # Bar 2: market open lands in 09:35:00
        assert res.iloc[1]["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
        assert res.iloc[1]["open"] == 100.0

    def test_resample_1600_close_boundary_separation(self) -> None:
        """Bars from 15:55:00 to 15:59:59 form the 16:00:00 bar, while 16:00:00 forms the 16:05:00 post-market bar."""
        base = datetime(2026, 9, 21, 15, 55, 0, tzinfo=UTC)
        bars = [
            {"timestamp": base + timedelta(minutes=i), "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "volume": 100.0}
            for i in range(5)  # 15:55, 15:56, 15:57, 15:58, 15:59
        ]
        # Add auction bar at exactly 16:00:00
        bars.append({"timestamp": datetime(2026, 9, 21, 16, 0, 0, tzinfo=UTC), "open": 105.0, "high": 105.0, "low": 105.0, "close": 105.0, "volume": 10000.0})

        res = resample_1m_to_5m(pd.DataFrame(bars))
        assert len(res) == 2
        # Final regular market bar labeled 16:00:00
        assert res.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 16:00:00+00:00")
        assert res.iloc[0]["open"] == 100.0
        assert res.iloc[0]["close"] == 104.5
        assert res.iloc[0]["volume"] == 500.0
        # Closing auction / post-market bar labeled 16:05:00
        assert res.iloc[1]["timestamp"] == pd.Timestamp("2026-09-21 16:05:00+00:00")
        assert res.iloc[1]["open"] == 105.0
        assert res.iloc[1]["volume"] == 10000.0

    def test_resample_multi_bar_gaps_fill_missing_off(self) -> None:
        """With fill_missing=False, periods with no trading data must be omitted (no synthetic bars)."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            # 09:30-09:34 (Bucket 09:35)
            {"timestamp": base, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100.0},
            # Complete 30-min gap (missing 09:40, 09:45, 09:50, 09:55, 10:00)
            # 10:00-10:04 (Bucket 10:05)
            {"timestamp": base + timedelta(minutes=30), "open": 105.0, "high": 106.0, "low": 104.0, "close": 105.5, "volume": 200.0},
        ]
        res = resample_1m_to_5m(pd.DataFrame(bars), fill_missing=False)
        assert len(res) == 2
        assert res.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
        assert res.iloc[1]["timestamp"] == pd.Timestamp("2026-09-21 10:05:00+00:00")
        assert (~res["is_synthetic"]).all()

    def test_resample_multi_bar_gaps_fill_missing_on(self) -> None:
        """With fill_missing=True, missing intervals are synthesized with volume=0, is_synthetic=True, and forward-filled close."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            # Bucket 09:35
            {"timestamp": base, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100.0},
            # 30-minute halt/gap: resumes at 10:00 (Bucket 10:05)
            {"timestamp": base + timedelta(minutes=30), "open": 105.0, "high": 106.0, "low": 104.0, "close": 105.5, "volume": 200.0},
        ]
        res = resample_1m_to_5m(pd.DataFrame(bars), fill_missing=True)
        # Expected buckets: 09:35, 09:40, 09:45, 09:50, 09:55, 10:00, 10:05 -> 7 bars total
        assert len(res) == 7

        # First bar: real
        assert res.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
        assert bool(res.iloc[0]["is_synthetic"]) is False
        assert res.iloc[0]["volume"] == 100.0

        # Middle 5 bars: synthetic
        for i in range(1, 6):
            synthetic_bar = res.iloc[i]
            assert bool(synthetic_bar["is_synthetic"]) is True
            assert synthetic_bar["volume"] == 0.0
            assert synthetic_bar["trade_count"] == 0
            # OHLC and VWAP forward-filled from prior close (100.5)
            assert synthetic_bar["open"] == 100.5
            assert synthetic_bar["high"] == 100.5
            assert synthetic_bar["low"] == 100.5
            assert synthetic_bar["close"] == 100.5
            assert synthetic_bar["vwap"] == 100.5

        # Last bar: real
        assert res.iloc[6]["timestamp"] == pd.Timestamp("2026-09-21 10:05:00+00:00")
        assert bool(res.iloc[6]["is_synthetic"]) is False
        assert res.iloc[6]["volume"] == 200.0

    def test_resample_multi_bar_gaps_explicit_expected_index(self) -> None:
        """Providing an expected_index forces the exact requested timestamps and forward-fills gaps."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [{"timestamp": base, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 100.0}]
        df = pd.DataFrame(bars)

        expected = pd.date_range("2026-09-21 09:35:00", periods=4, freq="5min", tz="UTC")
        res = resample_1m_to_5m(df, expected_index=expected)

        assert len(res) == 4
        assert list(res["timestamp"]) == list(expected)
        assert bool(res.iloc[0]["is_synthetic"]) is False
        assert res.iloc[1:]["is_synthetic"].all()
        assert (res.iloc[1:]["volume"] == 0.0).all()

    def test_resample_flat_price_series_constant_returns(self) -> None:
        """Flat prices with varying volume must preserve constant OHLC and exact VWAP without numeric drift."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            {
                "timestamp": base + timedelta(minutes=i),
                "open": 150.0,
                "high": 150.0,
                "low": 150.0,
                "close": 150.0,
                "volume": float(i * 50),
                "vwap": 150.0,
            }
            for i in range(25)  # 5 buckets of 5 minutes
        ]
        res = resample_1m_to_5m(pd.DataFrame(bars))
        assert len(res) == 5
        assert (res["open"] == 150.0).all()
        assert (res["high"] == 150.0).all()
        assert (res["low"] == 150.0).all()
        assert (res["close"] == 150.0).all()
        assert (res["vwap"] == 150.0).all()

    def test_resample_extreme_large_prices_and_volumes(self) -> None:
        """Resampler must handle large nominal prices (BRK.A level) and multi-billion volumes without overflow."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = [
            {
                "timestamp": base,
                "open": 650000.0,
                "high": 655000.0,
                "low": 648000.0,
                "close": 652000.0,
                "volume": 2e9,
                "vwap": 651000.0,
                "trade_count": 400000,
            },
            {
                "timestamp": base + timedelta(minutes=1),
                "open": 652000.0,
                "high": 656000.0,
                "low": 651000.0,
                "close": 654000.0,
                "volume": 3e9,
                "vwap": 653000.0,
                "trade_count": 600000,
            },
        ]
        res = resample_1m_to_5m(pd.DataFrame(bars))
        assert len(res) == 1
        row = res.iloc[0]
        assert row["high"] == 656000.0
        assert row["low"] == 648000.0
        assert row["volume"] == 5e9
        assert row["trade_count"] == 1000000
        # Expected VWAP = (2e9 * 651000 + 3e9 * 653000) / 5e9 = (1.302e15 + 1.959e15) / 5e9 = 3.261e15 / 5e9 = 652200.0
        assert abs(row["vwap"] - 652200.0) < 1e-4

    def test_resample_multi_symbol_isolated_aggregation(self) -> None:
        """Resampling multiple symbols together must isolate each symbol's buckets without cross-contamination."""
        base = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
        bars = []
        for sym, p_base in [("SPY", 500.0), ("QQQ", 450.0), ("GLD", 200.0)]:
            for i in range(5):
                bars.append({
                    "timestamp": base + timedelta(minutes=i),
                    "symbol": sym,
                    "open": p_base + i,
                    "high": p_base + i + 1.0,
                    "low": p_base + i - 1.0,
                    "close": p_base + i + 0.5,
                    "volume": 100.0,
                })
        res = aggregate_1m_to_5m(pd.DataFrame(bars))
        assert len(res) == 3
        symbols = list(res["symbol"])
        assert symbols == ["GLD", "QQQ", "SPY"]
        spy_row = res[res["symbol"] == "SPY"].iloc[0]
        assert spy_row["open"] == 500.0
        assert spy_row["close"] == 504.5
        gld_row = res[res["symbol"] == "GLD"].iloc[0]
        assert gld_row["open"] == 200.0
        assert gld_row["close"] == 204.5

    def test_resample_tz_naive_and_string_timestamps(self) -> None:
        """Resampler must cleanly convert ISO-string timestamps and tz-naive indices to UTC."""
        df_str = pd.DataFrame([
            {"timestamp": "2026-09-21 09:30:00", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0}
        ])
        res_str = resample_1m_to_5m(df_str)
        assert res_str.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")

        df_naive = pd.DataFrame(
            [{"open": 20.0, "high": 21.0, "low": 19.0, "close": 20.5, "volume": 100.0}],
            index=pd.to_datetime(["2026-09-21 09:30:00"]),
        )
        res_naive = resample_1m_to_5m(df_naive)
        assert res_naive.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")


# ===========================================================================
# 2. DailyBarCache Concurrency, Idempotency & Fallback Tests
# ===========================================================================


class TestDailyBarCacheAdversarial:
    """Adversarial stress testing of DailyBarCache concurrency, multi-week gaps, and failure recovery."""

    @pytest.mark.asyncio
    async def test_concurrent_writes_identical_bars(self) -> None:
        """20 concurrent tasks attempting to write the exact same daily bar must succeed idempotently without conflicts."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        cache = DailyBarCache(session_factory=session_factory)

        identical_bar = {
            "symbol": "SPY",
            "date": date(2026, 1, 5),
            "open": 500.0,
            "high": 505.0,
            "low": 498.0,
            "close": 502.0,
            "volume": 1000000,
            "adjusted": True,
            "feed": "sip_delayed",
        }

        async def worker_write() -> int:
            return await cache.store_bars([identical_bar])

        # Launch 20 concurrent write coroutines
        results = await asyncio.gather(*(worker_write() for _ in range(20)), return_exceptions=True)

        # None of the 20 tasks should have thrown an exception
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert not exceptions, f"Concurrent writes threw exceptions: {exceptions}"

        # Exactly 1 row must exist in the database table
        async with session_factory() as session:
            rows = (await session.scalars(select(DailyBar))).all()
            assert len(rows) == 1
            assert rows[0].symbol == "SPY"
            assert rows[0].date == date(2026, 1, 5)

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_concurrent_writes_overlapping_ranges(self) -> None:
        """10 concurrent tasks writing overlapping date windows must resolve into the exact deduplicated union."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        cache = DailyBarCache(session_factory=session_factory)

        base_date = date(2026, 1, 1)

        async def write_window(offset: int) -> int:
            bars = [
                {
                    "symbol": "QQQ",
                    "date": base_date + timedelta(days=offset + d),
                    "open": 400.0,
                    "high": 405.0,
                    "low": 398.0,
                    "close": 402.0,
                    "volume": 500000,
                    "adjusted": True,
                    "feed": "sip_delayed",
                }
                for d in range(20)
            ]
            return await cache.store_bars(bars)

        # 10 workers with overlapping offsets (0, 3, 6, ..., 27). Total unique days: 20 + 27 = 47 days.
        results = await asyncio.gather(*(write_window(i * 3) for i in range(10)), return_exceptions=True)
        assert not any(isinstance(r, Exception) for r in results)

        async with session_factory() as session:
            rows = (await session.scalars(select(DailyBar).where(DailyBar.symbol == "QQQ"))).all()
            distinct_dates = {r.date for r in rows}
            assert len(rows) == len(distinct_dates)
            assert len(rows) == 47

        await engine.dispose()

    def test_multi_week_missing_ranges_efficient_bridging(self) -> None:
        """find_missing_date_ranges must bridge weekends and short breaks to collapse multi-week queries into minimal ranges."""
        start = date(2026, 1, 5)  # Monday
        end = date(2026, 3, 27)    # Friday (12 full weeks = 60 weekdays)

        cache = DailyBarCache()
        # Weeks 1, 6, and 12 are cached
        cached_dates: list[date] = []
        for w in [0, 5, 11]:
            w_start = start + timedelta(weeks=w)
            for d in range(5):
                cached_dates.append(w_start + timedelta(days=d))

        cached_df = pd.DataFrame({"date": cached_dates, "symbol": "SPY"})
        missing_ranges = cache.find_missing_ranges(["SPY"], start, end, cached_df)

        # 45 missing days should be collapsed into exactly 2 continuous ranges (Weeks 2-5 and Weeks 7-11)
        assert len(missing_ranges) == 2

        r1_start, r1_end = missing_ranges[0]
        assert r1_start == date(2026, 1, 12)  # Monday Week 2
        assert r1_end == date(2026, 2, 6)    # Friday Week 5

        r2_start, r2_end = missing_ranges[1]
        assert r2_start == date(2026, 2, 16)  # Monday Week 7
        assert r2_end == date(2026, 3, 20)   # Friday Week 11

    @pytest.mark.asyncio
    async def test_database_disconnect_read_fallback(self) -> None:
        """If the database throws an OperationalError during read, cache must catch, rollback, and fetch live data directly."""
        cache = DailyBarCache()
        broken_session = MagicMock(spec=AsyncSession)
        broken_session.scalars = AsyncMock(side_effect=OperationalError("Connection dropped by peer", params={}, orig=Exception()))
        broken_session.rollback = AsyncMock()

        async def live_fetch(symbols: list[str], start: date, end: date, feed: str, adjusted: bool) -> list[dict[str, Any]]:
            return [{
                "symbol": "SPY",
                "date": date(2026, 1, 5),
                "open": 500.0,
                "high": 505.0,
                "low": 498.0,
                "close": 502.0,
                "volume": 1000000,
                "adjusted": adjusted,
                "feed": feed,
            }]

        df = await cache.get_or_fetch_daily_bars(
            symbols=["SPY"],
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            fetch_fn=live_fetch,
            session=broken_session,
        )
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "SPY"
        assert df.iloc[0]["close"] == 502.0
        assert broken_session.rollback.called

    @pytest.mark.asyncio
    async def test_database_disconnect_write_fallback(self) -> None:
        """If the database throws an OperationalError during save_bars, it must roll back and return memory records gracefully."""
        cache = DailyBarCache()
        mock_session = MagicMock(spec=AsyncSession)
        # Read succeeds with cache miss
        scalars_result = MagicMock()
        scalars_result.all.return_value = []
        mock_session.scalars = AsyncMock(return_value=scalars_result)
        # Write fails
        mock_session.execute = AsyncMock(side_effect=OperationalError("Disk full / connection terminated", params={}, orig=Exception()))
        mock_session.rollback = AsyncMock()
        mock_session.get_bind.return_value.dialect.name = "sqlite"

        async def live_fetch(symbols: list[str], start: date, end: date, feed: str, adjusted: bool) -> list[dict[str, Any]]:
            return [{
                "symbol": "GLD",
                "date": date(2026, 1, 5),
                "open": 180.0,
                "high": 182.0,
                "low": 179.0,
                "close": 181.0,
                "volume": 200000,
                "adjusted": adjusted,
                "feed": feed,
            }]

        df = await cache.get_or_fetch_daily_bars(
            symbols=["GLD"],
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            fetch_fn=live_fetch,
            session=mock_session,
        )
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "GLD"
        assert df.iloc[0]["close"] == 181.0
        assert mock_session.rollback.called

    @pytest.mark.asyncio
    async def test_database_bypass_when_no_session(self) -> None:
        """When neither session nor session_factory is provided, cache must log warning and fetch without crashing."""
        cache = DailyBarCache(session_factory=None)

        async def fetch(symbols: list[str], start: date, end: date, feed: str, adjusted: bool) -> list[dict[str, Any]]:
            return [{
                "symbol": "AAPL",
                "date": date(2026, 1, 5),
                "open": 150.0,
                "high": 152.0,
                "low": 149.0,
                "close": 151.0,
                "volume": 500000,
                "adjusted": adjusted,
                "feed": feed,
            }]

        df = await cache.get_or_fetch_daily_bars(
            symbols=["AAPL"],
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            fetch_fn=fetch,
            session=None,
        )
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_multi_symbol_partial_cache_efficiency(self, test_session: AsyncSession) -> None:
        """Queries for multiple symbols must only fetch the missing subsets from Alpaca without re-fetching cached symbols."""
        cache = DailyBarCache()

        # Pre-seed database: SPY has Jan 5 and Jan 6; QQQ has only Jan 5; GLD has nothing
        await cache.save_bars(test_session, [
            {"symbol": "SPY", "date": date(2026, 1, 5), "open": 500, "high": 505, "low": 495, "close": 500, "volume": 100, "adjusted": True, "feed": "sip_delayed"},
            {"symbol": "SPY", "date": date(2026, 1, 6), "open": 502, "high": 507, "low": 497, "close": 502, "volume": 100, "adjusted": True, "feed": "sip_delayed"},
            {"symbol": "QQQ", "date": date(2026, 1, 5), "open": 400, "high": 405, "low": 395, "close": 400, "volume": 100, "adjusted": True, "feed": "sip_delayed"},
        ])

        fetched_calls: list[tuple[list[str], date, date]] = []

        async def mock_fetch(symbols: list[str], start: date, end: date, feed: str, adjusted: bool) -> list[dict[str, Any]]:
            fetched_calls.append((symbols, start, end))
            results = []
            for s in symbols:
                cur = start
                while cur <= end:
                    results.append({
                        "symbol": s,
                        "date": cur,
                        "open": 100.0,
                        "high": 105.0,
                        "low": 95.0,
                        "close": 100.0,
                        "volume": 500.0,
                        "adjusted": adjusted,
                        "feed": feed,
                    })
                    cur += timedelta(days=1)
            return results

        df = await cache.get_or_fetch_daily_bars(
            symbols=["SPY", "QQQ", "GLD"],
            start=date(2026, 1, 5),
            end=date(2026, 1, 6),
            fetch_fn=mock_fetch,
            session=test_session,
        )

        assert len(df) == 6
        # SPY was completely cached -> must NOT be fetched
        assert not any("SPY" in call[0] for call in fetched_calls)
        # QQQ was missing Jan 6 -> must be fetched for Jan 6 only
        qqq_calls = [call for call in fetched_calls if "QQQ" in call[0]]
        assert len(qqq_calls) == 1
        assert qqq_calls[0][1] == date(2026, 1, 6)
        assert qqq_calls[0][2] == date(2026, 1, 6)
        # GLD was completely absent -> must be fetched for Jan 5 to Jan 6
        gld_calls = [call for call in fetched_calls if "GLD" in call[0]]
        assert len(gld_calls) == 1
        assert gld_calls[0][1] == date(2026, 1, 5)
        assert gld_calls[0][2] == date(2026, 1, 6)

    @pytest.mark.asyncio
    async def test_cache_date_type_polymorphism(self, test_session: AsyncSession) -> None:
        """save_bars must accept date as ISO string, datetime.date, pd.Timestamp, and datetime.datetime."""
        cache = DailyBarCache()
        bars = [
            {"symbol": "SPY", "date": "2026-01-05", "open": 500, "high": 505, "low": 495, "close": 500, "volume": 100, "adjusted": True, "feed": "sip_delayed"},
            {"symbol": "QQQ", "date": date(2026, 1, 5), "open": 400, "high": 405, "low": 395, "close": 400, "volume": 100, "adjusted": True, "feed": "sip_delayed"},
            {"symbol": "GLD", "date": pd.Timestamp("2026-01-05"), "open": 180, "high": 185, "low": 175, "close": 180, "volume": 100, "adjusted": True, "feed": "sip_delayed"},
        ]
        saved = await cache.save_bars(test_session, bars)
        assert saved == 3

        df = await cache.get_bars(symbols=["SPY", "QQQ", "GLD"], start=date(2026, 1, 1), end=date(2026, 1, 10), session=test_session)
        assert len(df) == 3
        assert set(df["symbol"]) == {"SPY", "QQQ", "GLD"}

    def test_cache_feed_normalization_robustness(self) -> None:
        """Feed strings with varying casing, whitespace, or alias variants must normalize deterministically."""
        cache = DailyBarCache()
        assert cache._normalize_feed("sip") == "sip_delayed"
        assert cache._normalize_feed("SIP") == "sip_delayed"
        assert cache._normalize_feed("  sip_delayed  ") == "sip_delayed"
        assert cache._normalize_feed("IEX") == "iex"
        assert cache._normalize_feed("  iex\n") == "iex"

    @pytest.mark.asyncio
    async def test_cache_start_greater_than_end_empty_return(self) -> None:
        """Inverted date ranges (start > end) must immediately return an empty DataFrame with standard columns."""
        cache = DailyBarCache()
        df = await cache.get_or_fetch_daily_bars(
            symbols=["SPY"],
            start=date(2026, 1, 10),
            end=date(2026, 1, 5),
        )
        assert df.empty
        assert "symbol" in df.columns
        assert "close" in df.columns

    def test_cache_deduplication_and_sorting(self) -> None:
        """_to_dataframe must eliminate duplicate (symbol, date) pairs and guarantee deterministic sorting."""
        cache = DailyBarCache()
        records = [
            {"symbol": "SPY", "date": date(2026, 1, 6), "open": 502, "high": 507, "low": 501, "close": 506, "volume": 1000, "adjusted": True, "feed": "sip_delayed"},
            {"symbol": "SPY", "date": date(2026, 1, 5), "open": 500, "high": 505, "low": 498, "close": 502, "volume": 900, "adjusted": True, "feed": "sip_delayed"},
            # Duplicate update for Jan 5 with newer volume
            {"symbol": "SPY", "date": date(2026, 1, 5), "open": 500, "high": 505, "low": 498, "close": 502, "volume": 1500, "adjusted": True, "feed": "sip_delayed"},
        ]
        df = cache._to_dataframe(records)
        assert len(df) == 2
        assert df.iloc[0]["date"] == date(2026, 1, 5)
        assert df.iloc[0]["volume"] == 1500  # keep='last'
        assert df.iloc[1]["date"] == date(2026, 1, 6)

    def test_postgresql_dialect_on_conflict_compilation(self) -> None:
        """Verify PostgreSQL on_conflict_do_nothing compilation targets the (symbol, date, feed) unique constraint."""
        stmt = pg_insert(DailyBar).values([{
            "symbol": "SPY",
            "date": date(2026, 1, 5),
            "open": 500.0,
            "high": 505.0,
            "low": 495.0,
            "close": 500.0,
            "volume": 1000,
            "adjusted": True,
            "feed": "sip_delayed",
        }]).on_conflict_do_nothing(index_elements=["symbol", "date", "feed"])

        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (symbol, date, feed) DO NOTHING" in compiled


# ===========================================================================
# 3. Proxy Mapping Edge Cases & Invariance Tests
# ===========================================================================


class TestProxyMappingAdversarial:
    """Adversarial stress testing of ETF proxy mapping and mathematical invariances."""

    @pytest.mark.parametrize("raw_input, expected_trading, expected_data", [
        ("  SPY  ", "SPYM", "SPY"),
        ("\tspym\n", "SPYM", "SPY"),
        ("  qqq\r\n", "QQQM", "QQQ"),
        ("\tQQQM\t", "QQQM", "QQQ"),
        ("  gld  ", "GLDM", "GLD"),
        ("  GLDM  ", "GLDM", "GLD"),
        ("   ", "", ""),
        ("", "", ""),
    ])
    def test_proxy_whitespace_and_casing_resilience(
        self, raw_input: str, expected_trading: str, expected_data: str
    ) -> None:
        """Proxy resolution functions must strip whitespace, newlines, tabs, and normalize casing."""
        assert resolve_trading_proxy(raw_input) == expected_trading
        assert resolve_data_proxy(raw_input) == expected_data

    @pytest.mark.parametrize("ticker", [
        "BRK.B",
        "BRK/B",
        "BF.B",
        "BTC/USD",
        "EURUSD=X",
        "3M",
        "T-PR-A",
        "0005.HK",
    ])
    def test_proxy_non_standard_tickers_identity(self, ticker: str) -> None:
        """Non-canonical symbols with special characters or numbers must resolve to themselves without crashing."""
        assert resolve_trading_proxy(ticker) == ticker
        assert resolve_data_proxy(ticker) == ticker
        assert get_proxy_pair(ticker) == (ticker, ticker)
        assert is_proxy_symbol(ticker) is False
        assert is_canonical_symbol(ticker) is False

    @pytest.mark.parametrize("canonical_symbol", ["SPY", "QQQ", "GLD"])
    def test_proxy_round_trip_invariance(self, canonical_symbol: str) -> None:
        """Invariance 1: resolve_data_proxy(resolve_trading_proxy(S)) == S for all canonical symbols."""
        trading_proxy = resolve_trading_proxy(canonical_symbol)
        recovered_canonical = resolve_data_proxy(trading_proxy)
        assert recovered_canonical == canonical_symbol

    @pytest.mark.parametrize("symbol", [
        "SPY", "SPYM", "QQQ", "QQQM", "GLD", "GLDM", "AAPL", "MSFT", "NVDA", "BRK.B", ""
    ])
    def test_proxy_idempotency_trading_and_data(self, symbol: str) -> None:
        """Invariance 2 & 3: resolve_trading(resolve_trading(x)) == resolve_trading(x) and resolve_data(resolve_data(x)) == resolve_data(x)."""
        t1 = resolve_trading_proxy(symbol)
        t2 = resolve_trading_proxy(t1)
        assert t1 == t2

        d1 = resolve_data_proxy(symbol)
        d2 = resolve_data_proxy(d1)
        assert d1 == d2

    def test_proxy_pair_consistency_across_universe(self) -> None:
        """get_proxy_pair must return identical (signal, execution) tuple whether passed signal or execution symbol."""
        for sig, exc in CANONICAL_PROXIES.items():
            assert get_proxy_pair(sig) == (sig, exc)
            assert get_proxy_pair(exc) == (sig, exc)

        # Unmapped
        assert get_proxy_pair("NVDA") == ("NVDA", "NVDA")

    def test_proxy_directional_mapping_contract(self) -> None:
        """map_symbol must strictly adhere to signal_to_exec and exec_to_signal direction contracts."""
        assert map_symbol("SPY", direction="signal_to_exec") == "SPYM"
        assert map_symbol("SPYM", direction="exec_to_signal") == "SPY"
        assert map_symbol("AAPL", direction="signal_to_exec") == "AAPL"
        assert map_symbol("AAPL", direction="exec_to_signal") == "AAPL"

        with pytest.raises(ValueError, match="Dirección de mapeo desconocida"):
            map_symbol("SPY", direction="bidirectional")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_proxy_db_sync_whitespace_and_casing(self, test_session: AsyncSession) -> None:
        """ProxyMapper.sync_from_db must tolerate unnormalized whitespace and casing in database records."""
        test_session.add(UniverseSymbol(
            signal_symbol="  iwm  ",
            execution_symbol="  iwmm  ",
            cluster="small_cap",
            enabled=True,
            notes="Messy casing and whitespace",
            updated_at=datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC),
        ))
        await test_session.commit()

        mapper = ProxyMapper()
        await mapper.sync_from_db(test_session)

        assert mapper.resolve_trading("IWM") == "IWMM"
        assert mapper.resolve_trading("  iwm  ") == "IWMM"
        assert mapper.resolve_data("IWMM") == "IWM"
        assert mapper.resolve_data("  iwmm  ") == "IWM"
        # Canonical mappings unaffected
        assert mapper.resolve_trading("SPY") == "SPYM"
