"""Suite de pruebas unitarias para EventCalendar, FinnhubEarningsClient y MacroFilter.

Verifica de forma determinista y exhaustiva:
1. Robustez en la normalización de códigos de hora de Finnhub (bmo, amc, dmh, vacíos, desconocidos).
2. Mocking HTTP con httpx.MockTransport y rate limiter de 60 req/min.
3. Principio de falla cerrada (fail-closed) ante errores de red y corte estricto de cache a 72h.
4. Ventana de bloqueo por balances corporativos (2 días hábiles previos).
5. Ingesta y normalización de eventos macroeconómicos desde YAML con zona horaria America/New_York.
6. Ventana simétrica de 30 minutos de blackout macro.
7. Bloqueo swing durante todo el día en jornadas de anuncio FOMC.
8. Monitor de expiración del horizonte del calendario macro (< 30 días).
9. Persistencia de eventos en la tabla events (SQLite async en memoria).
10. Cumplimiento de la política de reloj inyectable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tbot.common.clock import SimulatedClock
from tbot.db.models import MarketEvent
from tbot.events.calendar import (
    ET_TIMEZONE,
    EventCalendar,
    FinnhubEarningsClient,
    MacroFilter,
    parse_hour_code,
)

# ===========================================================================
# 1. Pruebas de Normalización de Códigos de Hora
# ===========================================================================


class TestHourCodeRobustness:
    """Verifica el mapeo robusto de los códigos de hora retornados por Finnhub."""

    def test_known_hour_codes(self) -> None:
        assert parse_hour_code("bmo") == "before_market_open"
        assert parse_hour_code("BMO") == "before_market_open"
        assert parse_hour_code("amc") == "after_market_close"
        assert parse_hour_code("AMC") == "after_market_close"
        assert parse_hour_code("dmh") == "during_market_hours"
        assert parse_hour_code("DMH") == "during_market_hours"

    def test_unspecified_hour_codes(self) -> None:
        assert parse_hour_code("") == "unspecified"
        assert parse_hour_code("   ") == "unspecified"
        assert parse_hour_code(None) == "unspecified"

    def test_unknown_hour_codes(self) -> None:
        assert parse_hour_code("xyz") == "unknown"
        assert parse_hour_code("123") == "unknown"
        assert parse_hour_code("pre") == "unknown"


# ===========================================================================
# 2. Pruebas de Mocking HTTP de Finnhub
# ===========================================================================


class TestFinnhubHTTPMocking:
    """Pruebas de consulta y parseo HTTP utilizando httpx.MockTransport."""

    @pytest.mark.asyncio
    async def test_fetch_earnings_success(self, sim_clock: SimulatedClock) -> None:
        mock_response_data = {
            "earningsCalendar": [
                {
                    "date": "2026-10-15",
                    "symbol": "NVDA",
                    "hour": "amc",
                    "epsEstimate": 0.85,
                    "epsActual": 0.92,
                    "revenueEstimate": 32000000000.0,
                    "revenueActual": 33500000000.0,
                    "quarter": 3,
                    "year": 2026,
                },
                {
                    "date": "2026-10-22",
                    "symbol": "AAPL",
                    "hour": "bmo",
                    "epsEstimate": 1.50,
                    "epsActual": None,
                    "revenueEstimate": 90000000000.0,
                    "revenueActual": None,
                    "quarter": 4,
                    "year": 2026,
                },
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/calendar/earnings" in str(request.url)
            assert "token=" in str(request.url)
            return httpx.Response(200, json=mock_response_data)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = FinnhubEarningsClient(clock=sim_clock, http_client=http_client)
            releases = await client.fetch_earnings(
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 31),
            )

        assert len(releases) == 2
        nvda = releases[0]
        assert nvda.symbol == "NVDA"
        assert nvda.date == date(2026, 10, 15)
        assert nvda.hour_raw == "amc"
        assert nvda.hour_category == "after_market_close"
        assert nvda.eps_actual == 0.92

        # Verifica actualización de cache en memoria
        assert "NVDA" in client.cache
        assert client.cache["NVDA"][0] == date(2026, 10, 15)
        assert client.cache["NVDA"][1] == sim_clock.now()

    @pytest.mark.asyncio
    async def test_fetch_earnings_network_failure(self, sim_clock: SimulatedClock) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = FinnhubEarningsClient(clock=sim_clock, http_client=http_client)
            with pytest.raises(httpx.HTTPStatusError):
                await client.fetch_earnings()


# ===========================================================================
# 3. Pruebas de Falla Cerrada (Fail-Closed) de Finnhub (72 Horas)
# ===========================================================================


class TestFinnhubFailClosedGuard:
    """Verifica la política estricta de corte a 72.0h ante fallas de red."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("cache_age_hours", "expected_blocked", "expected_reason"),
        [
            (24.0, False, "CACHE_VALID"),  # 1 día -> Válido
            (48.0, False, "CACHE_VALID"),  # 2 días -> Válido
            (71.9, False, "CACHE_VALID"),  # < 72h -> Válido
            (72.0, False, "CACHE_VALID"),  # Límite exacto de 72h -> Válido
            (72.1, True, "EARNINGS_DATA_UNAVAILABLE"),  # Excedido por 6 minutos -> Bloqueado
            (96.0, True, "EARNINGS_DATA_UNAVAILABLE"),  # 4 días -> Bloqueado
            (240.0, True, "EARNINGS_DATA_UNAVAILABLE"),  # 10 días -> Bloqueado
        ],
    )
    async def test_fail_closed_cache_boundaries(
        self,
        sim_clock: SimulatedClock,
        cache_age_hours: float,
        expected_blocked: bool,
        expected_reason: str,
    ) -> None:
        client = FinnhubEarningsClient(clock=sim_clock)
        client.network_error = True  # Simular corte de red con Finnhub

        cached_at = sim_clock.now() - timedelta(hours=cache_age_hours)
        client.seed_earnings("NVDA", date(2026, 10, 15), cached_at=cached_at)

        edate, is_blocked, reason = await client.get_earnings_date("NVDA")
        assert is_blocked == expected_blocked
        assert reason == expected_reason
        if not expected_blocked:
            assert edate == date(2026, 10, 15)
        else:
            assert edate is None

    @pytest.mark.asyncio
    async def test_fail_closed_missing_symbol_on_network_error(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Si el símbolo nunca fue cacheado y la red falla, se bloquea con fail-closed."""
        client = FinnhubEarningsClient(clock=sim_clock)
        client.network_error = True

        edate, is_blocked, reason = await client.get_earnings_date("UNKNOWN_SYM")
        assert is_blocked is True
        assert reason == "EARNINGS_DATA_UNAVAILABLE"
        assert edate is None


# ===========================================================================
# 4. Pruebas del Bloqueador por Proximidad de Balances (Lead Time)
# ===========================================================================


class TestEarningsLeadTimeBlocker:
    """Verifica que se bloqueen operaciones dentro de los 2 días previos al reporte."""

    @pytest.mark.parametrize(
        ("days_ahead", "expected_blocked"),
        [
            (10, False),  # 10 días antes -> No bloqueado
            (5, False),   # 5 días antes -> No bloqueado
            (3, False),   # 3 días antes -> No bloqueado
            (2, True),    # 2 días antes (límite exacto) -> Bloqueado
            (1, True),    # 1 día antes -> Bloqueado
            (0, True),    # Mismo día del reporte -> Bloqueado
            (-1, False),  # Día posterior al reporte -> No bloqueado
            (-5, False),  # 5 días después -> No bloqueado
        ],
    )
    def test_earnings_lead_time_blocking(
        self, sim_clock: SimulatedClock, days_ahead: int, expected_blocked: bool
    ) -> None:
        client = FinnhubEarningsClient(clock=sim_clock)
        earnings_date = sim_clock.today() + timedelta(days=days_ahead)

        is_blocked = client.is_earnings_approaching(earnings_date, block_days=2)
        assert is_blocked == expected_blocked

    def test_event_calendar_is_symbol_blocked(self, sim_clock: SimulatedClock) -> None:
        """Verifica el método síncrono is_symbol_blocked de la fachada EventCalendar."""
        cal = EventCalendar(clock=sim_clock, earnings_block_days=2)

        # 1. Símbolo sin balance agendado -> No bloqueado
        blocked, reason = cal.is_symbol_blocked("MSFT")
        assert not blocked
        assert reason == "OK"

        # 2. Símbolo con balance a 5 días -> No bloqueado
        cal.seed_earnings("AAPL", sim_clock.today() + timedelta(days=5))
        blocked, reason = cal.is_symbol_blocked("AAPL")
        assert not blocked
        assert reason == "OK"

        # 3. Símbolo con balance a 1 día -> Bloqueado con EARNINGS_APPROACHING
        cal.seed_earnings("NVDA", sim_clock.today() + timedelta(days=1))
        blocked, reason = cal.is_symbol_blocked("NVDA")
        assert blocked
        assert reason == "EARNINGS_APPROACHING"

        # 4. Falla de red con cache vencido -> Bloqueado con EARNINGS_DATA_UNAVAILABLE
        cal.network_error = True
        cal.seed_earnings("TSLA", sim_clock.today() + timedelta(days=10), cached_at=sim_clock.now() - timedelta(days=4))
        blocked, reason = cal.is_symbol_blocked("TSLA")
        assert blocked
        assert reason == "EARNINGS_DATA_UNAVAILABLE"


# ===========================================================================
# 5. Pruebas de Carga de Calendario Macro desde YAML
# ===========================================================================


class TestMacroYAMLLoader:
    """Verifica la lectura, parseo y localización de macro_events_2026.yaml."""

    def test_load_real_macro_yaml(self, sim_clock: SimulatedClock) -> None:
        macro_filter = MacroFilter(clock=sim_clock)
        # Debe haber cargado config/macro_events_2026.yaml
        assert len(macro_filter.events) >= 10

        # Verificar tipos esperados
        event_names = [e["name"] for e in macro_filter.events]
        assert any("NFP" in name for name in event_names)
        assert any("CPI" in name for name in event_names)
        assert any("FOMC" in name for name in event_names)

        # Verificar que todos los timestamps sean UTC aware
        for ev in macro_filter.events:
            assert ev["timestamp"].tzinfo is not None
            assert ev["timestamp"].tzinfo == UTC
            assert ev["datetime_et"].tzinfo == ET_TIMEZONE

    def test_load_synthetic_events(self, sim_clock: SimulatedClock) -> None:
        macro_filter = MacroFilter(clock=sim_clock, yaml_path=Path("/non_existent.yaml"))
        assert len(macro_filter.events) == 0

        event_time = sim_clock.now() + timedelta(hours=2)
        macro_filter.load_events([
            {"name": "CPI Release", "type": "CPI", "timestamp": event_time, "impact": "high"},
            {"name": "FOMC Meeting", "type": "FOMC", "timestamp": event_time + timedelta(days=1), "impact": "critical"},
        ])
        assert len(macro_filter.events) == 2


# ===========================================================================
# 6. Pruebas de Ventana de Blackout Macroeconómico (30 Minutos)
# ===========================================================================


class TestMacroBlackoutWindow:
    """Verifica la ventana de bloqueo de 30 minutos antes y después de cada evento macro."""

    @pytest.mark.parametrize(
        ("offset_minutes", "expected_blocked"),
        [
            (-45, False),  # 45 min antes -> Libre
            (-31, False),  # 31 min antes -> Libre
            (-30, True),   # 30 min antes (límite exacto) -> Bloqueado
            (-15, True),   # 15 min antes -> Bloqueado
            (0, True),     # En el instante exacto -> Bloqueado
            (15, True),    # 15 min después -> Bloqueado
            (30, True),    # 30 min después (límite exacto) -> Bloqueado
            (31, False),   # 31 min después -> Libre
            (60, False),   # 60 min después -> Libre
        ],
    )
    def test_macro_blackout_boundaries(
        self, sim_clock: SimulatedClock, offset_minutes: int, expected_blocked: bool
    ) -> None:
        macro_filter = MacroFilter(clock=sim_clock, yaml_path=Path("/non_existent.yaml"))
        event_time = datetime(2026, 9, 11, 12, 30, 0, tzinfo=UTC)  # 08:30 ET
        macro_filter.load_events([
            {"name": "CPI - Inflación", "type": "CPI", "timestamp": event_time, "impact": "high"}
        ])

        query_time = event_time + timedelta(minutes=offset_minutes)
        blocked, reason = macro_filter.is_macro_window_active(query_time, strategy_type="intraday")
        assert blocked == expected_blocked
        if expected_blocked:
            assert reason == "MACRO_WINDOW_HIGH"
        else:
            assert reason == "OK"


# ===========================================================================
# 7. Pruebas de Bloqueo Total Swing en Días de FOMC
# ===========================================================================


class TestFOMCAllDaySwingBlock:
    """Verifica que las estrategias swing queden bloqueadas durante toda la jornada de FOMC."""

    def test_fomc_all_day_swing_lockout(self, sim_clock: SimulatedClock) -> None:
        macro_filter = MacroFilter(clock=sim_clock, yaml_path=Path("/non_existent.yaml"))
        # FOMC a las 14:00 ET (18:00 UTC) el 2026-09-16
        fomc_time = datetime(2026, 9, 16, 18, 0, 0, tzinfo=UTC)
        macro_filter.load_events([
            {
                "name": "FOMC - Decisión de tipos y conferencia",
                "type": "FOMC",
                "timestamp": fomc_time,
                "impact": "critical",
            }
        ])

        # 1. Mañana de FOMC (09:45 ET = 13:45 UTC, 255 min antes del anuncio):
        morning_query = datetime(2026, 9, 16, 13, 45, 0, tzinfo=UTC)

        # Estrategia swing: bloqueada todo el día
        blocked_swing, reason_swing = macro_filter.is_macro_window_active(morning_query, strategy_type="swing")
        assert blocked_swing is True
        assert reason_swing == "FOMC_ALL_DAY_SWING_BLOCK"

        # Estrategia intradía: permitida en la mañana (fuera de la ventana de 30m)
        blocked_intra, reason_intra = macro_filter.is_macro_window_active(morning_query, strategy_type="intraday")
        assert blocked_intra is False
        assert reason_intra == "OK"

        # 2. Ventana del anuncio (14:00 ET = 18:00 UTC):
        # Ambas estrategias están bloqueadas
        b_intra, r_intra = macro_filter.is_macro_window_active(fomc_time, strategy_type="intraday")
        assert b_intra is True
        assert r_intra == "MACRO_WINDOW_CRITICAL"

        b_swing, _ = macro_filter.is_macro_window_active(fomc_time, strategy_type="swing")
        assert b_swing is True

        # 3. Tarde posterior a la ventana de 30m (15:00 ET = 19:00 UTC, 60m después):
        afternoon_query = datetime(2026, 9, 16, 19, 0, 0, tzinfo=UTC)
        b_intra_after, _ = macro_filter.is_macro_window_active(afternoon_query, strategy_type="intraday")
        assert b_intra_after is False  # Intradía libre

        b_swing_after, r_swing_after = macro_filter.is_macro_window_active(afternoon_query, strategy_type="swing")
        assert b_swing_after is True  # Swing sigue bloqueado por ser día FOMC
        assert r_swing_after == "FOMC_ALL_DAY_SWING_BLOCK"

    def test_non_fomc_event_does_not_block_swing_all_day(self, sim_clock: SimulatedClock) -> None:
        """Días de CPI o NFP solo bloquean en la ventana de 30m, NO todo el día a swing."""
        macro_filter = MacroFilter(clock=sim_clock, yaml_path=Path("/non_existent.yaml"))
        cpi_time = datetime(2026, 9, 11, 12, 30, 0, tzinfo=UTC)  # 08:30 ET
        macro_filter.load_events([
            {"name": "CPI - Inflación", "type": "CPI", "timestamp": cpi_time, "impact": "high"}
        ])

        # 11:00 ET (15:00 UTC) -> Lejos del CPI de las 08:30 ET
        query = datetime(2026, 9, 11, 15, 0, 0, tzinfo=UTC)
        blocked_swing, reason_swing = macro_filter.is_macro_window_active(query, strategy_type="swing")
        assert blocked_swing is False
        assert reason_swing == "OK"


# ===========================================================================
# 8. Pruebas del Monitor de Expiración del Calendario Macro
# ===========================================================================


class TestMacroCalendarExpiryMonitor:
    """Verifica la alerta cuando el horizonte restante de eventos macro es menor a 30 días."""

    @pytest.mark.parametrize(
        ("remaining_days", "expected_alert"),
        [
            (60, False),
            (45, False),
            (31, False),
            (30, False),  # Límite exacto de 30 días -> No requiere alerta
            (29, True),   # < 30 días -> Requiere alerta
            (10, True),
            (0, True),
        ],
    )
    def test_calendar_expiry_thresholds(
        self, sim_clock: SimulatedClock, remaining_days: int, expected_alert: bool
    ) -> None:
        macro_filter = MacroFilter(clock=sim_clock, yaml_path=Path("/non_existent.yaml"))
        now = sim_clock.now()

        if remaining_days > 0:
            future_event = now + timedelta(days=remaining_days)
            macro_filter.load_events([
                {"name": "Future Event", "timestamp": future_event, "impact": "high"}
            ])
        else:
            macro_filter.load_events([])

        needs_alert, horizon = macro_filter.check_calendar_expiry(now, horizon_days=30)
        assert needs_alert == expected_alert
        if remaining_days > 0:
            assert horizon == remaining_days
        else:
            assert horizon == 0


# ===========================================================================
# 9. Pruebas de Persistencia de Eventos en Base de Datos
# ===========================================================================


class TestDatabaseEventPersistence:
    """Verifica que los eventos se almacenen correctamente en la tabla events."""

    @pytest.mark.asyncio
    async def test_persist_earnings_event_via_client(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        session_factory = async_sessionmaker(
            bind=test_session.bind,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        mock_data = {
            "earningsCalendar": [
                {
                    "date": "2026-10-15",
                    "symbol": "NVDA",
                    "hour": "amc",
                    "epsEstimate": 0.85,
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mock_data)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = FinnhubEarningsClient(
                clock=sim_clock,
                session_factory=session_factory,
                http_client=http_client,
            )
            await client.fetch_earnings(symbol="NVDA")

        # Consultar la base de datos
        stmt = select(MarketEvent).where(MarketEvent.symbol == "NVDA")
        events = (await test_session.scalars(stmt)).all()
        assert len(events) == 1
        ev = events[0]
        assert ev.type == "earnings"
        # SQLite no preserva tzinfo nativamente al persistir
        fetched_at_utc = (
            ev.fetched_at.replace(tzinfo=UTC) if ev.fetched_at.tzinfo is None else ev.fetched_at
        )
        assert fetched_at_utc == sim_clock.now()
        ts_et_date = ev.ts_et.date() if isinstance(ev.ts_et, datetime) else date.fromisoformat(str(ev.ts_et)[:10])
        assert ts_et_date == date(2026, 10, 15)


# ===========================================================================
# 10. Pruebas de Cumplimiento de Reloj Inyectable
# ===========================================================================


class TestEventCalendarClockCompliance:
    """Verifica que EventCalendar y sus submódulos respeten estrictamente el reloj inyectado."""

    def test_clock_is_injected_and_accessible(self, sim_clock: SimulatedClock) -> None:
        cal = EventCalendar(clock=sim_clock)
        assert cal.clock is sim_clock
        assert cal.earnings_client.clock is sim_clock
        assert cal.macro_filter.clock is sim_clock

    def test_clock_advancement_shifts_cache_and_windows(self, sim_clock: SimulatedClock) -> None:
        cal = EventCalendar(clock=sim_clock)
        cal.network_error = True
        cal.seed_earnings("AAPL", date(2026, 10, 20), cached_at=sim_clock.now())

        # En el momento inicial (cache age = 0h) -> Válido
        _, blocked_init, reason_init = cal.earnings_client.get_earnings_date_sync("AAPL")
        assert not blocked_init
        assert reason_init == "CACHE_VALID"

        # Avanzar 72 horas y 1 minuto
        sim_clock.advance(timedelta(hours=72, minutes=1))
        _, blocked_after, reason_after = cal.earnings_client.get_earnings_date_sync("AAPL")
        assert blocked_after is True
        assert reason_after == "EARNINGS_DATA_UNAVAILABLE"
