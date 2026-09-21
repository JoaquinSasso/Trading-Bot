"""Tests unitarios y de propiedades estadísticas para el Deflated Sharpe Ratio (DSR)."""

import numpy as np

from tbot.backtest.metrics import calculate_deflated_sharpe_ratio


def test_dsr_bounded_between_zero_and_one():
    rng = np.random.default_rng(42)
    # 252 días de retornos positivos con media 0.001 y vol 0.01 (Sharpe ~ 1.5)
    returns = rng.normal(0.001, 0.01, size=252)

    dsr = calculate_deflated_sharpe_ratio(returns, num_trials=1)
    assert 0.0 <= dsr <= 1.0
    # Con un Sharpe positivo y 252 días, DSR debería ser alto
    assert dsr > 0.80


def test_dsr_decreases_with_more_trials():
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0008, 0.01, size=252)

    dsr_1 = calculate_deflated_sharpe_ratio(returns, num_trials=1)
    dsr_10 = calculate_deflated_sharpe_ratio(returns, num_trials=10)
    dsr_100 = calculate_deflated_sharpe_ratio(returns, num_trials=100)

    # A mayor número de combinaciones probadas, mayor es la penalización por sobreajuste
    assert dsr_1 > dsr_10 > dsr_100


def test_dsr_zero_on_flat_or_empty_series():
    flat_rets = np.zeros(100)
    assert calculate_deflated_sharpe_ratio(flat_rets) == 0.0

    empty_rets = np.array([])
    assert calculate_deflated_sharpe_ratio(empty_rets) == 0.0

    short_rets = np.array([0.01, -0.01])
    assert calculate_deflated_sharpe_ratio(short_rets) == 0.0
