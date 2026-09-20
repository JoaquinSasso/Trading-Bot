"""Fixtures comunes para la suite de pruebas de tbot."""

import os
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Añadir el backend al path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tbot.api.main import app
from tbot.common.clock import SimulatedClock
from tbot.db.base import Base
from tbot.db.session import get_db_session

# Usar SQLite async en memoria para tests unitarios rápidos y deterministas
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def sim_clock() -> SimulatedClock:
    """Retorna un reloj simulado inicializado en una fecha fija."""
    fixed_time = datetime(2026, 9, 19, 15, 30, 0, tzinfo=UTC)
    return SimulatedClock(initial_time=fixed_time)


@pytest_asyncio.fixture
async def test_engine():
    """Motor SQLite async en memoria para tests."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Sesión de prueba aislada."""
    session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP asíncrono para probar los endpoints de FastAPI."""
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db_session] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
