"""Contratos e interfaces (Protocols) para la capa de datos de mercado."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from tbot.data.types import MarketClock, PriceQuote, TradeQuote, TradingDay


@runtime_checkable
class MarketDataProvider(Protocol):
    """Protocolo formal para el proveedor de datos de mercado."""

    async def get_daily_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Obtiene barras diarias históricas consolidadas."""
        ...

    async def get_intraday_bars(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        feed: str = "iex",
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Obtiene barras intradía (1m o 5m)."""
        ...

    async def get_latest_quote(self, symbol: str) -> PriceQuote:
        """Obtiene la última cotización bid/ask en tiempo real desde el feed IEX."""
        ...

    async def get_latest_trade(self, symbol: str) -> TradeQuote:
        """Obtiene el último trade ejecutado en tiempo real desde el feed IEX."""
        ...

    async def get_market_clock(self) -> MarketClock:
        """Retorna el estado actual del reloj de mercado."""
        ...

    async def get_calendar(self, start: date, end: date) -> list[TradingDay]:
        """Retorna las sesiones de negociación hábiles en el rango dado."""
        ...
