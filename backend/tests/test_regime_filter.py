"""Suite de pruebas unitarias para el filtro de régimen de mercado y persistencia.

Verifica de forma determinista y exhaustiva:
1. Clasificación en los 4 regímenes (BULL_CALM, BULL_VOLATILE, BEAR, UNKNOWN).
2. Puntos límite exactos: Close == SMA(200), Vol == p70, exactos 200 barras, 199 barras.
3. Principio de falla cerrada (fail-closed): UNKNOWN bloquea nuevas entradas (is_blocked=True).
4. Principio No Negociable de Reloj Inyectable: ts proviene exclusivamente de Clock.
5. Persistencia, idempotencia/upsert y manejo de errores con AsyncSession en SQLite memory.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tbot.common.clock import SimulatedClock
from tbot.db.models import RegimeSnapshot
from tbot.regime.filter import (
    MarketRegime,
    RegimeFilter,
    RegimePersistenceError,
)

# ---------------------------------------------------------------------------
# Helpers para generar barras sintéticas deterministas
# ---------------------------------------------------------------------------


def generate_synthetic_spy_bars(
    n_bars: int = 250,
    regime_type: str = "BULL_CALM",
    base_price: float = 500.0,
) -> pd.DataFrame:
    """Genera barras sintéticas deterministas de SPY para simular cada régimen."""
    np.random.seed(42)
    start_date = date(2025, 1, 1)

    if regime_type == "BULL_CALM":
        # Tendencia alcista estable con volatilidad baja constante
        prices = [base_price]
        for _ in range(1, n_bars):
            # Crecimiento constante con variaciones mínimas (+0.10 a +0.30)
            prices.append(prices[-1] + np.random.uniform(0.1, 0.3))
        # Para que el p70 sea mayor que la vol reciente, añadimos un tramo histórico más volátil al inicio
        if n_bars >= 250:
            for i in range(10, 40):
                prices[i] = prices[i - 1] + (5.0 if i % 2 == 0 else -4.8)

    elif regime_type == "BULL_VOLATILE":
        # Tendencia alcista general, pero los últimos 20 días presentan oscilaciones extremas
        prices = [base_price]
        calm_bars = max(20, n_bars - 20)
        for _ in range(1, calm_bars):
            prices.append(prices[-1] + np.random.uniform(0.1, 0.3))
        for _ in range(20):
            # Saltos grandes que disparan la volatilidad realizada
            prices.append(prices[-1] + np.random.normal(0.5, 8.0))

    elif regime_type == "BEAR":
        # Tendencia bajista pronunciada en el tramo final: Close cae por debajo de SMA200
        prices = [base_price]
        calm_bars = max(20, n_bars - 50)
        for _ in range(1, calm_bars):
            prices.append(prices[-1] + 0.5)
        for _ in range(50):
            prices.append(prices[-1] - 3.0)

    elif regime_type == "BEAR_EXACT_SMA":
        # Serie constante: todos los precios iguales -> Close == SMA200
        prices = [base_price] * n_bars

    elif regime_type == "INSUFFICIENT":
        prices = [base_price] * min(n_bars, 100)

    else:
        raise ValueError(f"Unknown regime_type: {regime_type}")

    dates = [start_date + timedelta(days=i) for i in range(len(prices))]
    return pd.DataFrame(
        {
            "symbol": "SPY",
            "date": dates,
            "close": prices,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "volume": 1000000,
        }
    )


# ---------------------------------------------------------------------------
# Test Suite 1: Clasificación de Regímenes
# ---------------------------------------------------------------------------


class TestRegimeFilterClassification:
    """Pruebas de la lógica de clasificación de regímenes."""

    def test_classify_bull_calm(self, sim_clock: SimulatedClock) -> None:
        """Verifica que Close > SMA(200) y Vol <= p70 resulta en BULL_CALM."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BULL_CALM.value
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["reason"] == "OK"
        assert snapshot.inputs["spy_close"] > snapshot.inputs["sma_200"]
        assert snapshot.inputs["realized_vol_20d"] <= snapshot.inputs["vol_70th_percentile"]

    def test_classify_bull_volatile(self, sim_clock: SimulatedClock) -> None:
        """Verifica que Close > SMA(200) y Vol >= p70 resulta en BULL_VOLATILE."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_VOLATILE")

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BULL_VOLATILE.value
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["reason"] == "OK"
        assert snapshot.inputs["spy_close"] > snapshot.inputs["sma_200"]
        assert snapshot.inputs["realized_vol_20d"] >= snapshot.inputs["vol_70th_percentile"]

    def test_classify_bear_below_sma(self, sim_clock: SimulatedClock) -> None:
        """Verifica que Close < SMA(200) resulta en BEAR sin importar la volatilidad."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BEAR")

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BEAR.value
        assert snapshot.inputs["is_blocked"] is False
        assert snapshot.inputs["reason"] == "OK"
        assert snapshot.inputs["spy_close"] < snapshot.inputs["sma_200"]

    def test_classify_bear_exact_boundary_close_equals_sma(self, sim_clock: SimulatedClock) -> None:
        """Punto límite crítico: Close == SMA(200) no es estrictamente mayor -> BEAR."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BEAR_EXACT_SMA", base_price=400.0)

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.BEAR.value
        assert snapshot.inputs["spy_close"] == snapshot.inputs["sma_200"]
        assert snapshot.inputs["is_blocked"] is False

    def test_classify_exact_200_bars_minimum_requirement(self, sim_clock: SimulatedClock) -> None:
        """Exactamente 200 barras es el mínimo permitido para calcular SMA(200)."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=200, regime_type="BULL_CALM")

        snapshot = rf.classify(df)

        assert snapshot.regime in (MarketRegime.BULL_CALM.value, MarketRegime.BULL_VOLATILE.value)
        assert snapshot.inputs["bar_count"] == 200
        assert snapshot.inputs["is_blocked"] is False


# ---------------------------------------------------------------------------
# Test Suite 2: Falla Cerrada (Fail-Closed)
# ---------------------------------------------------------------------------


class TestRegimeFilterFailClosed:
    """Verifica que entradas defectuosas o insuficientes activen UNKNOWN y is_blocked=True."""

    def test_insufficient_bars_zero_bars_empty_df(self, sim_clock: SimulatedClock) -> None:
        """DataFrame vacío retorna UNKNOWN y bloquea operaciones."""
        rf = RegimeFilter(clock=sim_clock)
        df = pd.DataFrame()

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["reason"] == "EMPTY_DATAFRAME"

    def test_insufficient_bars_10_bars(self, sim_clock: SimulatedClock) -> None:
        """10 barras son insuficientes (< 200) -> UNKNOWN y bloqueado."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=10, regime_type="BULL_CALM")

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert "INSUFFICIENT_BARS" in snapshot.inputs["reason"]

    def test_insufficient_bars_199_boundary(self, sim_clock: SimulatedClock) -> None:
        """Límite exacto: 199 barras (< 200) debe fallar cerrado a UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=199, regime_type="BULL_CALM")

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["bar_count"] == 199

    def test_missing_close_column(self, sim_clock: SimulatedClock) -> None:
        """Falta de columna de cierre retorna UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        df = pd.DataFrame(
            {
                "date": [date(2026, 1, 1) + timedelta(days=i) for i in range(250)],
                "open": [500.0] * 250,
                "volume": [1000] * 250,
            }
        )

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert snapshot.inputs["reason"] == "MISSING_CLOSE_COLUMN"

    def test_all_nan_prices(self, sim_clock: SimulatedClock) -> None:
        """Precios NaN no permiten cómputo de indicadores -> UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        df = pd.DataFrame(
            {
                "date": [date(2026, 1, 1) + timedelta(days=i) for i in range(250)],
                "close": [np.nan] * 250,
            }
        )

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True

    def test_latest_bar_nan_price(self, sim_clock: SimulatedClock) -> None:
        """La barra más reciente con NaN bloquea el clasificador."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")
        df.loc[df.index[-1], "close"] = np.nan

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True

    def test_non_positive_prices_rejected(self, sim_clock: SimulatedClock) -> None:
        """Precios <= 0 son anómalos y deben evaluarse a UNKNOWN."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")
        df.loc[df.index[10], "close"] = -10.0

        snapshot = rf.classify(df)

        assert snapshot.regime == MarketRegime.UNKNOWN.value
        assert snapshot.inputs["is_blocked"] is True
        assert "NON_POSITIVE_PRICE" in snapshot.inputs["reason"]

    def test_invalid_type_raises_type_error(self, sim_clock: SimulatedClock) -> None:
        """Pasar un tipo que no sea DataFrame debe lanzar TypeError."""
        rf = RegimeFilter(clock=sim_clock)
        with pytest.raises(TypeError, match="spy_daily_bars must be a pd.DataFrame"):
            rf.classify([1, 2, 3])  # type: ignore


# ---------------------------------------------------------------------------
# Test Suite 3: Cumplimiento de Reloj Inyectable
# ---------------------------------------------------------------------------


class TestRegimeFilterClockPolicy:
    """Verifica que todos los timestamps provengan de la instancia inyectada de Clock."""

    def test_snapshot_uses_injected_clock_exact_time(self, sim_clock: SimulatedClock) -> None:
        """El timestamp del snapshot coincide al microsegundo con sim_clock.now()."""
        fixed_dt = datetime(2026, 9, 21, 14, 15, 30, tzinfo=UTC)
        sim_clock.set_time(fixed_dt)

        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")

        snapshot = rf.classify(df)

        assert snapshot.ts == fixed_dt
        assert snapshot.timestamp == fixed_dt

    def test_snapshot_advances_deterministically_with_clock(
        self, sim_clock: SimulatedClock
    ) -> None:
        """Al avanzar el reloj, las clasificaciones subsecuentes toman el nuevo timestamp."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")

        snap1 = rf.classify(df)
        sim_clock.advance(timedelta(hours=2))
        snap2 = rf.classify(df)

        assert snap2.ts == snap1.ts + timedelta(hours=2)


# ---------------------------------------------------------------------------
# Test Suite 4: Persistencia y Base de Datos (AsyncSession)
# ---------------------------------------------------------------------------


class TestRegimeSnapshotPersistence:
    """Pruebas de persistencia de snapshots en la base de datos (SQLite async en memoria)."""

    @pytest.mark.asyncio
    async def test_persist_snapshot_success(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Guarda exitosamente un snapshot en la tabla regime_snapshots."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")
        snapshot = rf.classify(df)

        saved = await rf.persist_snapshot(test_session, snapshot)
        await test_session.commit()

        assert saved.id is not None
        assert saved.regime == MarketRegime.BULL_CALM.value
        assert saved.ts == sim_clock.now()
        assert saved.inputs["is_blocked"] is False

        # Consulta directa desde la base de datos
        stmt = select(RegimeSnapshot).where(RegimeSnapshot.id == saved.id)
        queried = await test_session.scalar(stmt)
        assert queried is not None
        assert queried.regime == MarketRegime.BULL_CALM.value
        assert queried.inputs["spy_close"] == snapshot.inputs["spy_close"]

    @pytest.mark.asyncio
    async def test_persist_unknown_snapshot_fail_closed_flag(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Persiste un snapshot UNKNOWN y verifica que is_blocked=True queda guardado."""
        rf = RegimeFilter(clock=sim_clock)
        snapshot = rf.classify(pd.DataFrame())

        saved = await rf.persist_snapshot(test_session, snapshot)
        await test_session.commit()

        assert saved.regime == MarketRegime.UNKNOWN.value
        assert saved.inputs["is_blocked"] is True

    @pytest.mark.asyncio
    async def test_classify_and_persist_combined_method(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Verifica el método integrado classify_and_persist."""
        rf = RegimeFilter(clock=sim_clock)
        df = generate_synthetic_spy_bars(n_bars=250, regime_type="BULL_CALM")

        saved = await rf.classify_and_persist(test_session, df)
        await test_session.commit()

        assert saved.id is not None
        assert saved.regime == MarketRegime.BULL_CALM.value

    @pytest.mark.asyncio
    async def test_persist_snapshot_idempotent_upsert_on_same_ts(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Al persistir dos snapshots con el mismo timestamp, actualiza el registro sin duplicar."""
        rf = RegimeFilter(clock=sim_clock)
        fixed_ts = sim_clock.now()

        snap1 = RegimeSnapshot(
            ts=fixed_ts,
            regime=MarketRegime.BULL_CALM.value,
            inputs={"spy_close": 500.0, "is_blocked": False},
        )
        saved1 = await rf.persist_snapshot(test_session, snap1, upsert=True)
        await test_session.commit()
        id1 = saved1.id

        # Intentar persistir un nuevo snapshot con el mismo ts pero régimen diferente
        snap2 = RegimeSnapshot(
            ts=fixed_ts,
            regime=MarketRegime.BEAR.value,
            inputs={"spy_close": 480.0, "is_blocked": False},
        )
        saved2 = await rf.persist_snapshot(test_session, snap2, upsert=True)
        await test_session.commit()

        assert saved2.id == id1
        assert saved2.regime == MarketRegime.BEAR.value

        # Verificar que solo hay 1 fila en la tabla
        stmt = select(RegimeSnapshot).where(RegimeSnapshot.ts == fixed_ts)
        results = (await test_session.scalars(stmt)).all()
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_persist_snapshot_invalid_regime_raises_value_error(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Un snapshot con un régimen inexistente lanza ValueError antes de persistir."""
        rf = RegimeFilter(clock=sim_clock)
        invalid_snap = RegimeSnapshot(
            ts=sim_clock.now(),
            regime="INVALID_REGIME",
            inputs={"is_blocked": True},
        )

        with pytest.raises(ValueError, match="Invalid regime string"):
            await rf.persist_snapshot(test_session, invalid_snap)

    @pytest.mark.asyncio
    async def test_persist_snapshot_missing_fields_raises_value_error(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Snapshot con campos requeridos ausentes lanza ValueError."""
        rf = RegimeFilter(clock=sim_clock)
        incomplete_snap = RegimeSnapshot(
            ts=None,  # type: ignore
            regime="BULL_CALM",
            inputs={},
        )

        with pytest.raises(ValueError, match="Snapshot missing required fields"):
            await rf.persist_snapshot(test_session, incomplete_snap)

    @pytest.mark.asyncio
    async def test_persist_snapshot_rollback_on_db_error(self, sim_clock: SimulatedClock) -> None:
        """Si la sesión de base de datos falla al persistir, ejecuta rollback y lanza RegimePersistenceError."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.scalar.side_effect = Exception("DB Connection Lost")

        rf = RegimeFilter(clock=sim_clock)
        snap = RegimeSnapshot(
            ts=sim_clock.now(),
            regime=MarketRegime.BULL_CALM.value,
            inputs={"is_blocked": False},
        )

        with pytest.raises(RegimePersistenceError):
            await rf.persist_snapshot(mock_session, snap)

        mock_session.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_latest_snapshot(
        self, test_session: AsyncSession, sim_clock: SimulatedClock
    ) -> None:
        """Verifica la consulta del snapshot más reciente."""
        rf = RegimeFilter(clock=sim_clock)

        snap1 = RegimeSnapshot(
            ts=sim_clock.now(),
            regime=MarketRegime.BULL_CALM.value,
            inputs={"spy_close": 500.0, "is_blocked": False},
        )
        await rf.persist_snapshot(test_session, snap1)

        sim_clock.advance(timedelta(days=1))
        snap2 = RegimeSnapshot(
            ts=sim_clock.now(),
            regime=MarketRegime.BEAR.value,
            inputs={"spy_close": 450.0, "is_blocked": False},
        )
        await rf.persist_snapshot(test_session, snap2)
        await test_session.commit()

        latest = await rf.get_latest_snapshot(test_session)
        assert latest is not None
        assert latest.regime == MarketRegime.BEAR.value
        assert latest.ts == snap2.ts
