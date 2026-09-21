"""Test de seguridad: Garantizar que ningún texto libre de noticias llegue al LLM de Veto."""

from datetime import datetime
from decimal import Decimal

from tbot.ai.veto import AIVeto
from tbot.news.models import NewsFeatures
from tbot.strategies.interfaces import Signal


def test_payload_contains_strictly_no_raw_news_text():
    veto = AIVeto(mode="required")

    sig = Signal.create(
        strategy_id="trend_pullback",
        version="1.1.0",
        symbol="AAPL",
        bar_ts=datetime(2025, 6, 10, 15, 45),
        side="buy",
        entry_type="limit",
        entry_price_ref=Decimal("200.00"),
        stop_price=Decimal("195.00"),
    )

    # Features con resumen cuantitativo
    news_feat = NewsFeatures(
        symbol="AAPL",
        window="24h",
        ts=datetime(2025, 6, 10, 15, 45),
        n_items=5,
        sentiment_mean=-0.25,
        sentiment_min=-0.80,
        negative_share=0.40,
        sources=["reuters", "benzinga"],
        top_topics=["guidance", "litigation"],
    )

    payload = veto.build_payload(
        signal=sig,
        features={"rsi14": 52.0, "atr14": 3.5},
        regime="BULL_CALM",
        events={"days_to_earnings": None, "macro_today": False},
        news=news_feat,
    )

    payload_json = payload.model_dump_json()

    # Inspeccionar las claves y estructura
    data = payload.model_dump()
    assert "news" in data
    news_data = data["news"]

    # Debe contener solo estadísticas numéricas y etiquetas cortas
    assert "sentiment_mean" in news_data
    assert "negative_share" in news_data
    assert "top_topics" in news_data

    # CERO campos de texto libre
    forbidden_keys = {"text", "headline", "summary", "article", "body", "content"}
    assert not any(k in news_data for k in forbidden_keys)
    assert not any(k in payload_json.lower() for k in ['"headline":', '"summary":', '"body":'])
