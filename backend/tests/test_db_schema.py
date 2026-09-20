"""Pruebas del esquema de base de datos y modelos ORM."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tbot.db.base import Base
from tbot.db.models import (
    DailyBar,
    OrderRecord,
    PositionMeta,
    UniverseSymbol,
)


def test_all_expected_tables_present_in_metadata() -> None:
    """Verifica que las 22 tablas requeridas estén registradas en el metadata."""
    expected_tables = {
        "runtime_config",
        "config_history",
        "universe",
        "daily_bars",
        "regime_snapshots",
        "events",
        "news_items",
        "news_features",
        "signals",
        "llm_calls",
        "veto_decisions",
        "risk_decisions",
        "orders",
        "position_meta",
        "trades",
        "equity_snapshots",
        "intents",
        "approvals",
        "alerts",
        "reports",
        "quota_usage",
        "audit_log",
    }
    registered_tables = set(Base.metadata.tables.keys())
    missing = expected_tables - registered_tables
    assert not missing, f"Faltan tablas en el modelo ORM: {missing}"


@pytest.mark.asyncio
async def test_insert_and_query_universe_with_proxies(test_session: AsyncSession) -> None:
    """Verifica la inserción y consulta en la tabla universe con proxy symbols."""
    now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
    spy_entry = UniverseSymbol(
        signal_symbol="SPY",
        execution_symbol="SPYM",
        cluster="index_broad",
        enabled=True,
        notes="Proxy S&P 500",
        updated_at=now,
    )
    test_session.add(spy_entry)
    await test_session.commit()

    stmt = select(UniverseSymbol).where(UniverseSymbol.signal_symbol == "SPY")
    result = await test_session.scalar(stmt)
    assert result is not None
    assert result.execution_symbol == "SPYM"
    assert result.cluster == "index_broad"
    assert result.enabled is True


@pytest.mark.asyncio
async def test_insert_daily_bar_and_precision(test_session: AsyncSession) -> None:
    """Verifica la inserción de barras con precisión decimal."""
    bar = DailyBar(
        symbol="SPYM",
        date=date(2026, 9, 18),
        open=Decimal("70.5000"),
        high=Decimal("71.2000"),
        low=Decimal("70.1000"),
        close=Decimal("70.9500"),
        volume=Decimal("1500000"),
        adjusted=True,
        feed="sip_delayed",
    )
    test_session.add(bar)
    await test_session.commit()

    stmt = select(DailyBar).where(DailyBar.symbol == "SPYM")
    saved_bar = await test_session.scalar(stmt)
    assert saved_bar is not None
    assert saved_bar.close == Decimal("70.9500")
    assert saved_bar.feed == "sip_delayed"


@pytest.mark.asyncio
async def test_order_record_and_position_meta(test_session: AsyncSession) -> None:
    """Verifica el registro de órdenes determinísticas y metadatos de posición."""
    now = datetime(2026, 9, 19, 15, 30, 0, tzinfo=UTC)
    order = OrderRecord(
        client_order_id="bot-sig123456789012345678901234-entry",
        broker_order_id="alpaca-ord-999",
        owner="bot",
        signal_id="sig123456789012345678901234",
        signal_symbol="SPY",
        execution_symbol="SPYM",
        leg="entry",
        type="market",
        tif="day",
        qty=Decimal("4.0000"),
        notional=Decimal("283.80"),
        status="filled",
        submitted_at=now,
        filled_qty=Decimal("4.0000"),
        avg_fill_price=Decimal("70.9500"),
    )
    pos = PositionMeta(
        symbol="SPY",
        execution_symbol="SPYM",
        owner="bot",
        strategy_id="intraday_momentum_v1",
        entry_price=Decimal("70.9500"),
        stop_price=Decimal("69.8000"),
        tp_price=Decimal("72.5000"),
        opened_at=now,
        path="whole",
        exit_at_close=True,
        unmanaged=False,
    )
    test_session.add_all([order, pos])
    await test_session.commit()

    saved_pos = await test_session.scalar(select(PositionMeta).where(PositionMeta.symbol == "SPY"))
    assert saved_pos is not None
    assert saved_pos.execution_symbol == "SPYM"
    assert saved_pos.path == "whole"
    assert saved_pos.exit_at_close is True
