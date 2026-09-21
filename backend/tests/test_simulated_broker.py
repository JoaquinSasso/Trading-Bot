"""Tests unitarios para SimulatedBroker, modelado de fills, gaps y comisiones regulatorias."""

from datetime import datetime
from decimal import Decimal

from tbot.backtest.simulated_broker import SimulatedBroker
from tbot.strategies.interfaces import Signal


def test_simulated_broker_buy_with_slippage():
    broker = SimulatedBroker(initial_capital=Decimal("2000.00"), etf_slippage_bps=2.0)

    sig = Signal.create(
        strategy_id="test",
        version="1.0.0",
        symbol="SPY",
        bar_ts=datetime(2025, 6, 10, 15, 30),
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("500.00"),
        stop_price=Decimal("495.00"),
    )

    # Apertura de la siguiente barra a $500.00
    pos = broker.submit_buy(
        sig,
        qty=Decimal("2"),
        next_bar_open=Decimal("500.00"),
        timestamp=datetime(2025, 6, 10, 15, 31),
    )
    assert pos is not None
    assert pos.symbol == "SPY"
    assert pos.qty == Decimal("2")
    # Con 2 bps de slippage, $500.00 * 1.0002 = $500.10
    assert pos.entry_price == Decimal("500.1000")
    # Efectivo descontado: 2 * 500.10 = $1000.20 -> Queda $999.80
    assert broker.cash == Decimal("999.8000")


def test_simulated_broker_stop_loss_with_gap_down():
    broker = SimulatedBroker(initial_capital=Decimal("2000.00"))

    sig = Signal.create(
        strategy_id="test",
        version="1.0.0",
        symbol="SPY",
        bar_ts=datetime(2025, 6, 10, 15, 30),
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("500.00"),
        stop_price=Decimal("495.00"),
    )
    broker.submit_buy(
        sig,
        qty=Decimal("1"),
        next_bar_open=Decimal("500.00"),
        timestamp=datetime(2025, 6, 10, 15, 31),
    )

    # Barra siguiente abre con GAP a $490.00 (por debajo del stop de $495.00)
    trade = broker.evaluate_bar(
        symbol="SPY",
        bar_open=Decimal("490.00"),
        bar_high=Decimal("492.00"),
        bar_low=Decimal("489.00"),
        bar_close=Decimal("491.00"),
        timestamp=datetime(2025, 6, 11, 9, 30),
    )

    assert trade is not None
    assert trade.exit_reason == "stop_loss"
    # El fill debe ser en la apertura ($490 - slippage), NO en $495 (modelado de gap)
    assert trade.exit_price < Decimal("495.00")
    assert trade.exit_price <= Decimal("490.00")
    assert trade.pnl < 0
    assert "SPY" not in broker.positions


def test_simulated_broker_regulatory_fees():
    broker = SimulatedBroker()
    # Venta de 100 acciones a $200.00 ($20,000 nocional)
    fees = broker.calculate_sell_regulatory_fees(qty=Decimal("100"), exit_price=Decimal("200.00"))
    # SEC fee = 20000 * 0.0000278 = $0.556
    # FINRA TAF = 100 * 0.000166 = $0.0166
    # Total ~ $0.5726
    assert fees > Decimal("0.50")
    assert fees < Decimal("0.70")
