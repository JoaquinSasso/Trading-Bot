"""Enrutador de órdenes (OrderRouter) con garantía de idempotencia y selección de camino whole vs fractional."""

from __future__ import annotations

from datetime import datetime

import structlog

from tbot.execution.interfaces import BrokerAdapter, BrokerOrder, OrderRequest
from tbot.risk.models import SizingResult
from tbot.strategies.interfaces import Signal

logger = structlog.get_logger(__name__)


class OrderRouter:
    """Enrutador de órdenes determinista e idempotente hacia el broker."""

    def __init__(self, broker: BrokerAdapter) -> None:
        self.broker = broker
        self._submitted_orders: dict[str, BrokerOrder] = {}

    def generate_client_order_id(self, signal_id: str, timestamp: datetime) -> str:
        """Genera un client_order_id determinístico para garantizar idempotencia."""
        return f"tbot_{signal_id}_{int(timestamp.timestamp())}"

    def route_signal(
        self,
        signal: Signal,
        sizing: SizingResult,
        now: datetime,
    ) -> BrokerOrder:
        """Enruta la señal dimensionada al broker según el camino (whole vs fractional_fallback)."""
        client_order_id = self.generate_client_order_id(signal.signal_id, now)

        # Chequeo de idempotencia: nunca duplicar una orden para el mismo client_order_id
        if client_order_id in self._submitted_orders:
            logger.warning(
                "Orden duplicada interceptada por idempotencia",
                client_order_id=client_order_id,
                symbol=sizing.execution_symbol,
            )
            return self._submitted_orders[client_order_id]

        if sizing.path == "whole":
            # Camino de acciones enteras: Bracket nativo GTC en el broker
            req = OrderRequest(
                symbol=sizing.execution_symbol,
                qty=sizing.shares,
                side="buy",
                order_type="market" if signal.entry_type == "market" else "limit",
                client_order_id=client_order_id,
                time_in_force="gtc",
                limit_price=sizing.entry_price if signal.entry_type == "limit" else None,
                take_profit_price=sizing.take_profit_price,
                stop_loss_price=sizing.stop_price,
                order_class="bracket",
            )
        else:
            # Camino fraccional: Alpaca solo permite Day Market/Limit simple para fraccionarios
            req = OrderRequest(
                symbol=sizing.execution_symbol,
                qty=sizing.shares,
                side="buy",
                order_type="market",
                client_order_id=client_order_id,
                time_in_force="day",
                order_class="simple",
            )

        order = self.broker.submit_order(req)
        self._submitted_orders[client_order_id] = order
        return order
