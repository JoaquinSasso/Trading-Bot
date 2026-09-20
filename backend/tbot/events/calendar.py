"""Calendario unificado de eventos corporativos (Finnhub) y macroeconómicos.

Principios respetados:
- Principio 2 (Falla cerrada): Si la consulta a Finnhub falla y la cache tiene más de 3 días
  (72h), el símbolo se bloquea con EARNINGS_DATA_UNAVAILABLE.
- Principio 6 (Reloj inyectable): Ninguna llamada a datetime.now() directo; todo usa Clock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tbot.common.clock import Clock
from tbot.config.settings import settings
from tbot.db.models import MarketEvent

if TYPE_CHECKING:
    from tbot.data.calendar import MarketCalendar

ET_TIMEZONE = ZoneInfo("America/New_York")


def parse_hour_code(raw_hour_code: Any) -> str:
    """Normaliza el código de hora retornado por Finnhub en /calendar/earnings."""
    if raw_hour_code is None:
        return "unspecified"
    code_str = str(raw_hour_code).strip().lower()
    if not code_str:
        return "unspecified"
    valid_codes = {
        "bmo": "before_market_open",
        "amc": "after_market_close",
        "dmh": "during_market_hours",
    }
    return valid_codes.get(code_str, "unknown")


@dataclass(frozen=True)
class EarningsRelease:
    symbol: str
    date: date
    hour_raw: str
    hour_category: str
    eps_estimate: float | None = None
    eps_actual: float | None = None
    revenue_estimate: float | None = None
    revenue_actual: float | None = None
    quarter: int | None = None
    year: int | None = None


@dataclass(frozen=True)
class MacroEvent:
    name: str
    type: str
    timestamp: datetime  # UTC aware
    impact: str


class AsyncRateLimiter:
    """Token bucket rate limiter para proteger el cupo de Finnhub (60 req/min)."""

    def __init__(self, rate: int = 60, per_seconds: float = 60.0) -> None:
        self.rate = rate
        self.per_seconds = per_seconds
        self.tokens = float(rate)
        self.last_update: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self.last_update == 0.0:
                self.last_update = now
            elapsed = now - self.last_update
            self.tokens = min(
                float(self.rate), self.tokens + elapsed * (self.rate / self.per_seconds)
            )
            self.last_update = now

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) * (self.per_seconds / self.rate)
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_update = loop.time()
            else:
                self.tokens -= 1.0


class FinnhubEarningsClient:
    """Cliente asíncrono para Finnhub Earnings Calendar con fail-closed y rate limiter."""

    def __init__(
        self,
        clock: Clock,
        api_key: str | None = None,
        base_url: str = "https://finnhub.io/api/v1",
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        http_client: httpx.AsyncClient | None = None,
        market_calendar: MarketCalendar | None = None,
    ) -> None:
        self.clock = clock
        self.market_calendar = market_calendar
        self.api_key = api_key if api_key is not None else settings.FINNHUB_API_KEY
        self.base_url = base_url.rstrip("/")
        self.session_factory = session_factory
        self.http_client = http_client
        self.rate_limiter = AsyncRateLimiter(rate=60, per_seconds=60.0)
        # Cache en memoria: symbol -> (earnings_date, cached_at_utc)
        self.cache: dict[str, tuple[date, datetime]] = {}
        self.network_error: bool = False  # Switch para simulación de fallos

    def seed_earnings(
        self, symbol: str, earnings_date: date, cached_at: datetime | None = None
    ) -> None:
        """Siembra la cache directamente (utilizado en tests)."""
        ts = cached_at or self.clock.now()
        self.cache[symbol] = (earnings_date, ts)

    async def fetch_earnings(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        symbol: str | None = None,
    ) -> list[EarningsRelease]:
        """Consulta Finnhub /calendar/earnings y actualiza la cache."""
        if self.network_error:
            raise httpx.ConnectError("Simulated network outage to Finnhub")

        await self.rate_limiter.acquire()
        now = self.clock.now()
        start = start_date or (now.date() - timedelta(days=7))
        end = end_date or (now.date() + timedelta(days=90))

        params: dict[str, Any] = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "token": self.api_key,
        }
        if symbol:
            params["symbol"] = symbol

        client = self.http_client or httpx.AsyncClient(timeout=10.0)
        close_client = self.http_client is None

        try:
            url = f"{self.base_url}/calendar/earnings"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if close_client:
                await client.aclose()

        releases: list[EarningsRelease] = []
        raw_items = data.get("earningsCalendar", [])
        for item in raw_items:
            sym = item.get("symbol")
            raw_d = item.get("date")
            if not sym or not raw_d:
                continue
            edate = date.fromisoformat(raw_d)
            raw_hour = item.get("hour", "")
            hour_cat = parse_hour_code(raw_hour)

            release = EarningsRelease(
                symbol=sym,
                date=edate,
                hour_raw=raw_hour,
                hour_category=hour_cat,
                eps_estimate=item.get("epsEstimate"),
                eps_actual=item.get("epsActual"),
                revenue_estimate=item.get("revenueEstimate"),
                revenue_actual=item.get("revenueActual"),
                quarter=item.get("quarter"),
                year=item.get("year"),
            )
            releases.append(release)
            # Actualizar cache en memoria
            self.cache[sym] = (edate, now)

            # Persistencia opcional en base de datos
            if self.session_factory:
                await self._persist_event(sym, edate, raw_hour, now)

        return releases

    async def _persist_event(self, symbol: str, edate: date, raw_hour: str, now: datetime) -> None:
        """Persiste el evento en la tabla events."""
        hour_code = (raw_hour or "").lower()
        if hour_code == "bmo":
            hour_int = 7
        elif hour_code == "amc":
            hour_int = 16
        else:
            hour_int = 9

        event_et = datetime(edate.year, edate.month, edate.day, hour_int, 30, 0, tzinfo=ET_TIMEZONE)

        async with self.session_factory() as session:
            ev = MarketEvent(
                type="earnings",
                symbol=symbol,
                ts_et=event_et,
                source="finnhub",
                fetched_at=now,
            )
            session.add(ev)
            await session.commit()

    def get_earnings_date_sync(
        self, symbol: str, current_time: datetime | None = None
    ) -> tuple[date | None, bool, str]:
        """Obtiene la fecha de balance con garantía de falla cerrada (3 días de cache) de forma síncrona."""
        now = current_time if current_time is not None else self.clock.now()

        if symbol in self.cache:
            earnings_date, cached_at = self.cache[symbol]
            if (now - cached_at) > timedelta(days=3):
                return None, True, "EARNINGS_DATA_UNAVAILABLE"
            return earnings_date, False, "CACHE_VALID"

        if self.network_error:
            return None, True, "EARNINGS_DATA_UNAVAILABLE"

        return None, False, "NO_UPCOMING_EARNINGS"

    async def get_earnings_date(
        self, symbol: str, current_time: datetime | None = None
    ) -> tuple[date | None, bool, str]:
        """Obtiene la fecha de balance con garantía de falla cerrada (3 días de cache).

        Retorna: (earnings_date, is_blocked, reason_code)
        """
        return self.get_earnings_date_sync(symbol, current_time=current_time)

    def is_earnings_approaching(
        self,
        earnings_date: date,
        block_days: int = 2,
        current_date: date | None = None,
    ) -> bool:
        """Verifica si la fecha de balance está dentro de la ventana de bloqueo de N días hábiles."""
        base_date = current_date if current_date is not None else self.clock.today()

        if earnings_date < base_date:
            return False
        if earnings_date == base_date:
            return block_days >= 0

        if self.market_calendar is not None:
            trading_days = self.market_calendar.trading_days_between(base_date, earnings_date) - 1
            return 0 <= trading_days <= block_days

        # Market calendar is None fallback:
        # If both base_date and earnings_date are weekdays (Mon-Fri), use business days calculation
        if base_date.weekday() < 5 and earnings_date.weekday() < 5:
            biz_days = int(np.busday_count(base_date, earnings_date))
            return 0 <= biz_days <= block_days

        # On weekend or across weekend gap, check calendar day difference
        day_diff = (earnings_date - base_date).days
        return 0 <= day_diff <= block_days


class MacroFilter:
    """Filtro y monitor del calendario de eventos macroeconómicos desde YAML."""

    def __init__(
        self,
        clock: Clock,
        yaml_path: Path | None = None,
        macro_block_minutes: int = 30,
    ) -> None:
        self.clock = clock
        self.macro_block_minutes = macro_block_minutes
        self.yaml_path = yaml_path or (settings.CONFIG_DIR / "macro_events_2026.yaml")
        self.events: list[dict[str, Any]] = []
        if self.yaml_path.exists():
            self.load_yaml(self.yaml_path)

    def load_yaml(self, path: Path) -> None:
        """Carga y procesa eventos macro desde un archivo YAML."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        raw_events = data.get("events", [])
        parsed_events: list[dict[str, Any]] = []

        for ev in raw_events:
            dt_raw = ev.get("datetime_et")
            if not dt_raw:
                continue

            dt_naive = datetime.fromisoformat(dt_raw)
            dt_et = dt_naive.replace(tzinfo=ET_TIMEZONE)
            dt_utc = dt_et.astimezone(UTC)

            ev_type = ev.get("type", "macro")
            name = ev.get("name", "")
            # Normalizar tipo para FOMC si el nombre lo indica
            if "FOMC" in name.upper():
                ev_type = "FOMC"

            parsed_events.append(
                {
                    "name": name,
                    "type": ev_type,
                    "timestamp": dt_utc,
                    "datetime_et": dt_et,
                    "impact": ev.get("impact", "high"),
                }
            )

        self.events = parsed_events

    def load_events(self, events: list[dict[str, Any]]) -> None:
        """Carga eventos directamente (compatible con el arnés de tests)."""
        normalized_events: list[dict[str, Any]] = []
        for ev in events:
            ev_copy = dict(ev)
            ts = ev_copy.get("timestamp")
            if ts and ts.tzinfo is None:
                ev_copy["timestamp"] = ts.replace(tzinfo=UTC)
            normalized_events.append(ev_copy)
        self.events = normalized_events

    def is_macro_window_active(
        self, current_time: datetime, strategy_type: str = "intraday"
    ) -> tuple[bool, str]:
        """Evalúa si el horario actual está en ventana de blackout macro.

        - Bloqueo de 30 min antes y después para todo el universo.
        - Días de FOMC: bloqueo durante todo el día para estrategias swing.
        """
        curr_utc = (
            current_time if current_time.tzinfo is not None else current_time.replace(tzinfo=UTC)
        )
        curr_et_date = curr_utc.astimezone(ET_TIMEZONE).date()

        for ev in self.events:
            ev_time = ev["timestamp"]
            impact = ev.get("impact", "high")
            ev_type = ev.get("type", "macro")
            name = ev.get("name", "")

            # Bloqueo total del día en FOMC para swing
            is_fomc = ev_type == "FOMC" or "FOMC" in name.upper()
            if is_fomc and strategy_type == "swing":
                ev_et_date = ev_time.astimezone(ET_TIMEZONE).date()
                if curr_et_date == ev_et_date:
                    return True, "FOMC_ALL_DAY_SWING_BLOCK"

            # Ventana de 30 minutos (simétrica)
            window_start = ev_time - timedelta(minutes=self.macro_block_minutes)
            window_end = ev_time + timedelta(minutes=self.macro_block_minutes)
            if window_start <= curr_utc <= window_end:
                return True, f"MACRO_WINDOW_{impact.upper()}"

        return False, "OK"

    def check_calendar_expiry(
        self, current_time: datetime, horizon_days: int = 30
    ) -> tuple[bool, int]:
        """Monitorea si el horizonte de eventos futuros es inferior a 30 días."""
        curr_utc = (
            current_time if current_time.tzinfo is not None else current_time.replace(tzinfo=UTC)
        )
        future_events = [ev for ev in self.events if ev["timestamp"] > curr_utc]
        if not future_events:
            return True, 0

        max_future_date = max(ev["timestamp"] for ev in future_events)
        remaining_days = (max_future_date.date() - curr_utc.date()).days
        needs_alert = remaining_days < horizon_days
        return needs_alert, remaining_days


class EventCalendar:
    """Fachada unificada para el calendario de balances corporativos y eventos macro."""

    def __init__(
        self,
        clock: Clock,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        api_key: str | None = None,
        macro_yaml_path: Path | None = None,
        earnings_block_days: int = 2,
        macro_block_minutes: int = 30,
        http_client: httpx.AsyncClient | None = None,
        market_calendar: MarketCalendar | None = None,
    ) -> None:
        self.clock = clock
        self.earnings_block_days = earnings_block_days
        self.macro_block_minutes = macro_block_minutes
        self.market_calendar = market_calendar
        self.earnings_client = FinnhubEarningsClient(
            clock=clock,
            api_key=api_key,
            session_factory=session_factory,
            http_client=http_client,
            market_calendar=market_calendar,
        )
        self.macro_filter = MacroFilter(
            clock=clock,
            yaml_path=macro_yaml_path,
            macro_block_minutes=macro_block_minutes,
        )
        self.failed_symbols: set[str] = set()

    # Delegación y compatibilidad
    @property
    def cache(self) -> dict[str, tuple[date, datetime]]:
        return self.earnings_client.cache

    @property
    def network_error(self) -> bool:
        return self.earnings_client.network_error

    @network_error.setter
    def network_error(self, value: bool) -> None:
        self.earnings_client.network_error = value

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.macro_filter.events

    @events.setter
    def events(self, value: list[dict[str, Any]]) -> None:
        self.macro_filter.load_events(value)

    def seed_earnings(
        self, symbol: str, earnings_date: date, cached_at: datetime | None = None
    ) -> None:
        self.earnings_client.seed_earnings(symbol, earnings_date, cached_at)

    async def get_earnings_date(
        self, symbol: str, current_time: datetime | None = None
    ) -> tuple[date | None, bool, str]:
        return await self.earnings_client.get_earnings_date(symbol, current_time=current_time)

    def is_earnings_approaching(
        self,
        earnings_date: date,
        block_days: int | None = None,
        current_date: date | None = None,
    ) -> bool:
        days = block_days if block_days is not None else self.earnings_block_days
        return self.earnings_client.is_earnings_approaching(
            earnings_date, block_days=days, current_date=current_date
        )

    def load_events(self, events: list[dict[str, Any]]) -> None:
        self.macro_filter.load_events(events)

    def is_macro_window_active(
        self, current_time: datetime, strategy_type: str = "intraday"
    ) -> tuple[bool, str]:
        return self.macro_filter.is_macro_window_active(current_time, strategy_type=strategy_type)

    def check_calendar_expiry(
        self, current_time: datetime, horizon_days: int = 30
    ) -> tuple[bool, int]:
        return self.macro_filter.check_calendar_expiry(current_time, horizon_days=horizon_days)

    async def refresh_earnings(self, symbols: list[str]) -> None:
        """Actualiza balances corporativos para el universo de símbolos dado."""
        for sym in symbols:
            try:
                await self.earnings_client.fetch_earnings(symbol=sym)
                self.failed_symbols.discard(sym)
            except Exception:
                self.failed_symbols.add(sym)

    def is_symbol_blocked(
        self, symbol: str, current_time: datetime | None = None
    ) -> tuple[bool, str]:
        """Evalúa si un símbolo está bloqueado para nuevas entradas por balances."""
        if symbol in self.failed_symbols and symbol not in self.earnings_client.cache:
            return True, "EARNINGS_DATA_UNAVAILABLE"

        edate, is_blocked, reason = self.earnings_client.get_earnings_date_sync(
            symbol, current_time=current_time
        )
        if is_blocked:
            return True, reason

        if edate is not None:
            curr_d = current_time.date() if current_time is not None else None
            if self.is_earnings_approaching(edate, current_date=curr_d):
                return True, "EARNINGS_APPROACHING"

        return False, "OK"
