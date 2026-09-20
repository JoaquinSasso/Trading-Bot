"""Unit tests for Cross-Feed Price Anomaly Detection (> 2 * ATR_5m).

Covers:
- Boundary conditions around |P_IEX - C_SIP| == 2 * ATR_5m (1.99x, 2.00x, 2.01x).
- Flash upward spikes and flash downward drops.
- Zero, None, NaN ATR handling with percentage fallback and fail-closed modes.
- Zero and negative price handling (fail-closed).
- Float, Decimal, int, and numpy type interoperability (no TypeError).
- Discard metrics tracking (PRICE_ANOMALY counter).
- validate_entry gating and validate_entry_or_raise exception behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest

from tbot.common.clock import SimulatedClock
from tbot.common.errors import PriceAnomalyError
from tbot.data.staleness import AnomalyDetector, StalenessGuard
from tbot.data.types import PriceQuote, TradeQuote


@pytest.fixture
def sim_clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC))


class TestAnomalyDetectorCore:
    """Verifica el cálculo de discrepancia cruzada entre IEX y SIP diferido."""

    def test_normal_divergence_passes(self) -> None:
        detector = AnomalyDetector()
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")

        # Divergencias dentro de 2*ATR pasan (0.5x, 1.0x, 1.5x, 1.9x)
        for factor in [Decimal("0.5"), Decimal("1.0"), Decimal("1.5"), Decimal("1.9")]:
            iex_price = sip_close + factor * atr_5m
            is_ok, reason = detector.check_price_anomaly(iex_price, sip_close, atr_5m)
            assert is_ok is True
            assert reason == "OK"

    def test_exact_2_atr_boundary_passes(self) -> None:
        detector = AnomalyDetector()
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")

        # Límite inclusivo: exactamente 2.0x ATR debe pasar
        iex_price_upper = sip_close + Decimal("2.00") * atr_5m
        is_ok, reason = detector.check_price_anomaly(iex_price_upper, sip_close, atr_5m)
        assert is_ok is True
        assert reason == "OK"

        iex_price_lower = sip_close - Decimal("2.00") * atr_5m
        is_ok, reason = detector.check_price_anomaly(iex_price_lower, sip_close, atr_5m)
        assert is_ok is True
        assert reason == "OK"

    def test_divergence_above_2_atr_fails_with_price_anomaly(self) -> None:
        detector = AnomalyDetector()
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")

        # 2.01x ATR -> PRICE_ANOMALY
        iex_price_upper = sip_close + Decimal("2.01") * atr_5m
        is_ok, reason = detector.check_price_anomaly(iex_price_upper, sip_close, atr_5m)
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"

        # -2.01x ATR -> PRICE_ANOMALY
        iex_price_lower = sip_close - Decimal("2.01") * atr_5m
        is_ok, reason = detector.check_price_anomaly(iex_price_lower, sip_close, atr_5m)
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"

    def test_flash_spike_and_flash_drop(self) -> None:
        detector = AnomalyDetector()
        sip_close = Decimal("100.00")
        atr_5m = Decimal("0.50")

        # Flash spike: precio salta a 105.00 (+10*ATR)
        is_ok, reason = detector.check_price_anomaly(Decimal("105.00"), sip_close, atr_5m)
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"

        # Flash drop: precio cae a 95.00 (-10*ATR)
        is_ok, reason = detector.check_price_anomaly(Decimal("95.00"), sip_close, atr_5m)
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"

    def test_detect_anomaly_predicate_formulation(self) -> None:
        detector = AnomalyDetector()
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")

        # Normal -> detect_anomaly es False
        is_anomaly, desc = detector.detect_anomaly(Decimal("501.50"), sip_close, atr_5m)
        assert is_anomaly is False
        assert desc == "OK"

        # Anomalía -> detect_anomaly es True
        is_anomaly, desc = detector.detect_anomaly(Decimal("502.50"), sip_close, atr_5m)
        assert is_anomaly is True
        assert "PRICE_ANOMALY: discrepancy > 2*ATR" in desc


class TestAnomalyCornerAndEdgeCases:
    """Verifica el comportamiento defensivo ante ATR cero, nulo o datos inválidos."""

    def test_zero_or_nan_atr_percentage_fallback(self) -> None:
        # Modo percentage (fallback por defecto de 2% de C_SIP)
        detector = AnomalyDetector(fallback_mode="percentage", fallback_pct=Decimal("0.02"))
        sip_close = Decimal("100.00")

        # ATR es 0.0 -> tolerancia es 2% de 100 = 2.00
        # Discrepancia de 1.50 <= 2.00 -> pasa
        is_ok, reason = detector.check_price_anomaly(Decimal("101.50"), sip_close, Decimal("0.0"))
        assert is_ok is True
        assert reason == "OK"

        # Discrepancia de 2.50 > 2.00 -> PRICE_ANOMALY
        is_ok, reason = detector.check_price_anomaly(Decimal("102.50"), sip_close, Decimal("0.0"))
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"

        # ATR es None -> usa fallback de 2%
        is_ok, reason = detector.check_price_anomaly(Decimal("101.50"), sip_close, None)
        assert is_ok is True

        # ATR es float("nan") -> usa fallback de 2%
        is_ok, reason = detector.check_price_anomaly(Decimal("101.50"), sip_close, float("nan"))
        assert is_ok is True

    def test_zero_or_none_atr_fail_closed_mode(self) -> None:
        detector = AnomalyDetector(fallback_mode="fail_closed")
        sip_close = Decimal("100.00")

        # ATR cero en fail_closed -> rechaza con MISSING_ATR
        is_ok, reason = detector.check_price_anomaly(Decimal("100.50"), sip_close, Decimal("0.0"))
        assert is_ok is False
        assert reason == "MISSING_ATR"

        # ATR None en fail_closed -> rechaza con MISSING_ATR
        is_ok, reason = detector.check_price_anomaly(Decimal("100.50"), sip_close, None)
        assert is_ok is False
        assert reason == "MISSING_ATR"

    def test_invalid_or_missing_prices(self) -> None:
        detector = AnomalyDetector()

        # Precio None
        assert detector.check_price_anomaly(None, Decimal("100"), Decimal("1")) == (False, "MISSING_PRICE_DATA")
        assert detector.check_price_anomaly(Decimal("100"), None, Decimal("1")) == (False, "MISSING_PRICE_DATA")

        # Precio cero o negativo
        assert detector.check_price_anomaly(Decimal("0.0"), Decimal("100"), Decimal("1")) == (False, "INVALID_PRICE")
        assert detector.check_price_anomaly(Decimal("-5.0"), Decimal("100"), Decimal("1")) == (False, "INVALID_PRICE")
        assert detector.check_price_anomaly(Decimal("100"), Decimal("0.0"), Decimal("1")) == (False, "INVALID_PRICE")

    def test_numeric_type_flexibility_without_type_error(self) -> None:
        detector = AnomalyDetector()

        # Mezcla de float de python y numpy float64 con Decimal
        is_ok, reason = detector.check_price_anomaly(
            iex_price=501.50,
            sip_close=Decimal("500.00"),
            atr_5m=np.float64(1.0),
        )
        assert is_ok is True
        assert reason == "OK"

        # Con strings numéricos
        is_ok, reason = detector.check_price_anomaly(
            iex_price="503.00",
            sip_close="500.00",
            atr_5m="1.00",
        )
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"


class TestStalenessGuardAnomalyIntegration:
    """Verifica la integración del detector de anomalías dentro de StalenessGuard."""

    def test_staleness_guard_check_price_anomaly_increments_metric(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock)
        sip_close = Decimal("500.00")
        atr_5m = Decimal("1.00")

        initial_count = guard.discard_counts["PRICE_ANOMALY"]

        # Precio normal -> no incrementa métrica
        is_ok, reason = guard.check_price_anomaly(Decimal("501.00"), sip_close, atr_5m)
        assert is_ok is True
        assert guard.discard_counts["PRICE_ANOMALY"] == initial_count

        # Precio anómalo -> incrementa contador de descartes
        is_ok, reason = guard.check_price_anomaly(Decimal("503.50"), sip_close, atr_5m)
        assert is_ok is False
        assert reason == "PRICE_ANOMALY"
        assert guard.discard_counts["PRICE_ANOMALY"] == initial_count + 1

    def test_validate_entry_blocks_on_price_anomaly(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        now = sim_clock.now()

        # Quote y trade frescos con precio anómalo respecto de SIP
        # SIP close = 500.00, ATR = 1.00. IEX price = 505.00 (+5*ATR)
        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("504.95"),
            ask=Decimal("505.05"),
            midpoint=Decimal("505.00"),
            spread_bps=2.0,
            timestamp=now,
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("505.00"), timestamp=now)

        price, is_valid, reason = guard.validate_entry(
            symbol="SPY",
            quote=quote,
            trade=trade,
            sip_close=Decimal("500.00"),
            atr_5m=Decimal("1.00"),
        )
        assert is_valid is False
        assert price is None
        assert reason == "PRICE_ANOMALY"

    def test_validate_entry_or_raise_raises_price_anomaly_error(self, sim_clock: SimulatedClock) -> None:
        guard = StalenessGuard(clock=sim_clock, enforce_market_hours=False)
        guard.record_message()
        now = sim_clock.now()

        quote = PriceQuote(
            symbol="SPY",
            bid=Decimal("504.95"),
            ask=Decimal("505.05"),
            midpoint=Decimal("505.00"),
            spread_bps=2.0,
            timestamp=now,
        )
        trade = TradeQuote(symbol="SPY", price=Decimal("505.00"), timestamp=now)

        with pytest.raises(PriceAnomalyError) as exc_info:
            guard.validate_entry_or_raise(
                symbol="SPY",
                quote=quote,
                trade=trade,
                sip_close=Decimal("500.00"),
                atr_5m=Decimal("1.00"),
            )
        assert "Price anomaly" in str(exc_info.value)
        assert exc_info.value.details["symbol"] == "SPY"
        assert exc_info.value.details["reason"] == "PRICE_ANOMALY"
