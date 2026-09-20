#!/usr/bin/env python3
"""Script autónomo para recopilar y procesar noticias históricas con FinBERT (GPU/CPU).

Diseñado para ejecutarse en PC de escritorio con GPU NVIDIA (CUDA) o CPU.
Descarga noticias desde la API de Alpaca, ejecuta inferencia por lotes con FinBERT,
extrae tópicos financieros y genera el dataset de características cuantitativas
(NewsFeatures) libre de sesgo de futuro para el backtest y el trading bot.

Uso típico:
    python scripts/extract_and_process_historical_news.py --symbols AAPL,NVDA,MSFT,SPY,QQQ --device auto
"""

from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

# Asegurar que el módulo backend/tbot esté en sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Cargar variables de entorno si existe .env
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

import pandas as pd

from tbot.news.aggregator import NewsAggregator
from tbot.news.classifier import SentimentClassifier
from tbot.news.models import NewsItem
from tbot.news.topics import TopicExtractor

EASTERN_TZ = ZoneInfo("America/New_York")


def get_alpaca_credentials(cli_key: str | None, cli_secret: str | None) -> tuple[str | None, str | None]:
    """Obtiene las credenciales de Alpaca desde CLI o variables de entorno."""
    key = cli_key or os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret = cli_secret or os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    return key, secret


def download_alpaca_news(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    api_key: str,
    secret_key: str,
    limit_total: int | None = None,
) -> list[dict[str, Any]]:
    """Descarga noticias históricas de Alpaca para un símbolo usando paginación."""
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
    except ImportError:
        print("[ERROR] alpaca-py no está instalado. Instálalo con: pip install alpaca-py")
        return []

    client = NewsClient(api_key=api_key, secret_key=secret_key)
    all_news: list[dict[str, Any]] = []
    page_token = None

    print(f"[{symbol}] Descargando noticias de Alpaca desde {start_dt.date()} hasta {end_dt.date()}...")

    while True:
        request = NewsRequest(
            symbols=[symbol],
            start=start_dt,
            end=end_dt,
            limit=50,
            include_content=False,
            exclude_contentless=False,
            page_token=page_token,
        )

        try:
            response = client.get_news(request)
        except Exception as e:
            print(f"[{symbol}] Error al solicitar noticias a Alpaca: {e}")
            break

        news_items = response.news if hasattr(response, "news") else []
        if not news_items:
            break

        for item in news_items:
            # Serializar item a diccionario
            created_at = getattr(item, "created_at", None) or getattr(item, "updated_at", None)
            all_news.append({
                "id": str(getattr(item, "id", f"{symbol}_{len(all_news)}")),
                "symbol": symbol,
                "headline": getattr(item, "headline", ""),
                "summary": getattr(item, "summary", "") or "",
                "source": getattr(item, "source", "alpaca"),
                "url": getattr(item, "url", ""),
                "published_at": created_at.isoformat() if created_at else datetime.now(EASTERN_TZ).isoformat(),
            })

            if limit_total and len(all_news) >= limit_total:
                break

        page_token = getattr(response, "next_page_token", None)
        if not page_token or (limit_total and len(all_news) >= limit_total):
            break

    print(f"[{symbol}] Total de noticias descargadas: {len(all_news)}")
    return all_news


def generate_synthetic_news_data(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list[dict[str, Any]]:
    """Genera datos sintéticos realistas para pruebas cuando no se configuran credenciales."""
    print(f"[{symbol}] Generando noticias sintéticas de prueba (2024-2026)...")
    sample_headlines = [
        ("reports Q3 earnings beat EPS estimates by 12%, revenue up 18% YoY", "positive"),
        ("faces new antitrust investigation and regulatory probe from FTC", "negative"),
        ("raises full-year fiscal guidance citing unprecedented AI demand", "positive"),
        ("analysts downgrade price target citing supply chain bottlenecks and weak consumer demand", "negative"),
        ("announces strategic cloud partnership and billion-dollar enterprise deal", "positive"),
        ("management warns of margin compression and cuts quarterly dividend outlook", "negative"),
        ("unveils next-generation flagship AI chip with 2x performance efficiency", "positive"),
        ("class action lawsuit filed alleging misrepresentation of autonomous tech", "negative"),
        ("shares rally to fresh all-time high following strong institutional inflows", "positive"),
        ("sec files inquiry into accounting practices and executive stock sales", "negative"),
        ("market flat as investors await Federal Reserve interest rate decision", "neutral"),
        ("trading volume normalizes in quiet holiday session", "neutral"),
    ]

    news_list: list[dict[str, Any]] = []
    curr = start_dt
    counter = 0

    while curr <= end_dt:
        # Generar 1 a 3 noticias cada 2-4 días hábiles
        if curr.weekday() < 5 and (counter % 3 == 0):
            template_headline, _ = sample_headlines[counter % len(sample_headlines)]
            headline = f"{symbol} {template_headline}"
            news_list.append({
                "id": f"syn_{symbol}_{counter}",
                "symbol": symbol,
                "headline": headline,
                "summary": f"Detailed report regarding {headline} with market implications.",
                "source": "benzinga" if counter % 2 == 0 else "reuters",
                "url": f"https://example.com/news/{symbol}/{counter}",
                "published_at": curr.replace(hour=10, minute=30, tzinfo=EASTERN_TZ).isoformat(),
            })
        counter += 1
        curr += timedelta(days=2)

    print(f"[{symbol}] Noticias sintéticas generadas: {len(news_list)}")
    return news_list


class FinBERTPredictor:
    """Ejecuta inferencia de FinBERT con PyTorch en GPU o CPU, con fallback léxico."""

    def __init__(self, device: str = "auto", batch_size: int = 32) -> None:
        self.batch_size = batch_size
        self.device = device
        self.model = None
        self.tokenizer = None
        self.is_finbert = False
        self.lexical_classifier = SentimentClassifier()
        self._init_model()

    def _init_model(self) -> None:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            if self.device == "auto":
                self.torch_device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                self.torch_device = self.device

            print(f"[INFO] Inicializando FinBERT en dispositivo: {self.torch_device.upper()}")
            if self.torch_device == "cuda":
                gpu_name = torch.cuda.get_device_name(0)
                print(f"[INFO] GPU Detectada: {gpu_name} (VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB)")

            model_name = "ProsusAI/finbert"
            print(f"[INFO] Cargando pesos de HuggingFace ({model_name})...")
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self.model.to(self.torch_device)
            self.model.eval()
            self.is_finbert = True
            print("[OK] FinBERT cargado exitosamente.")

        except Exception as e:
            print(f"[WARNING] No se pudo cargar PyTorch/FinBERT ({e}).")
            print("[INFO] Usando clasificador léxico determinista de alta velocidad como fallback.")
            self.is_finbert = False

    def predict_batch(self, texts: list[str]) -> list[tuple[str, float]]:
        """Predice el sentimiento para una lista de textos.

        Retorna lista de (label, score en [-1.0, 1.0]).
        """
        if not texts:
            return []

        if not self.is_finbert:
            return [self.lexical_classifier.classify(t) for t in texts]

        import torch

        results: list[tuple[str, float]] = []

        # ProsusAI/finbert label mapping: 0 -> positive, 1 -> negative, 2 -> neutral
        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i : i + self.batch_size]
            inputs = self.tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=128,
                return_tensors="pt",
            ).to(self.torch_device)

            with torch.no_grad():
                outputs = self.model(**inputs)
                probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

            # Extraer probabilidades
            for p in probs:
                pos_prob = float(p[0].item())
                neg_prob = float(p[1].item())
                neu_prob = float(p[2].item())

                # Label con mayor probabilidad
                if pos_prob > neg_prob and pos_prob > neu_prob:
                    label = "positive"
                    score = pos_prob
                elif neg_prob > pos_prob and neg_prob > neu_prob:
                    label = "negative"
                    score = -neg_prob
                else:
                    label = "neutral"
                    score = 0.0

                results.append((label, round(score, 4)))

        return results


def process_news_features(
    symbols: list[str],
    raw_news_by_symbol: dict[str, list[dict[str, Any]]],
    predictor: FinBERTPredictor,
    start_date: datetime,
    end_date: datetime,
) -> pd.DataFrame:
    """Procesa noticias con FinBERT y agrega características numéricas (NewsFeatures)."""
    topic_extractor = TopicExtractor()
    all_feature_rows: list[dict[str, Any]] = []

    for sym in symbols:
        raw_items = raw_news_by_symbol.get(sym, [])
        if not raw_items:
            continue

        print(f"[{sym}] Extrayendo tópicos e infiriendo sentimiento para {len(raw_items)} noticias...")
        texts_to_predict = [f"{it.get('headline', '')}. {it.get('summary', '')}".strip() for it in raw_items]
        predictions = predictor.predict_batch(texts_to_predict)

        parsed_items: list[NewsItem] = []
        for it, (label, score) in zip(raw_items, predictions, strict=False):
            pub_at = datetime.fromisoformat(it["published_at"])
            if pub_at.tzinfo is None:
                pub_at = pub_at.replace(tzinfo=EASTERN_TZ)

            headline = it.get("headline", "")
            summary = it.get("summary", "")
            topics = topic_extractor.extract_topics(f"{headline} {summary}")

            parsed_items.append(
                NewsItem(
                    id=it["id"],
                    symbol=sym,
                    source=it.get("source", "unknown"),
                    url=it.get("url", ""),
                    headline=headline,
                    summary=summary,
                    published_at=pub_at,
                    sentiment_label=label,  # type: ignore[arg-type]
                    sentiment_score=score,
                    whitelisted=True,
                    topics=topics,
                )
            )

        aggregator = NewsAggregator(parsed_items)

        # Generar características agregadas para cada día hábil a las 15:45 ET
        curr_dt = start_date.replace(hour=15, minute=45, second=0, microsecond=0)
        if curr_dt.tzinfo is None:
            curr_dt = curr_dt.replace(tzinfo=EASTERN_TZ)

        while curr_dt <= end_date:
            if curr_dt.weekday() < 5:  # Lunes a Viernes
                feat = aggregator.compute_features(symbol=sym, now=curr_dt, window="24h")
                all_feature_rows.append({
                    "timestamp": curr_dt.isoformat(),
                    "date": curr_dt.strftime("%Y-%m-%d"),
                    "symbol": sym,
                    "window": "24h",
                    "n_items": feat.n_items,
                    "sentiment_mean": feat.sentiment_mean,
                    "sentiment_min": feat.sentiment_min,
                    "negative_share": feat.negative_share,
                    "top_topics": json.dumps(feat.top_topics),
                    "sources": json.dumps(feat.sources),
                })
            curr_dt += timedelta(days=1)

    df_out = pd.DataFrame(all_feature_rows)
    return df_out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recopilación y procesamiento de noticias históricas con FinBERT."
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="AAPL,NVDA,MSFT,SPY,QQQ,AMZN,META,TSLA",
        help="Símbolos separados por coma (default: AAPL,NVDA,MSFT,SPY,QQQ,AMZN,META,TSLA)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2024-09-01",
        help="Fecha de inicio YYYY-MM-DD (default: 2024-09-01)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=datetime.now(EASTERN_TZ).strftime("%Y-%m-%d"),
        help="Fecha de fin YYYY-MM-DD (default: hoy)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        choices=["auto", "cuda", "cpu"],
        help="Dispositivo de aceleración (auto, cuda, cpu)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Tamaño de lote para FinBERT (default: 32)",
    )
    parser.add_argument(
        "--output-parquet",
        type=str,
        default="data/news_features/historical_news_features.parquet",
        help="Ruta del archivo Parquet de salida",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default="data/news_features/historical_news_features.csv",
        help="Ruta del archivo CSV de salida",
    )
    parser.add_argument(
        "--raw-dir",
        type=str,
        default="data/news/raw",
        help="Directorio para caché de noticias raw",
    )
    parser.add_argument(
        "--synthetic-fallback",
        action="store_true",
        help="Generar noticias sintéticas si no hay credenciales de Alpaca disponibles",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Límite opcional de noticias por símbolo (para pruebas rápidas)",
    )
    parser.add_argument("--api-key", type=str, default=None, help="Alpaca API Key")
    parser.add_argument("--secret-key", type=str, default=None, help="Alpaca Secret Key")

    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=EASTERN_TZ)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=EASTERN_TZ)

    print("=" * 70)
    print(" PIPELINE DE NOTICIAS HISTÓRICAS Y FINBERT PARA TRADING BOT")
    print(f" Símbolos: {symbols}")
    print(f" Rango Temporal: {args.start} -> {args.end}")
    print(f" Dispositivo: {args.device} | Batch Size: {args.batch_size}")
    print("=" * 70)

    raw_dir_path = PROJECT_ROOT / args.raw_dir
    raw_dir_path.mkdir(parents=True, exist_ok=True)

    api_key, secret_key = get_alpaca_credentials(args.api_key, args.secret_key)
    raw_news_by_symbol: dict[str, list[dict[str, Any]]] = {}

    for sym in symbols:
        raw_cache_file = raw_dir_path / f"{sym}_raw_news.json"

        # 1. Intentar cargar desde caché si existe
        if raw_cache_file.exists():
            print(f"[{sym}] Cargando noticias desde caché local: {raw_cache_file.name}")
            with open(raw_cache_file, encoding="utf-8") as f:
                news_data = json.load(f)
        elif api_key and secret_key:
            # 2. Descargar de Alpaca
            news_data = download_alpaca_news(
                symbol=sym,
                start_dt=start_dt,
                end_dt=end_dt,
                api_key=api_key,
                secret_key=secret_key,
                limit_total=args.limit,
            )
            # Guardar en caché
            with open(raw_cache_file, "w", encoding="utf-8") as f:
                json.dump(news_data, f, indent=2, ensure_ascii=False)
        elif args.synthetic_fallback or not (api_key and secret_key):
            print(f"[{sym}] Claves de Alpaca no encontradas. Usando generador sintético para pruebas.")
            news_data = generate_synthetic_news_data(sym, start_dt, end_dt)
            with open(raw_cache_file, "w", encoding="utf-8") as f:
                json.dump(news_data, f, indent=2, ensure_ascii=False)
        else:
            print(f"[{sym}] No hay credenciales de Alpaca ni flag --synthetic-fallback activo. Omitiendo.")
            news_data = []

        raw_news_by_symbol[sym] = news_data

    # Inicializar predictor FinBERT
    predictor = FinBERTPredictor(device=args.device, batch_size=args.batch_size)

    # Procesar características
    print("\nProcesando características cuantitativas de noticias (NewsFeatures)...")
    df_features = process_news_features(
        symbols=symbols,
        raw_news_by_symbol=raw_news_by_symbol,
        predictor=predictor,
        start_date=start_dt,
        end_date=end_dt,
    )

    if df_features.empty:
        print("[WARNING] No se generaron características de noticias.")
        return

    # Guardar en CSV (siempre disponible)
    csv_out = PROJECT_ROOT / args.output_csv
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    df_features.to_csv(csv_out, index=False)
    print(f"\n[OK] Características guardadas en CSV: {csv_out} ({len(df_features)} filas)")

    # Guardar en Parquet si pyarrow / fastparquet está disponible
    parquet_out = PROJECT_ROOT / args.output_parquet
    try:
        df_features.to_parquet(parquet_out, index=False)
        print(f"[OK] Características guardadas en Parquet: {parquet_out}")
    except Exception as e:
        print(f"[INFO] Parquet no disponible ({e}). El archivo CSV está listo para ser consumido.")

    # Resumen estadístico
    print("\n" + "=" * 70)
    print(" RESUMEN DEL DATASET GENERADO")
    print(f" - Total de observaciones diarias: {len(df_features)}")
    print(f" - Símbolos cubiertos: {df_features['symbol'].unique().tolist()}")
    print(f" - Rango de fechas: {df_features['date'].min()} a {df_features['date'].max()}")
    print(f" - Sentimiento Promedio Global: {df_features['sentiment_mean'].mean():.4f}")
    veto_candidates = df_features[df_features["negative_share"] >= 0.35]
    print(f" - Eventos con Veto Potencial (negative_share >= 0.35): {len(veto_candidates)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
