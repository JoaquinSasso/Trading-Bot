"""Tests unitarios para OrderRouter e idempotencia."""

from datetime import datetime
from decimal import Decimal

from tbot.execution.router import OrderRouter
from tbot.execution.simulated_broker_adapter import SimulatedBrokerAdapter
from tbot.risk.models import SizingResult
from tbot.strategies.interfaces import Signal


def _make_signal(symbol: str = "SPYM") -> Signal:
    return Signal(
        signal_id="sig_test_exec_1",
        strategy_id="s3_trend_pullback",
        symbol=symbol,
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("70.00"),
        stop_price=Decimal("68.50"),
        created_at=datetime(2026, 9, 21, 15, 45),
        take_profit_price=Decimal("73.00"),
        score=0.9,
    )


def test_order_router_routes_whole_bracket() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    broker.set_current_price("SPYM", Decimal("70.00"))
    router = OrderRouter(broker=broker)

    sig = _make_signal("SPYM")
    sizing = SizingResult(
        signal_symbol="SPY",
        execution_symbol="SPYM",
        shares=Decimal("5"),
        is_fractional=False,
        entry_price=Decimal("70.00"),
        stop_price=Decimal("68.50"),
        take_profit_price=Decimal("73.00"),
        risk_usd=Decimal("7.50"),
        notional_usd=Decimal("350.00"),
        path="whole",
    )

    now = datetime(2026, 9, 21, 15, 45)
    order = router.route_signal(sig, sizing, now)

    assert order.symbol == "SPYM"
    assert order.qty == Decimal("5")
    assert order.status == "filled"
    assert order.legs is not None
    assert len(order.legs) == 2  # TP y SL hijas


def test_order_router_routes_fractional_simple() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    broker.set_current_price("NVDA", Decimal("140.00"))
    router = OrderRouter(broker=broker)

    sig = _make_signal("NVDA")
    sizing = SizingResult(
        signal_symbol="NVDA",
        execution_symbol="NVDA",
        shares=Decimal("0.25"),
        is_fractional=True,
        entry_price=Decimal("140.00"),
        stop_price=Decimal("120.00"),
        take_profit_price=Decimal("180.00"),
        risk_usd=Decimal("5.00"),
        notional_usd=Decimal("35.00"),
        path="fractional_fallback",
    )

    now = datetime(2026, 9, 21, 15, 45)
    order = router.route_signal(sig, sizing, now)

    assert order.symbol == "NVDA"
    assert order.qty == Decimal("0.25")
    assert order.status == "filled"
    assert order.legs is None  # En fraccionarios no hay bracket nativo


def test_order_router_idempotency_prevents_duplicate_orders() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    broker.set_current_price("SPYM", Decimal("70.00"))
    router = OrderRouter(broker=broker)

    sig = _make_signal("SPYM")
    sizing = SizingResult(
        signal_symbol="SPY",
        execution_symbol="SPYM",
        shares=Decimal("5"),
        is_fractional=False,
        entry_price=Decimal("70.00"),
        stop_price=Decimal("68.50"),
        take_profit_price=Decimal("73.00"),
        risk_usd=Decimal("7.50"),
        notional_usd=Decimal("350.00"),
        path="whole",
    )

    now = datetime(2026, 9, 21, 15, 45)
    order1 = router.route_signal(sig, sizing, now)
    order2 = router.route_signal(sig, sizing, now)

    # Debe retornar la misma orden sin duplicar en el broker
    assert order1.order_id == order2.order_id
    assert order1.client_order_id == order2.client_order_id
    # En el broker solo debe haber 1 posición abierta de 5 acciones (no 10)
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("5")
