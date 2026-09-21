"""Tests de la política de Falla Cerrada (Fail-Closed) del Veto de IA."""

from datetime import datetime
from decimal import Decimal

import pytest

from tbot.ai.providers import MockAIProvider
from tbot.ai.schemas import VetoVerdict
from tbot.ai.veto import AIVeto
from tbot.news.models import NewsFeatures
from tbot.strategies.interfaces import Signal


@pytest.fixture
def sample_signal():
    return Signal.create(
        strategy_id="trend_pullback",
        version="1.1.0",
        symbol="NVDA",
        bar_ts=datetime(2025, 6, 10, 15, 45),
        side="buy",
        entry_type="limit",
        entry_price_ref=Decimal("120.00"),
        stop_price=Decimal("116.00"),
    )


@pytest.fixture
def empty_news():
    return NewsFeatures.empty("NVDA", datetime(2025, 6, 10, 15, 45))


@pytest.mark.asyncio
async def test_fail_closed_on_connection_error(sample_signal, empty_news):
    # Proveedor con error de red
    err_provider = MockAIProvider(simulate_error=True)
    veto = AIVeto(mode="required", primary_provider=err_provider, fallback_provider=err_provider)

    result = await veto.review(
        signal=sample_signal,
        features={},
        regime="BULL_CALM",
        events={},
        news=empty_news,
        now=datetime(2025, 6, 10, 15, 45),
    )

    # Debe fallar cerrado: status UNAVAILABLE y veredicto REJECT con multiplicador 0.0
    assert result.status == "UNAVAILABLE"
    assert result.verdict == VetoVerdict.REJECT
    assert result.size_multiplier == 0.0


@pytest.mark.asyncio
async def test_fail_closed_on_invalid_json(sample_signal, empty_news):
    # Proveedor que retorna JSON mal formado
    bad_json_provider = MockAIProvider(simulate_invalid_json=True)
    veto = AIVeto(
        mode="required", primary_provider=bad_json_provider, fallback_provider=bad_json_provider
    )

    result = await veto.review(
        signal=sample_signal,
        features={},
        regime="BULL_CALM",
        events={},
        news=empty_news,
        now=datetime(2025, 6, 10, 15, 45),
    )

    assert result.status == "UNAVAILABLE"
    assert result.verdict == VetoVerdict.REJECT
    assert result.size_multiplier == 0.0


@pytest.mark.asyncio
async def test_fallback_success_when_primary_fails(sample_signal, empty_news):
    # Primario falla, respaldo tiene éxito
    prim_failing = MockAIProvider(simulate_error=True)
    fall_success = MockAIProvider(default_verdict="CONFIRM", size_multiplier=1.0)

    veto = AIVeto(
        mode="required",
        primary_provider=prim_failing,
        fallback_provider=fall_success,
    )

    result = await veto.review(
        signal=sample_signal,
        features={},
        regime="BULL_CALM",
        events={},
        news=empty_news,
        now=datetime(2025, 6, 10, 15, 45),
    )

    assert result.status == "OK"
    assert result.verdict == VetoVerdict.CONFIRM
    assert result.size_multiplier == 1.0
    assert result.provider == fall_success.provider_name
