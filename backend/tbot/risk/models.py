"""Modelos y estructuras de datos para el motor de riesgo y dimensionamiento."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class AccountMode(StrEnum):
    """Modo operativo de la cuenta en Alpaca."""

    MARGIN_NO_LEVERAGE = "margin_no_leverage"
    CASH = "cash"


class CircuitBreakerState(StrEnum):
    """Estado del cortacircuitos diario del bot."""

    NORMAL = "NORMAL"
    PAUSED_DAILY_LOSS = "PAUSED_DAILY_LOSS"
    EMERGENCY_FLATTEN = "EMERGENCY_FLATTEN"


class RiskReasonCode(StrEnum):
    """Códigos explicativos de la decisión del RiskGate."""

    APPROVED = "APPROVED"
    REJECT_KILL_SWITCH = "REJECT_KILL_SWITCH"
    REJECT_CIRCUIT_BREAKER = "REJECT_CIRCUIT_BREAKER"
    REJECT_MAX_POSITIONS = "REJECT_MAX_POSITIONS"
    REJECT_OWNERSHIP_CONFLICT = "REJECT_OWNERSHIP_CONFLICT"
    REJECT_CLUSTER_LIMIT = "REJECT_CLUSTER_LIMIT"
    REJECT_INSUFFICIENT_BUYING_POWER = "REJECT_INSUFFICIENT_BUYING_POWER"
    REJECT_ZERO_SHARES = "REJECT_ZERO_SHARES"
    REJECT_BELOW_MIN_NOTIONAL = "REJECT_BELOW_MIN_NOTIONAL"
    REJECT_NOTIONAL_EXCEEDED = "REJECT_NOTIONAL_EXCEEDED"
    REJECT_UNSETTLED_CASH = "REJECT_UNSETTLED_CASH"
    REJECT_INVALID_STOP = "REJECT_INVALID_STOP"


@dataclass(frozen=True)
class SizingResult:
    """Resultado del cálculo de tamaño de posición."""

    signal_symbol: str
    execution_symbol: str
    shares: Decimal
    is_fractional: bool
    entry_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None
    risk_usd: Decimal
    notional_usd: Decimal
    path: str  # "whole" o "fractional_fallback"


@dataclass(frozen=True)
class RiskDecision:
    """Decisión emitida por RiskGate."""

    approved: bool
    reason_code: RiskReasonCode
    detail: str
    sizing: SizingResult | None = None
