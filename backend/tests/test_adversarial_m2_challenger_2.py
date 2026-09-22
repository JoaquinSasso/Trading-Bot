"""Adversarial Challenge Test Suite for Milestone M2 Gate Verification.

Audits:
1. Covariance Shrinkage & Vol Control: Collinear, singular, zero-variance, and extreme high-vol matrices.
   Verifies positive semi-definiteness (PSD) and volatility scale factor k in (0, 1].
2. Whole Shares & Micro-Account Stress: $500, $1000 accounts with target alloc < 1 share price.
   Verifies clean rejection, no partial fills, integer share execution, cash preservation, and no negative cash.
3. Cash Yield Accrual Stability: Real BIL series, zero interest on zero rates, zero interest on negative rates.
   Probes whether zero/negative rates erroneously trigger 4.5% annual fallback.
4. Backward Compatibility API Probe: Legacy ReplayEngine instantiation, positional run(start, end),
   tuple unpacking (metrics, trades, equity_curve), and sequence access.
"""

import math
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from tbot.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    ReplayEngine,
)
from tbot.backtest.metrics import (
    calculate_shrunk_covariance,
    compute_portfolio_volatility_and_scale,
    load_risk_free_rate_bil,
)
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


# ==============================================================================
# Helpers & Mocks
# ==============================================================================
class MockBuyStrategy:
    """Deterministic strategy for adversarial testing."""

    id: str = "adversarial_mock_strategy"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(self, target_symbols: list[str], stop_pct: float = 0.05) -> None:
        self.target_symbols = target_symbols
        self.stop_pct = stop_pct

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        signals = []
        for s in self.target_symbols:
            if s in ctx.current_prices and s not in ctx.portfolio_positions:
                price = ctx.current_prices[s]
                stop = price * Decimal(str(1.0 - self.stop_pct))
                signals.append(
                    Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=s,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=price,
                        stop_price=stop,
                    )
                )
        return signals


def _make_daily_df(
    symbol: str,
    dates: list[date],
    prices: list[float],
    is_synthetic: bool = True,
) -> pd.DataFrame:
    records = []
    for d, p in zip(dates, prices, strict=True):
        records.append(
            {
                "symbol": symbol,
                "date": d,
                "open": round(p, 2),
                "high": round(p * 1.01, 2),
                "low": round(p * 0.99, 2),
                "close": round(p, 2),
                "volume": 1_000_000,
            }
        )
    df = pd.DataFrame(records)
    if is_synthetic:
        df.attrs["is_synthetic"] = True
    return df


# ==============================================================================
# PROBE 1: Covariance Shrinkage & Vol Control Under Extreme Markets
# ==============================================================================
class TestCovarianceShrinkageAndVolControl:
    """Stress tests for calculate_shrunk_covariance and compute_portfolio_volatility_and_scale."""

    def test_collinear_returns_strictly_positive_definite(self):
        """Collinear returns produce singular sample covariance.

        Shrunk covariance with lambda=0.3 and positive variances must be strictly positive definite.
        """
        np.random.seed(42)
        n_obs = 100
        base_ret = np.random.normal(0.001, 0.02, size=n_obs)
        # 3 collinear assets: A, B = 2*A, C = -0.5*A
        returns = np.column_stack([base_ret, 2.0 * base_ret, -0.5 * base_ret])

        sample_cov = np.cov(returns, rowvar=False, ddof=1)
        sample_eigvals = np.linalg.eigvalsh(sample_cov)
        # Sample covariance must be rank 1 (two eigenvalues ~ 0)
        assert sample_eigvals[0] < 1e-10
        assert sample_eigvals[1] < 1e-10

        # Shrunk covariance
        shrunk_cov = calculate_shrunk_covariance(returns, shrinkage_lambda=0.3)
        shrunk_eigvals = np.linalg.eigvalsh(shrunk_cov)

        # All eigenvalues of shrunk_cov must be strictly positive
        assert np.all(shrunk_eigvals > 0.0), f"Eigenvalues not all positive: {shrunk_eigvals}"
        # Smallest eigenvalue must be strictly greater than zero due to diagonal shrinkage
        assert shrunk_eigvals[0] > 1e-6

        # Portfolio volatility and scale factor k
        weights = {"A": 0.4, "B": 0.4, "C": 0.2}
        ret_df = pd.DataFrame(returns, columns=["A", "B", "C"])
        sigma_p, k_scale, cov_out = compute_portfolio_volatility_and_scale(
            weights=weights,
            returns_matrix=ret_df,
            target_vol=0.12,
            shrinkage_lambda=0.3,
        )
        assert sigma_p > 0.0
        assert 0.0 < k_scale <= 1.0
        expected_k = min(1.0, 0.12 / sigma_p)
        assert math.isclose(k_scale, expected_k, rel_tol=1e-6)

    def test_singular_high_dimensional_regime_t_less_than_m(self):
        """When observations T < assets M, sample cov is singular.

        Verify shrunk covariance is positive semi-definite and full rank with positive variances.
        """
        np.random.seed(123)
        t_obs = 5
        m_assets = 10
        returns = np.random.normal(0.0005, 0.015, size=(t_obs, m_assets))

        shrunk_cov = calculate_shrunk_covariance(returns, shrinkage_lambda=0.3)
        assert shrunk_cov.shape == (m_assets, m_assets)

        eigvals = np.linalg.eigvalsh(shrunk_cov)
        assert np.all(eigvals >= -1e-12), f"Negative eigenvalues found: {eigvals}"
        # Due to diagonal shrinkage, all diagonal elements are positive variances
        assert np.all(eigvals > 0.0)

        # Uniform weights
        weights = np.ones(m_assets) / m_assets
        sigma_p, k_scale, _ = compute_portfolio_volatility_and_scale(
            weights=weights,
            returns_matrix=returns,
            target_vol=0.12,
            shrinkage_lambda=0.3,
        )
        assert sigma_p > 0.0
        assert 0.0 < k_scale <= 1.0

    def test_zero_variance_asset_and_all_zero_returns(self):
        """Asset with constant price (zero variance) or entire matrix zero."""
        # 1. One asset has constant zero returns
        r_one_zero = np.array([
            [0.01, 0.0],
            [-0.02, 0.0],
            [0.015, 0.0],
            [-0.005, 0.0],
        ])
        shrunk_one = calculate_shrunk_covariance(r_one_zero, shrinkage_lambda=0.3)
        eig_one = np.linalg.eigvalsh(shrunk_one)
        # Must be PSD (eigenvalues >= -1e-12)
        assert np.all(eig_one >= -1e-12), f"Negative eigenvalues: {eig_one}"

        # 2. All assets have zero variance
        r_all_zero = np.zeros((10, 3))
        shrunk_all = calculate_shrunk_covariance(r_all_zero, shrinkage_lambda=0.3)
        assert np.allclose(shrunk_all, 0.0)

        weights = {"A": 0.5, "B": 0.5}
        sigma_p, k_scale, _ = compute_portfolio_volatility_and_scale(
            weights=weights,
            returns_matrix=pd.DataFrame(r_all_zero[:, :2], columns=["A", "B"]),
            target_vol=0.12,
        )
        assert sigma_p == 0.0
        assert k_scale == 1.0  # Fallback when sigma_port <= 1e-8

    def test_extreme_high_volatility_proper_downscaling(self):
        """Extreme volatility returns (50% daily std dev, annualized > 700%).

        Verify no numeric overflow and k strictly scales down allocation into (0, 0.05].
        """
        np.random.seed(999)
        # Daily return std = 0.50 -> Annualized vol ~ 0.50 * sqrt(252) ~ 793%
        r_extreme = np.random.normal(0.0, 0.50, size=(100, 2))
        ret_df = pd.DataFrame(r_extreme, columns=["HI1", "HI2"])
        weights = {"HI1": 0.5, "HI2": 0.5}

        sigma_p, k_scale, _ = compute_portfolio_volatility_and_scale(
            weights=weights,
            returns_matrix=ret_df,
            target_vol=0.12,
            shrinkage_lambda=0.3,
        )
        assert sigma_p > 5.0  # Volatility is > 500%
        assert 0.0 < k_scale < 0.05  # Highly defensive scaling factor
        assert math.isclose(k_scale, 0.12 / sigma_p, rel_tol=1e-5)


# ==============================================================================
# PROBE 2: Whole Shares & Micro-Account Stress ($500, $1000)
# ==============================================================================
class TestWholeSharesAndMicroAccountStress:
    """Verify clean rejection or integer share sizing on small capital accounts."""

    def test_micro_account_500_target_alloc_less_than_one_share(self):
        """Account = $500. Single-position cap = 25% ($125).

        Stock price = $300. Target alloc $125 < 1 share ($300).
        With integer_shares=True, qty must floor to 0 and be cleanly rejected.
        Final cash must be preserved at $500.00 without partial fills or negative cash.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        prices_spy = [400.0] * 10
        prices_exp = [300.0] * 10

        df_spy = _make_daily_df("SPY", dates, prices_spy)
        df_exp = _make_daily_df("EXP300", dates, prices_exp)

        strat = MockBuyStrategy(["EXP300"])
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("500.00"),
            single_position_cap=0.25,  # $125 max
            min_position_usd=50.0,     # Floor set below $125 to isolate whole shares check
            integer_shares=True,
            enable_vol_control=False,
            enable_cash_yield=False,   # Disable cash yield to check pure capital preservation
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "EXP300": df_exp},
        )
        result = engine.run(dates[0], dates[-1])

        # 0 trades must have executed
        assert len(result.trades) == 0
        assert len(engine.broker.positions) == 0
        # Cash must be perfectly preserved
        assert engine.broker.settled_cash == Decimal("500.00")
        assert engine.broker.cash == Decimal("500.00")
        assert float(result.equity_curve.iloc[-1]) == 500.00

    def test_micro_account_1000_expensive_stock_800(self):
        """Account = $1000. Single-position cap = 25% ($250).

        Stock price = $800. Target alloc $250 < $800.
        Must cleanly reject without crash or partial fill.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 10)
        df_exp = _make_daily_df("EXP800", dates, [800.0] * 10)

        strat = MockBuyStrategy(["EXP800"])
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("1000.00"),
            single_position_cap=0.25,
            min_position_usd=50.0,
            integer_shares=True,
            enable_vol_control=False,
            enable_cash_yield=False,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "EXP800": df_exp},
        )
        result = engine.run(dates[0], dates[-1])

        assert len(result.trades) == 0
        assert engine.broker.settled_cash == Decimal("1000.00")
        assert float(result.equity_curve.iloc[-1]) == 1000.00

    def test_micro_account_500_valid_whole_share_execution(self):
        """Account = $500. Single-position cap = 25% ($125).

        Stock price = $100. Target alloc $125 allows exactly 1 share ($100).
        Verify exactly 1 whole share is bought, retail friction applied,
        and settled cash remains strictly positive (~$399.96).
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(5)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 5)
        df_stk = _make_daily_df("STK100", dates, [100.0] * 5)

        strat = MockBuyStrategy(["STK100"])
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("500.00"),
            risk_per_trade_pct=2.0,    # 2% risk on $500 = $10. With 5% stop ($5/unit) -> $200 raw alloc, capped at 25% = $125.
            single_position_cap=0.25,  # $125 max -> allows exactly 1 share of $100 ($100 <= $125)
            min_position_usd=50.0,
            integer_shares=True,
            apply_retail_costs=True,
            enable_vol_control=False,
            enable_cash_yield=False,
        )

        engine = BacktestEngine(
            config=config,
            historical_daily={"SPY": df_spy, "STK100": df_stk},
        )
        result = engine.run(dates[0], dates[-1])

        # Trade should have closed at end_of_period
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.qty == Decimal("1")  # Exactly 1 whole share
        assert isinstance(trade.qty, Decimal)
        # Cash should be non-negative
        assert engine.broker.settled_cash >= Decimal("0.0")
        assert engine.broker.cash > Decimal("490.0")  # Bought and sold at $100 minus small retail friction
        assert float(result.equity_curve.iloc[-1]) > 490.0
        # Check that unsettled cash holds the proceeds under Reg T Cash T+1
        assert engine.broker.unsettled_cash > Decimal("90.0")

    def test_micro_account_multi_asset_cannot_exceed_buying_power(self):
        """Account = $500. 4 assets priced $120.

        Ensure broker buying power check prevents cash from ever going negative.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(5)]
        daily_data = {"SPY": _make_daily_df("SPY", dates, [400.0] * 5)}
        symbols = ["SYM1", "SYM2", "SYM3", "SYM4"]
        for s in symbols:
            daily_data[s] = _make_daily_df(s, dates, [120.0] * 5)

        strat = MockBuyStrategy(symbols)
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("500.00"),
            single_position_cap=0.30,  # $150 max per asset -> 1 share ($120) each
            min_position_usd=50.0,
            integer_shares=True,
            apply_retail_costs=True,
            enable_vol_control=False,
            enable_cash_yield=False,
        )

        engine = BacktestEngine(config=config, historical_daily=daily_data)
        result = engine.run(dates[0], dates[-1])

        # Even with 4 positions, total cost is ~4 * $120 = $480 <= $500
        for t in result.trades:
            assert t.qty == Decimal("1")
        assert engine.broker.settled_cash >= Decimal("0.0")


# ==============================================================================
# PROBE 3: Cash Yield Accrual Stability (Zero / Negative Rates & Real BIL)
# ==============================================================================
class TestCashYieldAccrualStability:
    """Audits cash yield behavior against real BIL series and non-positive rate regimes."""

    def test_zero_interest_rate_accrues_zero_yield(self):
        """CRITICAL CHALLENGE: When daily risk-free rate is exactly 0.0,

        cash yield credited MUST be exactly $0.00.
        Engine must NOT replace 0.0 with 4.5% annual fallback.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 10)

        # Zero interest series for all 10 days
        rf_zero = pd.Series(0.0, index=dates)

        config = BacktestConfig(
            strategy=None,  # 100% cash
            initial_capital=Decimal("2000.00"),
            enable_cash_yield=True,
            rf_series=rf_zero,
            annual_cash_yield_fallback=0.045,  # Default 4.5% fallback
        )

        engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy})
        result = engine.run(dates[0], dates[-1])

        final_equity = float(result.equity_curve.iloc[-1])

        # EMPIRICAL PROBE: In an account with 0.0 interest, equity must remain exactly 2000.00
        # If engine replaced 0.0 with 4.5% fallback, equity will be ~2003.50.
        assert math.isclose(final_equity, 2000.00, abs_tol=1e-4), (
            f"VULNERABILITY DETECTED: With rf_series = 0.0, engine accrued phantom yield! "
            f"Expected $2000.00, got ${final_equity:.4f}. "
            f"Engine incorrectly substituted 0.0 with annual_cash_yield_fallback (4.5%)."
        )

    def test_negative_interest_rate_accrues_zero_yield(self):
        """CRITICAL CHALLENGE: When daily risk-free rate is negative (e.g. -0.0002),

        broker cash balance must not suffer losses, nor should it earn positive 4.5% yield.
        Yield credited must be exactly $0.00.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 10)

        rf_neg = pd.Series(-0.0002, index=dates)

        config = BacktestConfig(
            strategy=None,
            initial_capital=Decimal("2000.00"),
            enable_cash_yield=True,
            rf_series=rf_neg,
            annual_cash_yield_fallback=0.045,
        )

        engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy})
        result = engine.run(dates[0], dates[-1])

        final_equity = float(result.equity_curve.iloc[-1])
        assert math.isclose(final_equity, 2000.00, abs_tol=1e-4), (
            f"VULNERABILITY DETECTED: With negative rates, engine credited phantom yield! "
            f"Expected $2000.00, got ${final_equity:.4f}."
        )

    def test_missing_dates_versus_present_zero_dates(self):
        """Fallback should only apply if current_day is NOT in rf_series.

        If current_day IS in rf_series and has value 0.0, it must NOT use fallback.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(4)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 4)

        # Day 0, 1: explicitly 0.0 in series.
        # Day 2, 3: missing from series.
        rf_partial = pd.Series({dates[0]: 0.0, dates[1]: 0.0})

        config = BacktestConfig(
            strategy=None,
            initial_capital=Decimal("2000.00"),
            enable_cash_yield=True,
            rf_series=rf_partial,
            annual_cash_yield_fallback=0.045,
        )

        engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy})
        result = engine.run(dates[0], dates[-1])

        # If implemented properly: Day 0 & 1 yield 0.0. Day 2 & 3 yield fallback.
        # We document the exact trajectory.
        eq_curve = result.equity_curve
        day0_eq = float(eq_curve.iloc[0])
        day1_eq = float(eq_curve.iloc[1])

        # On Day 0 and Day 1, equity must not increase if rates are 0.0
        assert math.isclose(day0_eq, 2000.0, abs_tol=1e-4), (
            f"Day 0 equity inflated from $2000.0 to ${day0_eq:.4f} despite explicit 0.0 rate in series"
        )
        assert math.isclose(day1_eq, 2000.0, abs_tol=1e-4), (
            f"Day 1 equity inflated from $2000.0 to ${day1_eq:.4f} despite explicit 0.0 rate in series"
        )

    def test_real_bil_series_accrual_fidelity(self):
        """Verify compounding against real BIL series over 2020-2022.

        In 2020-2022, actual non-negative BIL compounded cash by +4.14%.
        Engine should not accrue +13.69% due to 4.5% fallback override on 58.3% of zero/negative days.
        """
        rf_full = load_risk_free_rate_bil()
        dates_sub = [d for d in rf_full.index if date(2020, 1, 2) <= d <= date(2022, 12, 30)]

        # Calculate expected benchmark: sum of positive daily_rf
        expected_cash = 2000.0
        for d in dates_sub:
            r = rf_full.get(d, 0.0)
            if r > 0.0:
                expected_cash *= (1.0 + r)

        df_spy = _make_daily_df("SPY", dates_sub, [400.0] * len(dates_sub))
        config = BacktestConfig(
            strategy=None,
            initial_capital=Decimal("2000.00"),
            enable_cash_yield=True,
            rf_series=rf_full,
            annual_cash_yield_fallback=0.045,
        )
        engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy})
        result = engine.run(dates_sub[0], dates_sub[-1])

        actual_final_equity = float(result.equity_curve.iloc[-1])

        # We assert that actual final equity matches real BIL within reasonable tolerance
        # rather than being inflated by +9.55% (over $190 on $2000).
        assert abs(actual_final_equity - expected_cash) < 20.0, (
            f"VULNERABILITY: Real BIL series accrued ${actual_final_equity:.2f} vs expected ${expected_cash:.2f}. "
            f"Discrepancy of ${actual_final_equity - expected_cash:.2f} due to fallback override on zero/neg days."
        )


# ==============================================================================
# PROBE 4: Backward Compatibility API Probe
# ==============================================================================
class TestBackwardCompatibilityAPIProbe:
    """Verify legacy ReplayEngine call patterns and tuple unpacking."""

    def test_replay_engine_positional_init_and_run(self):
        """Legacy ReplayEngine(strategy, historical_daily) with positional run(start, end)."""
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 10)

        strat = MockBuyStrategy(["SPY"])
        # Legacy positional instantiation
        engine = ReplayEngine(strat, {"SPY": df_spy})

        assert isinstance(engine, BacktestEngine)
        # Positional run(start, end)
        res = engine.run(dates[0], dates[-1])
        assert isinstance(res, BacktestResult)

    def test_tuple_unpacking_three_elements(self):
        """Legacy pattern: metrics, trades, equity_curve = engine.run(...)."""
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 10)

        strat = MockBuyStrategy(["SPY"])
        engine = ReplayEngine(strategy=strat, historical_daily={"SPY": df_spy})

        # Must unpack into exactly 3 elements
        metrics, trades, equity_curve = engine.run(dates[0], dates[-1])

        assert hasattr(metrics, "sharpe_ratio")
        assert hasattr(metrics, "total_return_pct")
        assert isinstance(trades, list)
        assert isinstance(equity_curve, pd.Series)
        assert len(equity_curve) == len(dates)

    def test_result_indexing_and_attribute_access(self):
        """BacktestResult supports indexing res[0], res[1], res[2] and full attribute suite."""
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(10)]
        df_spy = _make_daily_df("SPY", dates, [400.0] * 10)

        engine = BacktestEngine(historical_daily={"SPY": df_spy})
        res = engine.run(dates[0], dates[-1])

        # Indexing
        assert res[0] is res.metrics
        assert res[1] is res.trades
        assert res[2] is res.equity_curve

        # Extended properties
        assert hasattr(res, "circuit_breaker_events")
        assert hasattr(res, "cb_events")
        assert hasattr(res, "daily_regime_scores")
        assert hasattr(res, "portfolio_daily_volatility")
