"""Modelos y estructuras de datos para noticias y características de sentimiento."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class NewsItem:
    """Representa una noticia individual indexada y clasificada."""

    id: str
    symbol: str
    source: str
    url: str
    headline: str
    published_at: datetime
    sentiment_label: Literal["positive", "negative", "neutral"]
    sentiment_score: float  # De -1.0 (muy negativo) a +1.0 (muy positivo)
    summary: str | None = None
    whitelisted: bool = True
    topics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NewsFeatures:
    """Características cuantitativas agregadas de noticias en una ventana temporal (24h o 72h)."""

    symbol: str
    window: str  # "24h" o "72h"
    ts: datetime
    n_items: int
    sentiment_mean: float
    sentiment_min: float
    negative_share: float  # Proporción de noticias negativas en [0.0, 1.0]
    sources: list[str] = field(default_factory=list)
    top_topics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serializa a diccionario estructurado para el payload de veto de IA."""
        return {
            "n_24h": self.n_items,
            "sentiment_mean": round(self.sentiment_mean, 2),
            "sentiment_min": round(self.sentiment_min, 2),
            "negative_share": round(self.negative_share, 2),
            "sources": self.sources,
            "top_topics": self.top_topics,
        }

    @classmethod
    def empty(cls, symbol: str, ts: datetime, window: str = "24h") -> NewsFeatures:
        """Genera un registro neutro cuando no hay noticias en la ventana."""
        return cls(
            symbol=symbol,
            window=window,
            ts=ts,
            n_items=0,
            sentiment_mean=0.0,
            sentiment_min=0.0,
            negative_share=0.0,
            sources=[],
            top_topics=[],
        )
