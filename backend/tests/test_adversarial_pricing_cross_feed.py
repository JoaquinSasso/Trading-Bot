"""Adversarial stress test suite for Milestone 4: Cross-Feed Anomaly & Pricing Challenger.

Empirical verification covering:
1. Exact Boundary Checks:
   - diff == 2.0 * ATR vs 2.0000001 * ATR vs 1.9999999 * ATR (upward & downward).
   - High-precision Decimal boundaries.
2. Extreme Inputs:
   - Zero ATR, negative ATR (percentage fallback vs fail_closed).
   - Negative prices, zero bid/ask, inverted bid/ask (bid > ask / crossed book).
3. High-Precision & Type Safety (including bug reproductions):
   - High-precision (20-digit) Decimal precision preservation.
   - Empirical bug reproduction: Context precision overflow (>28 digits) produces false-positive PRICE_ANOMALY.
   - Mixed float/int/Decimal operations.
   - Float NaN and Inf handling.
   - Empirical bug reproduction: Decimal('NaN') and string 'NaN' crash AnomalyDetector with decimal.InvalidOperation.
   - Empirical bug reproduction: Decimal('Infinity') in TradeQuote selected as valid LAST_TRADE entry price.
4. Forward-Fill Invariant Enforcement & Type Robustness:
   - Synthetic bar dicts from create_synthetic_bar cannot bypass entry pricing in get_entry_price.
   - Empirical bug reproduction: passing dict to validate_entry crashes with AttributeError instead of fail-closed.
   - Forward-fill synthetic bars cannot bypass entry pricing.
5. Discard Metrics Tracking & Thread-Safety:
   - Single-threaded exact count fidelity for STALE_PRICE and PRICE_ANOMALY.
   - Empirical bug reproduction: multithreaded concurrent discards suffer from race conditions on discard_counts.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import StaleDataError
from tbot.data.staleness import (
    AnomalyDetector,
    StalenessGuard,
)
from tbot.data.types import PriceQuote, TradeQuote
from tbot.indicators.pure import create_synthetic_bar


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Reloj simulado en horario regular de mercado (14:00 UTC)."""
    return SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))


# ============================================================================
# 1. EXACT BOUNDARY CHECKS
# ============================================================================


class TestExactBoundaryChecks:
    """Stress testing de la frontera matemática exacta |P_IEX - C_SIP| > 2 * ATR."""

    def test_upward_divergence_exact_boundary(self) -> None:
        """Verifica la frontera exacta 2.0 * ATR hacia arriba.

        Especificación: condición de anomalía es estricta (diff > 2.0 * ATR).
        - diff == 2.0 * ATR -> APROBADO (True, 'OK')
        - diff == 2.0000001 * ATR -> RECHAZADO (False, 'PRICE_ANOMALY')
        - diff == 1.9999999 * ATR -> APROBADO (True, 'OK')
        """
        detector = AnomalyDetector()
        sip_close = Decimal("100.00")
        atr = Decimal("2.00")  # 2 * ATR = 4.00

        # Exactamente 2.0 * ATR: diff = 4.00, P_IEX = 104.00
        ok, reason = detector.check_price_anomaly(Decimal("104.00"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Supera por 0.0000001 * ATR (diff = 4.0000002): P_IEX = 104.0000002
        ok, reason = detector.check_price_anomaly(Decimal("104.0000002"), sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"

        # Por debajo por 0.0000001 * ATR (diff = 3.9999998): P_IEX = 103.9999998
        ok, reason = detector.check_price_anomaly(Decimal("103.9999998"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

    def test_downward_divergence_exact_boundary(self) -> None:
        """Verifica la frontera exacta 2.0 * ATR hacia abajo (flash drop).

        - diff == 2.0 * ATR -> APROBADO (True, 'OK')
        - diff == 2.0000001 * ATR -> RECHAZADO (False, 'PRICE_ANOMALY')
        - diff == 1.9999999 * ATR -> APROBADO (True, 'OK')
        """
        detector = AnomalyDetector()
        sip_close = Decimal("100.00")
        atr = Decimal("2.00")  # 2 * ATR = 4.00

        # Exactamente 2.0 * ATR abajo: diff = 4.00, P_IEX = 96.00
        ok, reason = detector.check_price_anomaly(Decimal("96.00"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Supera 2.0 * ATR abajo (diff = 4.0000002): P_IEX = 95.9999998
        ok, reason = detector.check_price_anomaly(Decimal("95.9999998"), sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"

        # Por debajo de 2.0 * ATR abajo (diff = 3.9999998): P_IEX = 96.0000002
        ok, reason = detector.check_price_anomaly(Decimal("96.0000002"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

    def test_boundary_with_custom_multiplier(self) -> None:
        """Verifica que el multiplicador configurable funcione exactamente en la frontera."""
        detector = AnomalyDetector(multiplier=Decimal("3"))
        sip_close = Decimal("50.00")
        atr = Decimal("1.50")  # 3 * ATR = 4.50

        # diff = 4.50 -> APROBADO
        ok, reason = detector.check_price_anomaly(Decimal("54.50"), sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # diff = 4.5000001 -> RECHAZADO
        ok, reason = detector.check_price_anomaly(Decimal("54.5000001"), sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"


# ============================================================================
# 2. EXTREME INPUTS
# ============================================================================


class TestExtremeInputs:
    """Stress testing de entradas extremas: zero ATR, precios negativos, libros invertidos."""

    def test_zero_and_negative_atr_percentage_fallback(self) -> None:
        """Verifica el comportamiento de ATR cero o negativo en modo 'percentage' (default)."""
        detector = AnomalyDetector(fallback_mode="percentage", fallback_pct=Decimal("0.02"))
        sip_close = Decimal("100.00")  # umbral fallback = 2% de 100 = 2.00

        # Zero ATR: diff = 1.50 <= 2.00 -> OK
        ok, reason = detector.check_price_anomaly(Decimal("101.50"), sip_close, Decimal("0.00"))
        assert ok is True
        assert reason == "OK"

        # Zero ATR: diff = 2.50 > 2.00 -> PRICE_ANOMALY
        ok, reason = detector.check_price_anomaly(Decimal("102.50"), sip_close, Decimal("0.00"))
        assert ok is False
        assert reason == "PRICE_ANOMALY"

        # Negative ATR: diff = 1.50 <= 2.00 -> OK
        ok, reason = detector.check_price_anomaly(Decimal("101.50"), sip_close, Decimal("-1.00"))
        assert ok is True
        assert reason == "OK"

        # Negative ATR: diff = 2.50 > 2.00 -> PRICE_ANOMALY
        ok, reason = detector.check_price_anomaly(Decimal("102.50"), sip_close, Decimal("-5.00"))
        assert ok is False
        assert reason == "PRICE_ANOMALY"

    def test_zero_and_negative_atr_fail_closed(self) -> None:
        """Verifica el comportamiento de ATR cero o negativo en modo 'fail_closed'."""
        detector = AnomalyDetector(fallback_mode="fail_closed")
        sip_close = Decimal("100.00")

        # Zero ATR -> MISSING_ATR
        ok, reason = detector.check_price_anomaly(Decimal("100.50"), sip_close, Decimal("0.00"))
        assert ok is False
        assert reason == "MISSING_ATR"

        # Negative ATR -> MISSING_ATR
        ok, reason = detector.check_price_anomaly(Decimal("100.50"), sip_close, Decimal("-0.50"))
        assert ok is False
        assert reason == "MISSING_ATR"

    def test_negative_and_zero_prices_in_anomaly_detector(self) -> None:
        """Verifica que precios no positivos sean rechazados con INVALID_PRICE."""
        detector = AnomalyDetector()
        atr = Decimal("1.00")

        # iex_price negativo
        ok, reason = detector.check_price_anomaly(Decimal("-10.00"), Decimal("100.00"), atr)
        assert ok is False
        assert reason == "INVALID_PRICE"

        # sip_close negativo
        ok, reason = detector.check_price_anomaly(Decimal("100.00"), Decimal("-5.00"), atr)
        assert ok is False
        assert reason == "INVALID_PRICE"

        # iex_price cero
        ok, reason = detector.check_price_anomaly(Decimal("0.00"), Decimal("100.00"), atr)
        assert ok is False
        assert reason == "INVALID_PRICE"

        # sip_close cero
        ok, reason = detector.check_price_anomaly(Decimal("100.00"), Decimal("0.00"), atr)
        assert ok is False
        assert reason == "INVALID_PRICE"

    def test_inverted_bid_ask_in_entry_pricing(self, sim_clock: SimulatedClock) -> None:
        """Verifica que libros invertidos/cruzados (bid > ask) no puedan seleccionar midpoint."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        # Libro invertido: bid = 105.00, ask = 95.00
        q_inverted = PriceQuote(
            symbol="XYZ",
            bid=Decimal("105.00"),
            ask=Decimal("95.00"),
            timestamp=t_now,
            bid_size=100,
            ask_size=100,
        )

        # Caso A: Trade fresco disponible -> recurre al trade
        t_fresh = TradeQuote(symbol="XYZ", price=Decimal("100.00"), timestamp=t_now, size=100)
        price, mode = guard.get_entry_price(q_inverted, t_fresh)
        assert price == Decimal("100.00")
        assert mode == "LAST_TRADE"

        # Caso B: Trade también stale -> descarta con STALE_PRICE
        t_stale = TradeQuote(
            symbol="XYZ",
            price=Decimal("100.00"),
            timestamp=t_now - timedelta(hours=1),
            size=100,
        )
        price, mode = guard.get_entry_price(q_inverted, t_stale)
        assert price is None
        assert mode == "STALE_PRICE"

    def test_zero_bid_and_zero_ask_in_entry_pricing(self, sim_clock: SimulatedClock) -> None:
        """Verifica que cotizaciones con bid=0 o ask=0 sean descartadas del midpoint."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()
        t_fresh = TradeQuote(symbol="XYZ", price=Decimal("50.00"), timestamp=t_now, size=100)

        # bid = 0, ask = 50
        q_zero_bid = PriceQuote(
            symbol="XYZ",
            bid=Decimal("0.00"),
            ask=Decimal("50.00"),
            timestamp=t_now,
            bid_size=0,
            ask_size=100,
        )
        price, mode = guard.get_entry_price(q_zero_bid, t_fresh)
        assert price == Decimal("50.00")
        assert mode == "LAST_TRADE"

        # bid = 50, ask = 0 (cruzado y zero)
        q_zero_ask = PriceQuote(
            symbol="XYZ",
            bid=Decimal("50.00"),
            ask=Decimal("0.00"),
            timestamp=t_now,
            bid_size=100,
            ask_size=0,
        )
        price, mode = guard.get_entry_price(q_zero_ask, t_fresh)
        assert price == Decimal("50.00")
        assert mode == "LAST_TRADE"

        # bid = 0, ask = 0
        q_both_zero = PriceQuote(
            symbol="XYZ",
            bid=Decimal("0.00"),
            ask=Decimal("0.00"),
            timestamp=t_now,
            bid_size=0,
            ask_size=0,
        )
        price, mode = guard.get_entry_price(q_both_zero, t_fresh)
        assert price == Decimal("50.00")
        assert mode == "LAST_TRADE"


# ============================================================================
# 3. HIGH PRECISION, TYPE SAFETY & BUG REPRODUCTIONS
# ============================================================================


class TestHighPrecisionAndTypeSafety:
    """Stress testing de alta precisión, interoperabilidad de tipos y detección de vulnerabilidades."""

    def test_20_digit_decimal_precision(self) -> None:
        """Verifica que Decimals de alta precisión (hasta 20 dígitos) mantengan exactitud matemática."""
        detector = AnomalyDetector()
        sip_close = Decimal("100.1234567890123456")
        atr = Decimal("1.0000000000000001")
        threshold = Decimal("2") * atr

        # Exactamente en la frontera
        p_iex_boundary = sip_close + threshold
        ok, reason = detector.check_price_anomaly(p_iex_boundary, sip_close, atr)
        assert ok is True
        assert reason == "OK"

        # Un infinitesimal por encima
        p_iex_above = p_iex_boundary + Decimal("0.0000000000000001")
        ok, reason = detector.check_price_anomaly(p_iex_above, sip_close, atr)
        assert ok is False
        assert reason == "PRICE_ANOMALY"

    def test_bug_reproduction_decimal_context_precision_boundary_drift(self) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN:
        Con la precisión elevada (localcontext ctx.prec = 50), las operaciones con más de
        28 dígitos significativos no sufren de redondeo espurio en la frontera exacta.
        """
        detector = AnomalyDetector()
        sip_close = Decimal("100.1234567890123456789012345678")
        atr = Decimal("1.0000000000000000000000000001")
        threshold = Decimal("2") * atr
        p_iex_boundary = sip_close + threshold

        # Verificación: exactamente en el umbral ya no genera falso positivo PRICE_ANOMALY
        ok, reason = detector.check_price_anomaly(p_iex_boundary, sip_close, atr)
        assert ok is True
        assert reason == "OK"


    def test_mixed_numeric_types_interoperability(self) -> None:
        """Verifica que ints, floats estándar y Decimals interoperen sin excepciones."""
        detector = AnomalyDetector()

        # float IEX, int SIP close, Decimal ATR
        ok, reason = detector.check_price_anomaly(101.5, 100, Decimal("1.0"))
        assert ok is True
        assert reason == "OK"

        # int IEX, float SIP close, float ATR
        ok, reason = detector.check_price_anomaly(105, 100.0, 1.0)
        assert ok is False
        assert reason == "PRICE_ANOMALY"

    def test_float_nan_and_inf_safe_handling(self) -> None:
        """Verifica que floats NaN e Inf sean detectados y no provoquen caídas."""
        detector = AnomalyDetector()

        # float NaN en IEX price -> MISSING_PRICE_DATA
        ok, reason = detector.check_price_anomaly(float("nan"), 100.0, 1.0)
        assert ok is False
        assert reason == "MISSING_PRICE_DATA"

        # float Inf en SIP close -> MISSING_PRICE_DATA
        ok, reason = detector.check_price_anomaly(100.0, float("inf"), 1.0)
        assert ok is False
        assert reason == "MISSING_PRICE_DATA"

        # float NaN en ATR con fallback percentage -> 2% de 100 = 2.0
        ok, reason = detector.check_price_anomaly(101.0, 100.0, float("nan"))
        assert ok is True
        assert reason == "OK"

    def test_bug_reproduction_decimal_nan_unhandled_crash(self) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN:
        Decimal('NaN'), string 'NaN', 'sNaN' son saneados limpiamente por _to_decimal a None.
        AnomalyDetector retorna (False, 'MISSING_PRICE_DATA') o (False, 'MISSING_ATR')
        sin lanzar excepciones no controladas.
        """
        detector = AnomalyDetector()

        # 1. Decimal('NaN') como iex_price retorna MISSING_PRICE_DATA
        ok, reason = detector.check_price_anomaly(Decimal("NaN"), Decimal("100"), Decimal("2"))
        assert ok is False
        assert reason == "MISSING_PRICE_DATA"

        # 2. Decimal('NaN') como atr_5m en modo fail_closed retorna MISSING_ATR
        detector_fc = AnomalyDetector(fallback_mode="fail_closed")
        ok, reason = detector_fc.check_price_anomaly(Decimal("100"), Decimal("100"), Decimal("NaN"))
        assert ok is False
        assert reason == "MISSING_ATR"

        # 3. String 'NaN' también es saneado a None sin arrojar excepción
        ok, reason = detector.check_price_anomaly("NaN", "100", "2")
        assert ok is False
        assert reason == "MISSING_PRICE_DATA"

    def test_bug_reproduction_decimal_infinity_bypasses(self, sim_clock: SimulatedClock) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN:
        1. TradeQuote(price=Decimal('Infinity')) es rechazado por get_entry_price (STALE_PRICE).
        2. Decimal('Infinity') en atr_5m es saneado por _to_decimal a None, evitando que una divergencia
           anómala pase inadvertida.
        """
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        # Verificación 1: Infinity en TradeQuote es rechazado con STALE_PRICE
        t_inf = TradeQuote(
            symbol="TEST",
            price=Decimal("Infinity"),
            timestamp=t_now,
            size=100,
        )
        entry_price, mode = guard.get_entry_price(None, t_inf)
        assert entry_price is None
        assert mode == "STALE_PRICE"

        # Verificación 2: Infinity en ATR no evade la detección de anomalías
        detector = AnomalyDetector()
        ok, reason = detector.check_price_anomaly(
            Decimal("1000000.00"), Decimal("100.00"), Decimal("Infinity")
        )
        assert ok is False
        assert reason in ("PRICE_ANOMALY", "MISSING_ATR")



# ============================================================================
# 4. FORWARD-FILL INVARIANT ENFORCEMENT & TYPE ROBUSTNESS
# ============================================================================


class TestForwardFillInvariantEnforcement:
    """Verifica el invariante no negociable: forward-fill / barras sintéticas NUNCA pueden ser precios de entrada."""

    def test_synthetic_bar_dict_rejected_in_get_entry_price(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Verifica que un diccionario de barra sintética (create_synthetic_bar) no pueda pasar como quote ni trade en get_entry_price."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        t_now = sim_clock.now()

        synth_bar = create_synthetic_bar(t_now, Decimal("150.00"))
        assert synth_bar["is_synthetic"] is True
        assert synth_bar["volume"] == Decimal("0")

        # Intentar pasar la barra sintética como quote o trade
        price, mode = guard.get_entry_price(synth_bar, None)  # type: ignore[arg-type]
        assert price is None
        assert mode == "STALE_PRICE"

        price, mode = guard.get_entry_price(None, synth_bar)  # type: ignore[arg-type]
        assert price is None
        assert mode == "STALE_PRICE"

    def test_bug_reproduction_synthetic_bar_dict_crashes_validate_entry_with_attribute_error(
        self, sim_clock: SimulatedClock
    ) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN:
        validate_entry utiliza getattr defensivo para el atributo timestamp.
        Al recibir un diccionario (barra sintética), no lanza AttributeError sino que
        falla cerrado limpiamente con (None, False, 'STALE_PRICE' o 'NO_DATA').
        """
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()
        synth_bar = create_synthetic_bar(t_now, Decimal("150.00"))

        price, is_valid, reason = guard.validate_entry(
            "AAPL",
            quote=synth_bar,  # type: ignore[arg-type]
            trade=synth_bar,  # type: ignore[arg-type]
            sip_close=Decimal("150.00"),
            atr_5m=Decimal("1.00"),
        )
        assert is_valid is False
        assert price is None
        assert reason in ("STALE_PRICE", "NO_DATA")

        # validate_entry_or_raise también falla cerrado con StaleDataError, nunca AttributeError
        with pytest.raises(StaleDataError):
            guard.validate_entry_or_raise(
                "AAPL",
                quote=synth_bar,  # type: ignore[arg-type]
                trade=synth_bar,  # type: ignore[arg-type]
                sip_close=Decimal("150.00"),
                atr_5m=Decimal("1.00"),
            )


    def test_forward_fill_cannot_bypass_when_passed_as_objects(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Verifica que objetos arbitrarios o sintéticos que simulan atributos no puedan saltarse el filtro."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()

        class SyntheticBarObject:
            timestamp = sim_clock.now()
            is_synthetic = True
            volume = 0
            close = Decimal("150.00")

        mock_bar = SyntheticBarObject()
        # get_entry_price requiere explícitamente isinstance(quote, PriceQuote) y isinstance(trade, TradeQuote)
        price, mode = guard.get_entry_price(mock_bar, mock_bar)  # type: ignore[arg-type]
        assert price is None
        assert mode == "STALE_PRICE"


# ============================================================================
# 5. DISCARD METRICS TRACKING & THREAD-SAFETY
# ============================================================================


class TestDiscardMetricsTrackingAndThreadSafety:
    """Stress testing de fidelidad de métricas de descarte y condiciones de carrera concurrentes."""

    def test_single_threaded_exact_discard_fidelity(self, sim_clock: SimulatedClock) -> None:
        """Verifica que en ejecución mono-hilo no haya desvíos en los contadores."""
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        t_now = sim_clock.now()

        # 10 descartes de STALE_PRICE por quote y trade antiguos
        q_stale = PriceQuote(
            symbol="SPY",
            bid=Decimal("400.00"),
            ask=Decimal("400.02"),
            timestamp=t_now - timedelta(hours=2),
            bid_size=100,
            ask_size=100,
        )
        t_stale = TradeQuote(
            symbol="SPY",
            price=Decimal("400.01"),
            timestamp=t_now - timedelta(hours=2),
            size=100,
        )

        for _ in range(10):
            guard.get_entry_price(q_stale, t_stale)

        assert guard.discard_counts["STALE_PRICE"] == 10
        assert guard.discard_counts["PRICE_ANOMALY"] == 0

        # 15 descartes de PRICE_ANOMALY
        for _ in range(15):
            guard.check_price_anomaly(Decimal("450.00"), Decimal("400.00"), Decimal("1.00"))

        assert guard.discard_counts["STALE_PRICE"] == 10
        assert guard.discard_counts["PRICE_ANOMALY"] == 15

    def test_non_anomaly_errors_do_not_falsely_increment_anomaly_metric(self) -> None:
        """Verifica que rechazos por INVALID_PRICE o MISSING_DATA no inflen falsamente PRICE_ANOMALY."""
        guard = StalenessGuard(enforce_market_hours=False)

        # Precio inválido
        guard.check_price_anomaly(Decimal("-10.00"), Decimal("100.00"), Decimal("1.00"))
        assert guard.discard_counts["PRICE_ANOMALY"] == 0

        # Dato faltante
        guard.check_price_anomaly(float("nan"), Decimal("100.00"), Decimal("1.00"))
        assert guard.discard_counts["PRICE_ANOMALY"] == 0

    def test_bug_reproduction_multithreaded_race_conditions_in_discard_counts(self) -> None:
        """VERIFICACIÓN DE REMEDIACIÓN:
        ThreadSafeDiscardCounts y _increment_discard garantizan que las métricas de descarte
        son 100% thread-safe bajo alta concurrencia de hilos, evitando pérdida de incrementos.
        """
        old_switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-7)  # Fuerza preemption de GIL entre bytecodes
        try:
            guard = StalenessGuard(enforce_market_hours=False)

            def worker_stale() -> None:
                q = PriceQuote(
                    symbol="T",
                    bid=Decimal("10"),
                    ask=Decimal("11"),
                    timestamp=datetime(2020, 1, 1, tzinfo=UTC),
                )
                t = TradeQuote(
                    symbol="T", price=Decimal("10"), timestamp=datetime(2020, 1, 1, tzinfo=UTC)
                )
                for _ in range(2500):
                    guard.get_entry_price(q, t)

            threads = [threading.Thread(target=worker_stale) for _ in range(8)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()

            expected_stale = 8 * 2500
            actual_stale = guard.discard_counts["STALE_PRICE"]
            lost_stale = expected_stale - actual_stale

            # Comprueba que la implementación thread-safe no pierde conteos
            assert lost_stale == 0, f"Expected zero lost discards, but got {lost_stale}"
            assert actual_stale == expected_stale
        finally:
            sys.setswitchinterval(old_switch)

