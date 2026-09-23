"""Tests del motor de backtest v2.3 (correcciones de la auditoría y optimización)."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tbot.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    _DailyIndex,
    _LazyDailyBars,
    register_trial,
)
from tbot.backtest.guards import holdout_bypass_context
from tbot.backtest.simulated_broker import SimulatedBroker
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    PositionSnapshot,
    Signal,
    StrategyContext,
    StrategyDataRequirements,
)
from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy


def _bdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _df(sym: str, dates: list[date], closes: list[float], spread: float = 0.01) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * (1 + spread) for c in closes],
            "low": [c * (1 - spread) for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(dates),
            "symbol": sym,
        }
    )
    df.attrs["is_synthetic"] = True
    return df


def _trend(n: int, start: float, daily: float, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    rets = daily + rng.normal(0, 0.012, n)
    return list(start * np.cumprod(1 + rets))


class _BuyOnce:
    """Compra un símbolo el primer día; sin salidas propias."""

    id = "buy_once"
    version = "1.0.0"
    schedule = ["15:45 America/New_York"]
    allowed_regimes = {MarketRegime.BULL_CALM, MarketRegime.BULL_VOLATILE, MarketRegime.BEAR}
    allows_open_window = False
    universe = None
    data_requirements = StrategyDataRequirements()

    def __init__(self, sym: str, stop_pct: float = 0.5, exit_at_close: bool = False, weight: float | None = 0.5):
        self.sym, self.stop_pct, self.eac, self.weight = sym, stop_pct, exit_at_close, weight
        self.done = False
        self.seen_rows: list[pd.Series] = []

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        if self.sym in ctx.daily_bars:
            self.seen_rows.append(ctx.daily_bars[self.sym].iloc[-1])
        if self.done or self.sym not in ctx.current_prices:
            return []
        self.done = True
        px = ctx.current_prices[self.sym]
        return [
            Signal.create(
                strategy_id=self.id, version=self.version, symbol=self.sym, bar_ts=ctx.now, side="buy",
                entry_type="market", entry_price_ref=px * Decimal("0.9"),  # referencia distinta al cierre
                stop_price=px * Decimal(str(1 - self.stop_pct)), max_holding=0,
                exit_at_close=self.eac, target_weight=self.weight,
            )
        ]


def _cfg(strategy, **kw) -> BacktestConfig:
    base = dict(
        strategy=strategy, initial_capital=Decimal("10000"), integer_shares=False,
        enable_vol_control=False, enable_cash_yield=False, enable_circuit_breakers=False,
        exit_overlay="never", apply_retail_costs=False, min_position_usd=1.0,
    )
    base.update(kw)
    return BacktestConfig(**base)


# ─────────────────────────────────────────────────────────────── ejecución y visibilidad
def test_entry_fills_at_session_close_not_signal_reference():
    dates = _bdays(date(2021, 1, 4), 10)
    data = {"AAA": _df("AAA", dates, [100.0 + i for i in range(10)])}
    strat = _BuyOnce("AAA")
    with holdout_bypass_context():
        res = BacktestEngine(config=_cfg(strat, single_position_cap=0.5), historical_daily=data).run(dates[0], dates[-1])
    tr = res.trades[0]
    assert tr.entry_price == Decimal("100.0000")  # cierre del día 0, no 0.9x
    assert tr.qty == Decimal("50.0000")  # target_weight 0.5 de 10.000 al precio de cierre


def test_same_bar_high_low_volume_are_masked_at_decision_time():
    dates = _bdays(date(2021, 1, 4), 5)
    df = _df("AAA", dates, [100.0] * 5, spread=0.05)
    strat = _BuyOnce("AAA")
    with holdout_bypass_context():
        BacktestEngine(config=_cfg(strat), historical_daily={"AAA": df}).run(dates[0], dates[-1])
    last = strat.seen_rows[0]
    assert last["high"] == 100.0 and last["low"] == 100.0
    assert math.isnan(last["volume"])
    # El DataFrame original no se modifica
    assert df["high"].iloc[0] == pytest.approx(105.0)


def test_next_open_execution_mode():
    dates = _bdays(date(2021, 1, 4), 5)
    closes = [100.0, 110.0, 120.0, 130.0, 140.0]
    df = _df("AAA", dates, closes)
    df["open"] = [c - 5 for c in closes]
    strat = _BuyOnce("AAA")
    with holdout_bypass_context():
        res = BacktestEngine(config=_cfg(strat, execution_timing="next_open"), historical_daily={"AAA": df}).run(
            dates[0], dates[-1]
        )
    assert res.trades[0].entry_price == Decimal("105.0000")  # apertura del día 1
    assert res.trades[0].entry_time.date() == dates[1]


# ─────────────────────────────────────────────────────────────── broker
def _nocost_broker(**kw) -> SimulatedBroker:
    return SimulatedBroker(etf_slippage_bps=0.0, stock_slippage_bps=0.0, sec_fee_rate=Decimal("0"),
                           finra_taf_per_share=Decimal("0"), cat_fee_per_share=Decimal("0"), **kw)


def test_stop_is_evaluated_before_exit_at_close():
    b = _nocost_broker(initial_capital=Decimal("10000"))
    sig = Signal.create("x", "1", "AAA", datetime(2021, 1, 4, 15, 45), "buy", "market", Decimal("100"),
                        Decimal("95"), exit_at_close=True)
    b.submit_buy(sig, Decimal("10"), Decimal("100"), datetime(2021, 1, 4, 15, 45))
    tr = b.evaluate_bar("AAA", Decimal("100"), Decimal("101"), Decimal("90"), Decimal("99"),
                        datetime(2021, 1, 5, 16, 0), is_market_close=True)
    assert tr.exit_reason == "stop_loss" and tr.exit_price == Decimal("95.0000")


def test_legacy_flag_reproduces_exit_at_close_first():
    b = SimulatedBroker(initial_capital=Decimal("10000"), legacy=True)
    sig = Signal.create("x", "1", "AAA", datetime(2021, 1, 4, 15, 45), "buy", "market", Decimal("100"),
                        Decimal("95"), exit_at_close=True)
    b.submit_buy(sig, Decimal("10"), Decimal("100"), datetime(2021, 1, 4, 15, 45))
    tr = b.evaluate_bar("AAA", Decimal("100"), Decimal("101"), Decimal("90"), Decimal("99"),
                        datetime(2021, 1, 5, 16, 0), is_market_close=True)
    assert tr.exit_reason == "exit_at_close"


def test_settlement_skips_market_holidays_with_calendar():
    b = SimulatedBroker(initial_capital=Decimal("1000"), settlement_days=1)
    # 2021-07-05 fue feriado (Independence Day observado)
    b.set_trading_days([date(2021, 7, 1), date(2021, 7, 2), date(2021, 7, 6), date(2021, 7, 7)])
    assert b.calculate_settlement_date(date(2021, 7, 2)) == date(2021, 7, 6)


def test_gfv_aware_cash_account_allows_unsettled_buys_and_counts_violations():
    b = _nocost_broker(initial_capital=Decimal("1000"), cash_account_model="gfv_aware")
    b.set_trading_days(_bdays(date(2021, 1, 4), 10))
    t0 = datetime(2021, 1, 4, 15, 45)
    s1 = Signal.create("x", "1", "AAA", t0, "buy", "market", Decimal("100"), Decimal("50"))
    b.submit_buy(s1, Decimal("10"), Decimal("100"), t0)
    b.close_position("AAA", Decimal("100"), t0, "x")  # $1000 sin liquidar hasta el 5/1
    assert b.settled_cash == Decimal("0") and b.buying_power == Decimal("1000")
    s2 = Signal.create("x", "1", "BBB", t0, "buy", "market", Decimal("100"), Decimal("50"))
    pos = b.submit_buy(s2, Decimal("10"), Decimal("100"), t0)
    assert pos is not None and pos.unsettled_funding_until == date(2021, 1, 5)
    tr = b.close_position("BBB", Decimal("100"), t0, "x")  # venta antes de liquidar -> GFV
    assert tr.gfv and b.gfv_count == 1


# ─────────────────────────────────────────────────────────────── control de volatilidad
def test_vol_control_aligns_returns_by_date():
    dates = _bdays(date(2020, 1, 2), 200)
    base = _trend(200, 100.0, 0.0, seed=1)
    a = _df("AAA", dates, base)
    # BBB: mismos retornos pero empieza 30 sesiones después (historia más corta)
    b = _df("BBB", dates[30:], [x * 2 for x in base[30:]])
    with holdout_bypass_context():
        eng = BacktestEngine(config=_cfg(None), historical_daily={"AAA": a, "BBB": b})
        eng._index("AAA"), eng._index("BBB")
        days_np = np.array([dates[-1]], dtype="datetime64[D]")
        le = {s: np.searchsorted(eng._index(s).dnp, days_np, "right") - 1 for s in ("AAA", "BBB")}
        sigma_new, _ = eng._vol_control(0, le, {"AAA": 0.5, "BBB": 0.5})
        eng._legacy = True
        sigma_legacy, _ = eng._vol_control(0, le, {"AAA": 0.5, "BBB": 0.5})
    # Correlación 1 por fecha. Con contracción lambda=0.3: var = 0.25v + 0.25v + 2*0.25*0.7v = 0.85v
    r = pd.Series(base).pct_change().iloc[-90:]
    single = float(r.std(ddof=1) * math.sqrt(252))
    assert sigma_new == pytest.approx(single * math.sqrt(0.85), rel=1e-6)
    assert sigma_legacy < sigma_new * 0.9  # el desfase por fila destruía la correlación


# ─────────────────────────────────────────────────────────────── cortacircuitos
def test_interpolated_emergency_flatten_exits_between_open_and_low():
    dates = _bdays(date(2021, 1, 4), 4)
    df = _df("AAA", dates, [100.0, 100.0, 100.0, 100.0], spread=0.001)
    df.loc[2, ["open", "high", "low", "close"]] = [100.0, 100.0, 80.0, 85.0]
    strat = _BuyOnce("AAA", stop_pct=0.5, weight=0.5)
    cfg = _cfg(strat, enable_circuit_breakers=True, emergency_loss_limit_pct=3.5, single_position_cap=0.5)
    with holdout_bypass_context():
        res = BacktestEngine(config=cfg, historical_daily={"AAA": df}).run(dates[0], dates[-1])
    cb_trades = [t for t in res.trades if t.exit_reason == "circuit_breaker_emergency_flatten"]
    assert len(cb_trades) == 1
    # 50% invertido: la cartera cae 3.5% cuando AAA cae 7% -> sale a ~93, no en el mínimo (80)
    assert float(cb_trades[0].exit_price) == pytest.approx(93.0, abs=0.05)


def test_consecutive_loss_pause_has_cooldown():
    dates = _bdays(date(2021, 1, 4), 3)
    data = {"SPY": _df("SPY", dates, [100.0, 100.0, 100.0])}
    cfg = _cfg(None, enable_circuit_breakers=True, max_consecutive_losses=2, consecutive_loss_cooldown_sessions=1)
    with holdout_bypass_context():
        eng = BacktestEngine(config=cfg, historical_daily=data)
    cb = eng.cb_manager
    cb.reset_daily(Decimal("10000"), dates[0])
    fake = type("T", (), {"pnl": Decimal("-1"), "exit_time": datetime(2021, 1, 4)})()
    eng._record_trade(fake, 0)
    eng._record_trade(fake, 0)
    assert not cb.can_open_new_positions()
    cb.reset_daily(Decimal("10000"), dates[1])
    eng._apply_streak_cooldown(1)
    assert cb.consecutive_losses == 0 and cb.can_open_new_positions()


# ─────────────────────────────────────────────────────────────── efectivo y métricas
def test_bil_price_returns_are_smoothed_not_clipped():
    dates = _bdays(date(2021, 1, 4), 60)
    rng = np.random.default_rng(0)
    raw = pd.Series(rng.normal(0.0, 0.0002, len(dates)), index=dates)  # media 0, mucho ruido
    data = {"SPY": _df("SPY", dates, [100.0] * len(dates))}
    cfg = _cfg(None, enable_cash_yield=True, rf_series=raw)
    with holdout_bypass_context():
        res = BacktestEngine(config=cfg, historical_daily=data).run(dates[0], dates[-1])
    clipped = 10000 * float(np.prod(1 + raw.clip(lower=0)))
    assert float(res.equity_curve.iloc[-1]) < 10000 + (clipped - 10000) * 0.5
    assert any("retornos de BIL" in w for w in res.warnings)


def test_trial_ledger_counts_distinct_variants(tmp_path):
    ledger = tmp_path / "trials.json"
    assert register_trial(ledger, "s5", "a") == 1
    assert register_trial(ledger, "s5", "a") == 1
    assert register_trial(ledger, "s5", "b") == 2
    dates = _bdays(date(2021, 1, 4), 5)
    data = {"AAA": _df("AAA", dates, [100.0] * 5)}
    with holdout_bypass_context():
        for stop in (0.1, 0.2, 0.3):
            cfg = _cfg(_BuyOnce("AAA", stop_pct=stop), trial_ledger_path=ledger, trial_family="buy_once")
            res = BacktestEngine(config=cfg, historical_daily=data).run(dates[0], dates[-1])
    assert res.metrics.num_tested_trials == 3


def test_synthetic_bypass_requires_all_frames_synthetic():
    from tbot.backtest.guards import HoldoutViolationError

    dates = _bdays(date(2023, 3, 1), 5)  # dentro del holdout diario
    real = _df("AAA", dates, [100.0] * 5)
    real.attrs.pop("is_synthetic")
    synth = _df("BBB", dates, [100.0] * 5)
    with pytest.raises(HoldoutViolationError):
        BacktestEngine(config=_cfg(None), historical_daily={"AAA": real, "BBB": synth}).run(dates[0], dates[-1])


# ─────────────────────────────────────────────────────────────── S5 v1.3
def _s5_ctx(regime, prices, bars, positions):
    return StrategyContext(
        now=datetime(2022, 6, 1, 15, 45), regime=regime, daily_bars=bars,
        current_prices=prices, portfolio_positions=set(positions), positions=positions,
    )


def _snap(sym, bars_held=5, stop=Decimal("1")):
    return PositionSnapshot(sym, Decimal("1"), Decimal("100"), datetime(2022, 5, 1), stop, stop, bars_held,
                            "dual_momentum_leader")


def test_s5_exits_everything_in_bear_regime():
    s5 = DualMomentumLeaderStrategy(universe=["AAA"], top_n_leaders=1)
    acts = s5.manage_positions(_s5_ctx(MarketRegime.BEAR, {"AAA": Decimal("100")}, {}, {"AAA": _snap("AAA")}))
    assert [(a.symbol, a.action, a.reason) for a in acts] == [("AAA", "exit", "regime_bear_exit")]


def test_s5_rotates_out_of_positions_below_rank_buffer():
    dates = _bdays(date(2022, 1, 3), 80)
    syms = ["A1", "A2", "A3", "A4"]
    bars = {s: _df(s, dates, [100.0 * (1 + (0.004 - 0.001 * i)) ** k for k in range(80)]) for i, s in enumerate(syms)}
    prices = {s: Decimal(str(round(bars[s]["close"].iloc[-1], 4))) for s in syms}
    s5 = DualMomentumLeaderStrategy(universe=syms, top_n_leaders=1, rotation_buffer=1)
    acts = s5.manage_positions(_s5_ctx(MarketRegime.BULL_CALM, prices, bars, {"A4": _snap("A4")}))
    # A4 es 4° en el ranking (> top 1 + buffer 1) -> rotación
    assert any(a.symbol == "A4" and a.reason == "rotation_rank" for a in acts)
    acts2 = s5.manage_positions(_s5_ctx(MarketRegime.BULL_CALM, prices, bars, {"A2": _snap("A2")}))
    assert not any(a.action == "exit" for a in acts2)  # 2° está dentro del buffer


def test_s5_renews_leader_at_max_holding():
    dates = _bdays(date(2022, 1, 3), 80)
    bars = {"A1": _df("A1", dates, [100.0 * 1.004**k for k in range(80)])}
    prices = {"A1": Decimal(str(round(bars["A1"]["close"].iloc[-1], 4)))}
    s5 = DualMomentumLeaderStrategy(universe=["A1"], top_n_leaders=1, max_holding_sessions=30)
    acts = s5.manage_positions(_s5_ctx(MarketRegime.BULL_CALM, prices, bars, {"A1": _snap("A1", bars_held=30)}))
    assert any(a.action == "reset_holding" for a in acts)
    assert not any(a.action == "exit" for a in acts)


def test_s5_fast_path_matches_general_path_in_engine(monkeypatch):
    dates = _bdays(date(2019, 1, 2), 400)
    syms = ["A1", "A2", "A3", "A4", "A5"]
    data = {s: _df(s, dates, _trend(400, 50.0 + 10 * i, 0.0006 * (i - 1), seed=i)) for i, s in enumerate(syms)}
    data["SPY"] = _df("SPY", dates, _trend(400, 300.0, 0.0004, seed=99))

    def run():
        cfg = BacktestConfig(
            strategy=DualMomentumLeaderStrategy(universe=syms, top_n_leaders=2),
            initial_capital=Decimal("10000"), integer_shares=False, enable_cash_yield=False,
        )
        with holdout_bypass_context():
            return BacktestEngine(config=cfg, historical_daily=data).run(dates[250], dates[-1])

    fast = run()
    monkeypatch.setattr(DualMomentumLeaderStrategy, "prepare", lambda self, d: None)
    slow = run()
    assert len(fast.trades) > 0
    assert [(t.symbol, t.entry_time, t.exit_time, t.qty, t.pnl) for t in fast.trades] == [
        (t.symbol, t.entry_time, t.exit_time, t.qty, t.pnl) for t in slow.trades
    ]
    assert fast.equity_curve.equals(slow.equity_curve)


def test_lazy_bars_row_index_and_mapping_protocol():
    dates = _bdays(date(2021, 1, 4), 3)
    df = _df("AAA", dates, [1.0, 2.0, 3.0])
    df["_parsed_date"] = df["date"]
    ix = {"AAA": _DailyIndex(df)}
    lazy = _LazyDailyBars(ix, {"AAA": 2, "BBB": 0}, dates[1], mask_today=False)
    assert "AAA" in lazy and "BBB" not in lazy and list(lazy) == ["AAA"]
    assert len(lazy["AAA"]) == 2 and lazy.row_index("AAA") == 1 and lazy.get("ZZZ") is None


# ─────────────────────────────────────────────────────────────── apalancamiento (margin)
def _lev_cfg(strategy, **kw) -> BacktestConfig:
    base = dict(account_type="margin", leverage=2.0, single_position_cap=1.0, margin_annual_rate=0.065)
    base.update(kw)
    return _cfg(strategy, **base)


def test_leverage_requires_margin_account_and_respects_reg_t():
    with pytest.raises(ValueError, match="margin"):
        BacktestEngine(config=_cfg(None, leverage=1.5), historical_daily={})
    with pytest.raises(ValueError, match="Reg T"):
        BacktestEngine(config=_cfg(None, account_type="margin", leverage=2.5), historical_daily={})


def test_margin_buying_power_reg_t_cap_and_minimum_equity():
    b = _nocost_broker(initial_capital=Decimal("10000"), account_type="margin", max_gross_leverage=Decimal("2"))
    assert b.buying_power == Decimal("20000")
    b.max_gross_leverage = Decimal("1.5")
    assert b.buying_power == Decimal("15000.0")
    small = _nocost_broker(initial_capital=Decimal("1500"), account_type="margin", max_gross_leverage=Decimal("2"))
    assert small.buying_power == Decimal("1500")  # Alpaca: < $2.000 de equity -> 1x


def test_leveraged_position_size_and_weekend_interest():
    dates = _bdays(date(2021, 1, 4), 10)  # lunes 4 -> viernes 15
    data = {"AAA": _df("AAA", dates, [100.0] * 10)}
    strat = _BuyOnce("AAA", weight=1.0)
    with holdout_bypass_context():
        res = BacktestEngine(config=_lev_cfg(strat), historical_daily=data).run(dates[0], dates[-1])
    assert res.trades[0].qty == Decimal("200.0000")  # 2x de 10.000 a $100
    # Deuda de 10.000 desde el cierre del 4/1 hasta el 15/1: 11 días calendario (incluye fin de
    # semana). El interés se debita cada sesión, así que la deuda crece con lo ya cobrado.
    debit, expected = 10000.0, 0.0
    for prev, cur in zip(dates[:-1], dates[1:], strict=True):
        charge = debit * 0.065 * (cur - prev).days / 360
        expected += charge
        debit += charge
    assert res.margin_interest_paid == pytest.approx(expected, rel=1e-9)
    assert float(res.equity_curve.iloc[-1]) == pytest.approx(10000 - expected, abs=1e-6)
    assert res.gross_leverage.iloc[0] == pytest.approx(2.0)


def test_margin_call_liquidates_at_next_open():
    dates = _bdays(date(2021, 1, 4), 5)
    df = _df("AAA", dates, [100.0, 60.0, 60.0, 60.0, 60.0], spread=0.0)
    df.loc[2, "open"] = 58.0
    strat = _BuyOnce("AAA", stop_pct=0.9, weight=1.0)
    with holdout_bypass_context():
        res = BacktestEngine(config=_lev_cfg(strat, margin_annual_rate=0.0), historical_daily=data_dict(df)).run(
            dates[0], dates[-1]
        )
    kinds = [e.event_type for e in res.margin_events]
    # Día 1: equity 2.000 sobre 12.000 de exposición < 30% de mantenimiento -> margin call
    assert kinds[:2] == ["MARGIN_CALL", "MARGIN_LIQUIDATION"]
    liq = [t for t in res.trades if t.exit_reason == "margin_call_liquidation"][0]
    assert liq.exit_time.date() == dates[2] and liq.exit_price == Decimal("58.0000")


def data_dict(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"AAA": df}


def test_negative_equity_guard_liquidates_before_equity_goes_below_zero():
    dates = _bdays(date(2021, 1, 4), 3)
    df = _df("AAA", dates, [100.0, 100.0, 100.0], spread=0.0)
    df.loc[1, ["open", "high", "low", "close"]] = [100.0, 100.0, 40.0, 45.0]
    strat = _BuyOnce("AAA", stop_pct=0.9, weight=1.0)
    with holdout_bypass_context():
        res = BacktestEngine(config=_lev_cfg(strat, margin_annual_rate=0.0), historical_daily=data_dict(df)).run(
            dates[0], dates[-1]
        )
    assert any(e.event_type == "NEGATIVE_EQUITY_LIQUIDATION" for e in res.margin_events)
    liq = [t for t in res.trades if t.exit_reason == "negative_equity_liquidation"][0]
    assert liq.exit_price == Decimal("50.0000")  # 200 acciones * 50 = deuda de 10.000 -> equity 0
    assert float(res.equity_curve.iloc[-1]) == pytest.approx(0.0, abs=1e-6)


def test_vol_target_scales_with_leverage():
    dates = _bdays(date(2020, 1, 2), 150)
    a = _df("AAA", dates, _trend(150, 100.0, 0.0, seed=3))
    with holdout_bypass_context():
        eng = BacktestEngine(config=_lev_cfg(None, enable_vol_control=True, target_portfolio_vol=0.10),
                             historical_daily={"AAA": a})
        days_np = np.array([dates[-1]], dtype="datetime64[D]")
        le = {"AAA": np.searchsorted(eng._index("AAA").dnp, days_np, "right") - 1}
        sigma, k = eng._vol_control(0, le, {"AAA": 2.0})
    assert k == pytest.approx(min(1.0, 0.20 / sigma))
