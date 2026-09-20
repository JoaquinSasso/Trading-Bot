"""Unit tests for Two-Level Staleness Guard, Feed Health State Machine, and Entry Pricing.

Covers:
- Level 1: GlobalFeedMonitor (WebSocket heartbeat, silence thresholds, state machine transitions, REST fallback).
- Level 2: SymbolFreshnessMonitor (tau calibration, clamping [180s, 600s], freshness evaluation, halt tracking).
- Strict Entry Pricing Policy (10 bps spread threshold, quote midpoint vs last trade, no forward-fill).
- Unified StalenessGuard facade (is_entry_allowed, validate_entry, validate_entry_or_raise, discard metrics).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import StaleDataError
from tbot.data.staleness import (
    DEFAULT_TAU_SECONDS,
    EntryPriceMode,
    FeedHealthState,
    GlobalFeedMonitor,
    StalenessGuard,
    SymbolFreshnessMonitor,
    calibrate_freshness,
)
from tbot.data.types import PriceQuote, TradeQuote


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Reloj simulado a las 14:00:00 UTC (10:00 AM ET - rueda regular activa)."""
    return SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))


# ============================================================================
# 1. PRUEBAS DE MÁQUINA DE ESTADOS DEL FEED GLOBAL (NIVEL 1)
# ============================================================================


class TestGlobalFeedMonitorStateMachine:
    """Verifica las transiciones de estado del feed global y conmutación a REST."""

    def test_initial_state_disconnected(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        assert monitor.state == FeedHealthState.DISCONNECTED
        assert monitor.is_feed_healthy() is False
        assert monitor.is_fallback_active is True
        assert monitor.rest_poll_interval == 10.0
        assert monitor.rest_poll_interval_seconds == 10.0
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_DISCONNECTED"

    def test_transition_to_healthy_on_first_message(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True
        assert monitor.is_fallback_active is False
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is True
        assert reason == "OK"

    def test_transition_healthy_to_degraded_at_60s_boundary(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()

        # 59.9s -> HEALTHY
        sim_clock.advance(timedelta(seconds=59.9))
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True

        # Exactamente 60.0s -> HEALTHY (límite inclusivo)
        sim_clock.advance(timedelta(seconds=0.1))
        assert monitor.state == FeedHealthState.HEALTHY

        # 60.1s -> DEGRADED
        sim_clock.advance(timedelta(seconds=0.1))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_feed_healthy() is False
        assert monitor.is_fallback_active is True
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_DEGRADED"

    def test_recovery_from_degraded_on_new_message(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        sim_clock.advance(timedelta(seconds=90))
        assert monitor.state == FeedHealthState.DEGRADED

        # Nuevo mensaje recibido -> recupera inmediatamente
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True
        assert monitor.is_fallback_active is False
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is True
        assert reason == "OK"

    def test_transition_to_stale_after_300s(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()

        # 300.0s -> DEGRADED (límite de degraded)
        sim_clock.advance(timedelta(seconds=300))
        assert monitor.state == FeedHealthState.DEGRADED

        # 300.1s -> STALE
        sim_clock.advance(timedelta(seconds=0.1))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_feed_healthy() is False
        assert monitor.is_fallback_active is True
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_STALE"

    def test_recovery_from_stale_on_new_message(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        sim_clock.advance(timedelta(seconds=500))
        assert monitor.state == FeedHealthState.STALE

        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True
        assert monitor.is_fallback_active is False

    def test_explicit_disconnect_triggers_fallback(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY

        monitor.record_disconnect(reason="WebSocket closed unexpectedly")
        assert monitor.state == FeedHealthState.DISCONNECTED
        assert monitor.is_fallback_active is True
        assert monitor.is_feed_healthy() is False
        allowed, reason = monitor.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_DISCONNECTED"

    def test_check_global_feed_with_explicit_timestamp(self, sim_clock: SimulatedClock) -> None:
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        now = sim_clock.now()

        # Timestamp reciente (30s de silencio) -> True
        assert monitor.check_global_feed(now - timedelta(seconds=30)) is True
        # Timestamp de exactamente 60s -> True
        assert monitor.check_global_feed(now - timedelta(seconds=60)) is True
        # Timestamp de 60.1s -> False
        assert monitor.check_global_feed(now - timedelta(seconds=60.1)) is False
        # None sin mensajes previos -> False
        assert monitor.check_global_feed(None) is False

    def test_market_hours_enforcement_skips_degradation_outside_hours(
        self, sim_clock: SimulatedClock
    ) -> None:
        # 22:00 UTC (fuera de 13..20 UTC)
        sim_clock.set_time(datetime(2026, 9, 21, 22, 0, 0, tzinfo=UTC))
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=True)
        # Fuera de rueda regular, check_global_feed retorna True
        assert monitor.check_global_feed(sim_clock.now() - timedelta(minutes=30)) is True


# ============================================================================
# 2. PRUEBAS DE CALIBRACIÓN Y FRESCURA POR SÍMBOLO (NIVEL 2)
# ============================================================================


class TestSymbolFreshnessAndCalibration:
    """Verifica la fórmula de calibración de tau, acotamiento y frescura por símbolo."""

    def test_calibrate_freshness_empty_or_invalid_returns_default(self) -> None:
        assert calibrate_freshness([]) == DEFAULT_TAU_SECONDS
        assert calibrate_freshness([float("nan"), -5.0, float("inf")]) == DEFAULT_TAU_SECONDS

    def test_calibrate_freshness_sub_3min_clamping(self) -> None:
        # Valores inferiores a 180s se acotan al mínimo de 180s (3m)
        assert calibrate_freshness([0.0]) == 180.0
        assert calibrate_freshness([10.0, 20.0, 30.0]) == 180.0
        assert calibrate_freshness([179.9]) == 180.0
        assert calibrate_freshness([180.0]) == 180.0

    def test_calibrate_freshness_in_range(self) -> None:
        # Valores entre 180s y 600s se respetan exactamente
        intervals = [float(x) for x in range(1, 251)]  # p99 cercano a 250
        result = calibrate_freshness(intervals)
        assert 180.0 <= result <= 600.0

    def test_calibrate_freshness_super_10min_clamping(self) -> None:
        # Valores superiores a 600s se acotan al máximo de 600s (10m)
        assert calibrate_freshness([600.1]) == 600.0
        assert calibrate_freshness([750.0]) == 600.0
        assert calibrate_freshness([1200.0]) == 600.0

    def test_calculate_tau_symbol_returns_timedelta(self, sim_clock: SimulatedClock) -> None:
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        tau = monitor.calculate_tau_symbol(50.0)
        assert isinstance(tau, timedelta)
        assert tau.total_seconds() == 180.0

        tau = monitor.calculate_tau_symbol(400.0)
        assert tau.total_seconds() == 400.0

        tau = monitor.calculate_tau_symbol(900.0)
        assert tau.total_seconds() == 600.0

    def test_symbol_tau_configuration_and_lookup(self, sim_clock: SimulatedClock) -> None:
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        # Símbolo no registrado usa default (300s)
        assert monitor.get_symbol_tau("SPY").total_seconds() == DEFAULT_TAU_SECONDS

        # Registro con float
        monitor.set_symbol_tau("SPY", 240.0)
        assert monitor.get_symbol_tau("SPY").total_seconds() == 240.0

        # Insensibilidad a mayúsculas/minúsculas
        assert monitor.get_symbol_tau("spy").total_seconds() == 240.0

        # Registro con timedelta
        monitor.set_symbol_tau("AAPL", timedelta(seconds=450))
        assert monitor.get_symbol_tau("AAPL").total_seconds() == 450.0

    def test_symbol_freshness_evaluation_boundaries(self, sim_clock: SimulatedClock) -> None:
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        monitor.set_symbol_tau("SPY", timedelta(seconds=180))
        now = sim_clock.now()

        # Sin datos -> NO_DATA
        is_fresh, reason = monitor.check_symbol_freshness("SPY")
        assert is_fresh is False
        assert reason == "NO_DATA"

        # Registrar cotización fresca (hace 100s)
        quote = PriceQuote(symbol="SPY", bid=Decimal("500.00"), ask=Decimal("500.10"), timestamp=now - timedelta(seconds=100))
        monitor.record_quote("SPY", quote)

        is_fresh, reason = monitor.check_symbol_freshness("SPY")
        assert is_fresh is True
        assert reason == "OK"

        # Avanzar el reloj hasta exactamente los 180s de edad
        sim_clock.advance(timedelta(seconds=80))
        is_fresh, reason = monitor.check_symbol_freshness("SPY")
        assert is_fresh is True

        # Avanzar 1s más (edad 181s > 180s tau) -> POTENTIAL_HALT
        sim_clock.advance(timedelta(seconds=1))
        is_fresh, reason = monitor.check_symbol_freshness("SPY")
        assert is_fresh is False
        assert reason == "POTENTIAL_HALT"

        allowed, entry_reason = monitor.is_symbol_entry_allowed("SPY")
        assert allowed is False
        assert entry_reason == "STALE_SYMBOL_FEED"

    def test_exchange_halt_status_handling(self, sim_clock: SimulatedClock) -> None:
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        now = sim_clock.now()
        monitor.record_quote("SPY", PriceQuote(symbol="SPY", bid=Decimal("500"), ask=Decimal("500.10"), timestamp=now))

        # Estado T (Trading) -> permitido
        monitor.record_exchange_status("SPY", "T")
        assert monitor.is_symbol_entry_allowed("SPY")[0] is True

        # Estado H (Halted) -> bloqueado
        monitor.record_exchange_status("SPY", "H")
        allowed, reason = monitor.check_symbol_freshness("SPY")
        assert allowed is False
        assert reason == "SYMBOL_HALTED"
        assert monitor.is_symbol_entry_allowed("SPY") == (False, "SYMBOL_HALTED")

        # Estado P (Paused) -> bloqueado
        monitor.record_exchange_status("SPY", "P")
        assert monitor.is_symbol_entry_allowed("SPY") == (False, "SYMBOL_HALTED")

        # Estado Q (Quote only) -> bloqueado
        monitor.record_exchange_status("SPY", "Q")
        assert monitor.is_symbol_entry_allowed("SPY") == (False, "SYMBOL_HALTED")

        # Estado R (Resumed) -> permitido de nuevo
        monitor.record_exchange_status("SPY", "R")
        assert monitor.is_symbol_entry_allowed("SPY") == (True, "OK")


# ============================================================================
# 3. PRUEBAS DE POLÍTICA ESTRICTA DE PRECIO DE ENTRADA (SIN FORWARD-FILL)
# ============================================================================


class TestStrictEntryPricingPolicy:
    """Verifica la selección estricta del precio de entrada y prohibición de forward-fill."""

    def test_narrow_spread_fresh_quote_selects_midpoint(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        # Spread de 5 bps: bid=500.00, ask=500.25 -> mid=500.125
        bid = Decimal("500.00")
        ask = Decimal("500.25")
        mid = Decimal("500.125")
        quote = PriceQuote(symbol="SPY", bid=bid, ask=ask, midpoint=mid, spread_bps=5.0, timestamp=now)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.10"), timestamp=now)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price == mid
        assert mode == "MIDPOINT"
        assert mode == "QUOTE_MIDPOINT"
        assert isinstance(mode, EntryPriceMode)

    def test_exact_10bps_boundary_selects_midpoint(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        # Exactamente 10.0 bps: 500.00 a 500.50 -> mid=500.25, spread=10.0 bps
        bid = Decimal("500.00")
        ask = Decimal("500.50")
        mid = Decimal("500.25")
        quote = PriceQuote(symbol="SPY", bid=bid, ask=ask, midpoint=mid, spread_bps=10.0, timestamp=now)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.20"), timestamp=now)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price == mid
        assert mode == "MIDPOINT"

    def test_spread_above_10bps_falls_through_to_last_trade(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        # Spread 10.1 bps -> excede 10 bps, pasa al trade fresco
        bid = Decimal("500.00")
        ask = Decimal("500.505")
        quote = PriceQuote(symbol="SPY", bid=bid, ask=ask, spread_bps=10.1, timestamp=now)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.30"), timestamp=now)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price == Decimal("500.30")
        assert mode == "LAST_TRADE"

    def test_stale_quote_with_fresh_trade_selects_last_trade(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        # Quote con spread 2 bps pero vieja (200s de antigüedad > 180s)
        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.10"),
            spread_bps=2.0,
            timestamp=now - timedelta(seconds=200),
        )
        # Trade fresco (30s de antigüedad)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.05"), timestamp=now - timedelta(seconds=30))

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price == Decimal("500.05")
        assert mode == "LAST_TRADE"

    def test_wide_spread_and_stale_trade_discards_with_stale_price(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        # Quote con spread 25 bps (ancho)
        quote = PriceQuote(symbol="SPY", bid=Decimal("500.00"), ask=Decimal("501.25"), spread_bps=25.0, timestamp=now)
        # Trade viejo (250s > 180s)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.50"), timestamp=now - timedelta(seconds=250))

        initial_discards = guard.discard_counts["STALE_PRICE"]
        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price is None
        assert mode == "STALE_PRICE"
        assert guard.discard_counts["STALE_PRICE"] == initial_discards + 1

    def test_both_quote_and_trade_stale_discards_with_stale_price(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.05"),
            spread_bps=1.0,
            timestamp=now - timedelta(seconds=300),
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("500.02"), timestamp=now - timedelta(seconds=300))

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price is None
        assert mode == "STALE_PRICE"

    def test_crossed_book_falls_through_to_trade(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        # Libro cruzado: ask < bid
        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("501.00"),
            ask=Decimal("500.00"),
            timestamp=now,
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("500.50"), timestamp=now)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price == Decimal("500.50")
        assert mode == "LAST_TRADE"

    def test_non_positive_bid_or_ask_falls_through(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        now = sim_clock.now()
        tau = timedelta(seconds=180)

        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("0.00"),
            ask=Decimal("500.00"),
            timestamp=now,
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("500.00"), timestamp=now)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price == Decimal("500.00")
        assert mode == "LAST_TRADE"

    def test_missing_timestamps_treated_as_stale(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        tau = timedelta(seconds=180)

        quote = PriceQuote(symbol="SPY", bid=Decimal("500.00"), ask=Decimal("500.10"), timestamp=None)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.05"), timestamp=None)

        price, mode = guard.get_entry_price(quote, trade, tau)
        assert price is None
        assert mode == "STALE_PRICE"

    def test_entry_price_mode_dual_equality(self) -> None:
        mode = EntryPriceMode("MIDPOINT")
        assert mode == "MIDPOINT"
        assert mode == "QUOTE_MIDPOINT"
        assert mode in ("MIDPOINT", "LAST_TRADE")
        assert mode in ("QUOTE_MIDPOINT", "LAST_TRADE")
        assert hash(EntryPriceMode("MIDPOINT")) == hash(EntryPriceMode("QUOTE_MIDPOINT"))


# ============================================================================
# 4. PRUEBAS DE LA FACHADA INTEGRAL (STALENESSGUARD)
# ============================================================================


class TestUnifiedStalenessGuardFacade:
    """Verifica la coordinación jerárquica de dos niveles en StalenessGuard."""

    def test_is_entry_allowed_checks_both_levels(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)

        # Nivel 1 desconectado -> bloqueado
        allowed, reason = guard.is_entry_allowed()
        assert allowed is False
        assert reason == "FEED_DISCONNECTED"

        # Enviar mensaje -> Nivel 1 saludable
        guard.record_message()
        assert guard.is_entry_allowed()[0] is True

        # Consultar símbolo sin datos -> bloqueado por Nivel 2
        allowed, reason = guard.is_entry_allowed("SPY")
        assert allowed is False
        assert reason == "NO_DATA"

        # Registrar cotización fresca para SPY -> permitido
        now = sim_clock.now()
        guard.record_quote("SPY", PriceQuote(symbol="SPY", bid=Decimal("500"), ask=Decimal("500.10"), timestamp=now))
        assert guard.is_entry_allowed("SPY") == (True, "OK")

        # Poner símbolo en halt -> bloqueado
        guard.record_exchange_status("SPY", "H")
        assert guard.is_entry_allowed("SPY") == (False, "SYMBOL_HALTED")

    def test_validate_entry_full_flow(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        now = sim_clock.now()
        guard.record_message()

        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("500.00"),
            ask=Decimal("500.20"),
            midpoint=Decimal("500.10"),
            spread_bps=4.0,
            timestamp=now,
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("500.10"), timestamp=now)
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")

        # Flujo exitoso: precio válido, consistente con SIP
        price, is_valid, reason = guard.validate_entry("SPY", quote, trade, sip_close, atr_5m)
        assert is_valid is True
        assert reason == "OK"
        assert price == Decimal("500.10")

    def test_validate_entry_blocked_by_stale_feed(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        now = sim_clock.now()

        # Simular salto en el tiempo de 90s (feed global degradado)
        sim_clock.advance(timedelta(seconds=90))

        quote = PriceQuote(symbol="SPY", bid=Decimal("500.00"), ask=Decimal("500.10"), timestamp=now)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.05"), timestamp=now)

        price, is_valid, reason = guard.validate_entry("SPY", quote, trade, Decimal("500.00"), Decimal("1.00"))
        assert is_valid is False
        assert price is None
        assert reason == "FEED_DEGRADED"

    def test_validate_entry_or_raise_raises_stale_data_error(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        now = sim_clock.now()

        # Quote y trade viejos (400s > tau 300s)
        stale_time = now - timedelta(seconds=400)
        quote = PriceQuote(symbol="SPY", bid=Decimal("500.00"), ask=Decimal("500.10"), timestamp=stale_time)
        trade = TradeQuote(symbol="SPY", price=Decimal("500.05"), timestamp=stale_time)

        with pytest.raises(StaleDataError) as exc_info:
            guard.validate_entry_or_raise("SPY", quote, trade, Decimal("500.00"), Decimal("1.00"))
        assert "POTENTIAL_HALT" in str(exc_info.value) or "STALE_PRICE" in str(exc_info.value)
