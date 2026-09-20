"""Reloj inyectable para el bot de trading.

Ningún módulo del sistema debe llamar a datetime.now() directamente;
todos los componentes reciben una instancia de Clock (Inversión de Control).
Esto garantiza determinismo en backtest, replay y tests unitarios.
"""

from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, timedelta


class Clock(ABC):
    """Interfaz abstracta para el proveedor de tiempo."""

    @abstractmethod
    def now(self) -> datetime:
        """Retorna la fecha y hora actual siempre en UTC con timezone aware."""
        ...

    def today(self) -> date:
        """Retorna la fecha actual en UTC."""
        return self.now().date()


class SystemClock(Clock):
    """Implementación de producción que consulta el reloj del sistema operativo."""

    def now(self) -> datetime:
        # Única invocación autorizada al reloj del sistema en toda la aplicación
        return datetime.now(UTC)


class SimulatedClock(Clock):
    """Implementación simulada para backtesting, replay y pruebas unitarias."""

    def __init__(self, initial_time: datetime | None = None) -> None:
        if initial_time is None:
            self._current_time = datetime(2026, 1, 1, 9, 30, 0, tzinfo=UTC)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=UTC)
            else:
                self._current_time = initial_time.astimezone(UTC)

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        """Establece explícitamente el tiempo simulado."""
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=UTC)
        else:
            self._current_time = new_time.astimezone(UTC)

    def advance(self, delta: timedelta) -> datetime:
        """Avanza el reloj simulado por un delta de tiempo y retorna el nuevo tiempo."""
        self._current_time += delta
        return self._current_time
