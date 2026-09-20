"""Entrypoint de la API FastAPI del Bot de Trading Algorítmico."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tbot import __version__
from tbot.common.clock import Clock, SystemClock
from tbot.common.logging import configure_logging, get_logger
from tbot.config.settings import settings
from tbot.db.session import get_async_engine, get_db_session

logger = get_logger("tbot.api")
clock: Clock = SystemClock()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Ciclo de vida de la aplicación: inicialización y cierre de recursos."""
    configure_logging(log_level=settings.LOG_LEVEL)
    logger.info(
        "Iniciando API FastAPI",
        env=settings.ENV,
        version=__version__,
        time_utc=clock.now().isoformat(),
    )

    # Verificar conectividad a base de datos de manera resiliente
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Conexión con base de datos verificada exitosamente")
    except Exception as exc:
        logger.warning(
            "No se pudo conectar a la base de datos durante el arranque",
            error=str(exc),
        )

    yield

    logger.info("Cerrando API FastAPI")
    engine = get_async_engine()
    await engine.dispose()


app = FastAPI(
    title="Trading Bot API",
    description="API para control, configuración y monitoreo del bot de trading algorítmico",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
async def root() -> dict[str, str]:
    """Endpoint raíz con metadatos del servicio."""
    return {
        "service": "Trading Bot API",
        "version": __version__,
        "status": "online",
    }


@app.get("/health", tags=["Health"])
async def health(session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict[str, Any]:
    """Chequeo de salud del servicio y verificación de conectividad con la base de datos."""
    db_status = "connected"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"disconnected: {exc}"
        logger.error("Fallo de salud en base de datos", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "degraded",
                "database": db_status,
                "time_utc": clock.now().isoformat(),
            },
        ) from exc

    return {
        "status": "ok",
        "environment": settings.ENV,
        "version": __version__,
        "time_utc": clock.now().isoformat(),
        "database": db_status,
    }
