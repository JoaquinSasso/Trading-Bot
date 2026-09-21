"""Tests unitarios para PositionGuardian y conciliación activa."""

from datetime import datetime, timedelta
from decimal import Decimal

from tbot.execution.interfaces import OrderRequest
from tbot.execution.simulated_broker_adapter import SimulatedBrokerAdapter
from tbot.guardian.guardian import PositionGuardian
from tbot.risk.ownership import OwnershipLedger


def test_position_guardian_detects_external_manual_position() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    ledger = OwnershipLedger()
    guardian = PositionGuardian(broker=broker, ownership_ledger=ledger)

    # El usuario compra manualmente AAPL desde la web de Alpaca (simulado)
    broker.set_current_price("AAPL", Decimal("150.00"))
    broker.submit_order(
        OrderRequest(
            symbol="AAPL",
            qty=Decimal("10"),
            side="buy",
            order_type="market",
            client_order_id="manual_order_1",
        )
    )

    now = datetime(2026, 9, 21, 10, 0)
    actions = guardian.reconcile(now)

    assert any("AAPL" in a and "manual" in a for a in actions)
    assert ledger.get_owner("AAPL") == "manual"
    assert guardian.tracked_positions["AAPL"].unmanaged is True


def test_position_guardian_emergency_stop_when_unprotected_exceeds_60s() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    ledger = OwnershipLedger()
    guardian = PositionGuardian(
        broker=broker, ownership_ledger=ledger, max_unprotected_seconds=60.0
    )

    # El bot compra SPYM con una orden simple (ej. fallo o fraccional sin stop inicial)
    broker.set_current_price("SPYM", Decimal("70.00"))
    broker.submit_order(
        OrderRequest(
            symbol="SPYM",
            qty=Decimal("5"),
            side="buy",
            order_type="market",
            client_order_id="bot_buy_1",
            order_class="simple",
        )
    )

    # Registrar en el guardian
    t0 = datetime(2026, 9, 21, 15, 30, 0)
    guardian.register_bot_position(
        symbol="SPY",
        execution_symbol="SPYM",
        strategy_id="s3_trend_pullback",
        entry_price=Decimal("70.00"),
        stop_price=Decimal("68.50"),
        take_profit_price=Decimal("73.00"),
        opened_at=t0,
        path="whole",
    )

    # Primer check a los 10 segundos -> se detecta sin stop pero elapsed < 60s
    t1 = t0 + timedelta(seconds=10)
    actions1 = guardian.reconcile(t1)
    assert not any("Stop de emergencia" in a for a in actions1)

    # Segundo check a los 65 segundos después de la primera detección -> elapsed >= 60s -> debe emitir stop de emergencia
    t2 = t1 + timedelta(seconds=65)
    actions2 = guardian.reconcile(t2)
    assert any("Stop de emergencia emitido para SPYM" in a for a in actions2)

    # Verificar que el broker ahora tiene la orden stop abierta
    open_orders = broker.get_open_orders()
    assert any(o.symbol == "SPYM" and o.order_type == "stop" for o in open_orders)


def test_position_guardian_time_exit_at_close() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    guardian = PositionGuardian(broker=broker)

    broker.set_current_price("SPY", Decimal("590.00"))
    broker.submit_order(
        OrderRequest(
            symbol="SPY",
            qty=Decimal("1"),
            side="buy",
            order_type="market",
            client_order_id="s1_buy_1",
        )
    )

    guardian.register_bot_position(
        symbol="SPY",
        execution_symbol="SPY",
        strategy_id="s1_intraday_momentum",
        entry_price=Decimal("590.00"),
        stop_price=Decimal("585.00"),
        take_profit_price=None,
        opened_at=datetime(2026, 9, 21, 15, 30),
        path="whole",
        exit_at_close=True,
    )

    # A las 15:58 ET debe cerrar la posición intradiaria
    t_close = datetime(2026, 9, 21, 15, 58, 30)
    actions = guardian.check_time_exits(t_close)
    assert any("Cierre intradía ejecutado en SPY" in a for a in actions)
    assert len(broker.get_positions()) == 0


def test_position_guardian_kill_switch_flatten() -> None:
    broker = SimulatedBrokerAdapter(initial_capital=Decimal("2000.00"))
    guardian = PositionGuardian(broker=broker)

    broker.set_current_price("SPYM", Decimal("70.00"))
    broker.submit_order(
        OrderRequest(
            symbol="SPYM",
            qty=Decimal("5"),
            side="buy",
            order_type="market",
            client_order_id="bot_buy_1",
        )
    )

    guardian.register_bot_position(
        symbol="SPY",
        execution_symbol="SPYM",
        strategy_id="s3_trend_pullback",
        entry_price=Decimal("70.00"),
        stop_price=Decimal("68.50"),
        take_profit_price=None,
        opened_at=datetime(2026, 9, 21, 10, 0),
        path="whole",
    )

    # Activar Kill Switch con flatten
    actions = guardian.activate_kill_switch(flatten=True)
    assert guardian.kill_switch_active is True
    assert any("liquidada" in a for a in actions)
    assert len(broker.get_positions()) == 0
