"""Proceso independiente del Worker para el Bot de Trading Algorítmico."""

import asyncio
import contextlib
import signal
import sys

from sqlalchemy import text

from tbot import __version__
from tbot.common.clock import Clock, SystemClock
from tbot.common.logging import configure_logging, get_logger
from tbot.config.runtime import RuntimeConfigManager
from tbot.config.settings import settings
from tbot.db.session import get_async_engine

logger = get_logger("tbot.worker")


class Worker:
    """Orquestador del proceso Worker independiente."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.is_running = False
        self.config_manager = RuntimeConfigManager()

    async def start(self) -> None:
        """Inicia el worker y sus bucles operativos."""
        configure_logging(log_level=settings.LOG_LEVEL)
        logger.info(
            "Iniciando Worker",
            version=__version__,
            env=settings.ENV,
            now_utc=self.clock.now().isoformat(),
        )

        # Carga inicial de defaults y universo
        defaults = self.config_manager.load_defaults()
        universe = self.config_manager.load_universe()
        logger.info(
            "Configuración inicial cargada",
            parameters_count=len(defaults),
            universe_symbols_count=len(universe),
        )

        # Verificar conexión con base de datos
        try:
            engine = get_async_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Worker conectado a base de datos PostgreSQL exitosamente")
        except Exception as exc:
            logger.warning("Base de datos no disponible al iniciar worker", error=str(exc))

        self.is_running = True
        logger.info("Worker operativo y listo para tareas de scheduler (Fase 0)")

        # Bucle de liveness para mantener el proceso vivo y receptivo a señales
        while self.is_running:
            await asyncio.sleep(5)

    async def stop(self) -> None:
        """Detiene de forma limpia el worker."""
        logger.info("Deteniendo Worker...")
        self.is_running = False
        engine = get_async_engine()
        await engine.dispose()
        logger.info("Worker detenido")


def handle_signals(worker: Worker, loop: asyncio.AbstractEventLoop) -> None:
    """Configura el apagado elegante ante SIGINT y SIGTERM."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))


async def main() -> None:
    worker = Worker()
    loop = asyncio.get_running_loop()
    handle_signals(worker, loop)

    try:
        await worker.start()
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
