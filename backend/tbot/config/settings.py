"""Configuración estática desde variables de entorno y archivo .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ajustes leídos de .env y variables del entorno."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # Base de datos
    DATABASE_URL: str = "postgresql+asyncpg://tbot_user:tbot_password@localhost:5432/tbot_db"
    DATABASE_SYNC_URL: str = "postgresql://tbot_user:tbot_password@localhost:5432/tbot_db"

    # Broker Alpaca
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_PAPER: bool = True
    LIVE_TRADING_ENABLED: bool = False

    # LLM Providers
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Datos adicionales
    FINNHUB_API_KEY: str = ""

    # Notificaciones por correo
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    NOTIFICATION_EMAIL: str = ""

    # Seguridad
    APPROVAL_HMAC_SECRET: str = "default_hmac_secret_for_dev_32_bytes_min"
    CLOUDFLARE_ACCESS_TEAM_DOMAIN: str = ""
    CLOUDFLARE_ACCESS_POLICY_AUD: str = ""
    BYPASS_CLOUDFLARE_AUTH_LOCAL: bool = True

    # Monitoreo
    HEALTHCHECKS_PING_URL: str = ""

    # Rutas relativas del proyecto
    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent.parent
    CONFIG_DIR: Path = PROJECT_ROOT / "config"


# Instancia global singleton de Settings
settings = Settings()
