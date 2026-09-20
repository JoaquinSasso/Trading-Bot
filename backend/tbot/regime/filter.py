"""Filtro de régimen de mercado para el bot de trading algorítmico.

Clasifica el estado del mercado en BULL_CALM, BULL_VOLATILE, BEAR, o UNKNOWN
utilizando barras diarias de SPY (S&P 500) a partir de SMA(200) y el percentil 70
de la volatilidad realizada de 20 días sobre un horizonte de 2 años (~504 días de trading).

Diseño de falla cerrada (fail-closed): Si los datos son insuficientes (< 200 barras)
o inválidos, el régimen evalúa a UNKNOWN, estableciendo is_blocked=True y bloqueando
todas las entradas nuevas en cualquier estrategia.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tbot.common.clock import Clock
from tbot.db.models import RegimeSnapshot
from tbot.indicators.pure import realized_volatility_20d, sma


class MarketRegime(StrEnum):
    """Regímenes de mercado reconocidos por el sistema."""

    BULL_CALM = "BULL_CALM"
    BULL_VOLATILE = "BULL_VOLATILE"
    BEAR = "BEAR"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def values(cls) -> list[str]:
        """Retorna la lista de valores de cadena válidos."""
        return [e.value for e in cls]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Indica si una cadena corresponde a un régimen válido."""
        return value in cls.values()


# Alias para ergonomía
Regime = MarketRegime


class RegimePersistenceError(Exception):
    """Excepción lanzada cuando falla la persistencia de un snapshot de régimen."""


class RegimeFilter:
    """Motor de evaluación y clasificación de régimen de mercado sobre SPY."""

    def __init__(
        self,
        clock: Clock,
        min_bars: int = 200,
        sma_period: int = 200,
        vol_window: int = 20,
        vol_lookback_bars: int = 504,
        vol_percentile: float = 70.0,
        inclusive_volatile: bool = True,
    ) -> None:
        """Inicializa el filtro con reloj inyectable y parámetros de umbral."""
        self.clock = clock
        self.min_bars = min_bars
        self.sma_period = sma_period
        self.vol_window = vol_window
        self.vol_lookback_bars = vol_lookback_bars
        self.vol_percentile = vol_percentile
        self.inclusive_volatile = inclusive_volatile

    def _build_unknown_snapshot(
        self,
        reason: str,
        spy_daily_bars: pd.DataFrame | None = None,
        close: float | None = None,
        sma_val: float | None = None,
        vol_val: float | None = None,
        p70_val: float | None = None,
    ) -> RegimeSnapshot:
        """Construye un snapshot en estado UNKNOWN aplicando fail-closed."""
        bar_count = (
            len(spy_daily_bars)
            if spy_daily_bars is not None and isinstance(spy_daily_bars, pd.DataFrame)
            else 0
        )
        return RegimeSnapshot(
            ts=self.clock.now(),
            regime=MarketRegime.UNKNOWN.value,
            inputs={
                "spy_close": round(close, 4) if close is not None else None,
                "sma_200": round(sma_val, 4) if sma_val is not None else None,
                "realized_vol_20d": round(vol_val, 6) if vol_val is not None else None,
                "vol_70th_percentile": round(p70_val, 6) if p70_val is not None else None,
                "bar_count": bar_count,
                "is_blocked": True,
                "reason": reason,
                "sma_period": self.sma_period,
                "vol_window": self.vol_window,
                "vol_lookback_bars": self.vol_lookback_bars,
                "vol_percentile": self.vol_percentile,
            },
        )

    def classify(self, spy_daily_bars: pd.DataFrame) -> RegimeSnapshot:
        """Clasifica el régimen de mercado a partir de un DataFrame de barras diarias de SPY."""
        if not isinstance(spy_daily_bars, pd.DataFrame):
            raise TypeError(f"spy_daily_bars must be a pd.DataFrame, got {type(spy_daily_bars).__name__}")

        if spy_daily_bars.empty:
            return self._build_unknown_snapshot(reason="EMPTY_DATAFRAME", spy_daily_bars=spy_daily_bars)

        df = spy_daily_bars
        if "symbol" in df.columns:
            df = df[df["symbol"].str.upper() == "SPY"]
            if df.empty:
                return self._build_unknown_snapshot(reason="NO_SPY_BARS_FOUND", spy_daily_bars=spy_daily_bars)

        close_col = None
        for col in ("close", "Close"):
            if col in df.columns:
                close_col = col
                break

        if close_col is None:
            return self._build_unknown_snapshot(reason="MISSING_CLOSE_COLUMN", spy_daily_bars=df)

        if "date" in df.columns:
            df = df.sort_values("date")
        elif "Date" in df.columns:
            df = df.sort_values("Date")
        elif "timestamp" in df.columns:
            df = df.sort_values("timestamp")
        elif "Timestamp" in df.columns:
            df = df.sort_values("Timestamp")
        elif not df.index.is_monotonic_increasing:
            df = df.sort_index()

        for d_col in ("date", "Date", "timestamp", "Timestamp"):
            if d_col in df.columns:
                df = df.drop_duplicates(subset=[d_col], keep="last")
                break

        bar_count = len(df)
        if bar_count < self.min_bars:
            return self._build_unknown_snapshot(
                reason=f"INSUFFICIENT_BARS ({bar_count} < {self.min_bars})",
                spy_daily_bars=df,
            )

        close_series = pd.to_numeric(df[close_col], errors="coerce").reset_index(drop=True)

        if close_series.isna().any() and pd.isna(close_series.iloc[-1]):
            return self._build_unknown_snapshot(reason="LATEST_CLOSE_IS_NAN", spy_daily_bars=df)

        if (close_series <= 0).any():
            return self._build_unknown_snapshot(reason="NON_POSITIVE_PRICE_DETECTED", spy_daily_bars=df)

        sma_series = sma(close_series, period=self.sma_period)
        curr_sma = sma_series.iloc[-1]
        if pd.isna(curr_sma):
            return self._build_unknown_snapshot(reason="SMA_CALCULATION_NAN", spy_daily_bars=df)

        vol_series = realized_volatility_20d(close_series, annualized=True, window=self.vol_window)
        curr_vol = vol_series.iloc[-1]
        if pd.isna(curr_vol):
            return self._build_unknown_snapshot(
                reason="VOLATILITY_CALCULATION_NAN",
                spy_daily_bars=df,
                sma_val=float(curr_sma),
            )

        valid_vols = vol_series.dropna()
        if valid_vols.empty:
            return self._build_unknown_snapshot(
                reason="EMPTY_VOLATILITY_HISTORY",
                spy_daily_bars=df,
                sma_val=float(curr_sma),
            )

        lookback_vols = valid_vols.iloc[-self.vol_lookback_bars:]
        p70 = float(np.percentile(lookback_vols, self.vol_percentile))

        curr_close = float(close_series.iloc[-1])
        curr_sma = float(curr_sma)
        curr_vol = float(curr_vol)

        if curr_close <= curr_sma:
            regime = MarketRegime.BEAR
        else:
            is_volatile = (curr_vol >= p70) if self.inclusive_volatile else (curr_vol > p70)
            regime = MarketRegime.BULL_VOLATILE if is_volatile else MarketRegime.BULL_CALM

        return RegimeSnapshot(
            ts=self.clock.now(),
            regime=regime.value,
            inputs={
                "spy_close": round(curr_close, 4),
                "sma_200": round(curr_sma, 4),
                "realized_vol_20d": round(curr_vol, 6),
                "vol_70th_percentile": round(p70, 6),
                "bar_count": bar_count,
                "is_blocked": False,
                "reason": "OK",
                "sma_period": self.sma_period,
                "vol_window": self.vol_window,
                "vol_lookback_bars": self.vol_lookback_bars,
                "vol_percentile": self.vol_percentile,
            },
        )

    async def persist_snapshot(
        self,
        session: AsyncSession,
        snapshot: RegimeSnapshot,
        upsert: bool = True,
    ) -> RegimeSnapshot:
        """Persiste un RegimeSnapshot en la tabla regime_snapshots."""
        if snapshot.ts is None or snapshot.regime is None or snapshot.inputs is None:
            raise ValueError("Snapshot missing required fields: ts, regime, inputs")

        if not MarketRegime.is_valid(snapshot.regime):
            raise ValueError(
                f"Invalid regime string '{snapshot.regime}'. Must be one of {MarketRegime.values()}"
            )

        try:
            if upsert:
                stmt = select(RegimeSnapshot).where(RegimeSnapshot.ts == snapshot.ts)
                existing = await session.scalar(stmt)
                if existing is not None:
                    existing.regime = snapshot.regime
                    existing.inputs = snapshot.inputs
                    await session.flush()
                    return existing

            session.add(snapshot)
            await session.flush()
            return snapshot
        except Exception as e:
            await session.rollback()
            raise RegimePersistenceError(f"Failed to persist regime snapshot: {e}") from e

    async def classify_and_persist(
        self,
        session: AsyncSession,
        spy_daily_bars: pd.DataFrame,
        upsert: bool = True,
    ) -> RegimeSnapshot:
        """Clasifica el régimen y persiste el snapshot en una sola operación."""
        snapshot = self.classify(spy_daily_bars)
        return await self.persist_snapshot(session, snapshot, upsert=upsert)

    async def get_latest_snapshot(self, session: AsyncSession) -> RegimeSnapshot | None:
        """Consulta el snapshot de régimen más reciente por timestamp."""
        stmt = select(RegimeSnapshot).order_by(RegimeSnapshot.ts.desc()).limit(1)
        return await session.scalar(stmt)

    async def get_snapshots_range(
        self,
        session: AsyncSession,
        start_ts: Any,
        end_ts: Any,
        limit: int = 500,
    ) -> list[RegimeSnapshot]:
        """Consulta snapshots dentro de un rango temporal [start_ts, end_ts]."""
        stmt = (
            select(RegimeSnapshot)
            .where(RegimeSnapshot.ts >= start_ts, RegimeSnapshot.ts <= end_ts)
            .order_by(RegimeSnapshot.ts.asc())
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return list(result.all())


async def persist_regime_snapshot(
    session: AsyncSession,
    snapshot: RegimeSnapshot,
    upsert: bool = True,
) -> RegimeSnapshot:
    """Función de utilidad independiente para persistir snapshots."""
    filter_engine = RegimeFilter(clock=None)  # type: ignore[arg-type]
    return await filter_engine.persist_snapshot(session, snapshot, upsert=upsert)
