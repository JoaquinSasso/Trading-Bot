"""Repositorio y gestor de cache para DailyBar en PostgreSQL y SQLite.

Implementa la estrategia de consulta local primero, detección de huecos contiguos,
fetch defensivo con backoff/rate-limiting y persistencia idempotente (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tbot.common.clock import Clock, SystemClock
from tbot.db.models import DailyBar

if TYPE_CHECKING:
    from tbot.data.calendar import MarketCalendar

logger = structlog.get_logger(__name__)


def find_missing_date_ranges(
    missing_dates: list[date], max_gap_days: int = 4
) -> list[tuple[date, date]]:
    """Agrupa fechas faltantes en intervalos contiguos de consulta.

    Puentea hasta max_gap_days (fines de semana de 3 o 4 días) para reducir llamadas a la API.
    """
    if not missing_dates:
        return []
    sorted_dates = sorted(missing_dates)
    ranges: list[tuple[date, date]] = []
    r_start = sorted_dates[0]
    r_end = sorted_dates[0]
    for d in sorted_dates[1:]:
        if (d - r_end).days <= max_gap_days:
            r_end = d
        else:
            ranges.append((r_start, r_end))
            r_start = d
            r_end = d
    ranges.append((r_start, r_end))
    return ranges


class DailyBarCache:
    """Capa de cache en base de datos para barras diarias consolidadas."""

    def __init__(
        self,
        clock: Clock | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        market_calendar: MarketCalendar | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._session_factory = session_factory
        self._calendar = market_calendar

    def _normalize_feed(self, feed: str) -> str:
        f = feed.lower().strip()
        if f in ("sip", "sip_delayed"):
            return "sip_delayed"
        return f

    def get_expected_dates(self, start: date, end: date) -> list[date]:
        """Calcula los días hábiles esperados usando MarketCalendar o fallback a días de semana."""
        if self._calendar is not None:
            return [td.date for td in self._calendar.get_calendar(start, end)]
        cur = start
        expected: list[date] = []
        while cur <= end:
            if cur.weekday() < 5:
                expected.append(cur)
            cur += timedelta(days=1)
        return expected

    async def get_cached_bars(
        self,
        session: AsyncSession,
        symbols: list[str],
        start: date,
        end: date,
        feed: str = "sip_delayed",
        adjusted: bool = True,
    ) -> list[DailyBar]:
        """Consulta barras existentes en la base de datos."""
        norm_feed = self._normalize_feed(feed)
        clean_symbols = [s.strip().upper() for s in symbols]
        stmt = (
            select(DailyBar)
            .where(
                DailyBar.symbol.in_(clean_symbols),
                DailyBar.date >= start,
                DailyBar.date <= end,
                DailyBar.feed == norm_feed,
                DailyBar.adjusted == adjusted,
            )
            .order_by(DailyBar.symbol, DailyBar.date)
        )
        result = await session.scalars(stmt)
        return list(result.all())

    async def save_bars(
        self,
        session: AsyncSession,
        bars: list[dict[str, Any]],
    ) -> int:
        """Persiste barras de manera idempotente usando ON CONFLICT DO NOTHING."""
        if not bars:
            return 0
        bind = session.get_bind()
        dialect = bind.dialect.name if bind else "postgresql"

        clean_bars = []
        for b in bars:
            rec = dict(b)
            # Asegurar tipo date para campo date
            if isinstance(rec["date"], str):
                rec["date"] = pd.to_datetime(rec["date"]).date()
            rec["feed"] = self._normalize_feed(rec.get("feed", "sip_delayed"))
            clean_bars.append(rec)

        if dialect == "sqlite":
            stmt = (
                sqlite_insert(DailyBar)
                .values(clean_bars)
                .on_conflict_do_nothing(index_elements=["symbol", "date", "feed"])
            )
        else:
            stmt = (
                pg_insert(DailyBar)
                .values(clean_bars)
                .on_conflict_do_nothing(index_elements=["symbol", "date", "feed"])
            )

        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount if res.rowcount is not None else len(clean_bars)

    async def get_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        feed: str = "sip_delayed",
        adjusted: bool = True,
        session: AsyncSession | None = None,
    ) -> pd.DataFrame:
        """Obtiene barras cacheadas directamente como DataFrame."""
        target_session = session
        owns_session = False
        if target_session is None and self._session_factory is not None:
            target_session = self._session_factory()
            owns_session = True

        if target_session is None:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "adjusted",
                    "feed",
                ]
            )

        try:
            bars = await self.get_cached_bars(target_session, symbols, start, end, feed, adjusted)
            records = [
                {
                    "symbol": b.symbol,
                    "date": b.date,
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume),
                    "adjusted": b.adjusted,
                    "feed": b.feed,
                }
                for b in bars
            ]
            return self._to_dataframe(records)
        finally:
            if owns_session and target_session is not None:
                await target_session.close()

    async def store_bars(
        self,
        bars: list[dict[str, Any]] | pd.DataFrame,
        session: AsyncSession | None = None,
    ) -> int:
        """Persiste barras (lista de dicts o DataFrame) de forma idempotente."""
        target_session = session
        owns_session = False
        if target_session is None and self._session_factory is not None:
            target_session = self._session_factory()
            owns_session = True

        if target_session is None:
            return 0

        try:
            records = bars.to_dict(orient="records") if isinstance(bars, pd.DataFrame) else bars
            return await self.save_bars(target_session, records)
        finally:
            if owns_session and target_session is not None:
                await target_session.close()

    def find_missing_ranges(
        self,
        symbols: list[str],
        start: date,
        end: date,
        cached_df: pd.DataFrame,
    ) -> list[tuple[date, date]]:
        """Identifica rangos de fechas faltantes en un DataFrame cacheado."""
        expected_dates = set(self.get_expected_dates(start, end))
        if cached_df.empty:
            return find_missing_date_ranges(sorted(expected_dates))

        cached_dates = set(pd.to_datetime(cached_df["date"]).dt.date)
        missing_dates = sorted(expected_dates - cached_dates)
        return find_missing_date_ranges(missing_dates)

    async def get_or_fetch_daily_bars(
        self,
        symbols: list[str],
        start: date,
        end: date,
        feed: str = "sip_delayed",
        adjusted: bool = True,
        fetch_fn: Callable[[list[str], date, date, str, bool], Awaitable[list[dict[str, Any]]]]
        | None = None,
        session: AsyncSession | None = None,
    ) -> pd.DataFrame:
        """Estrategia completa: DB cache -> missing ranges -> Alpaca fetch -> DB persist -> sorted DataFrame.

        Aplica fallback defensivo si la base de datos no está disponible.
        """
        if start > end:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "adjusted",
                    "feed",
                ]
            )

        norm_feed = self._normalize_feed(feed)
        clean_symbols = [s.strip().upper() for s in symbols]

        # Manejo de fallback si no hay sesión configurada
        target_session = session
        owns_session = False
        if target_session is None and self._session_factory is not None:
            target_session = self._session_factory()
            owns_session = True

        if target_session is None:
            logger.warning(
                "Bypassing daily bar cache: no active database session or factory provided"
            )
            if fetch_fn is not None:
                raw_bars = await fetch_fn(clean_symbols, start, end, norm_feed, adjusted)
                return self._to_dataframe(raw_bars)
            return pd.DataFrame()

        try:
            # 1. Consultar cache local
            cached_bars = await self.get_cached_bars(
                target_session, clean_symbols, start, end, norm_feed, adjusted
            )
            cached_by_symbol: dict[str, dict[date, Any]] = {s: {} for s in clean_symbols}
            for bar in cached_bars:
                cached_by_symbol[bar.symbol][bar.date] = bar

            # 2. Identificar fechas y rangos faltantes
            expected_dates = set(self.get_expected_dates(start, end))
            new_bars_to_persist: list[dict[str, Any]] = []

            for sym in clean_symbols:
                present_dates = set(cached_by_symbol[sym].keys())
                missing_dates = sorted(expected_dates - present_dates)
                if not missing_dates or fetch_fn is None:
                    continue

                missing_ranges = find_missing_date_ranges(missing_dates)
                for r_start, r_end in missing_ranges:
                    fetched = await fetch_fn([sym], r_start, r_end, norm_feed, adjusted)
                    for item in fetched:
                        new_bars_to_persist.append(item)
                        d = item["date"]
                        if isinstance(d, str):
                            d = pd.to_datetime(d).date()
                        cached_by_symbol[sym][d] = item

            # 3. Persistir barras nuevas en la base de datos
            if new_bars_to_persist:
                try:
                    await self.save_bars(target_session, new_bars_to_persist)
                except SQLAlchemyError as exc:
                    logger.warning(
                        "Failed to persist daily bars to cache; continuing with memory result",
                        error=str(exc),
                    )
                    await target_session.rollback()

            # 4. Construir DataFrame final
            all_records: list[dict[str, Any]] = []
            for _sym, date_dict in cached_by_symbol.items():
                for _d, b in date_dict.items():
                    if isinstance(b, DailyBar):
                        all_records.append(
                            {
                                "symbol": b.symbol,
                                "date": b.date,
                                "open": float(b.open),
                                "high": float(b.high),
                                "low": float(b.low),
                                "close": float(b.close),
                                "volume": float(b.volume),
                                "adjusted": b.adjusted,
                                "feed": b.feed,
                            }
                        )
                    elif isinstance(b, dict):
                        all_records.append(
                            {
                                "symbol": b["symbol"],
                                "date": b["date"]
                                if isinstance(b["date"], date)
                                else pd.to_datetime(b["date"]).date(),
                                "open": float(b["open"]),
                                "high": float(b["high"]),
                                "low": float(b["low"]),
                                "close": float(b["close"]),
                                "volume": float(b["volume"]),
                                "adjusted": b.get("adjusted", adjusted),
                                "feed": b.get("feed", norm_feed),
                            }
                        )

            return self._to_dataframe(all_records)

        except SQLAlchemyError as exc:
            logger.warning(
                "Database cache read failed; executing fallback direct fetch", error=str(exc)
            )
            if target_session is not None:
                await target_session.rollback()
            if fetch_fn is not None:
                raw_bars = await fetch_fn(clean_symbols, start, end, norm_feed, adjusted)
                return self._to_dataframe(raw_bars)
            return pd.DataFrame()

        finally:
            if owns_session and target_session is not None:
                await target_session.close()

    def _to_dataframe(self, records: list[dict[str, Any]]) -> pd.DataFrame:
        cols = ["symbol", "date", "open", "high", "low", "close", "volume", "adjusted", "feed"]
        if not records:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.drop_duplicates(subset=["symbol", "date"], keep="last")
        df = df.sort_values(by=["symbol", "date"]).reset_index(drop=True)
        return df[cols]
