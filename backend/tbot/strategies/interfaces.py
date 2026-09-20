"""Contratos, tipos de datos y protocolos base para las estrategias de trading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd

from tbot.regime.filter import MarketRegime


def compute_signal_id(
    strategy_id: str,
    version: str,
    symbol: str,
    bar_ts: datetime,
) -> str:
    """Genera un hash determinístico de 24 caracteres para identificar unívocamente una señal."""
    key = f"{strategy_id}:{version}:{symbol}:{bar_ts.isoformat()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Signal:
    """Representa una señal de trading generada por una estrategia."""

    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["buy"]  # Solo largos en la especificación
    entry_type: Literal["market", "limit"]
    entry_price_ref: Decimal
    stop_price: Decimal
    created_at: datetime
    limit_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    max_holding: timedelta | int = 5  # Minutos o días hábiles
    exit_at_close: bool = False  # Para estrategias intradía
    score: float = 1.0  # 0.0 - 1.0
    features: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        strategy_id: str,
        version: str,
        symbol: str,
        bar_ts: datetime,
        side: Literal["buy"],
        entry_type: Literal["market", "limit"],
        entry_price_ref: Decimal,
        stop_price: Decimal,
        limit_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
        max_holding: timedelta | int = 5,
        exit_at_close: bool = False,
        score: float = 1.0,
        features: dict[str, Any] | None = None,
    ) -> Signal:
        """Constructor helper que autogenera el signal_id determinístico."""
        sig_id = compute_signal_id(strategy_id, version, symbol, bar_ts)
        return cls(
            signal_id=sig_id,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            entry_type=entry_type,
            entry_price_ref=entry_price_ref,
            stop_price=stop_price,
            created_at=bar_ts,
            limit_price=limit_price,
            take_profit_price=take_profit_price,
            max_holding=max_holding,
            exit_at_close=exit_at_close,
            score=score,
            features=features or {},
        )


@dataclass(frozen=True)
class StrategyDataRequirements:
    """Requerimientos de datos que la estrategia declara para el pipeline."""

    needs_daily_bars: bool = True
    daily_lookback_days: int = 250
    needs_intraday_bars: bool = False
    intraday_timeframe: str = "5m"  # "1m" o "5m"
    intraday_lookback_bars: int = 100
    requires_sip_delayed: bool = True


@dataclass
class StrategyContext:
    """Contexto de ejecución inyectado a la estrategia en cada evaluación."""

    now: datetime  # Hora actual de la simulación o de mercado (UTC o con tz)
    regime: MarketRegime
    daily_bars: dict[
        str, pd.DataFrame
    ]  # symbol -> DataFrame con columnas [date, open, high, low, close, volume]
    intraday_bars: dict[str, pd.DataFrame] = field(
        default_factory=dict
    )  # symbol -> DataFrame de barras intradía
    current_prices: dict[str, Decimal] = field(
        default_factory=dict
    )  # symbol -> último precio conocido
    portfolio_positions: set[str] = field(default_factory=set)  # Símbolos ya abiertos
    parameters: dict[str, Any] = field(default_factory=dict)  # Parámetros custom de la estrategia


@runtime_checkable
class Strategy(Protocol):
    """Protocolo formal que toda estrategia de trading algorítmico debe implementar."""

    id: str
    version: str
    schedule: list[str]  # Especificaciones horarias (ej. ["15:30 America/New_York"])
    allowed_regimes: set[MarketRegime]
    allows_open_window: bool
    universe: list[str] | None  # None indica usar el universo general habilitado
    data_requirements: StrategyDataRequirements

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Evalúa las condiciones y genera la lista de señales deterministas."""
        ...
