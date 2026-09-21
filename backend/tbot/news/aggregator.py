"""Agregador de noticias y cálculo de características numéricas (NewsFeatures)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from tbot.news.models import NewsFeatures, NewsItem


class NewsAggregator:
    """Agrega noticias en ventanas rodantes (24h / 72h) y calcula métricas de sentimiento."""

    def __init__(self, items: list[NewsItem] | None = None) -> None:
        self._items: list[NewsItem] = list(items) if items else []

    def add_item(self, item: NewsItem) -> None:
        """Añade una noticia a la colección."""
        self._items.append(item)

    def compute_features(
        self,
        symbol: str,
        now: datetime,
        window: str = "24h",
    ) -> NewsFeatures:
        """Calcula las características cuantitativas para un símbolo en la ventana dada."""
        hours = 72 if window == "72h" else 24
        window_start = now - timedelta(hours=hours)

        # Filtrar por símbolo, rango temporal y fuente permitida
        # Manejar comparación segura de datetimes con o sin timezone
        matching: list[NewsItem] = []
        for item in self._items:
            if item.symbol != symbol or not item.whitelisted:
                continue

            item_dt = item.published_at
            if now.tzinfo is not None and item_dt.tzinfo is None:
                item_dt = item_dt.replace(tzinfo=now.tzinfo)
            elif now.tzinfo is None and item_dt.tzinfo is not None:
                item_dt = item_dt.replace(tzinfo=None)

            if window_start <= item_dt <= now:
                matching.append(item)

        if not matching:
            return NewsFeatures.empty(symbol=symbol, ts=now, window=window)

        n = len(matching)
        scores = [it.sentiment_score for it in matching]
        mean_score = sum(scores) / n
        min_score = min(scores)

        neg_count = sum(1 for it in matching if it.sentiment_label == "negative")
        negative_share = neg_count / n

        sources = sorted(list({it.source for it in matching}))

        # Extraer tópicos ordenados por frecuencia
        all_topics: list[str] = []
        for it in matching:
            all_topics.extend(it.topics)
        topic_counts = Counter(all_topics)
        top_topics = [t for t, _ in topic_counts.most_common(5)]

        return NewsFeatures(
            symbol=symbol,
            window=window,
            ts=now,
            n_items=n,
            sentiment_mean=round(mean_score, 4),
            sentiment_min=round(min_score, 4),
            negative_share=round(negative_share, 4),
            sources=sources,
            top_topics=top_topics,
        )
