"""Comprehensive unit, boundary, and mathematical precision test suite for pure technical indicators.

Verifies:
1. Analytical precision with tolerance <= 1e-6 against JSON and CSV reference fixtures.
2. Boundary and corner cases: empty series, single element, flat series (50.0 for RSI, 0.0 for ATR),
   monotonic trends (100.0 / 0.0 for RSI), volatile gaps, and division by zero.
3. Strict domain input validation and exception raising (ValueError, TypeError).
4. Synthetic bar creation and forward-fill helpers.
5. Strict Clock Policy compliance: no direct wall-clock calls.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from numpy.testing import assert_allclose

from tbot.indicators import (
    atr,
    create_synthetic_bar,
    ema,
    forward_fill_synthetic_bars,
    percentage_returns,
    realized_volatility_20d,
    relative_volume,
    rsi,
    sma,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def indicators_reference() -> dict[str, Any]:
    """Load JSON reference benchmarks."""
    json_path = FIXTURES_DIR / "indicators_reference.json"
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def oracle_50_bars(indicators_reference: dict[str, Any]) -> dict[str, Any]:
    """Retrieve 50-bar oracle reference series."""
    return indicators_reference["benchmarks"]["oracle_50_bars"]


@pytest.fixture(scope="session")
def reference_csv_bars() -> pd.DataFrame:
    """Load 30-bar reference CSV fixture."""
    csv_path = FIXTURES_DIR / "indicators_reference.csv"
    return pd.read_csv(csv_path)


# =====================================================================
# 1. Simple Moving Average (SMA) Tests
# =====================================================================


class TestSMA:
    def test_sma_matches_reference_simple_linear(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["simple_linear"]
        prices = pd.Series(bench["prices"], dtype=float)
        period = bench["period"]
        expected = np.array([np.nan if x is None else x for x in bench["expected_sma"]])

        result = sma(prices, period=period)
        assert isinstance(result, pd.Series)
        assert len(result) == len(prices)
        assert result.index.equals(prices.index)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_sma_matches_reference_stockcharts_10(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["stockcharts_ema_10"]
        prices = pd.Series(bench["prices"], dtype=float)
        period = bench["period"]
        expected = np.array([np.nan if x is None else x for x in bench["expected_sma"]])

        result = sma(prices, period=period)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_sma_matches_oracle_50_bars(self, oracle_50_bars: dict[str, Any]) -> None:
        prices = pd.Series(oracle_50_bars["prices"], dtype=float)
        expected_sma5 = np.array([np.nan if x is None else x for x in oracle_50_bars["sma_5"]])
        expected_sma20 = np.array([np.nan if x is None else x for x in oracle_50_bars["sma_20"]])

        res5 = sma(prices, period=5)
        res20 = sma(prices, period=20)
        assert_allclose(res5.to_numpy(), expected_sma5, atol=1e-6, rtol=1e-6, equal_nan=True)
        assert_allclose(res20.to_numpy(), expected_sma20, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_sma_constant_flat_series(self) -> None:
        prices = pd.Series([100.0] * 25, dtype=float)
        period = 5
        result = sma(prices, period=period)
        assert result.iloc[:4].isna().all()
        assert_allclose(result.iloc[4:].to_numpy(), np.full(21, 100.0), atol=1e-6)

    def test_sma_period_1_equals_input(self) -> None:
        prices = pd.Series([12.5, 14.2, 11.8, 16.9], dtype=float)
        result = sma(prices, period=1)
        assert_allclose(result.to_numpy(), prices.to_numpy(), atol=1e-6)

    def test_sma_empty_series(self) -> None:
        empty_s = pd.Series([], dtype=float)
        result = sma(empty_s, period=5)
        assert isinstance(result, pd.Series)
        assert result.empty
        assert result.dtype == np.float64

    def test_sma_shorter_than_period(self) -> None:
        short_s = pd.Series([10.0, 11.0, 12.0], dtype=float)
        result = sma(short_s, period=5)
        assert len(result) == 3
        assert result.isna().all()

    @pytest.mark.parametrize("invalid_period", [0, -1, -10])
    def test_sma_invalid_period_raises_value_error(self, invalid_period: int) -> None:
        prices = pd.Series([10.0, 11.0, 12.0], dtype=float)
        with pytest.raises(ValueError, match="period must be a positive integer"):
            sma(prices, period=invalid_period)

    def test_sma_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="series must be a pd.Series"):
            sma([10.0, 11.0, 12.0], period=3)  # type: ignore[arg-type]


# =====================================================================
# 2. Exponential Moving Average (EMA) Tests
# =====================================================================


class TestEMA:
    def test_ema_matches_reference_simple_linear(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["simple_linear"]
        prices = pd.Series(bench["prices"], dtype=float)
        period = bench["period"]
        expected = np.array(bench["expected_ema"], dtype=float)

        result = ema(prices, period=period)
        assert isinstance(result, pd.Series)
        assert len(result) == len(prices)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6)

    def test_ema_matches_reference_stockcharts_10(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["stockcharts_ema_10"]
        prices = pd.Series(bench["prices"], dtype=float)
        period = bench["period"]
        expected = np.array(bench["expected_ema"], dtype=float)

        result = ema(prices, period=period)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6)

    def test_ema_matches_oracle_50_bars(self, oracle_50_bars: dict[str, Any]) -> None:
        prices = pd.Series(oracle_50_bars["prices"], dtype=float)
        expected_ema5 = np.array(oracle_50_bars["ema_5"], dtype=float)
        expected_ema20 = np.array(oracle_50_bars["ema_20"], dtype=float)

        res5 = ema(prices, period=5)
        res20 = ema(prices, period=20)
        assert not res5.isna().any()
        assert not res20.isna().any()
        assert_allclose(res5.to_numpy(), expected_ema5, atol=1e-6, rtol=1e-6)
        assert_allclose(res20.to_numpy(), expected_ema20, atol=1e-6, rtol=1e-6)

    def test_ema_constant_flat_series(self) -> None:
        prices = pd.Series([75.0] * 20, dtype=float)
        period = 5
        result = ema(prices, period=period)
        assert not result.isna().any()
        assert_allclose(result.to_numpy(), np.full(20, 75.0), atol=1e-6)

    def test_ema_period_1_equals_input(self) -> None:
        prices = pd.Series([10.0, 20.0, 30.0, 40.0], dtype=float)
        result = ema(prices, period=1)
        assert_allclose(result.to_numpy(), prices.to_numpy(), atol=1e-6)

    def test_ema_first_element_matches_price(self) -> None:
        prices = pd.Series([500.0, 497.5, 501.48], dtype=float)
        result = ema(prices, period=14)
        assert math.isclose(result.iloc[0], prices.iloc[0], rel_tol=1e-6)

    def test_ema_empty_series(self) -> None:
        empty_s = pd.Series([], dtype=float)
        result = ema(empty_s, period=10)
        assert isinstance(result, pd.Series)
        assert result.empty

    @pytest.mark.parametrize("invalid_period", [0, -1, -5])
    def test_ema_invalid_period_raises_value_error(self, invalid_period: int) -> None:
        prices = pd.Series([10.0, 11.0], dtype=float)
        with pytest.raises(ValueError, match="period must be a positive integer"):
            ema(prices, period=invalid_period)

    def test_ema_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="series must be a pd.Series"):
            ema([10.0, 20.0], period=2)  # type: ignore[arg-type]


# =====================================================================
# 3. Wilder's Relative Strength Index (RSI) Tests
# =====================================================================


class TestRSI:
    def test_rsi_matches_oracle_50_bars(self, oracle_50_bars: dict[str, Any]) -> None:
        prices = pd.Series(oracle_50_bars["prices"], dtype=float)
        expected_rsi14 = np.array([np.nan if x is None else x for x in oracle_50_bars["rsi_14"]])

        result = rsi(prices, period=14)
        assert isinstance(result, pd.Series)
        assert len(result) == len(prices)
        assert_allclose(result.to_numpy(), expected_rsi14, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_rsi_flat_series_division_by_zero_mandates_50(
        self, indicators_reference: dict[str, Any]
    ) -> None:
        bench = indicators_reference["benchmarks"]["flat_series_30"]
        prices = pd.Series(bench["prices"], dtype=float)
        expected = np.array([np.nan if x is None else x for x in bench["expected_rsi_14"]])

        result = rsi(prices, period=14)
        assert result.iloc[:1].isna().all()
        assert_allclose(result.iloc[1:].to_numpy(), np.full(29, 50.0), atol=1e-6)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, equal_nan=True)

    def test_rsi_monotonic_up_mandates_100(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["monotonic_up_20"]
        prices = pd.Series(bench["prices"], dtype=float)
        expected = np.array([np.nan if x is None else x for x in bench["expected_rsi_14"]])

        result = rsi(prices, period=14)
        assert result.iloc[:1].isna().all()
        assert_allclose(result.iloc[1:].to_numpy(), np.full(19, 100.0), atol=1e-6)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, equal_nan=True)

    def test_rsi_monotonic_down_mandates_0(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["monotonic_down_20"]
        prices = pd.Series(bench["prices"], dtype=float)
        expected = np.array([np.nan if x is None else x for x in bench["expected_rsi_14"]])

        result = rsi(prices, period=14)
        assert result.iloc[:1].isna().all()
        assert_allclose(result.iloc[1:].to_numpy(), np.full(19, 0.0), atol=1e-6)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, equal_nan=True)

    def test_rsi_bounds_invariant_on_random_walk(self) -> None:
        np.random.seed(123)
        prices = pd.Series(100.0 + np.cumsum(np.random.randn(100)), dtype=float)
        result = rsi(prices, period=14)
        valid = result.dropna()
        assert (valid >= 0.0).all()
        assert (valid <= 100.0).all()

    def test_rsi_empty_series(self) -> None:
        empty_s = pd.Series([], dtype=float)
        result = rsi(empty_s, period=14)
        assert result.empty

    def test_rsi_single_element_series(self) -> None:
        single = pd.Series([100.0], dtype=float)
        result = rsi(single, period=14)
        assert len(result) == 1
        assert result.isna().iloc[0]

    @pytest.mark.parametrize("invalid_period", [0, -1])
    def test_rsi_invalid_period_raises_value_error(self, invalid_period: int) -> None:
        prices = pd.Series([10.0, 11.0, 12.0], dtype=float)
        with pytest.raises(ValueError, match="period must be a positive integer"):
            rsi(prices, period=invalid_period)

    def test_rsi_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="series must be a pd.Series"):
            rsi([10.0, 11.0], period=14)  # type: ignore[arg-type]


# =====================================================================
# 4. Wilder's Average True Range (ATR) Tests
# =====================================================================


class TestATR:
    def test_atr_matches_oracle_50_bars(self, oracle_50_bars: dict[str, Any]) -> None:
        high = pd.Series(oracle_50_bars["highs"], dtype=float)
        low = pd.Series(oracle_50_bars["lows"], dtype=float)
        close = pd.Series(oracle_50_bars["prices"], dtype=float)
        expected_atr14 = np.array(oracle_50_bars["atr_14"], dtype=float)

        result = atr(high, low, close, period=14)
        assert isinstance(result, pd.Series)
        assert len(result) == len(high)
        assert not result.isna().any()
        assert_allclose(result.to_numpy(), expected_atr14, atol=1e-6, rtol=1e-6)

    def test_atr_single_bar_input(self) -> None:
        high_s = pd.Series([105.0], dtype=float)
        low_s = pd.Series([95.0], dtype=float)
        close_s = pd.Series([100.0], dtype=float)

        # Single bar: TR_0 = H_0 - L_0 = 10.0
        res = atr(high_s, low_s, close_s, period=14)
        assert len(res) == 1
        assert_allclose(res.iloc[0], 10.0, atol=1e-6)

    def test_atr_period_1_equals_true_range(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["volatile_gap_bars"]
        bars = pd.DataFrame(bench["bars"])
        high_s = bars["high"]
        low_s = bars["low"]
        close_s = bars["close"]
        expected_tr = np.array(bench["expected_tr"], dtype=float)

        res_p1 = atr(high_s, low_s, close_s, period=1)
        assert_allclose(res_p1.to_numpy(), expected_tr, atol=1e-6)

    def test_atr_gap_up_and_gap_down_handling(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["volatile_gap_bars"]
        bars = pd.DataFrame(bench["bars"])
        high_s = bars["high"]
        low_s = bars["low"]
        close_s = bars["close"]
        expected_atr2 = np.array(bench["expected_atr_2"], dtype=float)

        res_p2 = atr(high_s, low_s, close_s, period=2)
        assert_allclose(res_p2.to_numpy(), expected_atr2, atol=1e-6)

    def test_atr_flat_series_returns_zero(self, indicators_reference: dict[str, Any]) -> None:
        bench = indicators_reference["benchmarks"]["flat_series_30"]
        p = pd.Series(bench["prices"], dtype=float)
        expected = np.array(bench["expected_atr_14"], dtype=float)

        result = atr(high=p, low=p, close=p, period=14)
        assert not result.isna().any()
        assert_allclose(result.to_numpy(), expected, atol=1e-6)

    def test_atr_empty_series(self) -> None:
        e = pd.Series([], dtype=float)
        result = atr(e, e, e, period=14)
        assert result.empty

    def test_atr_mismatched_lengths_raises_value_error(self) -> None:
        high_s = pd.Series([10.0, 11.0])
        low_s = pd.Series([9.0])
        close_s = pd.Series([9.5, 10.5])
        with pytest.raises(ValueError, match="matching lengths"):
            atr(high_s, low_s, close_s, period=2)

    def test_atr_inverted_prices_raises_value_error(self) -> None:
        high_s = pd.Series([10.0, 8.0, 12.0])  # high(8.0) < low(9.0)
        low_s = pd.Series([9.0, 9.0, 11.0])
        close_s = pd.Series([9.5, 8.5, 11.5])
        with pytest.raises(ValueError, match="high price cannot be lower than low price"):
            atr(high_s, low_s, close_s, period=2)

    def test_atr_negative_prices_raises_value_error(self) -> None:
        high_s = pd.Series([-1.0, 5.0])
        low_s = pd.Series([-2.0, 4.0])
        close_s = pd.Series([-1.5, 4.5])
        with pytest.raises(ValueError, match="Prices must be non-negative"):
            atr(high_s, low_s, close_s, period=2)

    def test_atr_invalid_period_raises_value_error(self) -> None:
        p = pd.Series([10.0, 11.0], dtype=float)
        with pytest.raises(ValueError, match="period must be a positive integer"):
            atr(p, p, p, period=0)

    def test_atr_invalid_type_raises_type_error(self) -> None:
        p = pd.Series([10.0, 11.0], dtype=float)
        with pytest.raises(TypeError, match="high must be a pd.Series"):
            atr([10.0, 11.0], p, p, period=2)  # type: ignore[arg-type]


# =====================================================================
# 5. Percentage Returns Tests
# =====================================================================


class TestPercentageReturns:
    def test_percentage_returns_matches_reference_simple_linear(
        self, indicators_reference: dict[str, Any]
    ) -> None:
        bench = indicators_reference["benchmarks"]["simple_linear"]
        prices = pd.Series(bench["prices"], dtype=float)
        expected = np.array([np.nan if x is None else x for x in bench["expected_returns_1"]])

        result = percentage_returns(prices, periods=1)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_percentage_returns_matches_reference_csv(self, reference_csv_bars: pd.DataFrame) -> None:
        close = reference_csv_bars["close"]
        expected = reference_csv_bars["returns_1"].to_numpy()

        result = percentage_returns(close, periods=1)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_percentage_returns_constant_flat_returns_zero(
        self, indicators_reference: dict[str, Any]
    ) -> None:
        bench = indicators_reference["benchmarks"]["flat_series_30"]
        prices = pd.Series(bench["prices"], dtype=float)
        expected = np.array([np.nan if x is None else x for x in bench["expected_returns_1"]])

        result = percentage_returns(prices, periods=1)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, equal_nan=True)

    def test_percentage_returns_initial_nans(self) -> None:
        prices = pd.Series([10.0, 12.0, 15.0, 20.0, 25.0], dtype=float)
        res_p2 = percentage_returns(prices, periods=2)
        assert res_p2.iloc[:2].isna().all()
        assert not res_p2.iloc[2:].isna().any()

    def test_percentage_returns_empty_series(self) -> None:
        empty_s = pd.Series([], dtype=float)
        result = percentage_returns(empty_s, periods=1)
        assert result.empty

    @pytest.mark.parametrize("invalid_periods", [0, -1, -5])
    def test_percentage_returns_invalid_periods_raises_value_error(self, invalid_periods: int) -> None:
        prices = pd.Series([10.0, 11.0], dtype=float)
        with pytest.raises(ValueError, match="periods must be a positive integer"):
            percentage_returns(prices, periods=invalid_periods)

    def test_percentage_returns_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="series must be a pd.Series"):
            percentage_returns([10.0, 11.0], periods=1)  # type: ignore[arg-type]


# =====================================================================
# 6. 20-Day Realized Volatility Tests
# =====================================================================


class TestRealizedVolatility20d:
    def test_realized_volatility_matches_oracle_50_bars(self, oracle_50_bars: dict[str, Any]) -> None:
        close = pd.Series(oracle_50_bars["prices"], dtype=float)
        expected_vol = np.array([np.nan if x is None else x for x in oracle_50_bars["vol_20d"]])

        result = realized_volatility_20d(close, annualized=True, window=20)
        assert_allclose(result.to_numpy(), expected_vol, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_realized_volatility_matches_reference_csv(self, reference_csv_bars: pd.DataFrame) -> None:
        close = reference_csv_bars["close"]
        expected = reference_csv_bars["volatility_20d"].to_numpy()

        result = realized_volatility_20d(close, annualized=True, window=20)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, rtol=1e-6, equal_nan=True)

    def test_realized_volatility_constant_flat_returns_zero(
        self, indicators_reference: dict[str, Any]
    ) -> None:
        bench = indicators_reference["benchmarks"]["flat_series_30"]
        prices = pd.Series(bench["prices"], dtype=float)
        expected = np.array([np.nan if x is None else x for x in bench["expected_volatility_20d"]])

        result = realized_volatility_20d(prices, annualized=True, window=20)
        assert_allclose(result.to_numpy(), expected, atol=1e-6, equal_nan=True)

    def test_realized_volatility_annualized_ratio(self) -> None:
        np.random.seed(42)
        close = pd.Series(100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.015, 40))))
        res_ann = realized_volatility_20d(close, annualized=True, window=20)
        res_raw = realized_volatility_20d(close, annualized=False, window=20)

        ratio = res_ann / res_raw
        valid_ratios = ratio.dropna()
        assert_allclose(valid_ratios.to_numpy(), np.full(len(valid_ratios), np.sqrt(252)), atol=1e-6)

    def test_realized_volatility_shorter_than_window(self) -> None:
        short_s = pd.Series([100.0] * 15, dtype=float)
        result = realized_volatility_20d(short_s, window=20)
        assert len(result) == 15
        assert result.isna().all()

    def test_realized_volatility_empty_series(self) -> None:
        empty_s = pd.Series([], dtype=float)
        result = realized_volatility_20d(empty_s, window=20)
        assert result.empty

    @pytest.mark.parametrize("invalid_window", [0, 1, -5])
    def test_realized_volatility_invalid_window_raises_value_error(self, invalid_window: int) -> None:
        prices = pd.Series([100.0] * 10, dtype=float)
        with pytest.raises(ValueError, match="window must be >= 2"):
            realized_volatility_20d(prices, window=invalid_window)

    def test_realized_volatility_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="close_series must be a pd.Series"):
            realized_volatility_20d([100.0, 101.0], window=20)  # type: ignore[arg-type]


# =====================================================================
# 7. Relative Volume (RVOL) Tests
# =====================================================================


class TestRelativeVolume:
    def test_relative_volume_standard_calculation(self) -> None:
        hist = pd.Series([1000.0, 1200.0, 800.0, 1000.0, 1000.0], dtype=float)  # mean = 1000.0
        rvol = relative_volume(current_volume=1500.0, historical_volumes=hist)
        assert_allclose(rvol, 1.5, atol=1e-6)

    def test_relative_volume_zero_mean_zero_current(self) -> None:
        hist = pd.Series([0.0, 0.0, 0.0], dtype=float)
        rvol = relative_volume(0.0, hist)
        assert_allclose(rvol, 1.0, atol=1e-6)

    def test_relative_volume_zero_mean_positive_current(self) -> None:
        hist = pd.Series([0.0, 0.0, 0.0], dtype=float)
        rvol = relative_volume(50000.0, hist)
        assert_allclose(rvol, 0.0, atol=1e-6)

    def test_relative_volume_empty_historical_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="historical_volumes cannot be empty"):
            relative_volume(100.0, pd.Series([], dtype=float))

    def test_relative_volume_negative_volume_raises_value_error(self) -> None:
        hist = pd.Series([100.0, 200.0], dtype=float)
        with pytest.raises(ValueError, match="current_volume must be non-negative"):
            relative_volume(-50.0, hist)

    def test_relative_volume_negative_history_raises_value_error(self) -> None:
        hist = pd.Series([100.0, -200.0], dtype=float)
        with pytest.raises(ValueError, match="historical_volumes cannot contain negative values"):
            relative_volume(50.0, hist)

    def test_relative_volume_invalid_type_raises_type_error(self) -> None:
        hist = pd.Series([100.0, 200.0], dtype=float)
        with pytest.raises(TypeError, match="current_volume must be numeric"):
            relative_volume("invalid", hist)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="historical_volumes must be a pd.Series"):
            relative_volume(100.0, [100.0, 200.0])  # type: ignore[arg-type]


# =====================================================================
# 8. Synthetic Bar Generation Tests
# =====================================================================


class TestSyntheticBar:
    def test_create_synthetic_bar_fields_and_types(self) -> None:
        ts = datetime(2026, 9, 20, 15, 30, 0, tzinfo=UTC)
        price = Decimal("450.25")
        bar = create_synthetic_bar(timestamp=ts, close_price=price)

        assert isinstance(bar, dict)
        assert bar["timestamp"] == ts
        assert bar["open"] == price
        assert bar["high"] == price
        assert bar["low"] == price
        assert bar["close"] == price
        assert bar["volume"] == Decimal("0")
        assert bar["is_synthetic"] is True

    def test_create_synthetic_bar_with_atr_continuity(self) -> None:
        ts0 = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)
        bars = [create_synthetic_bar(ts0, Decimal("100.0")) for _ in range(20)]
        df = pd.DataFrame(bars)
        high_s = df["high"].astype(float)
        low_s = df["low"].astype(float)
        close_s = df["close"].astype(float)

        res_atr = atr(high_s, low_s, close_s, period=14)
        assert not res_atr.isna().any()
        assert_allclose(res_atr.to_numpy(), np.zeros(20), atol=1e-6)

    def test_create_synthetic_bar_naive_datetime_raises_value_error(self) -> None:
        naive_ts = datetime(2026, 9, 20, 12, 0, 0)
        with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
            create_synthetic_bar(naive_ts, Decimal("100.0"))

    def test_create_synthetic_bar_non_positive_price_raises_value_error(self) -> None:
        ts = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="close_price must be positive"):
            create_synthetic_bar(ts, Decimal("0.0"))
        with pytest.raises(ValueError, match="close_price must be positive"):
            create_synthetic_bar(ts, Decimal("-10.0"))

    def test_create_synthetic_bar_invalid_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="timestamp must be a datetime instance"):
            create_synthetic_bar("2026-09-20T12:00:00Z", Decimal("100.0"))  # type: ignore[arg-type]
        ts = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
        with pytest.raises(TypeError, match="close_price must be Decimal or float"):
            create_synthetic_bar(ts, "invalid")  # type: ignore[arg-type]


# =====================================================================
# 9. Forward Fill Synthetic Bars Helper Tests
# =====================================================================


class TestForwardFillSyntheticBars:
    def test_forward_fill_synthetic_bars_reindexing(self) -> None:
        idx_present = pd.date_range("2026-09-20 09:30", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0],
                "high": [102.0, 103.0, 104.0],
                "low": [99.0, 100.0, 101.0],
                "close": [101.0, 102.0, 103.0],
                "volume": [1000.0, 1200.0, 1100.0],
            },
            index=idx_present,
        )

        expected_idx = pd.date_range("2026-09-20 09:30", periods=5, freq="5min", tz="UTC")
        filled = forward_fill_synthetic_bars(df, expected_idx)

        assert len(filled) == 5
        assert filled.index.equals(expected_idx)
        # Original bars
        assert not filled.iloc[:3]["is_synthetic"].any()
        # Synthetic filled bars at 09:45 and 09:50
        assert filled.iloc[3:]["is_synthetic"].all()
        assert (filled.iloc[3:]["volume"] == 0.0).all()
        assert (filled.iloc[3:]["close"] == 103.0).all()
        assert (filled.iloc[3:]["open"] == 103.0).all()
        assert (filled.iloc[3:]["high"] == 103.0).all()
        assert (filled.iloc[3:]["low"] == 103.0).all()

    def test_forward_fill_synthetic_bars_empty(self) -> None:
        expected_idx = pd.date_range("2026-09-20 09:30", periods=3, freq="5min", tz="UTC")
        df_empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        filled = forward_fill_synthetic_bars(df_empty, expected_idx)
        assert len(filled) == 3
        assert filled["is_synthetic"].all()

    def test_forward_fill_synthetic_bars_missing_columns_raises_value_error(self) -> None:
        expected_idx = pd.date_range("2026-09-20 09:30", periods=3, freq="5min", tz="UTC")
        df_bad = pd.DataFrame({"open": [1.0], "close": [1.0]})
        with pytest.raises(ValueError, match="df must contain column"):
            forward_fill_synthetic_bars(df_bad, expected_idx)

    def test_forward_fill_synthetic_bars_invalid_type_raises_type_error(self) -> None:
        expected_idx = pd.date_range("2026-09-20 09:30", periods=3, freq="5min", tz="UTC")
        with pytest.raises(TypeError, match="df must be a pd.DataFrame"):
            forward_fill_synthetic_bars([1, 2, 3], expected_idx)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="expected_index must be a pd.DatetimeIndex"):
            forward_fill_synthetic_bars(pd.DataFrame(), [1, 2, 3])  # type: ignore[arg-type]
