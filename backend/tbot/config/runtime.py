"""Carga, validación y gestión de parámetros de configuración en runtime."""

from pathlib import Path
from typing import Any

import yaml

from tbot.common.errors import ConfigurationError
from tbot.config.settings import settings


class RuntimeConfigManager:
    """Gestiona la carga de defaults.yaml y la validación de rangos."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or settings.CONFIG_DIR
        self.defaults_file = self.config_dir / "defaults.yaml"
        self.universe_file = self.config_dir / "universe.yaml"
        self._raw_defaults: dict[str, Any] = {}
        self._parameters: dict[str, Any] = {}
        self.load_defaults()

    def load_defaults(self) -> dict[str, Any]:
        """Carga el archivo defaults.yaml y extrae los valores por defecto."""
        if not self.defaults_file.exists():
            raise ConfigurationError(f"Archivo de defaults no encontrado: {self.defaults_file}")

        with open(self.defaults_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._raw_defaults = data
        params_meta = data.get("parameters", {})
        self._parameters = {k: v.get("default") for k, v in params_meta.items()}
        return self._parameters

    def validate_parameter(self, key: str, value: Any) -> None:
        """Valida que un valor esté dentro de los rangos o valores permitidos."""
        params_meta = self._raw_defaults.get("parameters", {})
        if key not in params_meta:
            raise ConfigurationError(f"Parámetro desconocido: {key}")

        meta = params_meta[key]
        if "min" in meta and value < meta["min"]:
            raise ConfigurationError(
                f"El valor {value} para {key} es inferior al mínimo permitido ({meta['min']})"
            )
        if "max" in meta and value > meta["max"]:
            raise ConfigurationError(
                f"El valor {value} para {key} supera el máximo permitido ({meta['max']})"
            )
        if "allowed_values" in meta and value not in meta["allowed_values"]:
            raise ConfigurationError(
                f"El valor {value} para {key} no es válido. Opciones permitidas: {meta['allowed_values']}"
            )

    def get(self, key: str, default: Any = None) -> Any:
        return self._parameters.get(key, default)

    def load_universe(self) -> list[dict[str, Any]]:
        """Carga la lista de símbolos configurados en universe.yaml."""
        if not self.universe_file.exists():
            raise ConfigurationError(f"Archivo de universo no encontrado: {self.universe_file}")

        with open(self.universe_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return data.get("symbols", [])
