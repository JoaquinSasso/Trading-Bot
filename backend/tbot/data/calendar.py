"""Servicio de Calendario de Mercado y Reloj con Inyección de Dependencias.

Implementa la abstracción MarketCalendar sobre la API de Alpaca, respetando
estrictamente el Principio No Negociable 6 (Reloj Inyectable):
- CERO llamadas directas a datetime.now() o datetime.utcnow().
- Conversión determinista de horarios de Eastern Time (ET) a UTC timezone-aware.
- Cache en memoria de días hábiles, feriados y cierres tempranos para no repetir llamadas a la API.
- Ventanas dinámicas de apertura (9:30-10:00 ET) y cierre (últimos 10 minutos adaptables).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from tbot.common.clock import Clock
from tbot.common.errors import MarketDataError
from tbot.data.types import ET_ZONE, MarketClock, TradingDay

if TYPE_CHECKING:
    from alpaca.trading.client import TradingClient


class MarketCalendar:
    """Gestor del calendario y reloj de mercado para la bolsa de valores estadounidense."""

    def __init__(
        self,
        clock: Clock,
        trading_client: TradingClient | None = None,
        initial_days: list[TradingDay] | None = None,
        covered_range: tuple[date, date] | None = None,
        cache_lookahead_days: int = 365,
        cache_lookback_days: int = 365,
    ) -> None:
        """Inicializa el calendario de mercado con reloj inyectado.

        Args:
            clock: Instancia de Clock (SystemClock en producción, SimulatedClock en tests).
            trading_client: Cliente opcional de Alpaca TradingClient.
            initial_days: Lista opcional de TradingDay para pre-cargar el cache.
            covered_range: Tupla opcional (start_date, end_date) que define el rango cubierto.
            cache_lookahead_days: Días hacia el futuro a consultar en sincronización automática.
            cache_lookback_days: Días hacia el pasado a consultar en sincronización automática.
        """
        self._clock = clock
        self._trading_client = trading_client
        self._days_cache: dict[date, TradingDay] = {}
        self._covered_dates: set[date] = set()
        self._cache_lookahead_days = cache_lookahead_days
        self._cache_lookback_days = cache_lookback_days

        if initial_days:
            self.load_days(initial_days, covered_range=covered_range)
        elif covered_range:
            self.mark_range_covered(covered_range[0], covered_range[1])

    @property
    def clock(self) -> Clock:
        """Retorna el reloj inyectado asociado al calendario."""
        return self._clock

    def mark_range_covered(self, start: date, end: date) -> None:
        """Marca un intervalo de fechas continuo como cubierto en el cache."""
        cur = start
        while cur <= end:
            self._covered_dates.add(cur)
            cur += timedelta(days=1)

    def load_days(
        self,
        days: list[TradingDay],
        covered_range: tuple[date, date] | None = None,
    ) -> None:
        """Carga una colección de TradingDay directamente en el cache en memoria.

        Marca como cubierto el rango especificado o el intervalo continuo entre la fecha mínima y máxima provista.
        """
        if not days:
            if covered_range:
                self.mark_range_covered(covered_range[0], covered_range[1])
            return

        for day in days:
            self._days_cache[day.date] = day

        if covered_range is not None:
            self.mark_range_covered(covered_range[0], covered_range[1])
        else:
            min_date = min(d.date for d in days)
            max_date = max(d.date for d in days)
            self.mark_range_covered(min_date, max_date)

    def sync_calendar(self, start: date, end: date) -> list[TradingDay]:
        """Sincroniza el calendario contra la API de Alpaca para el rango especificado.

        Convierte datetimes naive de Eastern Time a datetimes conscientes en UTC
        y detecta automáticamente sesiones con cierre temprano.
        """
        if self._trading_client is None:
            raise MarketDataError(
                f"No se puede sincronizar calendario entre {start} y {end}: "
                "trading_client no configurado."
            )

        from alpaca.trading.requests import GetCalendarRequest

        try:
            req = GetCalendarRequest(start=start, end=end)
            alpaca_calendars = self._trading_client.get_calendar(filters=req)
        except Exception as exc:
            raise MarketDataError(
                f"Error al consultar el calendario de Alpaca para el rango [{start}, {end}]: {exc}"
            ) from exc

        if not isinstance(alpaca_calendars, list):
            alpaca_calendars = []

        new_days: list[TradingDay] = []
        for item in alpaca_calendars:
            day_date = item.date
            open_dt = item.open
            close_dt = item.close

            # Localizar naive ET a ZoneInfo America/New_York y convertir a UTC aware
            if open_dt.tzinfo is None:
                open_utc = open_dt.replace(tzinfo=ET_ZONE).astimezone(UTC)
            else:
                open_utc = open_dt.astimezone(UTC)

            if close_dt.tzinfo is None:
                close_utc = close_dt.replace(tzinfo=ET_ZONE).astimezone(UTC)
            else:
                close_utc = close_dt.astimezone(UTC)

            # En horario ET, si la hora de cierre es menor a las 16:00, es cierre temprano
            close_et = close_utc.astimezone(ET_ZONE)
            is_early_close = close_et.hour < 16

            trading_day = TradingDay(
                date=day_date,
                open_time=open_utc,
                close_time=close_utc,
                is_early_close=is_early_close,
            )
            self._days_cache[day_date] = trading_day
            new_days.append(trading_day)

        # Marcar todo el rango consultado como cubierto
        cur = start
        while cur <= end:
            self._covered_dates.add(cur)
            cur += timedelta(days=1)

        return new_days

    def _ensure_date_covered(self, target_date: date) -> None:
        """Garantiza que una fecha esté cubierta en el cache, sincronizando si es posible."""
        if target_date in self._covered_dates:
            return

        if self._trading_client is not None:
            sync_start = target_date - timedelta(days=self._cache_lookback_days)
            sync_end = target_date + timedelta(days=self._cache_lookahead_days)
            self.sync_calendar(sync_start, sync_end)
        else:
            raise MarketDataError(
                f"La fecha {target_date} no se encuentra en el cache de MarketCalendar "
                "y no se configuró un TradingClient para sincronizar automáticamente."
            )

    def is_trading_day(self, target_date: date) -> bool:
        """Determina si la fecha especificada es un día de negociación bursátil (no fin de semana ni feriado)."""
        self._ensure_date_covered(target_date)
        return target_date in self._days_cache

    def get_trading_day(self, target_date: date) -> TradingDay | None:
        """Retorna la sesión de negociación de la fecha, o None si no hubo rueda."""
        self._ensure_date_covered(target_date)
        return self._days_cache.get(target_date)

    def get_calendar(self, start: date, end: date) -> list[TradingDay]:
        """Retorna la lista de todas las sesiones de negociación comprendidas entre start y end (inclusive)."""
        cur = start
        while cur <= end:
            self._ensure_date_covered(cur)
            cur += timedelta(days=1)

        result: list[TradingDay] = []
        cur = start
        while cur <= end:
            day = self._days_cache.get(cur)
            if day is not None:
                result.append(day)
            cur += timedelta(days=1)

        return result

    def is_early_close(self, target_date: date | None = None) -> bool:
        """Indica si el día indicado (o el día actual según el reloj) tiene horario de cierre reducido."""
        if target_date is None:
            # Obtener fecha en horario de Nueva York a partir del reloj inyectado
            target_date = self._clock.now().astimezone(ET_ZONE).date()

        day = self.get_trading_day(target_date)
        return day.is_early_close if day is not None else False

    def get_session_times(self, target_date: date) -> tuple[datetime, datetime] | None:
        """Retorna (open_time_utc, close_time_utc) para una fecha dada, o None si el mercado está cerrado."""
        day = self.get_trading_day(target_date)
        if day is None:
            return None
        return day.open_time, day.close_time

    def is_market_open(self, timestamp: datetime | None = None) -> bool:
        """Verifica si la rueda de operaciones regular está abierta en el instante dado.

        Por defecto evalúa el instante actual provisto por el reloj inyectado (self.clock.now()).
        """
        ts = self._normalize_timestamp(timestamp)
        et_date = ts.astimezone(ET_ZONE).date()
        day = self.get_trading_day(et_date)
        if day is None:
            return False

        return day.open_time <= ts < day.close_time

    def is_open_window(self, timestamp: datetime | None = None) -> bool:
        """Verifica si el instante dado cae en la ventana de apertura (09:30 a 10:00 ET).

        Bloquea entradas estándar de estrategias salvo aquellas que declaran allows_open_window=True.
        """
        ts = self._normalize_timestamp(timestamp)
        et_date = ts.astimezone(ET_ZONE).date()
        day = self.get_trading_day(et_date)
        if day is None:
            return False

        open_window_end = day.open_time + timedelta(minutes=30)
        return day.open_time <= ts < open_window_end

    def is_close_window(self, timestamp: datetime | None = None) -> bool:
        """Verifica si el instante dado cae en la ventana de cierre (últimos 10 minutos de la rueda).

        Se calcula dinámicamente según la hora de cierre del día (15:50 a 16:00 ET en rueda estándar,
        o 12:50 a 13:00 ET en cierres tempranos).
        """
        ts = self._normalize_timestamp(timestamp)
        et_date = ts.astimezone(ET_ZONE).date()
        day = self.get_trading_day(et_date)
        if day is None:
            return False

        close_window_start = day.close_time - timedelta(minutes=10)
        return close_window_start <= ts <= day.close_time

    def is_regular_trading_window(self, timestamp: datetime | None = None) -> bool:
        """Indica si el instante está dentro de la rueda regular, fuera de las ventanas de apertura y cierre."""
        ts = self._normalize_timestamp(timestamp)
        return (
            self.is_market_open(ts)
            and not self.is_open_window(ts)
            and not self.is_close_window(ts)
        )

    def minutes_until_close(self, timestamp: datetime | None = None) -> float | None:
        """Calcula los minutos restantes hasta el cierre de la sesión regular actual.

        Retorna None si el mercado está actualmente cerrado.
        """
        ts = self._normalize_timestamp(timestamp)
        if not self.is_market_open(ts):
            return None

        et_date = ts.astimezone(ET_ZONE).date()
        day = self.get_trading_day(et_date)
        if day is None:
            return None

        return max(0.0, (day.close_time - ts).total_seconds() / 60.0)

    def minutes_since_open(self, timestamp: datetime | None = None) -> float | None:
        """Calcula los minutos transcurridos desde la apertura de la sesión regular actual.

        Retorna None si el instante es previo a la apertura o no es día de rueda.
        """
        ts = self._normalize_timestamp(timestamp)
        et_date = ts.astimezone(ET_ZONE).date()
        day = self.get_trading_day(et_date)
        if day is None or ts < day.open_time:
            return None

        return max(0.0, (ts - day.open_time).total_seconds() / 60.0)

    def get_market_clock(self, timestamp: datetime | None = None) -> MarketClock:
        """Genera una instantánea del reloj de mercado (MarketClock) para el instante dado.

        Determina si el mercado está abierto, el próximo horario de apertura y el próximo cierre.
        """
        ts = self._normalize_timestamp(timestamp)
        et_date = ts.astimezone(ET_ZONE).date()
        day = self.get_trading_day(et_date)

        if day is not None:
            if ts < day.open_time:
                # Pre-mercado en un día hábil
                is_open = False
                next_open = day.open_time
                next_close = day.close_time
            elif day.open_time <= ts < day.close_time:
                # Mercado abierto
                is_open = True
                next_close = day.close_time
                next_trading_day = self.get_next_trading_day(et_date)
                next_open = next_trading_day.open_time
            else:
                # Post-mercado en un día hábil
                is_open = False
                next_trading_day = self.get_next_trading_day(et_date)
                next_open = next_trading_day.open_time
                next_close = next_trading_day.close_time
        else:
            # Fin de semana o feriado
            is_open = False
            next_trading_day = self.get_next_trading_day(et_date)
            next_open = next_trading_day.open_time
            next_close = next_trading_day.close_time

        return MarketClock(
            timestamp=ts,
            is_open=is_open,
            next_open=next_open,
            next_close=next_close,
        )

    def get_next_trading_day(self, from_date: date) -> TradingDay:
        """Retorna el siguiente día hábil bursátil estrictamente posterior a from_date."""
        cur = from_date + timedelta(days=1)
        max_search_days = 30
        for _ in range(max_search_days):
            if self.is_trading_day(cur):
                day = self._days_cache.get(cur)
                if day is not None:
                    return day
            cur += timedelta(days=1)

        raise MarketDataError(
            f"No se encontró un día hábil en los {max_search_days} días posteriores a {from_date}."
        )

    def get_previous_trading_day(self, from_date: date) -> TradingDay:
        """Retorna el día hábil bursátil inmediatamente anterior a from_date."""
        cur = from_date - timedelta(days=1)
        max_search_days = 30
        for _ in range(max_search_days):
            if self.is_trading_day(cur):
                day = self._days_cache.get(cur)
                if day is not None:
                    return day
            cur -= timedelta(days=1)

        raise MarketDataError(
            f"No se encontró un día hábil en los {max_search_days} días anteriores a {from_date}."
        )

    def add_trading_days(self, from_date: date, n: int) -> date:
        """Añade (o sustrae si n < 0) una cantidad de días hábiles bursátiles a partir de una fecha."""
        if n == 0:
            return from_date

        step = 1 if n > 0 else -1
        remaining = abs(n)
        cur = from_date
        while remaining > 0:
            cur += timedelta(days=step)
            if self.is_trading_day(cur):
                remaining -= 1

        return cur

    def trading_days_between(self, start: date, end: date) -> int:
        """Cuenta la cantidad de días hábiles bursátiles existentes en el intervalo inclusivo [start, end]."""
        if start > end:
            return 0

        cur = start
        count = 0
        while cur <= end:
            if self.is_trading_day(cur):
                count += 1
            cur += timedelta(days=1)

        return count

    def _normalize_timestamp(self, ts: datetime | None) -> datetime:
        """Normaliza un timestamp asegurando que sea consciente de zona horaria (UTC)."""
        if ts is None:
            return self._clock.now()

        if ts.tzinfo is None:
            raise ValueError("El timestamp debe contener información de zona horaria (tzinfo).")

        return ts.astimezone(UTC)

    @classmethod
    def generate_standard_trading_days(
        cls,
        start: date,
        end: date,
        holidays: set[date] | None = None,
        early_closes: dict[date, tuple[int, int]] | None = None,
    ) -> list[TradingDay]:
        """Utilidad de fábrica para generar sesiones bursátiles estándar de EE. UU.

        Útil para pruebas unitarias, fixtures sintéticos y backtesting reproducible.
        """
        holidays_set = holidays or set()
        early_closes_map = early_closes or {}
        days: list[TradingDay] = []

        cur = start
        while cur <= end:
            # Días de semana (Lunes=0 a Viernes=4) excluyendo feriados
            if cur.weekday() < 5 and cur not in holidays_set:
                is_early = cur in early_closes_map
                close_hour, close_min = early_closes_map.get(cur, (16, 0))

                open_et = datetime(cur.year, cur.month, cur.day, 9, 30, tzinfo=ET_ZONE)
                close_et = datetime(cur.year, cur.month, cur.day, close_hour, close_min, tzinfo=ET_ZONE)

                days.append(
                    TradingDay(
                        date=cur,
                        open_time=open_et.astimezone(UTC),
                        close_time=close_et.astimezone(UTC),
                        is_early_close=is_early,
                    )
                )
            cur += timedelta(days=1)

        return days
