"""Adaptador simulado para pruebas locales y simulaciones sin credenciales de Alpaca."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from tbot.backtest.simulated_broker import SimulatedBroker, SimulatedPosition
from tbot.common.clock import Clock, SystemClock
from tbot.execution.interfaces import (
    BrokerAccountInfo,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
    OrderRequest,
)


class SimulatedBrokerAdapter(BrokerAdapter):
    """Adaptador de Broker simulado conforme con BrokerAdapter."""

    def __init__(
        self,
        initial_capital: Decimal = Decimal("2000.00"),
        clock: Clock | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.sim_broker = SimulatedBroker(initial_capital=initial_capital)
        self._open_orders: dict[str, BrokerOrder] = {}
        self._current_prices: dict[str, Decimal] = {}

    def set_current_price(self, symbol: str, price: Decimal) -> None:
        """Actualiza el precio de referencia para valoración de equity."""
        self._current_prices[symbol] = price

    def get_account_info(self) -> BrokerAccountInfo:
        eq = self.sim_broker.get_equity(self._current_prices)
        return BrokerAccountInfo(
            equity=eq,
            cash=self.sim_broker.cash,
            buying_power=self.sim_broker.cash,
            non_marginable_buying_power=self.sim_broker.cash,
            pattern_day_trader=False,
            trading_blocked=False,
            transfers_blocked=False,
        )

    def get_positions(self) -> list[BrokerPosition]:
        res: list[BrokerPosition] = []
        for sym, pos in self.sim_broker.positions.items():
            cur_p = self._current_prices.get(sym, pos.entry_price)
            mkt_val = pos.qty * cur_p
            unrealized = mkt_val - (pos.qty * pos.entry_price)
            unrealized_pct = (
                float(unrealized / (pos.qty * pos.entry_price)) if pos.entry_price else 0.0
            )
            res.append(
                BrokerPosition(
                    symbol=sym,
                    qty=pos.qty,
                    entry_price=pos.entry_price,
                    current_price=cur_p,
                    market_value=mkt_val,
                    unrealized_pl=unrealized,
                    unrealized_plpc=unrealized_pct,
                    side="long",
                )
            )
        return res

    def get_open_orders(self) -> list[BrokerOrder]:
        return list(self._open_orders.values())

    def submit_order(self, request: OrderRequest) -> BrokerOrder:
        order_id = str(uuid.uuid4())
        legs: list[BrokerOrder] = []

        if request.side == "buy":
            # Ejecución simulada inmediata al precio solicitado o de referencia
            fill_p = request.limit_price or self._current_prices.get(
                request.symbol, Decimal("100.00")
            )
            slippage = self.sim_broker._get_slippage_rate(request.symbol)
            effective_fill = fill_p * (Decimal("1.0") + slippage)

            notional = request.qty * effective_fill
            if notional > self.sim_broker.cash:
                raise ValueError("Efectivo insuficiente en broker simulado.")

            self.sim_broker.cash -= notional
            self.sim_broker.positions[request.symbol] = SimulatedPosition(
                symbol=request.symbol,
                qty=request.qty,
                entry_price=effective_fill,
                entry_time=self.clock.now(),
                initial_stop=request.stop_loss_price or (effective_fill * Decimal("0.95")),
                current_stop=request.stop_loss_price or (effective_fill * Decimal("0.95")),
                take_profit=request.take_profit_price,
                strategy_id="simulated",
            )
            self._current_prices[request.symbol] = effective_fill

            # Si es bracket, crear las piernas hijas
            if request.order_class == "bracket":
                if request.take_profit_price:
                    legs.append(
                        BrokerOrder(
                            order_id=str(uuid.uuid4()),
                            client_order_id=f"{request.client_order_id}_tp",
                            symbol=request.symbol,
                            qty=request.qty,
                            filled_qty=Decimal("0"),
                            side="sell",
                            order_type="limit",
                            status="new",
                            limit_price=request.take_profit_price,
                        )
                    )
                if request.stop_loss_price:
                    legs.append(
                        BrokerOrder(
                            order_id=str(uuid.uuid4()),
                            client_order_id=f"{request.client_order_id}_sl",
                            symbol=request.symbol,
                            qty=request.qty,
                            filled_qty=Decimal("0"),
                            side="sell",
                            order_type="stop",
                            status="new",
                            stop_price=request.stop_loss_price,
                        )
                    )

            order = BrokerOrder(
                order_id=order_id,
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                qty=request.qty,
                filled_qty=request.qty,
                side="buy",
                order_type=request.order_type,
                status="filled",
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                filled_avg_price=effective_fill,
                legs=legs if legs else None,
            )
            # Las piernas activas de protección quedan abiertas
            for leg in legs:
                self._open_orders[leg.order_id] = leg

            return order

        # Sell
        if request.order_type in ("stop", "stop_limit"):
            # Orden de stop condicional de protección: queda abierta como resting order
            order = BrokerOrder(
                order_id=order_id,
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                qty=request.qty,
                filled_qty=Decimal("0"),
                side="sell",
                order_type=request.order_type,
                status="new",
                stop_price=request.stop_price,
            )
            self._open_orders[order_id] = order
            return order

        # Orden a mercado o ejecución inmediata de venta
        if request.symbol in self.sim_broker.positions:
            pos = self.sim_broker.positions.pop(request.symbol)
            fill_p = request.limit_price or self._current_prices.get(
                request.symbol, pos.entry_price
            )
            slippage = self.sim_broker._get_slippage_rate(request.symbol)
            effective_fill = fill_p * (Decimal("1.0") - slippage)
            fees = self.sim_broker.calculate_sell_regulatory_fees(request.qty, effective_fill)
            self.sim_broker.cash += (request.qty * effective_fill) - fees

        order = BrokerOrder(
            order_id=order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            qty=request.qty,
            filled_qty=request.qty,
            side="sell",
            order_type=request.order_type,
            status="filled",
            filled_avg_price=self._current_prices.get(request.symbol, Decimal("100.00")),
        )
        return order

    def cancel_order(self, order_id: str) -> bool:
        return self._open_orders.pop(order_id, None) is not None

    def close_position(self, symbol: str) -> bool:
        if symbol in self.sim_broker.positions:
            pos = self.sim_broker.positions.pop(symbol)
            cur_p = self._current_prices.get(symbol, pos.entry_price)
            fees = self.sim_broker.calculate_sell_regulatory_fees(pos.qty, cur_p)
            self.sim_broker.cash += (pos.qty * cur_p) - fees
            # Cancelar órdenes hijas asociadas a este símbolo
            to_del = [oid for oid, o in self._open_orders.items() if o.symbol == symbol]
            for oid in to_del:
                self._open_orders.pop(oid, None)
            return True
        return False
