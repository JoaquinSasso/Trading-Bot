"""Clasificador de sentimiento para titulares y resúmenes de noticias financieras."""

from __future__ import annotations

import re
from typing import Literal


class SentimentClassifier:
    """Clasifica noticias en {positive, negative, neutral} con score en [-1.0, 1.0]."""

    POSITIVE_WORDS = {
        "surge",
        "jump",
        "beat",
        "beats",
        "upgrade",
        "upgraded",
        "profit",
        "profitable",
        "record",
        "bullish",
        "growth",
        "soar",
        "soars",
        "gain",
        "gains",
        "dividend",
        "rally",
        "rallies",
        "higher",
        "outperform",
        "expand",
        "strong",
        "boost",
    }

    NEGATIVE_WORDS = {
        "plunge",
        "plunges",
        "drop",
        "drops",
        "dropped",
        "fall",
        "falls",
        "miss",
        "misses",
        "downgrade",
        "downgraded",
        "loss",
        "losses",
        "lawsuit",
        "probe",
        "investigation",
        "slump",
        "bearish",
        "warning",
        "warns",
        "decline",
        "declines",
        "fraud",
        "crash",
        "weak",
        "subpoena",
        "cut",
        "cuts",
    }

    def __init__(self, use_onnx: bool = False, model_path: str | None = None) -> None:
        self.use_onnx = use_onnx
        self.model_path = model_path
        self._onnx_session = None

        if self.use_onnx and model_path:
            try:
                import onnxruntime as ort

                self._onnx_session = ort.InferenceSession(model_path)
            except Exception:
                self._onnx_session = None

    def classify(self, text: str) -> tuple[Literal["positive", "negative", "neutral"], float]:
        """Clasifica el texto y retorna (etiqueta, score de -1.0 a +1.0)."""
        if not text or not text.strip():
            return "neutral", 0.0

        # Análisis léxico financiero de alta velocidad
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        if not words:
            return "neutral", 0.0

        pos_count = sum(1 for w in words if w in self.POSITIVE_WORDS)
        neg_count = sum(1 for w in words if w in self.NEGATIVE_WORDS)

        if pos_count > neg_count:
            score = min(1.0, 0.3 + (pos_count - neg_count) * 0.25)
            return "positive", round(score, 2)
        elif neg_count > pos_count:
            score = max(-1.0, -0.3 - (neg_count - pos_count) * 0.25)
            return "negative", round(score, 2)
        else:
            return "neutral", 0.0
