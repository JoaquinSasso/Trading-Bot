"""Gestión de exclusividad y pertenencia de activos (Ownership Ledger)."""

from __future__ import annotations

from typing import Literal

OwnerType = Literal["bot", "manual", "none"]


class OwnershipLedger:
    """Garantiza el principio no negociable: Un dueño por ticker en todo momento."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    def get_owner(self, symbol: str) -> str:
        """Retorna 'bot', 'manual' o 'none' para el símbolo dado."""
        clean = symbol.strip().upper()
        return self._owners.get(clean, "none")

    def assign(self, symbol: str, owner: Literal["bot", "manual"]) -> bool:
        """Asigna propiedad sobre un activo si no está en conflicto."""
        clean = symbol.strip().upper()
        current = self._owners.get(clean, "none")

        if current != "none" and current != owner:
            return False  # Conflicto de pertenencia

        self._owners[clean] = owner
        return True

    def release(self, symbol: str) -> None:
        """Libera la titularidad de un activo tras el cierre de su posición."""
        clean = symbol.strip().upper()
        self._owners.pop(clean, None)

    def can_bot_trade(self, symbol: str) -> bool:
        """Indica si el bot puede operar sobre el ticker sin violar exclusividad."""
        owner = self.get_owner(symbol)
        return owner in ("none", "bot")

    def take_control_manual(self, symbol: str) -> None:
        """Acción del usuario: toma el control de un ticker gestionado por el bot."""
        clean = symbol.strip().upper()
        self._owners[clean] = "manual"
