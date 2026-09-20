"""Mapeo bidireccional de proxies de ETFs para ejecución y datos de mercado.

Permite calcular señales e indicadores sobre activos canónicos de alta liquidez
(SPY, QQQ, GLD) y enrutar las órdenes de ejecución a proxies de menor valor nominal
(SPYM, QQQM, GLDM) para operar brackets nativos sin fraccionales en cuentas pequeñas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from tbot.db.models import UniverseSymbol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Mapeos canónicos por defecto (docs/PLAN.md Sección 4.7)
CANONICAL_PROXIES: dict[str, str] = {
    "SPY": "SPYM",  # State Street S&P 500 mini (~$70, expense 0.02%)
    "QQQ": "QQQM",  # Invesco Nasdaq 100 mini (~$210, expense 0.15%)
    "GLD": "GLDM",  # SPDR Gold mini (~$45, expense 0.10%)
}

REVERSE_PROXIES: dict[str, str] = {v: k for k, v in CANONICAL_PROXIES.items()}


def resolve_trading_proxy(symbol: str) -> str:
    """Retorna el símbolo de ejecución (proxy de bajo costo nominal), o el original si no tiene mapeo.

    Ejemplos:
        resolve_trading_proxy("SPY") -> "SPYM"
        resolve_trading_proxy("spy") -> "SPYM"
        resolve_trading_proxy("SPYM") -> "SPYM"
        resolve_trading_proxy("AAPL") -> "AAPL"
    """
    clean_sym = symbol.strip().upper()
    return CANONICAL_PROXIES.get(clean_sym, clean_sym)


def resolve_data_proxy(symbol: str) -> str:
    """Retorna el símbolo canónico líquido para análisis histórico e indicadores, o el original.

    Ejemplos:
        resolve_data_proxy("SPYM") -> "SPY"
        resolve_data_proxy("spym") -> "SPY"
        resolve_data_proxy("SPY") -> "SPY"
        resolve_data_proxy("AAPL") -> "AAPL"
    """
    clean_sym = symbol.strip().upper()
    return REVERSE_PROXIES.get(clean_sym, clean_sym)


def is_proxy_symbol(symbol: str) -> bool:
    """Indica si el símbolo es un proxy de ejecución reconocido."""
    return symbol.strip().upper() in REVERSE_PROXIES


def is_canonical_symbol(symbol: str) -> bool:
    """Indica si el símbolo es un activo canónico con proxy disponible."""
    return symbol.strip().upper() in CANONICAL_PROXIES


def get_proxy_pair(symbol: str) -> tuple[str, str]:
    """Retorna la tupla (signal_symbol, execution_symbol) para el símbolo dado."""
    clean = symbol.strip().upper()
    if clean in CANONICAL_PROXIES:
        return clean, CANONICAL_PROXIES[clean]
    if clean in REVERSE_PROXIES:
        return REVERSE_PROXIES[clean], clean
    return clean, clean


def map_symbol(
    symbol: str,
    direction: Literal["signal_to_exec", "exec_to_signal"] = "signal_to_exec",
) -> str:
    """Función de mapeo compatible con los contratos de MarketDataProvider."""
    if direction == "signal_to_exec":
        return resolve_trading_proxy(symbol)
    elif direction == "exec_to_signal":
        return resolve_data_proxy(symbol)
    raise ValueError(f"Dirección de mapeo desconocida: {direction}")


class ProxyMapper:
    """Gestor configurable de mapeos de proxies con soporte de base de datos."""

    def __init__(self, initial_mappings: dict[str, str] | None = None) -> None:
        self._signal_to_exec = dict(CANONICAL_PROXIES)
        self._exec_to_signal = dict(REVERSE_PROXIES)
        if initial_mappings:
            for sig, exc in initial_mappings.items():
                s_clean = sig.strip().upper()
                e_clean = exc.strip().upper()
                self._signal_to_exec[s_clean] = e_clean
                self._exec_to_signal[e_clean] = s_clean

    def resolve_trading(self, symbol: str) -> str:
        clean = symbol.strip().upper()
        return self._signal_to_exec.get(clean, clean)

    def resolve_data(self, symbol: str) -> str:
        clean = symbol.strip().upper()
        return self._exec_to_signal.get(clean, clean)

    def map_symbol(
        self,
        symbol: str,
        direction: Literal["signal_to_exec", "exec_to_signal"] = "signal_to_exec",
    ) -> str:
        if direction == "signal_to_exec":
            return self.resolve_trading(symbol)
        elif direction == "exec_to_signal":
            return self.resolve_data(symbol)
        raise ValueError(f"Dirección de mapeo desconocida: {direction}")

    async def sync_from_db(self, session: AsyncSession) -> None:
        """Carga mapeos adicionales desde la tabla universe."""
        stmt = select(UniverseSymbol).where(UniverseSymbol.enabled.is_(True))
        records = (await session.scalars(stmt)).all()
        for rec in records:
            s_clean = rec.signal_symbol.strip().upper()
            e_clean = rec.execution_symbol.strip().upper()
            self._signal_to_exec[s_clean] = e_clean
            self._exec_to_signal[e_clean] = s_clean
