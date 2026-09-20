"""Contratos y modelos normalizados para la interacción con brokers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class BrokerAccountInfo:
    """Información del balance y estado de la cuenta en el broker."""

    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    non_marginable_buying_power: Decimal
    pattern_day_trader: bool
    trading_blocked: bool
    transfers_blocked: bool


@dataclass(frozen=True)
class BrokerPosition:
    """Posición abierta reportada por el broker."""

    symbol: str
    qty: Decimal
    entry_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    unrealized_plpc: float
    side: Literal["long", "short"] = "long"


@dataclass(frozen=True)
class BrokerOrder:
    """Orden emitida reportada por el broker."""

    order_id: str
    client_order_id: str
    symbol: str
    qty: Decimal
    filled_qty: Decimal
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    status: str  # "new", "filled", "partially_filled", "canceled", "expired", etc.
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    filled_avg_price: Decimal | None = None
    legs: list[BrokerOrder] | None = None  # Para órdenes hijas de brackets (TP y SL)


@dataclass(frozen=True)
class OrderRequest:
    """Solicitud normalizada de emisión de orden."""

    symbol: str
    qty: Decimal
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    client_order_id: str
    time_in_force: Literal["day", "gtc"] = "gtc"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    stop_loss_price: Decimal | None = None
    order_class: Literal["simple", "bracket", "oco", "oto"] = "simple"


@runtime_checkable
class BrokerAdapter(Protocol):
    """Protocolo unificado para el broker de ejecución (Alpaca o Simulado)."""

    def get_account_info(self) -> BrokerAccountInfo: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_open_orders(self) -> list[BrokerOrder]: ...

    def submit_order(self, request: OrderRequest) -> BrokerOrder: ...

    def cancel_order(self, order_id: str) -> bool: ...

    def close_position(self, symbol: str) -> bool: ...
