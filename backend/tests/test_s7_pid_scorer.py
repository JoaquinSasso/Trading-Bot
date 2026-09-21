"""Tests unitarios para la estrategia S7: PID Scorer de Doble Sistema."""

import numpy as np
import pandas as pd
import pytest

from tbot.strategies.s7_pid_scorer import (
    compute_pid_terms_for_asset,
    score_universe_pid,
    zscore_series,
)


def generate_synthetic_stock(n_days=100, trend=0.001, vol=0.015, seed=42):
    np.random.seed(seed)
    rets = np.random.normal(trend, vol, n_days)
    prices = 100.0 * np.exp(np.cumsum(rets))
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": prices * (1.0 - 0.001),
        "high": prices * (1.0 + 0.005),
        "low": prices * (1.0 - 0.005),
        "close": prices,
        "volume": 1000000.0,
    })


def test_pid_terms_computation():
    df = generate_synthetic_stock(n_days=100, trend=0.002, vol=0.01)
    res = compute_pid_terms_for_asset(df, sign_p=1.0, window_ref=45)

    assert res["valid"] is True
    assert isinstance(res["P"], float)
    assert isinstance(res["I"], float)
    assert isinstance(res["D"], float)
    # Integral clipped between -3 and 3
    assert -3.0 <= res["I"] <= 3.0
    assert res["vol_exp"] > 0
    assert res["semidev"] > 0


def test_zscore_series():
    vals = [10.0, 20.0, 30.0]
    zs = zscore_series(vals)
    assert len(zs) == 3
    assert pytest.approx(np.mean(zs), abs=1e-6) == 0.0
    assert pytest.approx(np.std(zs), abs=1e-6) == 1.0


def test_score_universe_pid():
    df_bull = generate_synthetic_stock(n_days=100, trend=0.003, vol=0.01, seed=1)
    df_bear = generate_synthetic_stock(n_days=100, trend=-0.003, vol=0.02, seed=2)
    df_flat = generate_synthetic_stock(n_days=100, trend=0.0, vol=0.01, seed=3)
    df_spy = generate_synthetic_stock(n_days=100, trend=0.001, vol=0.01, seed=4)

    universe = {
        "BULL": df_bull,
        "BEAR": df_bear,
        "FLAT": df_flat,
    }

    scores = score_universe_pid(universe, df_spy=df_spy, sign_p=1.0)
    assert "BULL" in scores
    assert "BEAR" in scores
    assert "FLAT" in scores

    # Bullish asset should have higher U-score than bearish asset
    assert scores["BULL"].u_score > scores["BEAR"].u_score
    # Bearish asset with high vol should have higher defensive stress d_i
    assert scores["BEAR"].d_stress > scores["BULL"].d_stress


def test_short_data_fallback():
    df_short = generate_synthetic_stock(n_days=20)
    res = compute_pid_terms_for_asset(df_short, window_ref=45)
    assert res["valid"] is False
