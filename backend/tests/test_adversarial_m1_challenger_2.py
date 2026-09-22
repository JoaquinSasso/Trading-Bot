"""Adversarial stress-test suite for Milestone M1 Gate Verification.

Author: m1_challenger_2 (teamwork_preview_challenger)
Target Probes:
- Probe 1 (B-03): Covariance shrinkage under singularity, ill-conditioning, and collinearity.
- Probe 2 (B-06): Excess return metrics under negative, zero, and spiking risk-free rates.
- Probe 3 (B-07): Statsmodels OLS regression under collinear, zero-variance, and degenerate series.
- Probe 4 (B-08): Short-window annualization ban across series lengths T in [1, 300] and Sharpe SE.
- Probe 5 (B-10): SimulatedBroker T+1 settlement, rapid intraday round-trips, GFV defense, and Margin comparison.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tbot.backtest.metrics import (
    AlphaRegressionResult,
    calculate_shrunk_covariance,
    compute_backtest_metrics,
    compute_ols_alpha_beta,
    compute_portfolio_volatility_and_scale,
)
from tbot.backtest.simulated_broker import SimulatedBroker, SimulatedTrade
from tbot.strategies.interfaces import Signal


# ==============================================================================
# PROBE 1: Covariance Shrinkage Under Singularity, Ill-Conditioning & Collinearity (B-03)
# ==============================================================================
class TestProbe1CovarianceShrinkage:
    """Adversarial tests for calculate_shrunk_covariance and compute_portfolio_volatility_and_scale."""

    def test_collinear_assets_regularization(self):
        """Verify that perfectly collinear assets produce a strictly positive-definite shrunk covariance matrix."""
        np.random.seed(101)
        base_ret = np.random.normal(0.001, 0.02, (100, 1))
        # 4 assets: identical, scaled, inverted, and scaled inverted
        collinear_rets = np.column_stack([base_ret, base_ret, 2.5 * base_ret, -1.5 * base_ret])

        sample_cov = np.cov(collinear_rets, rowvar=False, ddof=1)
        # Sample covariance is rank 1 -> 3 zero eigenvalues
        sample_eigs = np.linalg.eigvalsh(sample_cov)
        assert np.sum(np.isclose(sample_eigs, 0.0, atol=1e-12)) == 3

        shrunk_cov = calculate_shrunk_covariance(collinear_rets, shrinkage_lambda=0.3)

        # 1. Diagonal preservation: Shrunk diagonal matches sample diagonal exactly
        sample_diag = np.diag(sample_cov)
        shrunk_diag = np.diag(shrunk_cov)
        np.testing.assert_allclose(shrunk_diag, sample_diag, rtol=1e-7, atol=1e-10)

        # 2. Off-diagonal shrinkage: Exactly (1 - 0.30) = 0.70 of sample covariance
        for i in range(4):
            for j in range(4):
                if i != j:
                    assert pytest.approx(shrunk_cov[i, j], rel=1e-6) == 0.70 * sample_cov[i, j]

        # 3. Positive semi-definiteness & strict positive definiteness: all eigenvalues > 0
        shrunk_eigs = np.linalg.eigvalsh(shrunk_cov)
        assert (shrunk_eigs > 0.0).all()
        # Minimum eigenvalue is strictly bounded away from zero by lambda * min(var)
        min_var = np.min(sample_diag)
        assert shrunk_eigs.min() >= 0.30 * min_var * 0.99  # theoretical lower bound

    def test_high_dimensional_rank_deficient_n_greater_than_t(self):
        """Verify N > T regime where sample covariance is heavily singular."""
        np.random.seed(202)
        # 50 assets, only 10 time periods -> sample rank <= 9
        t_bars, n_assets = 10, 50
        rets = np.random.normal(0.0005, 0.015, (t_bars, n_assets))

        sample_cov = np.cov(rets, rowvar=False, ddof=1)
        sample_eigs = np.linalg.eigvalsh(sample_cov)
        assert np.sum(sample_eigs <= 1e-12) >= (n_assets - t_bars)

        shrunk_cov = calculate_shrunk_covariance(rets, shrinkage_lambda=0.3)
        shrunk_eigs = np.linalg.eigvalsh(shrunk_cov)

        # All eigenvalues must be strictly positive
        assert (shrunk_eigs > 0.0).all()
        assert shrunk_eigs.min() > 1e-7

        # Test portfolio scaling with high-dimensional singular returns
        weights = {f"A_{i}": 1.0 / n_assets for i in range(n_assets)}
        rets_df = pd.DataFrame(rets, columns=[f"A_{i}" for i in range(n_assets)])
        sigma_port, k_scale, cov_out = compute_portfolio_volatility_and_scale(
            weights, rets_df, target_vol=0.10, shrinkage_lambda=0.3
        )
        assert sigma_port > 0.0
        assert 0.0 < k_scale <= 1.0
        assert cov_out.shape == (n_assets, n_assets)

    def test_single_asset_edge_case(self):
        """Single asset return matrix (N=1) must return 1x1 variance array."""
        np.random.seed(303)
        rets_1d = np.random.normal(0.001, 0.02, 50)
        cov_1d = calculate_shrunk_covariance(rets_1d)
        assert cov_1d.shape == (1, 1)
        assert pytest.approx(cov_1d[0, 0], rel=1e-6) == float(np.var(rets_1d, ddof=1))

        rets_2d = rets_1d.reshape(-1, 1)
        cov_2d = calculate_shrunk_covariance(rets_2d)
        assert cov_2d.shape == (1, 1)
        assert pytest.approx(cov_2d[0, 0], rel=1e-6) == float(np.var(rets_1d, ddof=1))

    def test_zero_variance_asset_handling(self):
        """Asset with zero variance (e.g. cash or halted stock) must maintain PSD property."""
        np.random.seed(404)
        r_normal = np.random.normal(0.001, 0.015, (50, 2))
        r_zero = np.zeros((50, 1))  # 0 variance
        rets = np.column_stack([r_normal, r_zero])

        shrunk_cov = calculate_shrunk_covariance(rets, shrinkage_lambda=0.3)
        assert shrunk_cov.shape == (3, 3)
        assert pytest.approx(shrunk_cov[2, 2], abs=1e-12) == 0.0
        assert pytest.approx(shrunk_cov[0, 2], abs=1e-12) == 0.0
        assert pytest.approx(shrunk_cov[1, 2], abs=1e-12) == 0.0

        eigs = np.linalg.eigvalsh(shrunk_cov)
        # All eigenvalues must be >= -1e-12 (positive semi-definite)
        assert (eigs >= -1e-12).all()

    def test_portfolio_volatility_degenerate_weights(self):
        """Empty weights, zero weights, or missing symbols in portfolio volatility."""
        r_df = pd.DataFrame(np.random.normal(0, 0.01, (30, 3)), columns=["X", "Y", "Z"])

        # Empty weights
        sig, k, cov = compute_portfolio_volatility_and_scale({}, r_df)
        assert sig == 0.0
        assert k == 1.0

        # Weights with non-existent symbols
        sig, k, cov = compute_portfolio_volatility_and_scale({"NON_EXISTENT": 1.0}, r_df)
        assert sig == 0.0
        assert k == 1.0

        # Zero weights
        sig, k, cov = compute_portfolio_volatility_and_scale({"X": 0.0, "Y": 0.0, "Z": 0.0}, r_df)
        assert sig == 0.0
        assert k == 1.0


# ==============================================================================
# PROBE 2: Excess Returns Under Negative, Zero, and Spiking Risk-Free Rates (B-06)
# ==============================================================================
class TestProbe2ExcessReturnsRiskFree:
    """Adversarial stress-testing of risk-free rate integration in Sharpe and Sortino metrics."""

    @pytest.fixture
    def synthetic_history(self):
        np.random.seed(505)
        n_days = 300
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        rets = np.random.normal(0.0008, 0.012, n_days)
        eq_vals = 2000.0 * np.cumprod(1.0 + rets)
        equity_curve = pd.Series(eq_vals, index=dates)

        trade = SimulatedTrade(
            trade_id="T1",
            symbol="SPY",
            strategy_id="test",
            entry_time=dates[10],
            exit_time=dates[20],
            entry_price=Decimal("100"),
            exit_price=Decimal("105"),
            qty=Decimal("10"),
            pnl=Decimal("50"),
            pnl_pct=0.05,
            pnl_r=1.0,
            fees=Decimal("0.10"),
            slippage_cost=Decimal("0.05"),
            exit_reason="take_profit",
            initial_risk_usd=Decimal("50"),
        )
        return dates, equity_curve, [trade]

    def test_monotonicity_across_rf_regimes(self, synthetic_history):
        """Sharpe and Sortino must decrease monotonically as the risk-free rate increases."""
        dates, equity_curve, trades = synthetic_history
        date_keys = [d.date() for d in dates]

        rf_levels = [
            -0.0010,  # Negative rates (-25% annualized, ECB NIRP stress)
            -0.0002,  # Moderate negative rates (-5% annualized)
            0.0,      # Zero interest rate policy (ZIRP)
            0.0002,   # Normal positive rates (~5% annualized)
            0.0010,   # High rates (~25% annualized)
            0.0050,   # Hyper-spiking rates (~126% annualized)
        ]

        sharpes = []
        sortinos = []

        for rf in rf_levels:
            rf_series = pd.Series(rf, index=date_keys)
            metrics = compute_backtest_metrics("test_strat", trades, equity_curve, rf_series=rf_series)
            assert metrics.sharpe_ratio is not None
            assert metrics.sortino_ratio is not None
            sharpes.append(metrics.sharpe_ratio)
            sortinos.append(metrics.sortino_ratio)

        # Strictly decreasing monotonicity
        for i in range(len(rf_levels) - 1):
            assert sharpes[i] > sharpes[i + 1], f"Sharpe non-monotonic: {sharpes[i]} <= {sharpes[i+1]}"
            assert sortinos[i] > sortinos[i + 1], f"Sortino non-monotonic: {sortinos[i]} <= {sortinos[i+1]}"

    def test_extreme_spiking_rf_stability(self, synthetic_history):
        """Huge risk-free rate spikes (e.g. 50% daily) must yield negative Sharpe without NaN or crash."""
        dates, equity_curve, trades = synthetic_history
        date_keys = [d.date() for d in dates]

        rf_series = pd.Series(0.50, index=date_keys)
        metrics = compute_backtest_metrics("test_strat", trades, equity_curve, rf_series=rf_series)
        assert metrics.sharpe_ratio is not None
        assert math.isfinite(metrics.sharpe_ratio)
        assert metrics.sharpe_ratio < -100.0
        assert metrics.sortino_ratio is not None
        assert math.isfinite(metrics.sortino_ratio)

    def test_all_positive_excess_returns_sortino(self):
        """When equity grows with only positive returns (no down-days), downside excess is empty; Sortino must be 0.0, not NaN."""
        dates = pd.date_range("2024-01-01", periods=260, freq="B")
        # Varying positive returns: e.g. +0.05% to +0.15% every day (std > 0, but min return > 0)
        daily_pcts = 0.0005 + 0.0005 * np.sin(np.arange(len(dates)))
        eq_vals = 1000.0 * np.cumprod(1.0 + daily_pcts)
        eq_series = pd.Series(eq_vals, index=dates)

        trade = SimulatedTrade(
            trade_id="T1", symbol="SPY", strategy_id="s",
            entry_time=dates[0], exit_time=dates[5],
            entry_price=Decimal("100"), exit_price=Decimal("101"),
            qty=Decimal("10"), pnl=Decimal("10"), pnl_pct=0.01, pnl_r=1.0,
            fees=Decimal("0"), slippage_cost=Decimal("0"),
            exit_reason="tp", initial_risk_usd=Decimal("10")
        )

        rf_series = pd.Series(0.0, index=[d.date() for d in dates])
        metrics = compute_backtest_metrics("strat", [trade], eq_series, initial_capital=1000.0, rf_series=rf_series)
        assert metrics.sharpe_ratio is not None
        assert metrics.sharpe_ratio > 0.0
        # No negative excess returns -> downside excess is empty -> Sortino is cleanly 0.0
        assert metrics.sortino_ratio == 0.0


# ==============================================================================
# PROBE 3: Statsmodels OLS Regression Under Collinear and Degenerate Inputs (B-07)
# ==============================================================================
class TestProbe3StatsmodelsOLSRegression:
    """Adversarial stress-testing of compute_ols_alpha_beta."""

    def test_perfect_positive_and_negative_collinearity(self):
        """OLS regression with perfect correlation (r = +1 and r = -1)."""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        np.random.seed(606)
        bench = pd.Series(np.random.normal(0.0005, 0.01, 100), index=dates)

        # Perfect positive: y = 2.5 * x
        strat_pos = 2.5 * bench
        res_pos = compute_ols_alpha_beta(strat_pos, bench, risk_free_returns=0.0)
        assert isinstance(res_pos, AlphaRegressionResult)
        assert pytest.approx(res_pos.beta, abs=1e-3) == 2.5
        assert pytest.approx(res_pos.alpha_annualized, abs=1e-3) == 0.0
        assert pytest.approx(res_pos.r_squared, abs=1e-3) == 1.0
        assert res_pos.beta_se == 0.0

        # Perfect negative: y = -1.8 * x
        strat_neg = -1.8 * bench
        res_neg = compute_ols_alpha_beta(strat_neg, bench, risk_free_returns=0.0)
        assert pytest.approx(res_neg.beta, abs=1e-3) == -1.8
        assert pytest.approx(res_neg.alpha_annualized, abs=1e-3) == 0.0
        assert pytest.approx(res_neg.r_squared, abs=1e-3) == 1.0

    def test_zero_variance_market_benchmark(self):
        """When benchmark excess returns have zero variance (var(x) < 1e-12), return safe degenerate AlphaRegressionResult."""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        bench_const = pd.Series(0.0001, index=dates)  # Constant return
        strat_rand = pd.Series(np.random.normal(0, 0.01, 100), index=dates)

        res = compute_ols_alpha_beta(strat_rand, bench_const, risk_free_returns=0.0001)
        assert res.beta == 1.0
        assert res.alpha_annualized == 0.0
        assert res.alpha_pvalue == 1.0
        assert res.r_squared == 0.0

    def test_zero_variance_strategy_returns(self):
        """When strategy returns are constant (var(y) == 0), OLS must estimate beta=0, alpha=constant without crashing."""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        np.random.seed(707)
        bench = pd.Series(np.random.normal(0.0005, 0.01, 100), index=dates)
        strat_const = pd.Series(0.0004, index=dates)  # Constant daily return

        res = compute_ols_alpha_beta(strat_const, bench, risk_free_returns=0.0)
        assert pytest.approx(res.beta, abs=1e-3) == 0.0
        assert pytest.approx(res.alpha_annualized, abs=1e-3) == 0.0004 * 252.0

    def test_insufficient_observations_raises_value_error(self):
        """Series with fewer than 5 common observations must raise ValueError cleanly."""
        dates = pd.date_range("2024-01-01", periods=4, freq="B")
        s = pd.Series([0.01, -0.01, 0.02, 0.00], index=dates)
        b = pd.Series([0.00, 0.01, -0.01, 0.01], index=dates)

        with pytest.raises(ValueError, match="Observaciones comunes insuficientes"):
            compute_ols_alpha_beta(s, b)

    def test_disjoint_dates_raises_value_error(self):
        """Non-overlapping dates must raise ValueError."""
        d1 = pd.date_range("2024-01-01", periods=10, freq="B")
        d2 = pd.date_range("2025-01-01", periods=10, freq="B")
        s = pd.Series(0.01, index=d1)
        b = pd.Series(0.01, index=d2)

        with pytest.raises(ValueError, match="Observaciones comunes insuficientes"):
            compute_ols_alpha_beta(s, b)


# ==============================================================================
# PROBE 4: Short-Window Annualization Ban and Sharpe SE Across T in [1, 300] (B-08)
# ==============================================================================
class TestProbe4ShortWindowAnnualizationBan:
    """Stress-testing the annualization boundary T < 252 vs T >= 252 and Sharpe SE formula."""

    @pytest.fixture
    def dummy_trade(self):
        return SimulatedTrade(
            trade_id="T1", symbol="SPY", strategy_id="s",
            entry_time=datetime(2024, 1, 2), exit_time=datetime(2024, 1, 5),
            entry_price=Decimal("100"), exit_price=Decimal("105"),
            qty=Decimal("10"), pnl=Decimal("50"), pnl_pct=0.05, pnl_r=1.0,
            fees=Decimal("0.10"), slippage_cost=Decimal("0.05"),
            exit_reason="tp", initial_risk_usd=Decimal("50")
        )

    def test_exhaustive_boundary_sweep_t_1_to_300(self, dummy_trade):
        """Test series lengths T from 1 to 300 sessions. Verify None return when T < 252 and floats when T >= 252."""
        dates_all = pd.date_range("2024-01-01", periods=350, freq="B")
        np.random.seed(808)
        all_rets = np.random.normal(0.0005, 0.01, 350)
        all_equity = 2000.0 * np.cumprod(1.0 + all_rets)

        # Sample test points: boundaries and interior points
        test_t_values = [1, 2, 5, 20, 60, 100, 200, 250, 251, 252, 253, 260, 300]

        for t in test_t_values:
            # t daily returns corresponds to t + 1 equity points
            sub_eq = pd.Series(all_equity[: t + 1], index=dates_all[: t + 1])
            m = compute_backtest_metrics("test_s", [dummy_trade], sub_eq)

            if t < 252:
                assert m.cagr_pct is None, f"CAGR not None at T={t}"
                assert m.sharpe_ratio is None, f"Sharpe not None at T={t}"
                assert m.sortino_ratio is None, f"Sortino not None at T={t}"
            else:
                assert m.cagr_pct is not None, f"CAGR is None at T={t}"
                assert m.sharpe_ratio is not None, f"Sharpe is None at T={t}"
                assert m.sortino_ratio is not None, f"Sortino is None at T={t}"

            # Sharpe SE must be computed for all t >= 2 (std > 0)
            if t >= 2:
                assert m.sharpe_se is not None
                assert m.sharpe_se > 0.0
                assert math.isfinite(m.sharpe_se)

    def test_sharpe_se_mathematical_precision(self, dummy_trade):
        """Verify that Sharpe SE strictly matches SE(S) = sqrt((1 + S^2 / 2) / T)."""
        np.random.seed(909)
        n_days = 60
        dates = pd.date_range("2024-01-01", periods=n_days + 1, freq="B")
        rets = np.random.normal(0.001, 0.015, n_days)
        eq_vals = np.empty(n_days + 1)
        eq_vals[0] = 2000.0
        eq_vals[1:] = 2000.0 * np.cumprod(1.0 + rets)
        eq = pd.Series(eq_vals, index=dates)

        # Zero rf for exact tracking
        rf_zero = pd.Series(0.0, index=[d.date() for d in dates])
        m = compute_backtest_metrics("test_s", [dummy_trade], eq, rf_series=rf_zero)

        mean_r = float(np.mean(rets))
        std_r = float(np.std(rets, ddof=1))
        expected_s = mean_r / std_r
        expected_se = math.sqrt((1.0 + 0.5 * (expected_s ** 2)) / n_days)

        assert pytest.approx(m.sharpe_se, abs=1e-5) == round(expected_se, 6)

    def test_t_0_and_t_1_edge_cases(self, dummy_trade):
        """Empty equity curve or single point equity curve."""
        # Empty curve
        m_empty = compute_backtest_metrics("test_s", [dummy_trade], pd.Series(dtype=float))
        assert m_empty.cagr_pct is None
        assert m_empty.sharpe_ratio is None
        assert m_empty.sharpe_se is None

        # Single point curve (T=0 return bars)
        m_single = compute_backtest_metrics("test_s", [dummy_trade], pd.Series([2000.0], index=[pd.Timestamp("2024-01-01")]))
        assert m_single.cagr_pct is None
        assert m_single.sharpe_ratio is None
        assert m_single.sharpe_se is None


# ==============================================================================
# PROBE 5: SimulatedBroker T+1 Settlement & GFV Attacks (B-10)
# ==============================================================================
class TestProbe5SimulatedBrokerSettlementAndGFV:
    """Stress-testing Cash vs Margin settlement mechanics, rapid intraday cycles, and GFV defense."""

    def test_rapid_intraday_cycles_and_gfv_rejection(self):
        """Cash account: High-frequency buy/sell cycles, verifying unsettled funds cannot be spent on same day."""
        broker = SimulatedBroker(initial_capital=Decimal("3000.00"), account_type="cash", settlement_days=1)
        assert broker.buying_power == Decimal("3000.00")

        # Cycle 1: Monday 09:30 Buy 2 SPY @ $500 -> Cost = 2 * 500.10 = 1000.20
        t0 = datetime(2025, 6, 9, 9, 30)
        sig1 = Signal.create("s", "1", "SPY", t0, "buy", "market", Decimal("500.00"), Decimal("490.00"))
        pos1 = broker.submit_buy(sig1, Decimal("2"), Decimal("500.00"), t0)
        assert pos1 is not None
        assert broker.settled_cash == Decimal("1999.8000")
        assert broker.unsettled_cash == Decimal("0.0")

        # Monday 10:00 Sell SPY @ $510 -> Gross = 1019.80 - fees
        t1 = datetime(2025, 6, 9, 10, 0)
        trade1 = broker.close_position("SPY", Decimal("510.00"), t1, "tp")
        assert trade1 is not None
        assert broker.settled_cash == Decimal("1999.8000")
        assert broker.unsettled_cash > Decimal("1019.00")
        assert broker.buying_power == Decimal("1999.8000")  # Exactly settled cash

        # Cycle 2: Monday 10:30 Buy 3 QQQ @ $500 -> Cost = 3 * 500.10 = 1500.30
        t2 = datetime(2025, 6, 9, 10, 30)
        sig2 = Signal.create("s", "1", "QQQ", t2, "buy", "market", Decimal("500.00"), Decimal("490.00"))
        pos2 = broker.submit_buy(sig2, Decimal("3"), Decimal("500.00"), t2)
        assert pos2 is not None
        assert broker.settled_cash == Decimal("499.5000")
        assert broker.buying_power == Decimal("499.5000")

        # Monday 11:00 Sell QQQ @ $520 -> Gross = 1559.70 - fees
        t3 = datetime(2025, 6, 9, 11, 0)
        trade2 = broker.close_position("QQQ", Decimal("520.00"), t3, "tp")
        assert trade2 is not None
        assert broker.settled_cash == Decimal("499.5000")
        # Unsettled cash accumulates both sells: ~1019 + ~1559 = ~2579
        assert broker.unsettled_cash > Decimal("2578.00")
        assert broker.cash > Decimal("3078.00")  # Total net worth is up
        assert broker.buying_power == Decimal("499.5000")  # But BP is strictly settled!

        # GFV ATTACK 1: Monday 11:30 Attempt to buy $2000 worth of IWM (4 shares @ $500)
        # Available buying power is only $499.50 -> Order must be downsized to 0 shares (returns None)!
        t4 = datetime(2025, 6, 9, 11, 30)
        sig3 = Signal.create("s", "1", "IWM", t4, "buy", "market", Decimal("500.00"), Decimal("490.00"))
        pos3 = broker.submit_buy(sig3, Decimal("4"), Decimal("500.00"), t4)
        assert pos3 is None, "GFV violation: Buy order was executed when settled cash was insufficient!"

        # GFV ATTACK 2: Monday 14:00 Attempt to buy 1 share of GLD @ $400 (Cost = 400.08)
        # Buying power is $499.50 -> Order can execute 1 share within settled cash
        t5 = datetime(2025, 6, 9, 14, 0)
        sig4 = Signal.create("s", "1", "GLD", t5, "buy", "market", Decimal("400.00"), Decimal("390.00"))
        pos4 = broker.submit_buy(sig4, Decimal("1"), Decimal("400.00"), t5)
        assert pos4 is not None
        assert pos4.qty == Decimal("1")
        assert broker.settled_cash < Decimal("100.00")
        assert broker.settled_cash >= Decimal("0.0")

        # Next business day: Tuesday 09:30 -> All unsettled credits must mature
        t_tue = datetime(2025, 6, 10, 9, 30)
        broker.process_settlement(t_tue)
        assert broker.unsettled_cash == Decimal("0.0")
        assert broker.settled_cash > Decimal("2600.00")
        assert broker.buying_power == broker.settled_cash

    def test_weekend_rollover_friday_to_monday(self):
        """Trades executed on Friday must skip Saturday and Sunday and settle on Monday."""
        broker = SimulatedBroker(initial_capital=Decimal("1000.00"), account_type="cash", settlement_days=1)

        # Friday 15:00 sell
        t_fri = datetime(2025, 6, 13, 15, 0)  # Friday
        settle_d = broker.calculate_settlement_date(t_fri)
        assert settle_d == date(2025, 6, 16)  # Monday

        # Simulate position close on Friday
        sig = Signal.create("s", "1", "SPY", t_fri, "buy", "market", Decimal("500.00"), Decimal("490.00"))
        broker.submit_buy(sig, Decimal("1"), Decimal("500.00"), t_fri)
        broker.close_position("SPY", Decimal("510.00"), t_fri, "tp")

        # Saturday 12:00 -> NOT settled
        t_sat = datetime(2025, 6, 14, 12, 0)
        broker.process_settlement(t_sat)
        assert broker.unsettled_cash > Decimal("0.0")

        # Sunday 18:00 -> NOT settled
        t_sun = datetime(2025, 6, 15, 18, 0)
        broker.process_settlement(t_sun)
        assert broker.unsettled_cash > Decimal("0.0")

        # Monday 09:30 -> SETTLED
        t_mon = datetime(2025, 6, 16, 9, 30)
        broker.process_settlement(t_mon)
        assert broker.unsettled_cash == Decimal("0.0")
        assert broker.settled_cash > Decimal("1000.00")

    def test_margin_account_immediate_buying_power(self):
        """In Margin accounts, sell proceeds are immediately available as buying power without GFV delay."""
        broker = SimulatedBroker(initial_capital=Decimal("2500.00"), account_type="margin")

        t_mon = datetime(2025, 6, 9, 9, 30)
        sig = Signal.create("s", "1", "SPY", t_mon, "buy", "market", Decimal("500.00"), Decimal("490.00"))
        # Buy 4 shares: cost = 4 * 500.10 = 2000.40 -> remaining settled cash = 499.60
        broker.submit_buy(sig, Decimal("4"), Decimal("500.00"), t_mon)
        assert broker.settled_cash == Decimal("499.6000")

        # Sell all 4 shares at 10:00: gross = 4 * 509.8980 = 2039.59 - fees
        t_sell = datetime(2025, 6, 9, 10, 0)
        broker.close_position("SPY", Decimal("510.00"), t_sell, "tp")

        # In Margin account, settled_cash is immediately credited
        assert broker.unsettled_cash == Decimal("0.0")
        assert broker.settled_cash > Decimal("2530.00")
        assert broker.buying_power > Decimal("2530.00")

        # Immediate re-buy on same day succeeds completely for 5 shares
        sig2 = Signal.create("s", "1", "QQQ", t_sell, "buy", "market", Decimal("500.00"), Decimal("490.00"))
        pos2 = broker.submit_buy(sig2, Decimal("5"), Decimal("500.00"), t_sell)
        assert pos2 is not None
        assert pos2.qty == Decimal("5")
