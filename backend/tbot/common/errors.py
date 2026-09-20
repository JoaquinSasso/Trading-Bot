"""Jerarquía de excepciones del dominio de tbot."""


class TBotError(Exception):
    """Excepción base para todos los errores de la aplicación."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(TBotError):
    """Error de configuración inválida o faltante."""


class DatabaseError(TBotError):
    """Error de persistencia o conexión a la base de datos."""


class MarketDataError(TBotError):
    """Error general de datos de mercado."""


class StaleDataError(MarketDataError):
    """Los datos de mercado superaron el umbral de frescura (guardia de datos viejos)."""


class PriceAnomalyError(MarketDataError):
    """Divergencia anómala entre el precio IEX en tiempo real y el cierre SIP."""


class StrategyError(TBotError):
    """Error en la lógica o ejecución de una estrategia."""


class RiskLimitError(TBotError):
    """La operación fue bloqueada por violación de los límites del RiskGate."""


class ExecutionError(TBotError):
    """Fallo en el enrutamiento o confirmación de una orden en el broker."""


class AIVetoError(TBotError):
    """Fallo en la comunicación, parsing o cuota del servicio de veto IA."""


class ReconciliationError(TBotError):
    """Discrepancia crítica entre el estado local y la verdad del broker."""
