"""Tests unitarios para la estrategia S8: PID Multi-Horizonte con 8 escalas temporales."""

import math
import numpy as np
import pandas as pd
import pytest

from tbot.strategies.s8_pid_multihorizon import (
    calculate_horizon_weights,
    compute_s8_pid_for_asset,
    score_universe_s8,
)


def generate_synthetic_series(n_days=100, n_intra_bars=500, seed=42):
    np.random.seed(seed)
    # Daily
    rets_d = np.random.normal(0.001, 0.015, n_days)
    prices_d = 100.0 * np.exp(np.cumsum(rets_d))
    dates_d = pd.date_range("2024-01-01", periods=n_days, freq="B")
    df_daily = pd.DataFrame({
        "date": dates_d,
        "open": prices_d * (1.0 - 0.001),
        "high": prices_d * (1.0 + 0.005),
        "low": prices_d * (1.0 - 0.005),
        "close": prices_d,
        "volume": 1000000.0,
    })

    # Intraday
    rets_i = np.random.normal(0.0001, 0.003, n_intra_bars)
    prices_i = prices_d[-1] * np.exp(np.cumsum(rets_i))
    dts_i = pd.date_range("2024-05-01 09:30", periods=n_intra_bars, freq="1h")
    df_intra = pd.DataFrame({
        "datetime_et": dts_i,
        "date": [d.strftime("%Y-%m-%d") for d in dts_i],
        "time": [d.strftime("%H:%M:%S") for d in dts_i],
        "open": prices_i * (1.0 - 0.0005),
        "high": prices_i * (1.0 + 0.001),
        "low": prices_i * (1.0 - 0.001),
        "close": prices_i,
        "volume": 50000.0,
    })

    return df_daily, df_intra


def test_horizon_weights_rule_a_and_b():
    horizons = ["10d", "5d", "1d", "2h", "1h"]
    w_a = calculate_horizon_weights(horizons, rule="A")
    w_b = calculate_horizon_weights(horizons, rule="B")

    # Sum of weights must equal 1.0
    assert pytest.approx(sum(w_a.values()), abs=1e-6) == 1.0
    assert pytest.approx(sum(w_b.values()), abs=1e-6) == 1.0

    # Under rule A, 10d (3900 min) must heavily outweigh 1h (60 min)
    assert w_a["10d"] > w_a["5d"] > w_a["1d"] > w_a["2h"] > w_a["1h"]
    # Under rule B (log), the slope is gentler:
    assert w_b["10d"] > w_b["5d"] > w_b["1d"] > w_b["2h"] > w_b["1h"]
    # Intraday proportion is much higher in B than A
    intra_a = w_a["2h"] + w_a["1h"]
    intra_b = w_b["2h"] + w_b["1h"]
    assert intra_b > intra_a


def test_compute_s8_pid_for_asset():
    df_d, df_i = generate_synthetic_series(seed=10)
    res_a = compute_s8_pid_for_asset(df_d, df_i, sign_p=1.0, weight_rule="A")
    res_b = compute_s8_pid_for_asset(df_d, df_i, sign_p=1.0, weight_rule="B")

    assert res_a["valid"] is True
    assert res_b["valid"] is True
    assert -3.0 <= res_a["I"] <= 3.0
    assert len(res_a["d_features"]) == 6


def test_score_universe_s8():
    d1, i1 = generate_synthetic_series(seed=1)
    d2, i2 = generate_synthetic_series(seed=2)
    spy_d, spy_i = generate_synthetic_series(seed=3)

    univ_d = {"SYM1": d1, "SYM2": d2}
    univ_i = {"SYM1": i1, "SYM2": i2}

    scores = score_universe_s8(univ_d, univ_i, df_spy=spy_d, sign_p=1.0, weight_rule="A")
    assert "SYM1" in scores
    assert "SYM2" in scores
    assert isinstance(scores["SYM1"].u_score, float)
    assert isinstance(scores["SYM1"].d_stress, float)
