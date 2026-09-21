"""Almacén y consulta de características históricas de noticias sin sesgo de futuro."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tbot.news.models import NewsFeatures


class HistoricalNewsFeatureStore:
    """Gestiona el acceso temporal indexado a las características cuantitativas de noticias."""

    def __init__(self, df: pd.DataFrame | None = None) -> None:
        self._df: pd.DataFrame = df if df is not None else pd.DataFrame()
        if not self._df.empty:
            self._prepare_index()

    def _prepare_index(self) -> None:
        """Asegura que el DataFrame tenga los tipos y columnas requeridos ordenados por tiempo."""
        if "timestamp" in self._df.columns:
            self._df["timestamp"] = pd.to_datetime(self._df["timestamp"], utc=True)
            self._df = self._df.sort_values("timestamp")
        elif "date" in self._df.columns:
            self._df["timestamp"] = pd.to_datetime(self._df["date"], utc=True)
            self._df = self._df.sort_values("timestamp")

    @classmethod
    def from_file(cls, file_path: str | Path) -> HistoricalNewsFeatureStore:
        """Carga características desde un archivo Parquet o CSV."""
        path = Path(file_path)
        if not path.exists():
            return cls(pd.DataFrame())

        if path.suffix == ".parquet":
            try:
                df = pd.read_parquet(path)
            except (ImportError, Exception):
                # Fallback suave al archivo CSV si pyarrow no está disponible
                csv_path = path.with_suffix(".csv")
                df = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
        else:
            df = pd.read_csv(path)

        return cls(df)

    def get_features(
        self,
        symbol: str,
        as_of: datetime,
        window: str = "24h",
        max_staleness_hours: float = 72.0,
    ) -> NewsFeatures:
        """Obtiene las características de noticias para un símbolo hasta `as_of`.

        Garantiza CERO sesgo de futuro (lookahead bias) buscando únicamente observaciones
        con timestamp <= as_of.
        """
        if self._df.empty:
            return NewsFeatures.empty(symbol=symbol, ts=as_of, window=window)

        # Normalizar as_of a UTC para comparación segura
        as_of_ts = pd.to_datetime(as_of, utc=True)

        sym_mask = self._df["symbol"].str.upper() == symbol.upper()
        time_mask = self._df["timestamp"] <= as_of_ts
        filtered = self._df[sym_mask & time_mask]

        if filtered.empty:
            return NewsFeatures.empty(symbol=symbol, ts=as_of, window=window)

        latest_row = filtered.iloc[-1]
        row_ts: datetime = latest_row["timestamp"].to_pydatetime()

        # Validar caducidad máxima
        staleness = as_of_ts - latest_row["timestamp"]
        if staleness.total_seconds() > max_staleness_hours * 3600:
            return NewsFeatures.empty(symbol=symbol, ts=as_of, window=window)

        top_topics = self._parse_list_field(latest_row.get("top_topics", []))
        sources = self._parse_list_field(latest_row.get("sources", []))

        return NewsFeatures(
            symbol=symbol,
            window=window,
            ts=row_ts,
            n_items=int(latest_row.get("n_items", 0)),
            sentiment_mean=float(latest_row.get("sentiment_mean", 0.0)),
            sentiment_min=float(latest_row.get("sentiment_min", 0.0)),
            negative_share=float(latest_row.get("negative_share", 0.0)),
            sources=sources,
            top_topics=top_topics,
        )

    @staticmethod
    def _parse_list_field(val: Any) -> list[str]:
        """Normaliza campos de lista que pueden venir como JSON, string separado por comas o lista."""
        if isinstance(val, list):
            return [str(x) for x in val]
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    loaded = json.loads(s)
                    if isinstance(loaded, list):
                        return [str(x) for x in loaded]
                except Exception:
                    pass
            return [p.strip() for p in s.split(",") if p.strip()]
        return []
