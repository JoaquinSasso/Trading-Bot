"""Pure, deterministic technical indicators implemented with NumPy and pandas.

Operates on raw pandas Series and NumPy arrays without external TA dependencies.
All calculations are vectorized, numerically stable, and adhere strictly to the
project specification and reference fixtures.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


def _validate_series(series: Any, name: str = "series") -> None:
    if not isinstance(series, pd.Series):
        raise TypeError(f"{name} must be a pd.Series, got {type(series).__name__}")


def _validate_positive_int(val: Any, name: str = "period") -> None:
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise ValueError(f"{name} must be a positive integer >= 1, got {val}")


def sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average (SMA).

    Args:
        series: Pandas Series containing price data.
        period: Rolling window size (positive integer >= 1).

    Returns:
        pd.Series of type float64 with first period - 1 elements as NaN.
    """
    _validate_series(series, "series")
    _validate_positive_int(period, "period")
    if series.empty:
        return pd.Series(dtype=float, index=series.index, name=series.name)
    res = series.astype(float).rolling(window=period, min_periods=period).mean()
    res.name = series.name
    return res


def ema(series: pd.Series, period: int, adjust: bool = False) -> pd.Series:
    """Calculate Exponential Moving Average (EMA) with alpha = 2 / (period + 1).

    Args:
        series: Pandas Series containing price data.
        period: Smoothing period (positive integer >= 1).
        adjust: Whether to divide by decaying adjustment factor (default False).

    Returns:
        pd.Series of type float64 with exponential smoothing.
    """
    _validate_series(series, "series")
    _validate_positive_int(period, "period")
    if series.empty:
        return pd.Series(dtype=float, index=series.index, name=series.name)
    alpha = 2.0 / (period + 1.0)
    res = series.astype(float).ewm(alpha=alpha, adjust=adjust).mean()
    res.name = series.name
    return res


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Wilder's Smoothed Relative Strength Index (RSI) with alpha = 1 / period.

    Handles division by zero:
        - Flat series (avg_gain == 0 and avg_loss == 0) -> 50.0
        - Monotonic up (avg_loss == 0 and avg_gain > 0) -> 100.0
        - Monotonic down (avg_gain == 0 and avg_loss > 0) -> 0.0

    Args:
        series: Pandas Series containing price data.
        period: Smoothing period (default 14, positive integer >= 1).

    Returns:
        pd.Series bounded in [0.0, 100.0] with index 0 as NaN.
    """
    _validate_series(series, "series")
    _validate_positive_int(period, "period")
    if series.empty:
        return pd.Series(dtype=float, index=series.index, name=series.name)
    if len(series) == 1:
        return pd.Series(np.nan, index=series.index, dtype=float, name=series.name)

    diff = series.astype(float).diff()
    gain = diff.clip(lower=0.0)
    loss = (-diff).clip(lower=0.0)

    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()

    total = avg_gain + avg_loss
    out = pd.Series(np.nan, index=series.index, dtype=float, name=series.name)

    both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
    loss_zero = (avg_loss == 0.0) & (avg_gain > 0.0)
    gain_zero = (avg_gain == 0.0) & (avg_loss > 0.0)
    normal = (~both_zero) & (~loss_zero) & (~gain_zero) & (~avg_gain.isna()) & (~avg_loss.isna())

    out[both_zero] = 50.0
    out[loss_zero] = 100.0
    out[gain_zero] = 0.0
    out[normal] = 100.0 * (avg_gain[normal] / total[normal])

    out.name = series.name
    return out


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Calculate Wilder's Average True Range (ATR).

    True Range = max(high - low, abs(high - close_prev), abs(low - close_prev))
    with TR_0 = high_0 - low_0.
    Smoothing uses alpha = 1 / period with adjust=False.

    Args:
        high: High prices series.
        low: Low prices series.
        close: Close prices series.
        period: Smoothing period (default 14, positive integer >= 1).

    Returns:
        pd.Series of type float64 with calculated ATR values.
    """
    _validate_series(high, "high")
    _validate_series(low, "low")
    _validate_series(close, "close")
    _validate_positive_int(period, "period")

    if len(high) != len(low) or len(high) != len(close):
        raise ValueError(
            f"Input series must have matching lengths: high={len(high)}, low={len(low)}, close={len(close)}"
        )
    if high.empty:
        return pd.Series(dtype=float, index=close.index, name="atr")

    high_f = high.astype(float)
    low_f = low.astype(float)
    close_f = close.astype(float)

    if (high_f < low_f).any():
        raise ValueError("Invalid price data: high price cannot be lower than low price")
    if (high_f < 0.0).any() or (low_f < 0.0).any() or (close_f < 0.0).any():
        raise ValueError("Prices must be non-negative")

    prev_close = close_f.shift(1)
    tr0 = high_f - low_f
    tr1 = (high_f - prev_close).abs()
    tr2 = (low_f - prev_close).abs()

    tr = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
    alpha = 1.0 / period
    res = tr.ewm(alpha=alpha, adjust=False).mean()
    res.name = "atr"
    return res


def percentage_returns(series: pd.Series, periods: int = 1) -> pd.Series:
    """Calculate percentage returns (P_t - P_{t-k}) / P_{t-k}.

    Args:
        series: Price series.
        periods: Shift lag k (default 1, positive integer >= 1).

    Returns:
        pd.Series of fractional returns with first k elements as NaN.
    """
    _validate_series(series, "series")
    _validate_positive_int(periods, "periods")
    if series.empty:
        return pd.Series(dtype=float, index=series.index, name=series.name)

    s = series.astype(float)
    ret = s.pct_change(periods=periods, fill_method=None)
    ret = ret.replace([np.inf, -np.inf], np.nan)
    ret.name = series.name
    return ret


def realized_volatility_20d(
    close_series: pd.Series,
    annualized: bool = True,
    window: int = 20,
) -> pd.Series:
    """Calculate 20-day realized volatility of returns.

    Sample standard deviation (ddof=1) of 1-day percentage returns over a rolling window.
    Annualized with sqrt(252) if annualized=True.

    Args:
        close_series: Daily close prices series.
        annualized: Whether to annualize with sqrt(252) (default True).
        window: Rolling window in trading days (default 20, positive integer >= 2).

    Returns:
        pd.Series of realized volatility.
    """
    _validate_series(close_series, "close_series")
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    if close_series.empty:
        return pd.Series(dtype=float, index=close_series.index, name=close_series.name)

    ret = percentage_returns(close_series, periods=1)
    vol = ret.rolling(window=window, min_periods=window).std(ddof=1)
    if annualized:
        vol = vol * np.sqrt(252.0)
    vol.name = close_series.name
    return vol


def relative_volume(
    current_volume: float | int,
    historical_volumes: pd.Series,
) -> float:
    """Calculate Relative Volume (RVOL = current / historical_average).

    Args:
        current_volume: Volume of current bar or session (non-negative number).
        historical_volumes: Series of historical volume observations.

    Returns:
        Float RVOL ratio. Returns 1.0 if both current and historical mean are 0.
        Returns 0.0 if historical mean is 0 and current volume is positive.
    """
    _validate_series(historical_volumes, "historical_volumes")
    if isinstance(current_volume, bool) or not isinstance(current_volume, (int, float, Decimal)):
        raise TypeError(f"current_volume must be numeric, got {type(current_volume).__name__}")
    if float(current_volume) < 0.0:
        raise ValueError(f"current_volume must be non-negative, got {current_volume}")

    valid_hist = historical_volumes.dropna().astype(float)
    if valid_hist.empty:
        raise ValueError("historical_volumes cannot be empty")
    if (valid_hist < 0.0).any():
        raise ValueError("historical_volumes cannot contain negative values")

    avg_vol = float(valid_hist.mean())
    if avg_vol == 0.0:
        return 1.0 if float(current_volume) == 0.0 else 0.0
    return float(float(current_volume) / avg_vol)


def create_synthetic_bar(
    timestamp: datetime,
    close_price: Decimal | float,
) -> dict[str, Any]:
    """Generate an auxiliary flat support bar with volume 0 for indicator continuity.

    Args:
        timestamp: Timezone-aware timestamp for the bar.
        close_price: Price to replicate across open, high, low, close.

    Returns:
        dict with keys: timestamp, open, high, low, close, volume, is_synthetic.
    """
    if not isinstance(timestamp, datetime):
        raise TypeError(f"timestamp must be a datetime instance, got {type(timestamp).__name__}")
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    if isinstance(close_price, bool) or not isinstance(close_price, (Decimal, float, int)):
        raise TypeError(f"close_price must be Decimal or float, got {type(close_price).__name__}")

    price = Decimal(str(close_price)) if not isinstance(close_price, Decimal) else close_price
    if price <= Decimal(0):
        raise ValueError(f"close_price must be positive, got {close_price}")

    return {
        "timestamp": timestamp,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": Decimal("0"),
        "is_synthetic": True,
    }


def forward_fill_synthetic_bars(
    df: pd.DataFrame,
    expected_index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Reindex and fill temporal gaps with synthetic bars (volume 0, is_synthetic True).

    Args:
        df: DataFrame of bars with columns ['open', 'high', 'low', 'close', 'volume'].
        expected_index: Full regular DatetimeIndex expected for the session.

    Returns:
        pd.DataFrame with complete index and is_synthetic boolean column.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pd.DataFrame")
    if not isinstance(expected_index, pd.DatetimeIndex):
        raise TypeError("expected_index must be a pd.DatetimeIndex")

    required_cols = ["open", "high", "low", "close", "volume"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"df must contain column '{col}'")

    if df.empty:
        empty_df = pd.DataFrame(index=expected_index, columns=df.columns)
        empty_df["is_synthetic"] = True
        empty_df["volume"] = 0.0
        return empty_df

    result = df.copy()
    if "is_synthetic" not in result.columns:
        result["is_synthetic"] = False

    reindexed = result.reindex(expected_index)
    synthetic_mask = reindexed["is_synthetic"].isna() | reindexed["close"].isna()

    reindexed["close"] = reindexed["close"].ffill()
    reindexed["open"] = reindexed["open"].combine_first(reindexed["close"])
    reindexed["high"] = reindexed["high"].combine_first(reindexed["close"])
    reindexed["low"] = reindexed["low"].combine_first(reindexed["close"])
    reindexed.loc[synthetic_mask, "volume"] = 0.0
    reindexed.loc[synthetic_mask, "is_synthetic"] = True

    return reindexed
