"""Tests unitarios para el monitor de cortacircuitos escalonados."""

from datetime import date, datetime
from decimal import Decimal

from tbot.risk.circuit_breakers import CircuitBreakerManager
from tbot.risk.models import CircuitBreakerState


def test_circuit_breaker_normal_operation() -> None:
    cb = CircuitBreakerManager(daily_loss_limit_pct=2.0, emergency_loss_limit_pct=3.5)
    cb.reset_daily(opening_equity=Decimal("2000.00"), current_date=date(2026, 9, 21))

    # Pérdida de $20 (1.0% de $2000) -> Dentro de lo normal
    state = cb.update_equity(Decimal("1980.00"))
    assert state == CircuitBreakerState.NORMAL
    assert cb.can_open_new_positions() is True
    assert cb.requires_flatten() is False


def test_circuit_breaker_daily_loss_soft_pause() -> None:
    cb = CircuitBreakerManager(daily_loss_limit_pct=2.0, emergency_loss_limit_pct=3.5)
    cb.reset_daily(opening_equity=Decimal("2000.00"), current_date=date(2026, 9, 21))

    # Pérdida de $42 (2.1% de $2000) -> Supera 2.0% -> PAUSED_DAILY_LOSS
    state = cb.update_equity(Decimal("1958.00"))
    assert state == CircuitBreakerState.PAUSED_DAILY_LOSS
    assert cb.can_open_new_positions() is False
    assert cb.requires_flatten() is False


def test_circuit_breaker_emergency_flatten() -> None:
    cb = CircuitBreakerManager(daily_loss_limit_pct=2.0, emergency_loss_limit_pct=3.5)
    cb.reset_daily(opening_equity=Decimal("2000.00"), current_date=date(2026, 9, 21))

    # Pérdida de $75 (3.75% de $2000) -> Supera 3.5% -> EMERGENCY_FLATTEN
    state = cb.update_equity(Decimal("1925.00"))
    assert state == CircuitBreakerState.EMERGENCY_FLATTEN
    assert cb.can_open_new_positions() is False
    assert cb.requires_flatten() is True


def test_circuit_breaker_consecutive_losses_limit() -> None:
    cb = CircuitBreakerManager(max_consecutive_losses=3)
    cb.reset_daily(opening_equity=Decimal("2000.00"), current_date=date(2026, 9, 21))

    now = datetime(2026, 9, 21, 10, 0)
    cb.record_trade(Decimal("-5.00"), now)
    assert cb.can_open_new_positions() is True

    cb.record_trade(Decimal("-5.00"), now)
    assert cb.can_open_new_positions() is True

    # Tercera pérdida consecutiva
    cb.record_trade(Decimal("-5.00"), now)
    assert cb.can_open_new_positions() is False
    assert cb.state == CircuitBreakerState.PAUSED_DAILY_LOSS


def test_circuit_breaker_manual_pause_and_resume() -> None:
    cb = CircuitBreakerManager()
    cb.reset_daily(opening_equity=Decimal("2000.00"), current_date=date(2026, 9, 21))

    cb.manual_pause()
    assert cb.can_open_new_positions() is False

    cb.manual_resume()
    assert cb.can_open_new_positions() is True
