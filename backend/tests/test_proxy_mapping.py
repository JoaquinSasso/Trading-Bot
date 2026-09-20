"""Unit tests for ETF proxy mapping (SPY->SPYM, QQQ->QQQM, GLD->GLDM)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tbot.data.proxies import (
    ProxyMapper,
    get_proxy_pair,
    is_canonical_symbol,
    is_proxy_symbol,
    map_symbol,
    resolve_data_proxy,
    resolve_trading_proxy,
)
from tbot.db.models import UniverseSymbol


def test_canonical_trading_proxy_resolution() -> None:
    assert resolve_trading_proxy("SPY") == "SPYM"
    assert resolve_trading_proxy("QQQ") == "QQQM"
    assert resolve_trading_proxy("GLD") == "GLDM"


def test_unmapped_and_proxy_inputs() -> None:
    assert resolve_trading_proxy("AAPL") == "AAPL"
    assert resolve_trading_proxy("MSFT") == "MSFT"
    assert resolve_trading_proxy("SPYM") == "SPYM"
    assert resolve_trading_proxy("QQQM") == "QQQM"
    assert resolve_trading_proxy("GLDM") == "GLDM"


def test_data_proxy_resolution() -> None:
    assert resolve_data_proxy("SPYM") == "SPY"
    assert resolve_data_proxy("QQQM") == "QQQ"
    assert resolve_data_proxy("GLDM") == "GLD"
    assert resolve_data_proxy("SPY") == "SPY"
    assert resolve_data_proxy("QQQ") == "QQQ"
    assert resolve_data_proxy("NVDA") == "NVDA"


def test_case_and_whitespace_sanitization() -> None:
    assert resolve_trading_proxy(" spy ") == "SPYM"
    assert resolve_trading_proxy("qqq") == "QQQM"
    assert resolve_trading_proxy("\tgld\n") == "GLDM"
    assert resolve_data_proxy("  qqqm\t") == "QQQ"
    assert resolve_data_proxy("spym") == "SPY"


def test_map_symbol_contract() -> None:
    assert map_symbol("SPY", direction="signal_to_exec") == "SPYM"
    assert map_symbol("SPYM", direction="exec_to_signal") == "SPY"
    assert map_symbol("AAPL", direction="signal_to_exec") == "AAPL"
    assert map_symbol("AAPL", direction="exec_to_signal") == "AAPL"

    with pytest.raises(ValueError, match="Dirección de mapeo desconocida"):
        map_symbol("SPY", direction="invalid_direction")  # type: ignore[arg-type]


def test_proxy_pair_and_predicates() -> None:
    assert is_proxy_symbol("SPYM") is True
    assert is_proxy_symbol("QQQM") is True
    assert is_proxy_symbol("GLDM") is True
    assert is_proxy_symbol("SPY") is False
    assert is_proxy_symbol("AAPL") is False

    assert is_canonical_symbol("SPY") is True
    assert is_canonical_symbol("QQQ") is True
    assert is_canonical_symbol("GLD") is True
    assert is_canonical_symbol("SPYM") is False
    assert is_canonical_symbol("AAPL") is False

    assert get_proxy_pair("SPY") == ("SPY", "SPYM")
    assert get_proxy_pair("SPYM") == ("SPY", "SPYM")
    assert get_proxy_pair("QQQ") == ("QQQ", "QQQM")
    assert get_proxy_pair("AAPL") == ("AAPL", "AAPL")


def test_proxy_mapper_custom_init() -> None:
    mapper = ProxyMapper(initial_mappings={"IWM": "IWMM", "DIA": "DIAM"})
    assert mapper.resolve_trading("IWM") == "IWMM"
    assert mapper.resolve_trading("SPY") == "SPYM"
    assert mapper.resolve_data("IWMM") == "IWM"
    assert mapper.resolve_data("SPYM") == "SPY"
    assert mapper.map_symbol("DIA", direction="signal_to_exec") == "DIAM"
    assert mapper.map_symbol("DIAM", direction="exec_to_signal") == "DIA"

    with pytest.raises(ValueError, match="Dirección de mapeo desconocida"):
        mapper.map_symbol("DIA", direction="invalid")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_proxy_mapper_db_sync(test_session: AsyncSession) -> None:
    # Insert custom proxy mapping into database
    custom_entry = UniverseSymbol(
        signal_symbol="VTI",
        execution_symbol="VTIM",
        cluster="broad_market",
        enabled=True,
        notes="Custom test mapping",
        updated_at=datetime.now(UTC),
    )
    disabled_entry = UniverseSymbol(
        signal_symbol="XLE",
        execution_symbol="XLEM",
        cluster="energy",
        enabled=False,
        notes="Disabled mapping",
        updated_at=datetime.now(UTC),
    )
    test_session.add(custom_entry)
    test_session.add(disabled_entry)
    await test_session.commit()

    mapper = ProxyMapper()
    await mapper.sync_from_db(test_session)

    assert mapper.resolve_trading("VTI") == "VTIM"
    assert mapper.resolve_data("VTIM") == "VTI"
    # Disabled entry should not be mapped
    assert mapper.resolve_trading("XLE") == "XLE"
    # Canonical mappings still intact
    assert mapper.resolve_trading("SPY") == "SPYM"
