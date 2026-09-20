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
