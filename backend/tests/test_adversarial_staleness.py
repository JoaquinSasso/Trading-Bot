"""Adversarial stress test suite for Milestone 4: Level 1 and Level 2 Staleness Logic.

Empirical verification covering:
1. Clock Jumps & State Machine Transitions:
   - Jumps at exact boundaries: 59s, 60s, 61s, 300s, 301s.
   - Exact state transitions: HEALTHY -> DEGRADED -> STALE.
   - Staged recovery on message arrival after degradation or staleness.
   - Massive forward clock jumps (10,000s) and backward jumps (NTP clock sync).
2. Rapid Feed Flapping & Connection Drops:
   - 500 continuous flap cycles (connect -> disconnect -> connect).
   - Validation that REST fallback flag and entry gating stay synchronized with state.
   - Reconnect recovery after violent disconnect loops.
   - Fail-closed gating during disconnect.
3. Non-Monotonic Message Arrival & Timestamp Anomalies:
   - Out-of-order message arrival regressing last_message_time (documenting empirical behavior).
   - Out-of-order quote/trade arrival in SymbolFreshnessMonitor.
   - Future timestamps (>1s rejected by entry pricing; slight <=1s allowed).
   - Historical epoch timestamps rejected.
4. Dynamic Threshold Calibration Edge Cases:
   - Empty sequence fallback (300.0s).
   - All zero gaps ([0.0] * 50 -> 180.0s).
   - Identical gaps below 180s, in-range, and above 600s.
   - Massive gaps (1,000,000s).
   - NaN, Inf, and negative interval filtering.
   - Bug reproduction: passing np.ndarray / pd.Series raises ValueError due to 'if not intervals:'.
5. Exchange Halt Status Transitions & Potential Halt Detection:
   - Full status cycle: T -> H -> P -> R -> Q -> T.
   - Case and whitespace insensitivity in halt codes.
   - Potential halt detection when feed goes silent beyond tau_symbol while in T status.
   - Cross-symbol isolation (halt on AAPL does not affect MSFT).
6. Strict Entry Pricing & Cross-Feed Anomaly Boundaries:
   - Exact 10.0 bps spread threshold boundary.
   - Crossed book (ask < bid) and non-positive prices fall-through.
   - Exact 2.0 * ATR divergence boundary.
   - Discard metrics tracker fidelity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import StaleDataError
from tbot.data.staleness import (
    AnomalyDetector,
    ExchangeHaltStatus,
    FeedHealthState,
    GlobalFeedMonitor,
    StalenessGuard,
    SymbolFreshnessMonitor,
    calibrate_freshness,
)
from tbot.data.types import PriceQuote, TradeQuote


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Reloj simulado fijado en horario regular de mercado (14:00 UTC = 10:00 AM ET)."""
    return SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))


# ============================================================================
# 1. PRUEBAS DE SALTOS TEMPORALES Y LÍMITES EXACTOS DE TRANSICIÓN
# ============================================================================


class TestAdversarialClockJumps:
    """Stress testing de saltos temporales y transiciones exactas de estado."""

    def test_clock_jumps_exact_boundaries(self, sim_clock: SimulatedClock) -> None:
        """Verifica las transiciones exactas: 59s, 60s, 61s, 300s, 301s.

        Invariantes:
        - <= 60.0s: HEALTHY
        - > 60.0s y <= 300.0s: DEGRADED
        - > 300.0s: STALE
        """
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY

        # Salto 1: +59.0s -> HEALTHY
        sim_clock.advance(timedelta(seconds=59.0))
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True
        assert monitor.is_fallback_active is False
        assert monitor.is_entry_allowed() == (True, "OK")

        # Salto 2: +1.0s (total 60.0s) -> HEALTHY (límite inclusivo exacto)
        sim_clock.advance(timedelta(seconds=1.0))
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True
        assert monitor.is_fallback_active is False
        assert monitor.is_entry_allowed() == (True, "OK")

        # Salto 3: +1.0s (total 61.0s) -> DEGRADED
        sim_clock.advance(timedelta(seconds=1.0))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_feed_healthy() is False
        assert monitor.is_fallback_active is True
        assert monitor.is_entry_allowed() == (False, "FEED_DEGRADED")

        # Salto 4: +239.0s (total 300.0s) -> DEGRADED (límite inclusivo exacto de degraded)
        sim_clock.advance(timedelta(seconds=239.0))
        assert monitor.state == FeedHealthState.DEGRADED
        assert monitor.is_feed_healthy() is False
        assert monitor.is_fallback_active is True
        assert monitor.is_entry_allowed() == (False, "FEED_DEGRADED")

        # Salto 5: +1.0s (total 301.0s) -> STALE
        sim_clock.advance(timedelta(seconds=1.0))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_feed_healthy() is False
        assert monitor.is_fallback_active is True
        assert monitor.is_entry_allowed() == (False, "FEED_STALE")

    def test_subsecond_boundary_precision(self, sim_clock: SimulatedClock) -> None:
        """Verifica la precisión sub-segundo en el umbral de 60s y 300s."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()

        # 59.999s -> HEALTHY
        sim_clock.advance(timedelta(seconds=59, microseconds=999000))
        assert monitor.state == FeedHealthState.HEALTHY

        # 60.000s -> HEALTHY
        sim_clock.advance(timedelta(microseconds=1000))
        assert monitor.state == FeedHealthState.HEALTHY

        # 60.001s -> DEGRADED
        sim_clock.advance(timedelta(microseconds=1000))
        assert monitor.state == FeedHealthState.DEGRADED

        # 299.999s total
        sim_clock.advance(timedelta(seconds=239, microseconds=998000))
        assert monitor.state == FeedHealthState.DEGRADED

        # 300.000s total
        sim_clock.advance(timedelta(microseconds=1000))
        assert monitor.state == FeedHealthState.DEGRADED

        # 300.001s total -> STALE
        sim_clock.advance(timedelta(microseconds=1000))
        assert monitor.state == FeedHealthState.STALE

    def test_massive_forward_jump_and_immediate_recovery(self, sim_clock: SimulatedClock) -> None:
        """Verifica salto masivo hacia el futuro (10,000 segundos) y recuperación instantánea."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY

        # Salto gigante de casi 3 horas
        sim_clock.advance(timedelta(seconds=10000))
        assert monitor.state == FeedHealthState.STALE
        assert monitor.is_fallback_active is True
        assert monitor.is_entry_allowed() == (False, "FEED_STALE")

        # Llega mensaje -> recuperación inmediata a HEALTHY
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False
        assert monitor.is_entry_allowed() == (True, "OK")

    def test_clock_backward_jump_handling(self, sim_clock: SimulatedClock) -> None:
        """Verifica que un retroceso de reloj (NTP sync / corrección temporal) no rompa el monitor."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()
        monitor.record_message(t_now)

        # Reloj retrocede 60 segundos
        sim_clock.set_time(t_now - timedelta(seconds=60))
        # El silencio es negativo (-60s <= 60s) -> permanece HEALTHY
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_feed_healthy() is True


# ============================================================================
# 2. PRUEBAS DE FLAPPING AGRESIVO, CAÍDAS DE CONEXIÓN Y RECUPERACIÓN
# ============================================================================


class TestAdversarialFeedFlapping:
    """Stress testing de oscilación rápida (flapping) y caídas de conexión."""

    def test_high_frequency_flapping_loop(self, sim_clock: SimulatedClock) -> None:
        """Ejecuta 500 ciclos continuos de flapping (mensaje -> desconexión -> mensaje)."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)

        for i in range(500):
            # 1. Llega mensaje -> HEALTHY
            monitor.record_message()
            assert monitor.state == FeedHealthState.HEALTHY
            assert monitor.is_fallback_active is False
            allowed, _ = monitor.is_entry_allowed()
            assert allowed is True

            sim_clock.advance(timedelta(milliseconds=20))

            # 2. Se corta la conexión -> DISCONNECTED
            monitor.record_disconnect(reason=f"flap_cycle_{i}")
            assert monitor.state == FeedHealthState.DISCONNECTED
            assert monitor.is_fallback_active is True
            allowed, reason = monitor.is_entry_allowed()
            assert allowed is False
            assert reason == "FEED_DISCONNECTED"

            sim_clock.advance(timedelta(milliseconds=20))

        # Recuperación final estable
        monitor.record_message()
        assert monitor.state == FeedHealthState.HEALTHY
        assert monitor.is_fallback_active is False
        assert monitor.is_entry_allowed() == (True, "OK")

    def test_evaluate_state_during_prolonged_disconnection(self, sim_clock: SimulatedClock) -> None:
        """Verifica que una desconexión explícita permanezca en DISCONNECTED sin importar el paso del tiempo."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        monitor.record_message()
        monitor.record_disconnect("socket dropped")

        # Pasa 1 minuto
        sim_clock.advance(timedelta(seconds=60))
        assert monitor.state == FeedHealthState.DISCONNECTED

        # Pasan 10 minutos
        sim_clock.advance(timedelta(seconds=600))
        assert monitor.state == FeedHealthState.DISCONNECTED

        # Pasa 1 día
        sim_clock.advance(timedelta(days=1))
        assert monitor.state == FeedHealthState.DISCONNECTED
        assert monitor.is_fallback_active is True

    def test_validate_entry_strictly_blocks_during_flapping_disconnects(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Verifica que StalenessGuard.validate_entry falle cerrado si el feed está desconectado."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)

        q = PriceQuote(
            symbol="SPY",
            bid=Decimal("400.00"),
            ask=Decimal("400.02"),
            bid_size=100,
            ask_size=100,
            timestamp=sim_clock.now(),
        )
        t = TradeQuote(
            symbol="SPY",
            price=Decimal("400.01"),
            size=100,
            timestamp=sim_clock.now(),
        )

        guard.record_message()
        guard.record_quote("SPY", q)
        guard.record_trade("SPY", t)

        price, ok, reason = guard.validate_entry("SPY", q, t, sip_close=Decimal("400.00"), atr_5m=Decimal("1.00"))
        assert ok is True
        assert price == Decimal("400.01")

        # Flap: desconexión repentina
        guard.record_disconnect("network reset")
        price, ok, reason = guard.validate_entry("SPY", q, t, sip_close=Decimal("400.00"), atr_5m=Decimal("1.00"))
        assert ok is False
        assert price is None
        assert reason == "FEED_DISCONNECTED"

        # validate_entry_or_raise debe levantar StaleDataError
        with pytest.raises(StaleDataError, match="FEED_DISCONNECTED"):
            guard.validate_entry_or_raise("SPY", q, t, sip_close=Decimal("400.00"), atr_5m=Decimal("1.00"))


# ============================================================================
# 3. PRUEBAS DE LLEGADA NO MONOTÓNICA DE MENSAJES Y ANOMALÍAS TEMPORALES
# ============================================================================


class TestAdversarialNonMonotonicArrivals:
    """Stress testing de mensajes fuera de orden, timestamps futuros e históricos."""

    def test_out_of_order_message_in_global_feed_monitor(self, sim_clock: SimulatedClock) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: Mensajes fuera de orden no retroceden el cursor de liveness."""
        monitor = GlobalFeedMonitor(clock=sim_clock, enforce_market_hours=False)
        t_recent = sim_clock.now()
        monitor.record_message(t_recent)
        assert monitor.state == FeedHealthState.HEALTHY

        # Llega un mensaje retrasado / retransmitido con timestamp de hace 400 segundos
        t_old = t_recent - timedelta(seconds=400)
        monitor.record_message(t_old)

        # Monotonía: last_message_time no retrocede a t_old
        assert monitor.last_message_time == t_recent
        assert monitor.state == FeedHealthState.HEALTHY

    def test_out_of_order_quotes_in_symbol_freshness_monitor(self, sim_clock: SimulatedClock) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: Cotizaciones fuera de orden no sobreescriben datos frescos."""
        monitor = SymbolFreshnessMonitor(clock=sim_clock, default_tau_seconds=300.0)
        t_now = sim_clock.now()

        # Cotización fresca y reciente a $150.00
        q_fresh = PriceQuote(
            symbol="AAPL",
            bid=Decimal("150.00"),
            ask=Decimal("150.05"),
            bid_size=100,
            ask_size=100,
            timestamp=t_now,
        )
        monitor.record_quote("AAPL", q_fresh)
        is_fresh, reason = monitor.check_symbol_freshness("AAPL")
        assert is_fresh is True
        assert reason == "OK"

        # Llega una cotización desfasada de hace 15 minutos a $140.00
        q_delayed = PriceQuote(
            symbol="AAPL",
            bid=Decimal("140.00"),
            ask=Decimal("140.05"),
            bid_size=100,
            ask_size=100,
            timestamp=t_now - timedelta(minutes=15),
        )
        monitor.record_quote("AAPL", q_delayed)

        # Monotonía: la cotización fresca se preserva y no pasa a POTENTIAL_HALT
        state = monitor._symbols["AAPL"]
        assert state.last_quote_time == t_now
        assert state.last_quote.bid == Decimal("150.00")
        is_fresh, reason = monitor.check_symbol_freshness("AAPL")
        assert is_fresh is True
        assert reason == "OK"


    def test_future_timestamps_in_entry_price_selector(self, sim_clock: SimulatedClock) -> None:
        """Verifica la respuesta ante timestamps en el futuro en get_entry_price.

        El selector permite una tolerancia leve de hasta 1s al futuro para tolerar desvíos de reloj.
        Timestamps > 1s en el futuro son rechazados con STALE_PRICE.
        """
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        # Caso 1: 500ms en el futuro (dentro del margen de 1s)
        q_slight = PriceQuote(
            symbol="AAPL",
            bid=Decimal("150.00"),
            ask=Decimal("150.05"),
            bid_size=100,
            ask_size=100,
            timestamp=t_now + timedelta(milliseconds=500),
        )
        t_slight = TradeQuote(
            symbol="AAPL",
            price=Decimal("150.02"),
            size=100,
            timestamp=t_now + timedelta(milliseconds=500),
        )
        price, mode = guard.get_entry_price(q_slight, t_slight)
        assert price == Decimal("150.025")
        assert mode == "MIDPOINT"

        # Caso 2: 5 segundos en el futuro (fuera de tolerancia)
        q_future = PriceQuote(
            symbol="AAPL",
            bid=Decimal("150.00"),
            ask=Decimal("150.05"),
            bid_size=100,
            ask_size=100,
            timestamp=t_now + timedelta(seconds=5),
        )
        t_future = TradeQuote(
            symbol="AAPL",
            price=Decimal("150.02"),
            size=100,
            timestamp=t_now + timedelta(seconds=5),
        )
        price, mode = guard.get_entry_price(q_future, t_future)
        assert price is None
        assert mode == "STALE_PRICE"

    def test_ancient_historical_timestamps(self, sim_clock: SimulatedClock) -> None:
        """Verifica que timestamps de la época Unix (1970) o días atrás sean rechazados inmediatamente."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        q_ancient = PriceQuote(
            symbol="AAPL",
            bid=Decimal("150.00"),
            ask=Decimal("150.05"),
            bid_size=100,
            ask_size=100,
            timestamp=datetime(1970, 1, 1, tzinfo=UTC),
        )
        t_ancient = TradeQuote(
            symbol="AAPL",
            price=Decimal("150.02"),
            size=100,
            timestamp=datetime(1970, 1, 1, tzinfo=UTC),
        )

        price, mode = guard.get_entry_price(q_ancient, t_ancient)
        assert price is None
        assert mode == "STALE_PRICE"


# ============================================================================
# 4. PRUEBAS DE CALIBRACIÓN DINÁMICA DE UMBRALES (EDGE CASES & BUGS)
# ============================================================================


class TestAdversarialThresholdCalibration:
    """Stress testing de calibrate_freshness y detección de fallos empíricos."""

    def test_calibrate_freshness_clean_edge_cases(self) -> None:
        """Verifica casos de borde válidos pasados como listas estándar de Python."""
        # 1. Lista vacía -> fallback 300.0s
        assert calibrate_freshness([]) == 300.0

        # 2. Lista de solo ceros -> p99 es 0.0 -> clamped a 180.0s
        assert calibrate_freshness([0.0] * 100) == 180.0

        # 3. Intervalos idénticos sub-mínimo (10.0s) -> clamped a 180.0s
        assert calibrate_freshness([10.0] * 100) == 180.0

        # 4. Intervalos idénticos dentro del rango (240.0s) -> 240.0s
        assert calibrate_freshness([240.0] * 100) == 240.0

        # 5. Intervalos idénticos super-máximo (900.0s) -> clamped a 600.0s
        assert calibrate_freshness([900.0] * 100) == 600.0

        # 6. Gaps masivos de 1,000,000s -> clamped a 600.0s
        assert calibrate_freshness([1_000_000.0] * 10) == 600.0

        # 7. Filtro de NaN, infinitos y negativos
        assert (
            calibrate_freshness([float("nan"), float("inf"), -999.0, 350.0])
            == 350.0
        )

        # 8. Todos los valores no válidos -> fallback 300.0s
        assert (
            calibrate_freshness([float("nan"), float("inf"), -1.0, -100.0])
            == 300.0
        )

    def test_calibrate_freshness_numpy_and_pandas_type_vulnerability(self) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN: calibrate_freshness soporta NumPy ndarrays y Pandas Series sin ValueError."""
        # Test 1: np.ndarray con múltiples elementos
        arr_multi = np.array([10.0, 20.0, 30.0])
        assert calibrate_freshness(arr_multi) == 180.0

        # Test 2: pd.Series
        ser = pd.Series([10.0, 20.0, 30.0])
        assert calibrate_freshness(ser) == 180.0

        # Test 3: np.ndarray vacío
        arr_empty = np.array([])
        assert calibrate_freshness(arr_empty) == 300.0



# ============================================================================
# 5. PRUEBAS DE TRANSICIONES DE ESTADO DE HALT Y DETECCIÓN DE HALT POTENCIAL
# ============================================================================


class TestAdversarialExchangeHaltAndPotentialHalt:
    """Stress testing de la secuencia de halts de bolsa y potencial halt por silencio de símbolo."""

    def test_exchange_halt_status_full_cycle(self, sim_clock: SimulatedClock) -> None:
        """Verifica la secuencia completa de estados de bolsa: T -> H -> P -> R -> Q -> T."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()

        t_now = sim_clock.now()
        q = PriceQuote(
            symbol="AAPL",
            bid=Decimal("150.00"),
            ask=Decimal("150.02"),
            bid_size=100,
            ask_size=100,
            timestamp=t_now,
        )
        t = TradeQuote(
            symbol="AAPL",
            price=Decimal("150.01"),
            size=100,
            timestamp=t_now,
        )
        guard.record_quote("AAPL", q)
        guard.record_trade("AAPL", t)

        # 1. Trading normal (T)
        guard.record_exchange_status("AAPL", ExchangeHaltStatus.TRADING)
        fresh, reason = guard.check_symbol_freshness("AAPL")
        assert fresh is True
        assert reason == "OK"
        price, ok, _ = guard.validate_entry("AAPL", q, t, sip_close=Decimal("150.00"), atr_5m=Decimal("1.00"))
        assert ok is True

        # 2. Halted (H)
        guard.record_exchange_status("AAPL", ExchangeHaltStatus.HALTED)
        fresh, reason = guard.check_symbol_freshness("AAPL")
        assert fresh is False
        assert reason == "SYMBOL_HALTED"
        price, ok, v_reason = guard.validate_entry("AAPL", q, t, sip_close=Decimal("150.00"), atr_5m=Decimal("1.00"))
        assert ok is False
        assert v_reason == "SYMBOL_HALTED"

        # 3. Paused (P)
        guard.record_exchange_status("AAPL", ExchangeHaltStatus.PAUSED)
        fresh, reason = guard.check_symbol_freshness("AAPL")
        assert fresh is False
        assert reason == "SYMBOL_HALTED"
        price, ok, v_reason = guard.validate_entry("AAPL", q, t, sip_close=Decimal("150.00"), atr_5m=Decimal("1.00"))
        assert ok is False
        assert v_reason == "SYMBOL_HALTED"

        # 4. Resumed (R)
        guard.record_exchange_status("AAPL", ExchangeHaltStatus.RESUMED)
        fresh, reason = guard.check_symbol_freshness("AAPL")
        assert fresh is True
        assert reason == "OK"
        price, ok, _ = guard.validate_entry("AAPL", q, t, sip_close=Decimal("150.00"), atr_5m=Decimal("1.00"))
        assert ok is True

        # 5. Quote Only (Q)
        guard.record_exchange_status("AAPL", ExchangeHaltStatus.QUOTE_ONLY)
        fresh, reason = guard.check_symbol_freshness("AAPL")
        assert fresh is False
        assert reason == "SYMBOL_HALTED"
        price, ok, v_reason = guard.validate_entry("AAPL", q, t, sip_close=Decimal("150.00"), atr_5m=Decimal("1.00"))
        assert ok is False
        assert v_reason == "SYMBOL_HALTED"

        # 6. Reanudación completa a Trading (T)
        guard.record_exchange_status("AAPL", ExchangeHaltStatus.TRADING)
        fresh, reason = guard.check_symbol_freshness("AAPL")
        assert fresh is True
        assert reason == "OK"
        price, ok, _ = guard.validate_entry("AAPL", q, t, sip_close=Decimal("150.00"), atr_5m=Decimal("1.00"))
        assert ok is True

    def test_halt_status_code_normalization(self, sim_clock: SimulatedClock) -> None:
        """Verifica que códigos con minúsculas y espacios se normalicen correctamente."""
        monitor = SymbolFreshnessMonitor(clock=sim_clock)
        monitor.record_exchange_status("MSFT", "  h  ")
        assert monitor._symbols["MSFT"].is_halted is True
        assert monitor._symbols["MSFT"].halt_code == "H"

        monitor.record_exchange_status("MSFT", " t \n")
        assert monitor._symbols["MSFT"].is_halted is False
        assert monitor._symbols["MSFT"].halt_code == "T"

    def test_potential_halt_detection_on_symbol_silence(self, sim_clock: SimulatedClock) -> None:
        """Verifica que el silencio prolongado de un símbolo active POTENTIAL_HALT aun si la bolsa reporta 'T'."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.set_symbol_tau("NVDA", timedelta(seconds=200))
        t_start = sim_clock.now()

        q = PriceQuote(
            symbol="NVDA",
            bid=Decimal("100.00"),
            ask=Decimal("100.05"),
            bid_size=100,
            ask_size=100,
            timestamp=t_start,
        )
        t = TradeQuote(
            symbol="NVDA",
            price=Decimal("100.02"),
            size=100,
            timestamp=t_start,
        )
        guard.record_message(t_start)
        guard.record_quote("NVDA", q)
        guard.record_trade("NVDA", t)
        guard.record_exchange_status("NVDA", "T")

        # Tiempo avanza 199s (dentro de tau=200s)
        sim_clock.advance(timedelta(seconds=199))
        guard.record_message(sim_clock.now())  # Mantiene feed global HEALTHY
        fresh, reason = guard.check_symbol_freshness("NVDA")
        assert fresh is True
        assert reason == "OK"

        # Tiempo avanza 2 segundos más (total 201s > 200s)
        sim_clock.advance(timedelta(seconds=2))
        guard.record_message(sim_clock.now())  # Feed global sigue HEALTHY
        fresh, reason = guard.check_symbol_freshness("NVDA")
        assert fresh is False
        assert reason == "POTENTIAL_HALT"

        # validate_entry debe bloquearse por POTENTIAL_HALT
        price, ok, v_reason = guard.validate_entry("NVDA", q, t, sip_close=Decimal("100.00"), atr_5m=Decimal("1.00"))
        assert ok is False
        assert price is None
        assert v_reason == "POTENTIAL_HALT"

    def test_symbol_isolation_under_halt(self, sim_clock: SimulatedClock) -> None:
        """Verifica que el halt de un símbolo no afecte la operativa de otro."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()

        # AAPL y MSFT con argumentos nombrados
        q_aapl = PriceQuote(symbol="AAPL", bid=Decimal("150.00"), ask=Decimal("150.02"), timestamp=t_now, bid_size=100, ask_size=100)
        t_aapl = TradeQuote(symbol="AAPL", price=Decimal("150.01"), timestamp=t_now, size=100)

        q_msft = PriceQuote(symbol="MSFT", bid=Decimal("300.00"), ask=Decimal("300.05"), timestamp=t_now, bid_size=100, ask_size=100)
        t_msft = TradeQuote(symbol="MSFT", price=Decimal("300.02"), timestamp=t_now, size=100)

        guard.record_quote("AAPL", q_aapl)
        guard.record_trade("AAPL", t_aapl)
        guard.record_quote("MSFT", q_msft)
        guard.record_trade("MSFT", t_msft)

        # Halt en AAPL solamente
        guard.record_exchange_status("AAPL", "H")
        guard.record_exchange_status("MSFT", "T")

        # AAPL bloqueado
        _, ok_aapl, reason_aapl = guard.validate_entry("AAPL", q_aapl, t_aapl, Decimal("150.00"), Decimal("1.00"))
        assert ok_aapl is False
        assert reason_aapl == "SYMBOL_HALTED"

        # MSFT aprobado
        price_msft, ok_msft, reason_msft = guard.validate_entry(
            "MSFT", q_msft, t_msft, Decimal("300.00"), Decimal("1.00")
        )
        assert ok_msft is True
        assert price_msft == Decimal("300.025")
        assert reason_msft == "OK"


# ============================================================================
# 6. PRUEBAS DE LÍMITES EXACTOS DE PRECIO DE ENTRADA Y ANOMALÍAS CRUZADAS
# ============================================================================


class TestAdversarialPricingAndAnomalyLimits:
    """Stress testing de umbrales exactos de spread y detección de anomalías cruzadas."""

    def test_exact_10bps_spread_boundary(self, sim_clock: SimulatedClock) -> None:
        """Verifica que <= 10.00000 bps seleccione MIDPOINT y 10.00001 bps salte a LAST_TRADE."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        # Caso A: spread de exactamente 10.0 bps
        # bid=99.95, ask=100.05 -> diff=0.10, mid=100.00 -> spread_bps = (0.10 / 100.00) * 10000 = 10.0 bps
        q_exact_10 = PriceQuote(symbol="TEST", bid=Decimal("99.95"), ask=Decimal("100.05"), timestamp=t_now, bid_size=100, ask_size=100)
        t = TradeQuote(symbol="TEST", price=Decimal("99.98"), timestamp=t_now, size=100)

        price, mode = guard.get_entry_price(q_exact_10, t)
        assert price == Decimal("100.00")
        assert mode == "MIDPOINT"

        # Caso B: spread infinitesimalmente superior a 10.0 bps
        # bid=99.9499, ask=100.0501 -> diff=0.1002, mid=100.00 -> spread_bps = 10.02 bps
        q_above_10 = PriceQuote(symbol="TEST", bid=Decimal("99.9499"), ask=Decimal("100.0501"), timestamp=t_now, bid_size=100, ask_size=100)
        price, mode = guard.get_entry_price(q_above_10, t)
        assert price == Decimal("99.98")
        assert mode == "LAST_TRADE"

    def test_crossed_and_invalid_order_books(self, sim_clock: SimulatedClock) -> None:
        """Verifica que libros cruzados (ask < bid) o precios negativos caigan al trade o descarten."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()
        t = TradeQuote(symbol="TEST", price=Decimal("100.00"), timestamp=t_now, size=100)

        # Libro cruzado: ask < bid
        q_crossed = PriceQuote(symbol="TEST", bid=Decimal("105.00"), ask=Decimal("95.00"), timestamp=t_now, bid_size=100, ask_size=100)
        price, mode = guard.get_entry_price(q_crossed, t)
        # El quote cruzado debe ignorarse; se utiliza el trade si está fresco
        assert price == Decimal("100.00")
        assert mode == "LAST_TRADE"

        # Bid negativo
        q_neg = PriceQuote(symbol="TEST", bid=Decimal("-10.00"), ask=Decimal("100.00"), timestamp=t_now, bid_size=100, ask_size=100)
        price, mode = guard.get_entry_price(q_neg, t)
        assert price == Decimal("100.00")
        assert mode == "LAST_TRADE"

    def test_exact_2_atr_divergence_boundary(self) -> None:
        """Verifica la frontera matemática exacta: |P_IEX - C_SIP| > 2 * ATR.

        - diff == 2 * ATR -> APROBADO (la especificación requiere '>', no '>=')
        - diff == 2 * ATR + 0.0001 -> PRICE_ANOMALY
        """
        detector = AnomalyDetector()
        sip_close = Decimal("100.00")
        atr = Decimal("2.50")  # 2 * ATR = 5.00

        # Exactamente 2 * ATR (diff = 5.00, P_IEX = 105.00)
        ok, reason = detector.check_price_anomaly(Decimal("105.00"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Exactamente 2 * ATR hacia abajo (diff = 5.00, P_IEX = 95.00)
        ok, reason = detector.check_price_anomaly(Decimal("95.00"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Supera ligeramente 2 * ATR (diff = 5.001, P_IEX = 105.001)
        ok, reason = detector.check_price_anomaly(Decimal("105.001"), sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"

    def test_discard_metrics_exact_counting(self, sim_clock: SimulatedClock) -> None:
        """Verifica que el contador de métricas no genere falsos positivos ni desvíos."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()

        # Inicialmente en 0
        assert guard.discard_counts["STALE_PRICE"] == 0
        assert guard.discard_counts["PRICE_ANOMALY"] == 0

        # 1. Operación normal aprobada -> no incrementa nada
        q_ok = PriceQuote(symbol="SPY", bid=Decimal("400.00"), ask=Decimal("400.02"), timestamp=t_now, bid_size=100, ask_size=100)
        t_ok = TradeQuote(symbol="SPY", price=Decimal("400.01"), timestamp=t_now, size=100)
        guard.validate_entry("SPY", q_ok, t_ok, Decimal("400.00"), Decimal("1.00"))
        assert guard.discard_counts["STALE_PRICE"] == 0
        assert guard.discard_counts["PRICE_ANOMALY"] == 0

        # 2. Descarte por STALE_PRICE (quote y trade de hace 1 hora)
        q_stale = PriceQuote(symbol="SPY", bid=Decimal("400.00"), ask=Decimal("400.02"), timestamp=t_now - timedelta(hours=1), bid_size=100, ask_size=100)
        t_stale = TradeQuote(symbol="SPY", price=Decimal("400.01"), timestamp=t_now - timedelta(hours=1), size=100)
        guard.get_entry_price(q_stale, t_stale)
        assert guard.discard_counts["STALE_PRICE"] == 1
        assert guard.discard_counts["PRICE_ANOMALY"] == 0

        # 3. Descarte por PRICE_ANOMALY (divergencia de $10 con ATR=1)
        guard.check_price_anomaly(Decimal("410.00"), Decimal("400.00"), Decimal("1.00"))
        assert guard.discard_counts["STALE_PRICE"] == 1
        assert guard.discard_counts["PRICE_ANOMALY"] == 1
