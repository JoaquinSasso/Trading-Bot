"""Pruebas unitarias para HistoricalNewsFeatureStore y consulta sin sesgo temporal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tbot.ai.schemas import VetoVerdict
from tbot.ai.veto import AIVeto
from tbot.news.store import HistoricalNewsFeatureStore
from tbot.strategies.interfaces import Signal

EASTERN_TZ = ZoneInfo("America/New_York")


@pytest.fixture
def sample_features_df() -> pd.DataFrame:
    """DataFrame representativo con features históricas para AAPL y NVDA."""
    rows = [
        {
            "timestamp": "2025-11-05T15:45:00-05:00",
            "date": "2025-11-05",
            "symbol": "AAPL",
            "window": "24h",
            "n_items": 3,
            "sentiment_mean": 0.45,
            "sentiment_min": 0.10,
            "negative_share": 0.0,
            "top_topics": '["earnings", "momentum"]',
            "sources": '["reuters", "benzinga"]',
        },
        {
            "timestamp": "2025-11-07T15:45:00-05:00",
            "date": "2025-11-07",
            "symbol": "AAPL",
            "window": "24h",
            "n_items": 4,
            "sentiment_mean": -0.65,
            "sentiment_min": -0.85,
            "negative_share": 0.75,
            "top_topics": '["litigation", "investigation"]',
            "sources": '["bloomberg"]',
        },
        {
            "timestamp": "2025-11-07T15:45:00-05:00",
            "date": "2025-11-07",
            "symbol": "NVDA",
            "window": "24h",
            "n_items": 2,
            "sentiment_mean": 0.80,
            "sentiment_min": 0.50,
            "negative_share": 0.0,
            "top_topics": '["guidance", "chips"]',
            "sources": '["benzinga"]',
        },
    ]
    return pd.DataFrame(rows)


def test_feature_store_prevents_lookahead_bias(sample_features_df: pd.DataFrame) -> None:
    """Garantiza que una consulta en T0 no vea noticias de T1 (cero sesgo de futuro)."""
    store = HistoricalNewsFeatureStore(sample_features_df)

    # Consulta el 6 de noviembre a las 10:00 (entre el día 5 y el 7)
    t_nov6 = datetime(2025, 11, 6, 10, 0, tzinfo=EASTERN_TZ)
    feat_nov6 = store.get_features("AAPL", as_of=t_nov6)

    # Debe retornar las noticias del día 5, NO las del día 7
    assert feat_nov6.n_items == 3
    assert feat_nov6.sentiment_mean == 0.45
    assert feat_nov6.negative_share == 0.0
    assert "earnings" in feat_nov6.top_topics

    # Consulta el 7 de noviembre a las 16:00 (después de publicarse las noticias negativas)
    t_nov7 = datetime(2025, 11, 7, 16, 0, tzinfo=EASTERN_TZ)
    feat_nov7 = store.get_features("AAPL", as_of=t_nov7)

    assert feat_nov7.n_items == 4
    assert feat_nov7.sentiment_mean == -0.65
    assert feat_nov7.negative_share == 0.75
    assert "litigation" in feat_nov7.top_topics


def test_feature_store_respects_staleness_limit(sample_features_df: pd.DataFrame) -> None:
    """Si una noticia es más antigua que max_staleness_hours, debe retornar empty."""
    store = HistoricalNewsFeatureStore(sample_features_df)

    # Consulta 10 días después (17 de noviembre) con max_staleness de 48h
    t_future = datetime(2025, 11, 17, 15, 45, tzinfo=EASTERN_TZ)
    feat_stale = store.get_features("AAPL", as_of=t_future, max_staleness_hours=48.0)

    assert feat_stale.n_items == 0
    assert feat_stale.sentiment_mean == 0.0
    assert feat_stale.negative_share == 0.0


def test_feature_store_unknown_symbol(sample_features_df: pd.DataFrame) -> None:
    """Un símbolo sin noticias debe retornar NewsFeatures vacías de forma segura."""
    store = HistoricalNewsFeatureStore(sample_features_df)
    now = datetime(2025, 11, 7, 15, 45, tzinfo=EASTERN_TZ)
    feat = store.get_features("MSFT", as_of=now)

    assert feat.symbol == "MSFT"
    assert feat.n_items == 0
    assert feat.sentiment_mean == 0.0
    assert feat.top_topics == []


def test_feature_store_from_file(tmp_path: Path, sample_features_df: pd.DataFrame) -> None:
    """Verifica la carga desde archivo CSV temporal."""
    csv_file = tmp_path / "test_features.csv"
    sample_features_df.to_csv(csv_file, index=False)

    store = HistoricalNewsFeatureStore.from_file(csv_file)
    now = datetime(2025, 11, 7, 16, 0, tzinfo=EASTERN_TZ)
    nvda_feat = store.get_features("NVDA", as_of=now)

    assert nvda_feat.symbol == "NVDA"
    assert nvda_feat.sentiment_mean == 0.80
    assert nvda_feat.top_topics == ["guidance", "chips"]


@pytest.mark.asyncio
async def test_feature_store_integration_with_quantitative_veto(sample_features_df: pd.DataFrame) -> None:
    """Prueba que el veto cuantitativo rechace o apruebe correctamente usando features del store."""
    store = HistoricalNewsFeatureStore(sample_features_df)
    veto = AIVeto(mode="quantitative")

    # Señal para AAPL el 7 de noviembre (día con noticias de litigio y negative_share 0.75)
    t_nov7 = datetime(2025, 11, 7, 15, 45, tzinfo=EASTERN_TZ)
    from decimal import Decimal

    sig_aapl = Signal(
        signal_id="sig_test_aapl_01",
        strategy_id="s3_trend_pullback",
        symbol="AAPL",
        side="buy",
        entry_type="limit",
        entry_price_ref=Decimal("220.00"),
        stop_price=Decimal("215.00"),
        take_profit_price=Decimal("230.00"),
        score=0.8,
        created_at=t_nov7,
    )

    news_aapl = store.get_features("AAPL", as_of=t_nov7)
    res_aapl = await veto.review(
        signal=sig_aapl,
        features={"score": sig_aapl.score},
        regime="BULL_CALM",
        events={},
        news=news_aapl,
        now=t_nov7,
    )

    assert res_aapl.verdict == VetoVerdict.REJECT
    assert res_aapl.reason_code == "NEWS_NEGATIVE_CLUSTER"

    # Señal para NVDA el 7 de noviembre (día con noticias positivas de chips/guidance)
    sig_nvda = Signal(
        signal_id="sig_test_nvda_01",
        strategy_id="s3_trend_pullback",
        symbol="NVDA",
        side="buy",
        entry_type="limit",
        entry_price_ref=Decimal("190.00"),
        stop_price=Decimal("185.00"),
        take_profit_price=Decimal("200.00"),
        score=0.9,
        created_at=t_nov7,
    )

    news_nvda = store.get_features("NVDA", as_of=t_nov7)
    res_nvda = await veto.review(
        signal=sig_nvda,
        features={"score": sig_nvda.score},
        regime="BULL_CALM",
        events={},
        news=news_nvda,
        now=t_nov7,
    )

    assert res_nvda.verdict == VetoVerdict.CONFIRM
    assert res_nvda.reason_code == "OK"
