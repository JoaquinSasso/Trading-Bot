"""Tests unitarios para los esquemas Pydantic del Veto de IA."""

import pytest
from pydantic import ValidationError

from tbot.ai.schemas import VetoOutput, VetoReasonCode, VetoResult, VetoVerdict


def test_veto_output_valid_confirm():
    data = {
        "analysis": "Tendencia alcista confirmada sin noticias adversas.",
        "verdict": "CONFIRM",
        "size_multiplier": 0.5,  # Debe ser forzado a 1.0 por el validador
        "reason_code": "OK",
    }
    output = VetoOutput.model_validate(data)
    assert output.verdict == VetoVerdict.CONFIRM
    assert output.size_multiplier == 1.0
    assert output.reason_code == VetoReasonCode.OK


def test_veto_output_valid_reduce():
    data = {
        "analysis": "Setup valido pero volatilidad elevada en el sector.",
        "verdict": "REDUCE",
        "size_multiplier": 0.5,
        "reason_code": "LOW_QUALITY_SETUP",
    }
    output = VetoOutput.model_validate(data)
    assert output.verdict == VetoVerdict.REDUCE
    assert output.size_multiplier == 0.5
    assert output.reason_code == VetoReasonCode.LOW_QUALITY_SETUP


def test_veto_output_valid_reject():
    data = {
        "analysis": "Multiples noticias negativas sobre investigacion regulatoria.",
        "verdict": "REJECT",
        "size_multiplier": 0.5,  # Debe ser forzado a 0.0
        "reason_code": "NEWS_NEGATIVE_CLUSTER",
    }
    output = VetoOutput.model_validate(data)
    assert output.verdict == VetoVerdict.REJECT
    assert output.size_multiplier == 0.0


def test_veto_output_rejects_missing_analysis():
    data = {
        "verdict": "CONFIRM",
        "size_multiplier": 1.0,
        "reason_code": "OK",
    }
    with pytest.raises(ValidationError):
        VetoOutput.model_validate(data)


def test_veto_output_rejects_analysis_too_long():
    data = {
        "analysis": "X" * 401,  # Más de 400 caracteres
        "verdict": "CONFIRM",
        "size_multiplier": 1.0,
        "reason_code": "OK",
    }
    with pytest.raises(ValidationError):
        VetoOutput.model_validate(data)


def test_veto_result_unavailable_and_bypassed():
    unavail = VetoResult.unavailable("API Timeout")
    assert unavail.status == "UNAVAILABLE"
    assert unavail.verdict == VetoVerdict.REJECT
    assert unavail.size_multiplier == 0.0

    bypassed = VetoResult.bypassed()
    assert bypassed.status == "BYPASSED"
    assert bypassed.verdict == VetoVerdict.CONFIRM
    assert bypassed.size_multiplier == 1.0


@pytest.mark.asyncio
async def test_quantitative_veto_mode_rejects_panic_cluster():
    from datetime import datetime
    from decimal import Decimal

    from tbot.ai.veto import AIVeto
    from tbot.news.models import NewsFeatures
    from tbot.strategies.interfaces import Signal

    veto = AIVeto(mode="quantitative")
    sig = Signal(
        signal_id="sig_test_q1",
        strategy_id="s3_trend_pullback",
        symbol="AAPL",
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("150.00"),
        stop_price=Decimal("145.00"),
        created_at=datetime(2026, 9, 21, 15, 45),
    )

    # 40% de noticias negativas (pánico/cluster)
    news = NewsFeatures(
        symbol="AAPL",
        window="24h",
        ts=datetime(2026, 9, 21, 15, 45),
        n_items=10,
        sentiment_mean=-0.2,
        sentiment_min=-0.8,
        negative_share=0.40,
        sources=["Reuters"],
        top_topics=["general"],
    )

    res = await veto.review(sig, {}, "BULL_CALM", {}, news, datetime(2026, 9, 21, 15, 45))
    assert res.verdict == VetoVerdict.REJECT
    assert res.size_multiplier == 0.0
    assert res.reason_code == VetoReasonCode.NEWS_NEGATIVE_CLUSTER
    assert res.provider == "quantitative_engine"


@pytest.mark.asyncio
async def test_quantitative_veto_mode_approves_normal_news():
    from datetime import datetime
    from decimal import Decimal

    from tbot.ai.veto import AIVeto
    from tbot.news.models import NewsFeatures
    from tbot.strategies.interfaces import Signal

    veto = AIVeto(mode="quantitative")
    sig = Signal(
        signal_id="sig_test_q2",
        strategy_id="s3_trend_pullback",
        symbol="NVDA",
        side="buy",
        entry_type="market",
        entry_price_ref=Decimal("140.00"),
        stop_price=Decimal("135.00"),
        created_at=datetime(2026, 9, 21, 15, 45),
    )

    news = NewsFeatures(
        symbol="NVDA",
        window="24h",
        ts=datetime(2026, 9, 21, 15, 45),
        n_items=5,
        sentiment_mean=0.35,
        sentiment_min=-0.05,
        negative_share=0.05,
        sources=["Benzinga"],
        top_topics=["earnings", "momentum"],
    )

    res = await veto.review(sig, {}, "BULL_CALM", {}, news, datetime(2026, 9, 21, 15, 45))
    assert res.verdict == VetoVerdict.CONFIRM
    assert res.size_multiplier == 1.0
    assert res.reason_code == VetoReasonCode.OK
    assert res.provider == "quantitative_engine"
