"""Gestión de cortacircuitos escalonados por riesgo intradiario y semanal."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from tbot.risk.models import CircuitBreakerState


class CircuitBreakerManager:
    """Monitorea pérdidas acumuladas y dispara pausas o cierres de emergencia."""

    def __init__(
        self,
        daily_loss_limit_pct: float = 2.0,
        emergency_loss_limit_pct: float = 3.5,
        weekly_loss_limit_pct: float = 5.0,
        max_consecutive_losses: int = 4,
    ) -> None:
        self.daily_loss_limit_pct = Decimal(str(daily_loss_limit_pct))
        self.emergency_loss_limit_pct = Decimal(str(emergency_loss_limit_pct))
        self.weekly_loss_limit_pct = Decimal(str(weekly_loss_limit_pct))
        self.max_consecutive_losses = max_consecutive_losses

        self._state: CircuitBreakerState = CircuitBreakerState.NORMAL
        self._current_date: date | None = None
        self._daily_starting_equity: Decimal = Decimal("0.0")
        self._weekly_starting_equity: Decimal = Decimal("0.0")
        self._consecutive_losses: int = 0
        self._is_manually_paused: bool = False

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    def reset_daily(self, opening_equity: Decimal, current_date: date) -> None:
        """Inicializa la sesión diaria con el balance de apertura."""
        if (
            self._current_date is None
            or self._current_date.isocalendar()[:2] != current_date.isocalendar()[:2]
        ):
            # Nuevo inicio de semana
            self._weekly_starting_equity = opening_equity

        self._current_date = current_date
        self._daily_starting_equity = opening_equity
        # Resetear solo si no estaba en pausa manual
        if not self._is_manually_paused:
            self._state = CircuitBreakerState.NORMAL

    def update_equity(self, current_equity: Decimal) -> CircuitBreakerState:
        """Evalúa el equity actual contra los límites diarios y semanales."""
        if self._is_manually_paused:
            self._state = CircuitBreakerState.PAUSED_DAILY_LOSS
            return self._state

        if self._daily_starting_equity <= Decimal("0"):
            return self._state

        # Pérdida diaria porcentual
        daily_drawdown_pct = (
            (self._daily_starting_equity - current_equity) / self._daily_starting_equity
        ) * Decimal("100")

        # Pérdida semanal porcentual
        weekly_drawdown_pct = Decimal("0")
        if self._weekly_starting_equity > Decimal("0"):
            weekly_drawdown_pct = (
                (self._weekly_starting_equity - current_equity) / self._weekly_starting_equity
            ) * Decimal("100")

        if daily_drawdown_pct >= self.emergency_loss_limit_pct:
            self._state = CircuitBreakerState.EMERGENCY_FLATTEN
        elif (
            daily_drawdown_pct >= self.daily_loss_limit_pct
            or weekly_drawdown_pct >= self.weekly_loss_limit_pct
            or self._consecutive_losses >= self.max_consecutive_losses
        ):
            self._state = CircuitBreakerState.PAUSED_DAILY_LOSS
        else:
            self._state = CircuitBreakerState.NORMAL

        return self._state

    def record_trade(self, pnl: Decimal, closed_at: datetime) -> None:
        """Registra el resultado de una operación para conteo de rachas perdedoras."""
        if pnl < Decimal("0"):
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.max_consecutive_losses:
                self._state = CircuitBreakerState.PAUSED_DAILY_LOSS
        else:
            self._consecutive_losses = 0

    @property
    def consecutive_losses(self) -> int:
        """Cantidad actual de operaciones perdedoras consecutivas."""
        return self._consecutive_losses

    def reset_consecutive_losses(self) -> None:
        """Reinicia la racha de pérdidas (p.ej. tras cumplir el enfriamiento de la pausa)."""
        self._consecutive_losses = 0

    def can_open_new_positions(self) -> bool:
        """Indica si el sistema tiene permitido enviar órdenes de compra."""
        return self._state == CircuitBreakerState.NORMAL and not self._is_manually_paused

    def requires_flatten(self) -> bool:
        """Indica si se requiere liquidar inmediatamente todas las posiciones del bot."""
        return self._state == CircuitBreakerState.EMERGENCY_FLATTEN

    def manual_pause(self) -> None:
        """Pone en pausa al bot manualmente."""
        self._is_manually_paused = True
        self._state = CircuitBreakerState.PAUSED_DAILY_LOSS

    def manual_resume(self) -> None:
        """Reanuda la operativa del bot tras verificación humana."""
        self._is_manually_paused = False
        self._consecutive_losses = 0
        self._state = CircuitBreakerState.NORMAL
