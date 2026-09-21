"""Unit tests for 1m to 5m bar resampling and VWAP calculation."""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from tbot.data.resampler import aggregate_1m_to_5m, resample_1m_to_5m


def test_resample_exact_5_minutes_label_right() -> None:
    # 09:30:00 to 09:34:00 (5 bars of 1 min)
    base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
    bars_1m = []
    prices = [100.0, 102.5, 99.0, 101.0, 103.0]
    for i, p in enumerate(prices):
        bars_1m.append(
            {
                "timestamp": base_time + timedelta(minutes=i),
                "symbol": "SPY",
                "open": p,
                "high": p + 1.0,
                "low": p - 1.0,
                "close": p + 0.5,
                "volume": 1000.0,
                "vwap": p + 0.25,
                "trade_count": 10,
            }
        )
    df_1m = pd.DataFrame(bars_1m)

    res = resample_1m_to_5m(df_1m)
    assert len(res) == 1
    row = res.iloc[0]

    # Timestamp must be right-closed (09:35:00)
    assert row["timestamp"] == pd.Timestamp("2026-09-21 09:35:00+00:00")
    assert row["symbol"] == "SPY"
    assert row["open"] == 100.0
    assert row["high"] == 104.0  # max(p + 1.0) = 103 + 1
    assert row["low"] == 98.0  # min(p - 1.0) = 99 - 1
    assert row["close"] == 103.5  # last close = 103 + 0.5
    assert row["volume"] == 5000.0
    assert row["trade_count"] == 50
    assert bool(row["is_synthetic"]) is False


def test_resample_next_bucket_labeling() -> None:
    # 09:35 to 09:39 -> labeled 09:40
    base_time = datetime(2026, 9, 21, 9, 35, 0, tzinfo=UTC)
    bars_1m = []
    for i in range(5):
        bars_1m.append(
            {
                "timestamp": base_time + timedelta(minutes=i),
                "open": 200.0,
                "high": 201.0,
                "low": 199.0,
                "close": 200.5,
                "volume": 500.0,
            }
        )
    df_1m = pd.DataFrame(bars_1m)
    res = resample_1m_to_5m(df_1m)
    assert len(res) == 1
    assert res.iloc[0]["timestamp"] == pd.Timestamp("2026-09-21 09:40:00+00:00")


def test_vwap_volume_weighted_calculation() -> None:
    base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
    df_1m = pd.DataFrame(
        [
            {
                "timestamp": base_time,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000.0,
                "vwap": 100.0,
            },
            {
                "timestamp": base_time + timedelta(minutes=1),
                "open": 105.0,
                "high": 106.0,
                "low": 104.0,
                "close": 105.0,
                "volume": 3000.0,
                "vwap": 105.0,
            },
        ]
    )
    res = resample_1m_to_5m(df_1m)
    assert len(res) == 1
    # Expected VWAP: (100.0 * 1000 + 105.0 * 3000) / 4000 = 415,000 / 4000 = 103.75
    assert abs(res.iloc[0]["vwap"] - 103.75) < 1e-6


def test_vwap_zero_volume_fallback_to_close() -> None:
    base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
    df_1m = pd.DataFrame(
        [
            {
                "timestamp": base_time + timedelta(minutes=i),
                "open": 50.0,
                "high": 51.0,
                "low": 49.0,
                "close": 50.5,
                "volume": 0.0,
            }
            for i in range(5)
        ]
    )
    res = resample_1m_to_5m(df_1m)
    assert len(res) == 1
    assert res.iloc[0]["volume"] == 0.0
    assert res.iloc[0]["vwap"] == 50.5  # Falls back to close


def test_missing_1m_bars_within_bucket() -> None:
    # Prints only at 09:30 and 09:34
    base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
    df_1m = pd.DataFrame(
        [
            {
                "timestamp": base_time,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 200.0,
            },
            {
                "timestamp": base_time + timedelta(minutes=4),
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.5,
                "volume": 300.0,
            },
        ]
    )
    res = resample_1m_to_5m(df_1m)
    assert len(res) == 1
    row = res.iloc[0]
    assert row["open"] == 100.0
    assert row["high"] == 103.0
    assert row["low"] == 99.0
    assert row["close"] == 102.5
    assert row["volume"] == 500.0


def test_empty_dataframe_returns_empty_with_columns() -> None:
    df_empty = pd.DataFrame()
    res = resample_1m_to_5m(df_empty)
    assert res.empty
    expected_cols = [
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "trade_count",
        "is_synthetic",
    ]
    for c in expected_cols:
        assert c in res.columns


def test_multi_symbol_resampling() -> None:
    base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
    bars = []
    for sym, price in [("SPY", 500.0), ("QQQ", 450.0)]:
        for i in range(5):
            bars.append(
                {
                    "timestamp": base_time + timedelta(minutes=i),
                    "symbol": sym,
                    "open": price,
                    "high": price + 1.0,
                    "low": price - 1.0,
                    "close": price + 0.5,
                    "volume": 100.0,
                }
            )
    df = pd.DataFrame(bars)
    res = aggregate_1m_to_5m(df)
    assert len(res) == 2
    assert set(res["symbol"]) == {"SPY", "QQQ"}


def test_synthetic_gap_filling() -> None:
    # 09:30-09:34 (label 09:35) and 09:40-09:44 (label 09:45). Missing 09:35-09:39 (label 09:40).
    base_time = datetime(2026, 9, 21, 9, 30, 0, tzinfo=UTC)
    bars = [
        # Bucket 1: 09:35
        {
            "timestamp": base_time,
            "symbol": "SPY",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 100.0,
        },
        # Bucket 3: 09:45 (gap between 09:35 and 09:45)
        {
            "timestamp": base_time + timedelta(minutes=10),
            "symbol": "SPY",
            "open": 102.0,
            "high": 103.0,
            "low": 101.0,
            "close": 102.5,
            "volume": 150.0,
        },
    ]
    df = pd.DataFrame(bars)
    res = resample_1m_to_5m(df, fill_missing=True)

    assert len(res) == 3
    # Mid-bar at 09:40 should be synthetic
    mid_bar = res.iloc[1]
    assert mid_bar["timestamp"] == pd.Timestamp("2026-09-21 09:40:00+00:00")
    assert bool(mid_bar["is_synthetic"]) is True
    assert mid_bar["volume"] == 0.0
    assert mid_bar["close"] == 100.5  # Forward filled from prior close
    assert mid_bar["open"] == 100.5


def test_invalid_dataframe_missing_columns() -> None:
    df = pd.DataFrame({"timestamp": [datetime.now(UTC)], "price": [100.0]})
    with pytest.raises(ValueError, match="Missing required price column"):
        resample_1m_to_5m(df)
