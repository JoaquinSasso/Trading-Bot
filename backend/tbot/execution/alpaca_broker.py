"""Adaptador de broker oficial para Alpaca Markets utilizando alpaca-py."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from tbot.config.settings import settings
from tbot.execution.interfaces import (
    BrokerAccountInfo,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
    OrderRequest,
)

logger = structlog.get_logger(__name__)


class AlpacaBroker(BrokerAdapter):
    """Adaptador de ejecución para Alpaca Markets (Paper y Live)."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        paper: bool = True,
    ) -> None:
        self.api_key = api_key or settings.ALPACA_API_KEY
        self.secret_key = secret_key or settings.ALPACA_SECRET_KEY
        self.paper = paper

        if not self.api_key or not self.secret_key:
            raise ValueError("Credenciales de Alpaca (API Key o Secret Key) no configuradas.")

        self.client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
        )

    def get_account_info(self) -> BrokerAccountInfo:
        """Obtiene el balance, capital y poder de compra de la cuenta."""
        acc = self.client.get_account()
        return BrokerAccountInfo(
            equity=Decimal(str(acc.equity)),
            cash=Decimal(str(acc.cash)),
            buying_power=Decimal(str(acc.buying_power)),
            non_marginable_buying_power=Decimal(str(acc.non_marginable_buying_power)),
            pattern_day_trader=acc.pattern_day_trader,
            trading_blocked=acc.trading_blocked,
            transfers_blocked=acc.transfers_blocked,
        )

    def get_positions(self) -> list[BrokerPosition]:
        """Obtiene todas las posiciones abiertas en la cuenta."""
        positions = self.client.get_all_positions()
        res: list[BrokerPosition] = []
        for p in positions:
            res.append(
                BrokerPosition(
                    symbol=p.symbol,
                    qty=Decimal(str(p.qty)),
                    entry_price=Decimal(str(p.avg_entry_price)),
                    current_price=Decimal(str(p.current_price)),
                    market_value=Decimal(str(p.market_value)),
                    unrealized_pl=Decimal(str(p.unrealized_pl)),
                    unrealized_plpc=float(p.unrealized_plpc),
                    side="long" if p.side.value == "long" else "short",
                )
            )
        return res

    def get_open_orders(self) -> list[BrokerOrder]:
        """Obtiene todas las órdenes abiertas en el broker."""
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = self.client.get_orders(filter=req)
        return [self._map_order(o) for o in orders]

    def submit_order(self, request: OrderRequest) -> BrokerOrder:
        """Emite una orden al broker (simple o bracket nativo)."""
        side = OrderSide.BUY if request.side == "buy" else OrderSide.SELL
        tif = TimeInForce.GTC if request.time_in_force == "gtc" else TimeInForce.DAY

        take_profit = None
        if request.take_profit_price is not None:
            take_profit = TakeProfitRequest(limit_price=float(request.take_profit_price))

        stop_loss = None
        if request.stop_loss_price is not None:
            stop_loss = StopLossRequest(stop_price=float(request.stop_loss_price))

        order_class = OrderClass.SIMPLE
        if request.order_class == "bracket":
            order_class = OrderClass.BRACKET

        qty_float = float(request.qty)

        if request.order_type == "market":
            order_data = MarketOrderRequest(
                symbol=request.symbol,
                qty=qty_float,
                side=side,
                time_in_force=tif,
                order_class=order_class,
                take_profit=take_profit,
                stop_loss=stop_loss,
                client_order_id=request.client_order_id,
            )
        elif request.order_type == "limit":
            if request.limit_price is None:
                raise ValueError("Limit price requerido para órdenes limit.")
            order_data = LimitOrderRequest(
                symbol=request.symbol,
                qty=qty_float,
                side=side,
                time_in_force=tif,
                limit_price=float(request.limit_price),
                order_class=order_class,
                take_profit=take_profit,
                stop_loss=stop_loss,
                client_order_id=request.client_order_id,
            )
        else:
            raise ValueError(f"Tipo de orden no soportado directamente: {request.order_type}")

        try:
            submitted = self.client.submit_order(order_data)
            logger.info(
                "Orden enviada a Alpaca exitosamente",
                symbol=request.symbol,
                order_id=str(submitted.id),
                client_order_id=request.client_order_id,
                qty=str(request.qty),
            )
            return self._map_order(submitted)
        except APIError as e:
            logger.error(
                "Error de API al enviar orden a Alpaca",
                symbol=request.symbol,
                error=str(e),
                code=getattr(e, "code", None),
            )
            raise

    def cancel_order(self, order_id: str) -> bool:
        """Cancela una orden activa en Alpaca."""
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            logger.error("Error al cancelar orden en Alpaca", order_id=order_id, error=str(e))
            return False

    def close_position(self, symbol: str) -> bool:
        """Cierra a mercado una posición abierta en Alpaca."""
        try:
            self.client.close_position(symbol)
            return True
        except Exception as e:
            logger.error("Error al cerrar posición en Alpaca", symbol=symbol, error=str(e))
            return False

    def _map_order(self, o: Any) -> BrokerOrder:
        """Mapea un objeto de orden de alpaca a BrokerOrder normalizado."""
        legs: list[BrokerOrder] = []
        if getattr(o, "legs", None):
            for leg in o.legs:
                legs.append(self._map_order(leg))

        return BrokerOrder(
            order_id=str(o.id),
            client_order_id=o.client_order_id or "",
            symbol=o.symbol,
            qty=Decimal(str(o.qty)),
            filled_qty=Decimal(str(o.filled_qty or "0")),
            side="buy" if o.side.value == "buy" else "sell",
            order_type=o.order_type.value if hasattr(o.order_type, "value") else str(o.order_type),
            status=o.status.value if hasattr(o.status, "value") else str(o.status),
            limit_price=Decimal(str(o.limit_price)) if o.limit_price else None,
            stop_price=Decimal(str(o.stop_price)) if getattr(o, "stop_price", None) else None,
            filled_avg_price=Decimal(str(o.filled_avg_price)) if o.filled_avg_price else None,
            legs=legs if legs else None,
        )
