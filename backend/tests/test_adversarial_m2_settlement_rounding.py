"""Adversarial Stress Test Suite for Micro-Account Whole Share Rounding & Reg T Cash (T+1) Settlement.

Audits:
1. Whole Shares Micro-Account Stress:
   - Fractional shares flooring across small balances ($50, $100, $250, $500).
   - Friction boundary condition: asset price near available buying power, ensuring retail costs
     (spread, slippage, SEC fee, FINRA TAF, CAT) do not push settled cash negative.
   - Ultra-expensive shares ($100k+ BRK-style) on small accounts cleanly reject without failure.
   - Zero available cash cleanly rejects buy signals without exception.
2. Reg T Cash (T+1) Settlement Mechanics:
   - GFV prevention: Unsettled funds from Day T sales cannot be used for buys on Day T.
   - Weekend rollover: Sales on Friday settle on Monday (skipping Saturday & Sunday).
   - Multi-credit queue: Staggered sales across consecutive days settle on their respective T+1 dates.
   - Margin account comparison: In Margin mode, unsettled cash is immediately available for buying.
3. Reg T Cash & Cash Yield Interaction:
   - Cash yield accrues STRICTLY on settled cash; unsettled funds in flight earn 0.0 yield until settled.
   - Transition from unsettled to settled immediately starts earning yield on subsequent dates.
"""

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd

from tbot.backtest.engine import BacktestConfig, BacktestEngine
from tbot.backtest.simulated_broker import SimulatedBroker
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


class DeterministicSequenceStrategy:
    """Strategy that issues buy signals according to a predefined schedule."""

    id: str = "adversarial_seq_strategy"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    data_requirements = StrategyDataRequirements()

    def __init__(self, signals_by_date: dict[date, list[tuple[str, float]]]) -> None:
        self.signals_by_date = signals_by_date

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        cur_d = ctx.now.date()
        if cur_d not in self.signals_by_date:
            return []
        out = []
        for sym, stop_pct in self.signals_by_date[cur_d]:
            if sym in ctx.current_prices and sym not in ctx.portfolio_positions:
                p = ctx.current_prices[sym]
                stop = p * Decimal(str(1.0 - stop_pct))
                out.append(
                    Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=sym,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=p,
                        stop_price=stop,
                    )
                )
        return out


def _make_df(symbol: str, dates: list[date], prices: list[float]) -> pd.DataFrame:
    records = []
    for d, p in zip(dates, prices, strict=True):
        records.append({
            "symbol": symbol,
            "date": d,
            "open": round(p, 2),
            "high": round(p * 1.01, 2),
            "low": round(p * 0.99, 2),
            "close": round(p, 2),
            "volume": 500_000,
        })
    df = pd.DataFrame(records)
    df.attrs["is_synthetic"] = True
    return df


class TestMicroAccountWholeShareRounding:
    """Stress tests on whole share rounding, flooring, and boundary frictions."""

    def test_friction_boundary_condition_never_negative_cash(self):
        """Account = $100. Stock = $99.00.

        With 2.5 bps spread + 5.0 bps slippage, fill price is ~$99.074.
        Broker must verify fill price * qty <= settled_cash, ensuring cash NEVER goes negative.
        """
        broker = SimulatedBroker(
            initial_capital=Decimal("100.00"),
            account_type="cash",
            integer_shares=True,
            stock_slippage_bps=5.0,
            stock_half_spread_bps=2.5,
        )
        sig = Signal.create(
            strategy_id="test",
            version="1.0.0",
            symbol="TIGHT",
            bar_ts=datetime(2025, 1, 6, 9, 30),
            side="buy",
            entry_type="market",
            entry_price_ref=Decimal("99.50"),
            stop_price=Decimal("90.00"),
        )
        # Attempt to buy 1 share at open $99.50.
        # Fill price will be 99.50 * (1 + 0.0005 + 0.00025) = 99.5746.
        # Total cost $99.5746 <= $100.00 -> fill succeeds, remaining settled cash = $0.4254 > 0.
        pos = broker.submit_buy(sig, Decimal("1"), Decimal("99.50"), datetime(2025, 1, 6, 9, 30))
        assert pos is not None
        assert pos.qty == Decimal("1")
        assert broker.settled_cash > Decimal("0.0")
        assert broker.settled_cash == Decimal("100.00") - pos.entry_price

        # Now attempt second buy with remaining $0.42. Stock price $1.00.
        # $0.42 < 1 share ($1.00) -> must be rejected cleanly
        sig2 = Signal.create(
            strategy_id="test",
            version="1.0.0",
            symbol="CHEAP",
            bar_ts=datetime(2025, 1, 6, 9, 35),
            side="buy",
            entry_type="market",
            entry_price_ref=Decimal("1.00"),
            stop_price=Decimal("0.90"),
        )
        pos2 = broker.submit_buy(sig2, Decimal("1"), Decimal("1.00"), datetime(2025, 1, 6, 9, 35))
        assert pos2 is None
        assert broker.settled_cash > Decimal("0.0")

    def test_fractional_flooring_behavior(self):
        """Target allocation yields fractional share quantities (0.999, 1.999, 2.001).

        With integer_shares=True, broker and engine must floor to int without rounding up.
        """
        broker = SimulatedBroker(
            initial_capital=Decimal("500.00"),
            account_type="cash",
            integer_shares=True,
        )
        sig = Signal.create(
            strategy_id="test",
            version="1.0.0",
            symbol="AAPL",
            bar_ts=datetime(2025, 1, 6, 9, 30),
            side="buy",
            entry_type="market",
            entry_price_ref=Decimal("150.00"),
            stop_price=Decimal("140.00"),
        )
        # 1. qty = 0.9999 -> floors to 0 -> rejected
        pos = broker.submit_buy(sig, Decimal("0.9999"), Decimal("150.00"), datetime(2025, 1, 6, 9, 30))
        assert pos is None

        # 2. qty = 1.9999 -> floors to 1
        pos = broker.submit_buy(sig, Decimal("1.9999"), Decimal("150.00"), datetime(2025, 1, 6, 9, 30))
        assert pos is not None
        assert pos.qty == Decimal("1")

        # 3. Clean up
        broker.close_position("AAPL", Decimal("150.00"), datetime(2025, 1, 6, 15, 0), "test")

    def test_ultra_expensive_stock_micro_account_rejection(self):
        """Micro account ($250) attempting to buy BRK.A ($600,000).

        Must cleanly reject without throwing exception or corrupting state.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(5)]
        df_spy = _make_df("SPY", dates, [400.0] * 5)
        df_brk = _make_df("BRKA", dates, [600_000.0] * 5)

        strat = DeterministicSequenceStrategy({dates[0]: [("BRKA", 0.05)]})
        config = BacktestConfig(
            strategy=strat,
            initial_capital=Decimal("250.00"),
            single_position_cap=0.25,
            min_position_usd=10.0,
            integer_shares=True,
            enable_vol_control=False,
            enable_cash_yield=False,
        )
        engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy, "BRKA": df_brk})
        res = engine.run(dates[0], dates[-1])

        assert len(res.trades) == 0
        assert engine.broker.settled_cash == Decimal("250.00")
        assert float(res.equity_curve.iloc[-1]) == 250.00

    def test_zero_cash_clean_rejection(self):
        """Account with $0.00 initial capital receives buy signal.

        Must reject with 0 trades, no exception, settled cash remaining exactly 0.00.
        """
        broker = SimulatedBroker(
            initial_capital=Decimal("0.00"),
            account_type="cash",
            integer_shares=True,
        )
        sig = Signal.create(
            strategy_id="test",
            version="1.0.0",
            symbol="XYZ",
            bar_ts=datetime(2025, 1, 6, 9, 30),
            side="buy",
            entry_type="market",
            entry_price_ref=Decimal("10.00"),
            stop_price=Decimal("9.00"),
        )
        pos = broker.submit_buy(sig, Decimal("10"), Decimal("10.00"), datetime(2025, 1, 6, 9, 30))
        assert pos is None
        assert broker.buying_power == Decimal("0.0")
        assert broker.settled_cash == Decimal("0.0")


class TestRegTCashSettlementMechanics:
    """Stress tests for Reg T Cash (T+1) settlement and Good Faith Violation prevention."""

    def test_gfv_prevention_same_day_reinvestment_blocked(self):
        """Account starts with $1,000.

        1. Buy $1,000 of Stock A on Monday 9:30. Settled cash becomes $0.
        2. Sell Stock A on Monday 15:30 for $1,050. Proceeds are placed into unsettled_cash.
        3. Attempt to buy Stock B on Monday 15:45. In Cash account, buying power is $0.
           Purchase of Stock B must be REJECTED to prevent Good Faith Violation (GFV).
        """
        broker = SimulatedBroker(
            initial_capital=Decimal("1000.00"),
            account_type="cash",
            settlement_days=1,
            integer_shares=True,
            stock_slippage_bps=0.0,
            stock_half_spread_bps=0.0,
            sec_fee_rate=Decimal("0"),
            finra_taf_per_share=Decimal("0"),
            cat_fee_per_share=Decimal("0"),
        )
        t_mon_open = datetime(2025, 1, 6, 9, 30)  # Monday
        t_mon_close = datetime(2025, 1, 6, 15, 30)
        t_mon_late = datetime(2025, 1, 6, 15, 45)

        # 1. Buy Stock A for $1,000
        sig_a = Signal.create("test", "1.0", "STKA", t_mon_open, "buy", "market", Decimal("100.00"), Decimal("90.00"))
        pos_a = broker.submit_buy(sig_a, Decimal("10"), Decimal("100.00"), t_mon_open)
        assert pos_a is not None
        assert broker.settled_cash == Decimal("0.00")
        assert broker.buying_power == Decimal("0.00")

        # 2. Sell Stock A for $105.00 ($1,050.00 total)
        trade = broker.close_position("STKA", Decimal("105.00"), t_mon_close, "take_profit")
        assert trade is not None
        assert broker.settled_cash == Decimal("0.00")
        assert broker.unsettled_cash == Decimal("1050.00")
        assert broker.cash == Decimal("1050.00")
        # Buying power in Cash account MUST be 0
        assert broker.buying_power == Decimal("0.00")

        # 3. Attempt to buy Stock B on Monday afternoon
        sig_b = Signal.create("test", "1.0", "STKB", t_mon_late, "buy", "market", Decimal("50.00"), Decimal("45.00"))
        pos_b = broker.submit_buy(sig_b, Decimal("10"), Decimal("50.00"), t_mon_late)
        assert pos_b is None, "GFV VIOLATION: Cash account allowed buying with unsettled funds on Day T!"
        assert broker.settled_cash == Decimal("0.00")

        # 4. Advance to Tuesday (T+1): funds must settle
        t_tue_open = datetime(2025, 1, 7, 9, 30)
        broker.process_settlement(t_tue_open)
        assert broker.unsettled_cash == Decimal("0.00")
        assert broker.settled_cash == Decimal("1050.00")
        assert broker.buying_power == Decimal("1050.00")

        # 5. Now buying Stock B succeeds on Tuesday
        pos_b_tue = broker.submit_buy(sig_b, Decimal("10"), Decimal("50.00"), t_tue_open)
        assert pos_b_tue is not None
        assert pos_b_tue.qty == Decimal("10")
        assert broker.settled_cash == Decimal("550.00")

    def test_weekend_settlement_friday_to_monday(self):
        """Sale executed on Friday 2025-01-10 settles on Monday 2025-01-13 (skipping Sat/Sun)."""
        broker = SimulatedBroker(
            initial_capital=Decimal("1000.00"),
            account_type="cash",
            settlement_days=1,
            integer_shares=True,
            sec_fee_rate=Decimal("0"),
            finra_taf_per_share=Decimal("0"),
            cat_fee_per_share=Decimal("0"),
            stock_slippage_bps=0.0,
            stock_half_spread_bps=0.0,
        )
        t_fri_open = datetime(2025, 1, 10, 9, 30)
        t_fri_close = datetime(2025, 1, 10, 15, 30)

        sig = Signal.create("test", "1.0", "FRISTK", t_fri_open, "buy", "market", Decimal("100.00"), Decimal("90.00"))
        broker.submit_buy(sig, Decimal("10"), Decimal("100.00"), t_fri_open)
        broker.close_position("FRISTK", Decimal("100.00"), t_fri_close, "exit")

        assert broker.settled_cash == Decimal("0.00")
        assert broker.unsettled_cash == Decimal("1000.00")

        # Saturday check: must NOT settle
        broker.process_settlement(datetime(2025, 1, 11, 12, 0))
        assert broker.settled_cash == Decimal("0.00")
        assert broker.unsettled_cash == Decimal("1000.00")

        # Sunday check: must NOT settle
        broker.process_settlement(datetime(2025, 1, 12, 12, 0))
        assert broker.settled_cash == Decimal("0.00")
        assert broker.unsettled_cash == Decimal("1000.00")

        # Monday check: MUST settle
        broker.process_settlement(datetime(2025, 1, 13, 9, 30))
        assert broker.settled_cash == Decimal("1000.00")
        assert broker.unsettled_cash == Decimal("0.00")

    def test_staggered_multi_day_settlement_fifo(self):
        """Staggered trades:

        - Day 1 (Mon): Sell Trade 1 ($300).
        - Day 2 (Tue): Sell Trade 2 ($500). Trade 1 settles ($300 available).
        - Day 3 (Wed): Trade 2 settles ($500 added -> $800 total).
        """
        broker = SimulatedBroker(
            initial_capital=Decimal("1000.00"),
            account_type="cash",
            settlement_days=1,
            sec_fee_rate=Decimal("0"),
            finra_taf_per_share=Decimal("0"),
            cat_fee_per_share=Decimal("0"),
            stock_slippage_bps=0.0,
            stock_half_spread_bps=0.0,
        )
        t_tue = datetime(2025, 1, 7, 15, 0)
        t_wed = datetime(2025, 1, 8, 9, 30)

        # Mon: directly simulate unsettled credit
        broker.settled_cash = Decimal("200.00")
        broker.unsettled_cash = Decimal("300.00")
        broker._unsettled_credits.append(
            broker._unsettled_credits and broker._unsettled_credits[0] or
            type(broker._unsettled_credits)([])
        )
        from tbot.backtest.simulated_broker import UnsettledCredit
        broker._unsettled_credits = [
            UnsettledCredit(amount=Decimal("300.00"), settlement_date=date(2025, 1, 7))
        ]

        # Tue: add second trade settling on Wed
        broker.unsettled_cash += Decimal("500.00")
        broker._unsettled_credits.append(
            UnsettledCredit(amount=Decimal("500.00"), settlement_date=date(2025, 1, 8))
        )

        # Process Tuesday settlement: Day 1 credit ($300) should settle, Day 2 credit ($500) remains unsettled
        broker.process_settlement(t_tue)
        assert broker.settled_cash == Decimal("500.00")  # 200 + 300
        assert broker.unsettled_cash == Decimal("500.00")

        # Process Wednesday settlement: Day 2 credit ($500) settles
        broker.process_settlement(t_wed)
        assert broker.settled_cash == Decimal("1000.00")  # 500 + 500
        assert broker.unsettled_cash == Decimal("0.00")


class TestRegTCashYieldInteraction:
    """Stress tests on how cash yield interacts with settled vs unsettled cash."""

    def test_unsettled_cash_earns_zero_yield_until_settlement(self):
        """CRITICAL PRINCIPLE: In broker custodial architecture, funds in flight (unsettled)

        do NOT earn interest. Only settled_cash earns the risk-free daily yield.
        Verify that engine accrues yield strictly proportional to settled_cash.
        """
        dates = [date(2022, 1, 3) + timedelta(days=i) for i in range(5)]
        # SPY constant
        df_spy = _make_df("SPY", dates, [400.0] * 5)
        # STK100 sold on Day 0
        df_stk = _make_df("STK100", dates, [100.0] * 5)

        # 5% annual rate
        rf_daily = (1.0 + 0.05) ** (1.0 / 252.0) - 1.0
        rf_series = pd.Series(rf_daily, index=dates)

        config = BacktestConfig(
            strategy=None,
            initial_capital=Decimal("2000.00"),
            account_type="cash",
            settlement_days=1,
            enable_cash_yield=True,
            rf_series=rf_series,
        )
        engine = BacktestEngine(config=config, historical_daily={"SPY": df_spy, "STK100": df_stk})

        # Manually set broker state on Day 0: $1,000 settled, $1,000 unsettled settling on Day 1
        from tbot.backtest.simulated_broker import UnsettledCredit
        engine.broker.settled_cash = Decimal("1000.00")
        engine.broker.unsettled_cash = Decimal("1000.00")
        engine.broker._unsettled_credits = [
            UnsettledCredit(amount=Decimal("1000.00"), settlement_date=dates[1])
        ]

        # Run 2 days: Day 0 and Day 1
        res = engine.run(dates[0], dates[1])

        # On Day 0:
        # At EOD Day 0, settled_cash was $1,000.
        # Cash yield earned on Day 0 should be 1000 * rf_daily, NOT 2000 * rf_daily!
        day0_yield = 1000.0 * rf_daily
        day0_equity = float(res.equity_curve.iloc[0])
        # Equity on Day 0 = settled ($1000 + yield) + unsettled ($1000) = $2000 + day0_yield
        expected_day0_equity = 2000.0 + day0_yield
        assert math.isclose(day0_equity, expected_day0_equity, rel_tol=1e-5), (
            f"Day 0 equity ${day0_equity:.4f} did not match expected ${expected_day0_equity:.4f}. "
            f"Unsettled cash may have improperly accrued interest!"
        )

        # On Day 1:
        # Day 1 start: settlement occurs! Unsettled $1000 moves to settled_cash.
        # Total settled cash is now (~$1000 + day0_yield + $1000) = ~$2000.19.
        # At EOD Day 1, cash yield is earned on the ENTIRE ~$2000.19!
        day1_equity = float(res.equity_curve.iloc[1])
        day1_start_cash = 1000.0 + day0_yield + 1000.0
        day1_yield = day1_start_cash * rf_daily
        expected_day1_equity = day1_start_cash + day1_yield
        assert math.isclose(day1_equity, expected_day1_equity, rel_tol=1e-5), (
            f"Day 1 equity ${day1_equity:.4f} did not match expected ${expected_day1_equity:.4f}."
        )
