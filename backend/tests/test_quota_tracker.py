"""Tests unitarios para el control de cuotas de LLM (QuotaTracker)."""

from datetime import datetime, timedelta

from tbot.ai.quota import ProviderQuotaConfig, QuotaTracker


def test_quota_tracker_rpm_cutoff():
    # Límite de 10 RPM -> 90% es 9 requests
    cfg = ProviderQuotaConfig(max_rpm=10, max_rpd=1000)
    tracker = QuotaTracker({"test_llm": cfg})

    now = datetime(2025, 6, 10, 15, 30, 0)

    # Las primeras 8 peticiones deben permitirse
    for _ in range(8):
        assert tracker.can_request("test_llm", now) is True
        tracker.record_request("test_llm", now)

    # Petición 9 (alcanza el 90%)
    assert tracker.can_request("test_llm", now) is True
    tracker.record_request("test_llm", now)

    # Petición 10: Bloqueada por protección preventiva al 90%
    assert tracker.can_request("test_llm", now) is False

    # Al cambiar de minuto, se reinicia el contador de RPM
    next_minute = now + timedelta(minutes=1)
    assert tracker.can_request("test_llm", next_minute) is True


def test_quota_tracker_rpd_cutoff():
    cfg = ProviderQuotaConfig(max_rpm=100, max_rpd=10)
    tracker = QuotaTracker({"test_llm": cfg})

    now = datetime(2025, 6, 10, 15, 30, 0)

    # 9 peticiones en el día
    for _ in range(9):
        assert tracker.can_request("test_llm", now) is True
        tracker.record_request("test_llm", now)

    # Petición 10 bloqueada (90% alcanzado)
    assert tracker.can_request("test_llm", now) is False
