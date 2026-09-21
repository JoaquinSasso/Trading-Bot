"""Guardián de posiciones (PositionGuardian) y motor de conciliación activa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import structlog

from tbot.common.clock import Clock, SystemClock
from tbot.execution.interfaces import BrokerAdapter, BrokerPosition, OrderRequest
from tbot.risk.ownership import OwnershipLedger

logger = structlog.get_logger(__name__)


@dataclass
class TrackedPosition:
    """Metadatos de seguimiento de una posición gestionada por el bot."""

    symbol: str
    execution_symbol: str
    owner: str  # "bot" o "manual"
    strategy_id: str
    entry_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None
    opened_at: datetime
    path: str  # "whole" o "fractional_fallback"
    exit_at_close: bool = False
    unmanaged: bool = False
    last_unprotected_seen_at: datetime | None = None


class PositionGuardian:
    """Vigila la integridad de posiciones abiertas, reconcilia con el broker y fuerza stops en < 60s."""

    def __init__(
        self,
        broker: BrokerAdapter,
        ownership_ledger: OwnershipLedger | None = None,
        max_unprotected_seconds: float = 60.0,
        clock: Clock | None = None,
    ) -> None:
        self.broker = broker
        self.ownership = ownership_ledger or OwnershipLedger()
        self.max_unprotected_seconds = max_unprotected_seconds
        self.clock = clock or SystemClock()

        self.tracked_positions: dict[str, TrackedPosition] = {}
        self.kill_switch_active: bool = False

    def register_bot_position(
        self,
        symbol: str,
        execution_symbol: str,
        strategy_id: str,
        entry_price: Decimal,
        stop_price: Decimal,
        take_profit_price: Decimal | None,
        opened_at: datetime,
        path: str,
        exit_at_close: bool = False,
    ) -> TrackedPosition:
        """Registra una nueva posición abierta por el bot para su custodia."""
        self.ownership.assign(symbol, "bot")
        pos = TrackedPosition(
            symbol=symbol,
            execution_symbol=execution_symbol,
            owner="bot",
            strategy_id=strategy_id,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            opened_at=opened_at,
            path=path,
            exit_at_close=exit_at_close,
        )
        self.tracked_positions[execution_symbol] = pos
        return pos

    def reconcile(self, now: datetime) -> list[str]:
        """Conciliación de 3 capas: sincroniza posiciones y órdenes con la realidad del broker."""
        actions_taken: list[str] = []
        broker_positions = {p.symbol: p for p in self.broker.get_positions()}
        open_orders = self.broker.get_open_orders()

        # 1. Detectar posiciones manuales o externas en el broker no registradas
        for sym, b_pos in broker_positions.items():
            if sym not in self.tracked_positions:
                # Posición creada fuera del bot (ej: interfaz web de Alpaca)
                self.ownership.assign(sym, "manual")
                self.tracked_positions[sym] = TrackedPosition(
                    symbol=sym,
                    execution_symbol=sym,
                    owner="manual",
                    strategy_id="manual",
                    entry_price=b_pos.entry_price,
                    stop_price=Decimal("0.0"),
                    take_profit_price=None,
                    opened_at=now,
                    path="whole",
                    unmanaged=True,
                )
                action = (
                    f"Posición externa detectada en {sym}: asignada a 'manual' y marcada unmanaged."
                )
                logger.info(action)
                actions_taken.append(action)

        # 2. Detectar posiciones cerradas en el broker
        closed_symbols = [s for s in self.tracked_positions if s not in broker_positions]
        for sym in closed_symbols:
            tracked = self.tracked_positions.pop(sym)
            self.ownership.release(tracked.symbol)
            action = f"Posición en {sym} cerrada en el broker. Titularidad liberada."
            logger.info(action)
            actions_taken.append(action)

        # 3. Regla crítica: Ninguna posición del bot sin stop loss más de 60 segundos
        active_stop_orders: set[str] = set()
        for o in open_orders:
            # Chequear órdenes simples tipo stop o piernas de brackets
            if o.order_type in ("stop", "stop_limit") and o.side == "sell":
                active_stop_orders.add(o.symbol)
            if o.legs:
                for leg in o.legs:
                    if leg.order_type in ("stop", "stop_limit") and leg.side == "sell":
                        active_stop_orders.add(leg.symbol)

        for sym, pos in list(self.tracked_positions.items()):
            if pos.owner != "bot" or pos.unmanaged:
                continue

            if sym not in active_stop_orders:
                # Posición sin orden de stop activa en el broker
                if pos.last_unprotected_seen_at is None:
                    pos.last_unprotected_seen_at = now
                    logger.warning(
                        "Posición sin stop detectada",
                        symbol=sym,
                        seen_at=now.isoformat(),
                    )
                else:
                    elapsed = (now - pos.last_unprotected_seen_at).total_seconds()
                    if elapsed >= self.max_unprotected_seconds:
                        # Superó el tiempo límite: Emitir stop de emergencia inmediato
                        logger.error(
                            "Posición sin stop superó el límite de seguridad (60s). Emitiendo stop de emergencia.",
                            symbol=sym,
                            elapsed_seconds=elapsed,
                        )
                        self._submit_emergency_stop(pos, broker_positions.get(sym))
                        pos.last_unprotected_seen_at = None
                        actions_taken.append(f"Stop de emergencia emitido para {sym}")
            else:
                # La posición tiene su stop correctamente colocado
                pos.last_unprotected_seen_at = None

        return actions_taken

    def check_time_exits(self, now: datetime) -> list[str]:
        """Evalúa salidas por tiempo (ej. 15:58 ET para intradía o límite de días para swing)."""
        actions_taken: list[str] = []
        is_near_market_close = (now.hour == 15 and now.minute >= 58) or now.hour >= 16

        for sym, pos in list(self.tracked_positions.items()):
            if pos.owner != "bot" or pos.unmanaged:
                continue

            if pos.exit_at_close and is_near_market_close:
                logger.info("Cierre de posición por fin de sesión intradiaria", symbol=sym)
                self.broker.close_position(sym)
                actions_taken.append(f"Cierre intradía ejecutado en {sym}")

        return actions_taken

    def resubmit_daily_stops(self, now: datetime) -> list[str]:
        """Rutina de las 9:20 ET: reenvía órdenes de stop diarias para posiciones fraccionales."""
        actions_taken: list[str] = []
        broker_positions = {p.symbol: p for p in self.broker.get_positions()}

        for sym, pos in self.tracked_positions.items():
            if pos.owner == "bot" and pos.path == "fractional_fallback" and sym in broker_positions:
                b_pos = broker_positions[sym]
                logger.info("Reenviando stop diario para posición fraccional (9:20 ET)", symbol=sym)
                req = OrderRequest(
                    symbol=sym,
                    qty=b_pos.qty,
                    side="sell",
                    order_type="stop",
                    client_order_id=f"stop_daily_{sym}_{int(now.timestamp())}",
                    time_in_force="day",
                    stop_price=pos.stop_price,
                )
                self.broker.submit_order(req)
                actions_taken.append(f"Stop diario reenviado para {sym} a ${pos.stop_price}")

        return actions_taken

    def activate_kill_switch(self, flatten: bool = False) -> list[str]:
        """Activa el Kill Switch: bloquea entradas y opcionalmente liquida posiciones del bot."""
        self.kill_switch_active = True
        actions: list[str] = ["Kill switch activado: entradas bloqueadas."]
        logger.warning("KILL SWITCH ACTIVADO")

        if flatten:
            for sym, pos in list(self.tracked_positions.items()):
                if pos.owner == "bot":
                    logger.warning("Liquidando posición por Kill Switch", symbol=sym)
                    self.broker.close_position(sym)
                    actions.append(f"Posición {sym} liquidada.")

        return actions

    def _submit_emergency_stop(self, pos: TrackedPosition, b_pos: BrokerPosition | None) -> None:
        """Emite una orden de stop de emergencia al broker."""
        if not b_pos:
            return

        stop_p = pos.stop_price
        if stop_p <= Decimal("0"):
            stop_p = (b_pos.current_price * Decimal("0.95")).quantize(Decimal("0.01"))

        req = OrderRequest(
            symbol=pos.execution_symbol,
            qty=b_pos.qty,
            side="sell",
            order_type="stop",
            client_order_id=f"emerg_stop_{pos.execution_symbol}_{int(self.clock.now().timestamp())}",
            time_in_force="gtc" if b_pos.qty % 1 == 0 else "day",
            stop_price=stop_p,
        )
        try:
            self.broker.submit_order(req)
        except Exception as e:
            logger.critical(
                "Fallo crítico al emitir stop de emergencia",
                symbol=pos.execution_symbol,
                error=str(e),
            )
