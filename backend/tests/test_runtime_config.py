"""Pruebas de carga y validación de configuración en runtime."""

import pytest

from tbot.common.errors import ConfigurationError
from tbot.config.runtime import RuntimeConfigManager


def test_runtime_config_manager_loads_defaults() -> None:
    """Verifica que el gestor cargue los defaults del archivo YAML."""
    mgr = RuntimeConfigManager()
    defaults = mgr.load_defaults()

    assert defaults["account_mode"] == "margin_no_leverage"
    assert defaults["bot_allocation_pct"] == 50.0
    assert defaults["risk_per_trade_pct"] == 0.5
    assert defaults["max_risk_per_trade_pct_hard"] == 1.0
    assert defaults["allow_fractional_bot"] is True
    assert defaults["veto_mode"] == "quantitative"
    assert defaults["daily_loss_limit_pct"] == 2.0
    assert defaults["emergency_loss_limit_pct"] == 3.5


def test_parameter_range_validation() -> None:
    """Verifica que los valores fuera de rango o inválidos lancen ConfigurationError."""
    mgr = RuntimeConfigManager()

    # Válidos
    mgr.validate_parameter("risk_per_trade_pct", 0.75)
    mgr.validate_parameter("account_mode", "cash")
    mgr.validate_parameter("account_mode", "margin_no_leverage")
    mgr.validate_parameter("max_open_positions", 5)

    # Menor que el mínimo
    with pytest.raises(ConfigurationError, match="inferior al mínimo"):
        mgr.validate_parameter("risk_per_trade_pct", 0.1)

    # Mayor que el máximo
    with pytest.raises(ConfigurationError, match="supera el máximo"):
        mgr.validate_parameter("risk_per_trade_pct", 2.5)

    # Valor no permitido en enum
    with pytest.raises(ConfigurationError, match="no es válido"):
        mgr.validate_parameter("account_mode", "portfolio_margin_full")

    # Parámetro desconocido
    with pytest.raises(ConfigurationError, match="desconocido"):
        mgr.validate_parameter("parametro_inexistente", 123)


def test_load_universe_symbols_and_proxies() -> None:
    """Verifica que el archivo universe.yaml cargue los tickers y sus proxies."""
    mgr = RuntimeConfigManager()
    symbols = mgr.load_universe()

    assert len(symbols) >= 10

    # Verificar mapeo de proxies
    symbol_map = {s["signal_symbol"]: s for s in symbols}
    assert "SPY" in symbol_map
    assert symbol_map["SPY"]["execution_symbol"] == "SPYM"
    assert symbol_map["SPY"]["enabled"] is True

    assert "QQQ" in symbol_map
    assert symbol_map["QQQ"]["execution_symbol"] == "QQQM"

    assert "GLD" in symbol_map
    assert symbol_map["GLD"]["execution_symbol"] == "GLDM"

    # Verificar símbolo excluido
    assert "ABAT" in symbol_map
    assert symbol_map["ABAT"]["enabled"] is False
