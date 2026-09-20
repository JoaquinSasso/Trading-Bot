"""Pruebas del endpoint /health y metadatos de la API FastAPI."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient) -> None:
    """Verifica que el endpoint raíz responda correctamente."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "version" in data
    assert data["service"] == "Trading Bot API"


@pytest.mark.asyncio
async def test_health_endpoint_healthy(client: AsyncClient) -> None:
    """Verifica que /health responda ok con base de datos conectada."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert "time_utc" in data
    assert "version" in data
