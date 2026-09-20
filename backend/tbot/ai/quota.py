"""Gestor de cuotas de llamadas a proveedores de LLM (RPM y RPD)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass
class ProviderQuotaConfig:
    """Límites de cuota configurables para un proveedor de LLM."""

    max_rpm: int = 15
    max_rpd: int = 1500
    alert_threshold_pct: float = 0.90  # 90% del límite


class QuotaTracker:
    """Rastrea el consumo de cuotas en ventanas de 1 minuto y 1 día por proveedor."""

    def __init__(self, configs: dict[str, ProviderQuotaConfig] | None = None) -> None:
        self.configs = configs or {
            "gemini": ProviderQuotaConfig(max_rpm=15, max_rpd=1500),
            "groq": ProviderQuotaConfig(max_rpm=30, max_rpd=14400),
        }
        # provider -> (minute_bucket_str, count)
        self._minute_counts: dict[str, tuple[str, int]] = {}
        # provider -> (date, count)
        self._daily_counts: dict[str, tuple[date, int]] = {}

    def _get_minute_bucket(self, now: datetime) -> str:
        return now.strftime("%Y-%m-%d %H:%M")

    def can_request(self, provider: str, now: datetime) -> bool:
        """Verifica si el proveedor tiene cuota disponible (por debajo del umbral del 90%)."""
        config = self.configs.get(provider)
        if config is None:
            return True

        minute_bucket = self._get_minute_bucket(now)
        today = now.date()

        # Chequeo RPM (límite por minuto con margen del 90%)
        curr_min_bucket, min_count = self._minute_counts.get(provider, ("", 0))
        if curr_min_bucket == minute_bucket and min_count >= int(
            config.max_rpm * config.alert_threshold_pct
        ):
            return False

        # Chequeo RPD (límite diario con margen del 90%)
        curr_day, day_count = self._daily_counts.get(provider, (today, 0))
        return not (
            curr_day == today and day_count >= int(config.max_rpd * config.alert_threshold_pct)
        )

    def record_request(self, provider: str, now: datetime) -> None:
        """Incrementa los contadores de peticiones por minuto y por día."""
        minute_bucket = self._get_minute_bucket(now)
        today = now.date()

        # Actualizar minuto
        curr_min_bucket, min_count = self._minute_counts.get(provider, ("", 0))
        if curr_min_bucket == minute_bucket:
            self._minute_counts[provider] = (minute_bucket, min_count + 1)
        else:
            self._minute_counts[provider] = (minute_bucket, 1)

        # Actualizar día
        curr_day, day_count = self._daily_counts.get(provider, (today, 0))
        if curr_day == today:
            self._daily_counts[provider] = (today, day_count + 1)
        else:
            self._daily_counts[provider] = (today, 1)

    def get_usage(self, provider: str, now: datetime) -> dict[str, Any]:
        """Retorna el estado de uso actual para auditoría o UI."""
        config = self.configs.get(provider, ProviderQuotaConfig())
        minute_bucket = self._get_minute_bucket(now)
        today = now.date()

        curr_min_bucket, min_count = self._minute_counts.get(provider, ("", 0))
        active_min_count = min_count if curr_min_bucket == minute_bucket else 0

        curr_day, day_count = self._daily_counts.get(provider, (today, 0))
        active_day_count = day_count if curr_day == today else 0

        return {
            "provider": provider,
            "rpm_current": active_min_count,
            "rpm_limit": config.max_rpm,
            "rpd_current": active_day_count,
            "rpd_limit": config.max_rpd,
            "can_request": self.can_request(provider, now),
        }
