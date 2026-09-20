"""Pruebas unitarias para MarketCalendar y Clock service.

Cubre exhaustivamente:
- Días estándar de negociación, horarios de apertura y cierre (ET vs UTC).
- Fines de semana y feriados del mercado de EE. UU.
- Sesiones con cierre temprano (e.g. 13:00 ET).
- Cálculo dinámico de ventanas: apertura (09:30-10:00 ET) y cierre (últimos 10 minutos).
- Cache en memoria y prevención de consultas duplicadas a la API de Alpaca.
- Avance determinista del reloj simulado (SimulatedClock).
- Política de reloj inyectable (cero datetime.now() directo).
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import MarketDataError
from tbot.data.calendar import MarketCalendar
from tbot.data.types import ET_ZONE


@pytest.fixture
def mock_holidays_2026() -> set[date]:
    """Conjunto representativo de feriados oficiales bursátiles para 2026."""
    return {
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day (observado)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26), # Thanksgiving
        date(2026, 12, 25), # Christmas Day
    }


@pytest.fixture
def mock_early_closes_2026() -> dict[date, tuple[int, int]]:
    """Días con cierre temprano en 2026 (13:00 ET)."""
    return {
        date(2026, 11, 27): (13, 0),  # Black Friday
        date(2026, 12, 24): (13, 0),  # Nochebuena
    }


@pytest.fixture
def populated_calendar(
    mock_holidays_2026: set[date],
    mock_early_closes_2026: dict[date, tuple[int, int]],
) -> MarketCalendar:
    """Fixture de MarketCalendar precargado para todo el año 2026 con SimulatedClock."""
    clock = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))  # Lunes 09:30 ET
    start_date = date(2026, 1, 1)
    end_date = date(2026, 12, 31)
    days = MarketCalendar.generate_standard_trading_days(
        start=start_date,
        end=end_date,
        holidays=mock_holidays_2026,
        early_closes=mock_early_closes_2026,
    )
    return MarketCalendar(
        clock=clock,
        initial_days=days,
        covered_range=(start_date, end_date),
    )


# ===========================================================================
# 1. Pruebas de Cumplimiento de Reloj Inyectable
# ===========================================================================


class TestMarketCalendarClockPolicy:
    """Valida la inyección estricta del reloj y rechazo de timestamps naive."""

    def test_clock_dependency_injection(self, populated_calendar: MarketCalendar) -> None:
        """Verifica que el calendario exponga el reloj inyectado."""
        assert isinstance(populated_calendar.clock, SimulatedClock)

    def test_naive_timestamp_rejection(self, populated_calendar: MarketCalendar) -> None:
        """Verifica que pasar un datetime naive dispare ValueError."""
        naive_dt = datetime(2026, 9, 21, 10, 0, 0)
        with pytest.raises(ValueError, match="tzinfo"):
            populated_calendar.is_market_open(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            populated_calendar.is_open_window(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            populated_calendar.is_close_window(naive_dt)

        with pytest.raises(ValueError, match="tzinfo"):
            populated_calendar.get_market_clock(naive_dt)


# ===========================================================================
# 2. Pruebas de Días Estándar y Zona Horaria (EDT vs EST)
# ===========================================================================


class TestStandardTradingDays:
    """Valida sesiones de rueda regular, conversión ET a UTC y horarios de sesión."""

    def test_standard_trading_days_identification(self, populated_calendar: MarketCalendar) -> None:
        """Lunes a viernes no feriados deben identificarse como días hábiles."""
        assert populated_calendar.is_trading_day(date(2026, 9, 21))  # Lunes
        assert populated_calendar.is_trading_day(date(2026, 9, 22))  # Martes
        assert populated_calendar.is_trading_day(date(2026, 9, 23))  # Miércoles
        assert populated_calendar.is_trading_day(date(2026, 9, 24))  # Jueves
        assert populated_calendar.is_trading_day(date(2026, 9, 25))  # Viernes

    def test_session_times_edt_summer(self, populated_calendar: MarketCalendar) -> None:
        """En horario de verano (EDT = UTC-4), 09:30 ET es 13:30 UTC y 16:00 ET es 20:00 UTC."""
        day = populated_calendar.get_trading_day(date(2026, 9, 21))
        assert day is not None
        assert day.open_time == datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
        assert day.close_time == datetime(2026, 9, 21, 20, 0, tzinfo=UTC)
        assert not day.is_early_close
        assert day.duration_minutes == 390.0  # 6.5 horas

        # Propiedades auxiliares en ET
        assert day.open_time_et.hour == 9
        assert day.open_time_et.minute == 30
        assert day.close_time_et.hour == 16
        assert day.close_time_et.minute == 0

    def test_session_times_est_winter(self, populated_calendar: MarketCalendar) -> None:
        """En horario estándar de invierno (EST = UTC-5), 09:30 ET es 14:30 UTC y 16:00 ET es 21:00 UTC."""
        day = populated_calendar.get_trading_day(date(2026, 12, 1))
        assert day is not None
        assert day.open_time == datetime(2026, 12, 1, 14, 30, tzinfo=UTC)
        assert day.close_time == datetime(2026, 12, 1, 21, 0, tzinfo=UTC)
        assert not day.is_early_close
        assert day.duration_minutes == 390.0

    def test_is_market_open_progression_standard_day(self, populated_calendar: MarketCalendar) -> None:
        """Verifica los límites exactos de apertura y cierre para un día estándar."""
        # 09:29:59 ET -> Cerrado
        pre_open = datetime(2026, 9, 21, 9, 29, 59, tzinfo=ET_ZONE)
        assert not populated_calendar.is_market_open(pre_open)

        # 09:30:00 ET -> Abierto
        at_open = datetime(2026, 9, 21, 9, 30, 0, tzinfo=ET_ZONE)
        assert populated_calendar.is_market_open(at_open)

        # 12:00:00 ET -> Abierto
        midday = datetime(2026, 9, 21, 12, 0, 0, tzinfo=ET_ZONE)
        assert populated_calendar.is_market_open(midday)

        # 15:59:59 ET -> Abierto
        just_before_close = datetime(2026, 9, 21, 15, 59, 59, tzinfo=ET_ZONE)
        assert populated_calendar.is_market_open(just_before_close)

        # 16:00:00 ET -> Cerrado (fin de rueda continua)
        at_close = datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET_ZONE)
        assert not populated_calendar.is_market_open(at_close)

        # 16:01:00 ET -> Cerrado
        post_close = datetime(2026, 9, 21, 16, 1, 0, tzinfo=ET_ZONE)
        assert not populated_calendar.is_market_open(post_close)


# ===========================================================================
# 3. Pruebas de Fines de Semana y Feriados
# ===========================================================================


class TestWeekendsAndHolidays:
    """Valida la detección de fines de semana y feriados del mercado."""

    def test_weekends_are_not_trading_days(self, populated_calendar: MarketCalendar) -> None:
        """Sábados y domingos no son días hábiles ni tienen mercado abierto."""
        saturday = date(2026, 9, 19)
        sunday = date(2026, 9, 20)

        assert not populated_calendar.is_trading_day(saturday)
        assert not populated_calendar.is_trading_day(sunday)
        assert populated_calendar.get_trading_day(saturday) is None
        assert populated_calendar.get_session_times(sunday) is None

        sat_dt = datetime(2026, 9, 19, 12, 0, 0, tzinfo=ET_ZONE)
        assert not populated_calendar.is_market_open(sat_dt)

    def test_market_clock_on_weekend_projects_to_monday(
        self, populated_calendar: MarketCalendar
    ) -> None:
        """En fin de semana, next_open debe apuntar al lunes a las 09:30 ET."""
        sat_dt = datetime(2026, 9, 19, 14, 0, 0, tzinfo=ET_ZONE)
        m_clock = populated_calendar.get_market_clock(sat_dt)

        assert not m_clock.is_open
        expected_monday_open = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)  # 09:30 EDT
        assert m_clock.next_open == expected_monday_open

    def test_official_market_holidays_excluded(
        self,
        populated_calendar: MarketCalendar,
        mock_holidays_2026: set[date],
    ) -> None:
        """Todos los feriados oficiales deben figurar como no negociables."""
        for holiday in mock_holidays_2026:
            assert not populated_calendar.is_trading_day(holiday), f"Falló feriado: {holiday}"
            holiday_dt = datetime(holiday.year, holiday.month, holiday.day, 12, 0, tzinfo=ET_ZONE)
            assert not populated_calendar.is_market_open(holiday_dt)

    def test_market_clock_over_holiday_projects_to_tuesday(
        self, populated_calendar: MarketCalendar
    ) -> None:
        """Durante el fin de semana de Labor Day (lunes feriado), next_open debe ser el martes."""
        labor_day_sunday = datetime(2026, 9, 6, 12, 0, 0, tzinfo=ET_ZONE)
        m_clock = populated_calendar.get_market_clock(labor_day_sunday)

        assert not m_clock.is_open
        expected_tuesday_open = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)  # Martes 8 sept
        assert m_clock.next_open == expected_tuesday_open


# ===========================================================================
# 4. Pruebas de Cierres Tempranos (Early Closes)
# ===========================================================================


class TestEarlyCloses:
    """Valida sesiones reducidas (Black Friday, Nochebuena) a las 13:00 ET."""

    def test_early_close_attributes(self, populated_calendar: MarketCalendar) -> None:
        """El día después de Acción de Gracias debe ser un día hábil con is_early_close=True."""
        black_friday = date(2026, 11, 27)
        assert populated_calendar.is_trading_day(black_friday)
        assert populated_calendar.is_early_close(black_friday)

        day = populated_calendar.get_trading_day(black_friday)
        assert day is not None
        assert day.is_early_close
        assert day.duration_minutes == 210.0  # 3.5 horas (09:30 a 13:00 ET)

        # En EST (UTC-5), 13:00 ET es 18:00 UTC
        assert day.close_time == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)

    def test_early_close_market_open_timeline(self, populated_calendar: MarketCalendar) -> None:
        """Mercado debe cerrar a las 13:00 ET en un día de cierre temprano."""
        # 12:59:59 ET -> Abierto
        open_dt = datetime(2026, 11, 27, 12, 59, 59, tzinfo=ET_ZONE)
        assert populated_calendar.is_market_open(open_dt)

        # 13:00:00 ET -> Cerrado
        close_dt = datetime(2026, 11, 27, 13, 0, 0, tzinfo=ET_ZONE)
        assert not populated_calendar.is_market_open(close_dt)

        # 13:15:00 ET -> Cerrado
        after_close = datetime(2026, 11, 27, 13, 15, 0, tzinfo=ET_ZONE)
        assert not populated_calendar.is_market_open(after_close)

    def test_early_close_market_clock(self, populated_calendar: MarketCalendar) -> None:
        """El reloj de mercado debe proyectar next_close a las 13:00 ET."""
        at_10am = datetime(2026, 11, 27, 10, 0, 0, tzinfo=ET_ZONE)
        m_clock = populated_calendar.get_market_clock(at_10am)

        assert m_clock.is_open
        expected_close = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # 13:00 EST
        assert m_clock.next_close == expected_close


# ===========================================================================
# 5. Pruebas de Ventanas Dinámicas: Apertura (09:30-10:00) y Cierre (últimos 10m)
# ===========================================================================


class TestDynamicWindows:
    """Valida el cálculo dinámico de las ventanas de apertura y cierre."""

    def test_open_window_boundaries(self, populated_calendar: MarketCalendar) -> None:
        """La ventana de apertura comprende estrictamente de 09:30 a 10:00 ET."""
        # 09:29:59 ET -> Fuera
        assert not populated_calendar.is_open_window(
            datetime(2026, 9, 21, 9, 29, 59, tzinfo=ET_ZONE)
        )

        # 09:30:00 ET -> Dentro
        assert populated_calendar.is_open_window(
            datetime(2026, 9, 21, 9, 30, 0, tzinfo=ET_ZONE)
        )

        # 09:45:00 ET -> Dentro
        assert populated_calendar.is_open_window(
            datetime(2026, 9, 21, 9, 45, 0, tzinfo=ET_ZONE)
        )

        # 09:59:59 ET -> Dentro
        assert populated_calendar.is_open_window(
            datetime(2026, 9, 21, 9, 59, 59, tzinfo=ET_ZONE)
        )

        # 10:00:00 ET -> Fuera (expiró la primera media hora)
        assert not populated_calendar.is_open_window(
            datetime(2026, 9, 21, 10, 0, 0, tzinfo=ET_ZONE)
        )

    def test_close_window_standard_day(self, populated_calendar: MarketCalendar) -> None:
        """En rueda normal (cierre 16:00 ET), la ventana de cierre es de 15:50 a 16:00 ET."""
        # 15:49:59 ET -> Fuera
        assert not populated_calendar.is_close_window(
            datetime(2026, 9, 21, 15, 49, 59, tzinfo=ET_ZONE)
        )

        # 15:50:00 ET -> Dentro
        assert populated_calendar.is_close_window(
            datetime(2026, 9, 21, 15, 50, 0, tzinfo=ET_ZONE)
        )

        # 15:55:00 ET -> Dentro
        assert populated_calendar.is_close_window(
            datetime(2026, 9, 21, 15, 55, 0, tzinfo=ET_ZONE)
        )

        # 16:00:00 ET -> Dentro (campana de cierre)
        assert populated_calendar.is_close_window(
            datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET_ZONE)
        )

        # 16:00:01 ET -> Fuera
        assert not populated_calendar.is_close_window(
            datetime(2026, 9, 21, 16, 0, 1, tzinfo=ET_ZONE)
        )

    def test_close_window_early_close_day(self, populated_calendar: MarketCalendar) -> None:
        """En día de cierre temprano (13:00 ET), la ventana de cierre se adapta dinámicamente a 12:50-13:00 ET."""
        # 12:49:59 ET -> Fuera
        assert not populated_calendar.is_close_window(
            datetime(2026, 11, 27, 12, 49, 59, tzinfo=ET_ZONE)
        )

        # 12:50:00 ET -> Dentro
        assert populated_calendar.is_close_window(
            datetime(2026, 11, 27, 12, 50, 0, tzinfo=ET_ZONE)
        )

        # 12:55:00 ET -> Dentro
        assert populated_calendar.is_close_window(
            datetime(2026, 11, 27, 12, 55, 0, tzinfo=ET_ZONE)
        )

        # 13:00:00 ET -> Dentro
        assert populated_calendar.is_close_window(
            datetime(2026, 11, 27, 13, 0, 0, tzinfo=ET_ZONE)
        )

        # 13:00:01 ET -> Fuera
        assert not populated_calendar.is_close_window(
            datetime(2026, 11, 27, 13, 0, 1, tzinfo=ET_ZONE)
        )

    def test_regular_trading_window_gate(self, populated_calendar: MarketCalendar) -> None:
        """is_regular_trading_window debe ser True exclusivamente entre 10:00 y 15:50 ET."""
        # 09:45 ET (ventana de apertura) -> False
        assert not populated_calendar.is_regular_trading_window(
            datetime(2026, 9, 21, 9, 45, 0, tzinfo=ET_ZONE)
        )

        # 10:00 ET (fin de apertura, antes de cierre) -> True
        assert populated_calendar.is_regular_trading_window(
            datetime(2026, 9, 21, 10, 0, 0, tzinfo=ET_ZONE)
        )

        # 14:00 ET (plena rueda) -> True
        assert populated_calendar.is_regular_trading_window(
            datetime(2026, 9, 21, 14, 0, 0, tzinfo=ET_ZONE)
        )

        # 15:50 ET (ventana de cierre) -> False
        assert not populated_calendar.is_regular_trading_window(
            datetime(2026, 9, 21, 15, 50, 0, tzinfo=ET_ZONE)
        )


# ===========================================================================
# 6. Pruebas de Aritmética de Días Hábiles
# ===========================================================================


class TestTradingDayCalculations:
    """Valida cálculos de adición de días hábiles requeridos por filtros de eventos."""

    def test_add_trading_days_forward(self, populated_calendar: MarketCalendar) -> None:
        """Viernes + 1 día hábil debe ser Lunes."""
        friday = date(2026, 9, 18)
        assert populated_calendar.add_trading_days(friday, 1) == date(2026, 9, 21)

    def test_add_trading_days_backward(self, populated_calendar: MarketCalendar) -> None:
        """Lunes - 2 días hábiles debe ser el Jueves previo (requerido para earnings_block_days)."""
        monday = date(2026, 9, 21)
        assert populated_calendar.add_trading_days(monday, -2) == date(2026, 9, 17)

    def test_add_trading_days_skips_holiday(self, populated_calendar: MarketCalendar) -> None:
        """Miércoles antes de Thanksgiving + 1 día hábil debe ser Black Friday (salta feriado)."""
        wed = date(2026, 11, 25)
        # Jueves 26 es Acción de Gracias (feriado) -> +1 día hábil = Viernes 27
        assert populated_calendar.add_trading_days(wed, 1) == date(2026, 11, 27)

    def test_trading_days_between_count(self, populated_calendar: MarketCalendar) -> None:
        """Verifica conteo exacto de días hábiles en un intervalo."""
        # Semana del 21 al 25 de septiembre (5 días hábiles)
        assert populated_calendar.trading_days_between(date(2026, 9, 21), date(2026, 9, 25)) == 5

        # Intervalo que incluye un fin de semana (Viernes 18 a Lunes 21 = 2 días hábiles)
        assert populated_calendar.trading_days_between(date(2026, 9, 18), date(2026, 9, 21)) == 2


# ===========================================================================
# 7. Pruebas de Cache en Memoria y Manejo de Alpaca API
# ===========================================================================


class TestCalendarCachingAndAlpacaSync:
    """Valida la prevención de llamadas duplicadas y sincronización contra Alpaca."""

    def test_cache_hit_prevents_duplicate_api_calls(self) -> None:
        """Consultas sobre fechas ya cacheadas no deben invocar al cliente de Alpaca."""
        mock_client = MagicMock()
        clock = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))
        calendar = MarketCalendar(clock=clock, trading_client=mock_client)

        # Simular respuesta de Alpaca
        mock_cal_day = MagicMock()
        mock_cal_day.date = date(2026, 9, 21)
        mock_cal_day.open = datetime(2026, 9, 21, 9, 30)  # naive ET
        mock_cal_day.close = datetime(2026, 9, 21, 16, 0)  # naive ET
        mock_client.get_calendar.return_value = [mock_cal_day]

        # Primera consulta: sincroniza
        assert calendar.is_trading_day(date(2026, 9, 21))
        assert mock_client.get_calendar.call_count == 1

        # Segunda consulta sobre la misma fecha: debe usar cache en memoria
        assert calendar.is_trading_day(date(2026, 9, 21))
        assert mock_client.get_calendar.call_count == 1  # No hubo segunda llamada

    def test_uncovered_date_without_client_raises_market_data_error(self) -> None:
        """Si una fecha no está en cache y no hay cliente disponible, debe fallar explícitamente."""
        clock = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))
        empty_calendar = MarketCalendar(clock=clock, trading_client=None)

        with pytest.raises(MarketDataError, match="no se encuentra en el cache"):
            empty_calendar.is_trading_day(date(2026, 9, 21))

    def test_alpaca_api_exception_wraps_in_market_data_error(self) -> None:
        """Si la llamada a Alpaca falla por error de red o 5xx, debe levantarse MarketDataError."""
        mock_client = MagicMock()
        mock_client.get_calendar.side_effect = ConnectionError("Alpaca endpoint unreachable")
        clock = SimulatedClock(datetime(2026, 9, 21, 13, 30, tzinfo=UTC))
        calendar = MarketCalendar(clock=clock, trading_client=mock_client)

        with pytest.raises(MarketDataError, match="Error al consultar el calendario de Alpaca"):
            calendar.sync_calendar(date(2026, 1, 1), date(2026, 12, 31))


# ===========================================================================
# 8. Pruebas de Avance Determinista con SimulatedClock
# ===========================================================================


class TestSimulatedClockAdvancement:
    """Valida la reactividad inmediata del calendario ante avances de SimulatedClock."""

    def test_progression_through_trading_session(
        self, populated_calendar: MarketCalendar
    ) -> None:
        """Avanzar el reloj simulado debe reflejarse en tiempo real en los estados del calendario."""
        sim_clock = populated_calendar.clock
        assert isinstance(sim_clock, SimulatedClock)

        # 1. 09:20 ET -> Mercado cerrado
        sim_clock.set_time(datetime(2026, 9, 21, 9, 20, 0, tzinfo=ET_ZONE).astimezone(UTC))
        assert not populated_calendar.is_market_open()
        assert not populated_calendar.is_open_window()

        # 2. Avanzar 10 minutos a 09:30 ET -> Mercado abierto y ventana de apertura activa
        sim_clock.advance(timedelta(minutes=10))
        assert populated_calendar.is_market_open()
        assert populated_calendar.is_open_window()
        assert not populated_calendar.is_regular_trading_window()

        # 3. Avanzar 30 minutos a 10:00 ET -> Ventana de apertura cerrada, rueda regular activa
        sim_clock.advance(timedelta(minutes=30))
        assert populated_calendar.is_market_open()
        assert not populated_calendar.is_open_window()
        assert populated_calendar.is_regular_trading_window()

        # 4. Avanzar 5 horas y 50 minutos a 15:50 ET -> Ventana de cierre activa
        sim_clock.advance(timedelta(hours=5, minutes=50))
        assert populated_calendar.is_market_open()
        assert populated_calendar.is_close_window()
        assert not populated_calendar.is_regular_trading_window()

        # 5. Avanzar 10 minutos a 16:00 ET -> Mercado cerrado
        sim_clock.advance(timedelta(minutes=10))
        assert not populated_calendar.is_market_open()

        # 6. Avanzar al sábado -> Fin de semana
        sim_clock.advance(timedelta(days=5))
        assert not populated_calendar.is_market_open()
        assert not populated_calendar.is_trading_day(sim_clock.now().astimezone(ET_ZONE).date())
