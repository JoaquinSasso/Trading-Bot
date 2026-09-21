"""Modelos de datos para el subsistema de datos de mercado y calendario.

Define las estructuras inmutables para cotizaciones, ejecuciones,
reloj de mercado y sesiones de negociación (TradingDay).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TradingDay:
    """Representa una sesión de negociación regular en el mercado de EE. UU.

    Todos los timestamps se almacenan en UTC con información de zona horaria (timezone-aware)
    para cumplir con la política de base de datos e invariantes del sistema.
    """

    date: date
    open_time: datetime  # UTC timezone-aware (09:30 ET convertido a UTC)
    close_time: datetime  # UTC timezone-aware (16:00 ET o 13:00 ET en cierres tempranos)
    is_early_close: bool = False

    @property
    def open_time_et(self) -> datetime:
        """Hora de apertura convertida a zona horaria de Nueva York (Eastern Time)."""
        return self.open_time.astimezone(ET_ZONE)

    @property
    def close_time_et(self) -> datetime:
        """Hora de cierre convertida a zona horaria de Nueva York (Eastern Time)."""
        return self.close_time.astimezone(ET_ZONE)

    @property
    def duration_minutes(self) -> float:
        """Duración de la sesión de negociación en minutos."""
        return (self.close_time - self.open_time).total_seconds() / 60.0


@dataclass(frozen=True)
class MarketClock:
    """Estado y proyección temporal del reloj de mercado."""

    timestamp: datetime  # UTC timezone-aware
    is_open: bool
    next_open: datetime  # UTC timezone-aware
    next_close: datetime  # UTC timezone-aware


@dataclass(frozen=True)
class PriceQuote:
    """Cotización en tiempo real del feed IEX (top-of-book)."""

    symbol: str = ""
    bid: Decimal = Decimal("0")
    ask: Decimal = Decimal("0")
    timestamp: datetime | None = None
    bid_size: int = 0
    ask_size: int = 0
    midpoint: Decimal | None = None
    spread_bps: float | None = None
    is_stale: bool = False
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.midpoint is None:
            calc_mid = (self.bid + self.ask) / Decimal("2")
            object.__setattr__(self, "midpoint", calc_mid)
        if self.spread_bps is None:
            mid = (
                self.midpoint if self.midpoint is not None else (self.bid + self.ask) / Decimal("2")
            )
            if mid > Decimal("0"):
                calc_spread = float(((self.ask - self.bid) / mid) * Decimal("10000"))
            else:
                calc_spread = 0.0
            object.__setattr__(self, "spread_bps", calc_spread)


@dataclass(frozen=True)
class TradeQuote:
    """Último trade reportado en IEX."""

    symbol: str = ""
    price: Decimal = Decimal("0")
    size: int = 0
    timestamp: datetime | None = None
    is_stale: bool = False
    reason_code: str | None = None
