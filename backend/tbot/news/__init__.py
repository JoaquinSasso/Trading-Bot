"""Módulo de procesamiento de noticias, sentimiento y features para el bot de trading."""

from tbot.news.aggregator import NewsAggregator
from tbot.news.classifier import SentimentClassifier
from tbot.news.models import NewsFeatures, NewsItem
from tbot.news.store import HistoricalNewsFeatureStore
from tbot.news.topics import TopicExtractor

__all__ = [
    "NewsItem",
    "NewsFeatures",
    "SentimentClassifier",
    "TopicExtractor",
    "NewsAggregator",
    "HistoricalNewsFeatureStore",
]

