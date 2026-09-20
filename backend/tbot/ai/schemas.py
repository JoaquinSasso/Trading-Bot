"""Esquemas Pydantic estrictos para el Veto de IA."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VetoVerdict(StrEnum):
    """Veredictos admitidos por el Veto de IA."""

    CONFIRM = "CONFIRM"
    REDUCE = "REDUCE"
    REJECT = "REJECT"


class VetoReasonCode(StrEnum):
    """Códigos cerrados de motivo para la decisión de veto."""

    OK = "OK"
    NEWS_NEGATIVE_CLUSTER = "NEWS_NEGATIVE_CLUSTER"
    EVENT_RISK = "EVENT_RISK"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    EXTENDED_MOVE = "EXTENDED_MOVE"
    LOW_QUALITY_SETUP = "LOW_QUALITY_SETUP"
    OTHER = "OTHER"


class VetoInput(BaseModel):
    """Payload de entrada inyectado al LLM de Veto (sin texto libre de noticias)."""

    model_config = ConfigDict(extra="forbid")

    signal: dict[str, Any]
    features: dict[str, Any]
    regime: str
    events: dict[str, Any]
    news: dict[str, Any]


class VetoOutput(BaseModel):
    """Salida estructurada y validada devuelta por el modelo LLM."""

    model_config = ConfigDict(extra="ignore")

    # El campo analysis DEBE preceder al veredicto para forzar razonamiento previo (Chain of Thought conciso)
    analysis: str = Field(
        ...,
        max_length=400,
        description="Razonamiento conciso previo a la decisión (máximo 400 caracteres).",
    )
    verdict: VetoVerdict
    size_multiplier: float = Field(
        default=1.0,
        ge=0.25,
        le=1.0,
        description="Multiplicador de tamaño de posición [0.25, 1.0].",
    )
    reason_code: VetoReasonCode

    @model_validator(mode="after")
    def validate_verdict_and_multiplier(self) -> VetoOutput:
        """Asegura que si el veredicto es CONFIRM, el multiplicador se fuerce estrictamente a 1.0."""
        if self.verdict == VetoVerdict.CONFIRM:
            self.size_multiplier = 1.0
        elif self.verdict == VetoVerdict.REJECT:
            self.size_multiplier = 0.0
        return self


class VetoResult(BaseModel):
    """Resultado procesado de la consulta de veto, listo para el RiskGate."""

    verdict: VetoVerdict
    size_multiplier: float
    reason_code: VetoReasonCode
    analysis: str
    status: Literal["OK", "UNAVAILABLE", "BYPASSED"]
    provider: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    llm_call_id: int | None = None

    @classmethod
    def unavailable(cls, reason: str = "Error or timeout communicating with LLM") -> VetoResult:
        """Retorna un resultado de falla cerrada (UNAVAILABLE)."""
        return cls(
            verdict=VetoVerdict.REJECT,
            size_multiplier=0.0,
            reason_code=VetoReasonCode.OTHER,
            analysis=f"Falla cerrada: {reason}",
            status="UNAVAILABLE",
        )

    @classmethod
    def bypassed(cls) -> VetoResult:
        """Retorna confirmación automática cuando el veto está desactivado (modo off)."""
        return cls(
            verdict=VetoVerdict.CONFIRM,
            size_multiplier=1.0,
            reason_code=VetoReasonCode.OK,
            analysis="Veto desactivado (modo off).",
            status="BYPASSED",
        )
