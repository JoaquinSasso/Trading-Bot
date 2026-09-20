"""Motor de agregación y remuestreo de barras 1m a 5m para el feed IEX.

Implementa la agregación con intervalos [t, t + 5m) rotulados en el cierre (closed='left', label='right'),
cálculo de VWAP ponderado por volumen con fallback ante volumen cero, y soporte para
inyección de barras sintéticas planas para indicadores técnicos.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def resample_1m_to_5m(
    df_1m: pd.DataFrame,
    fill_missing: bool = False,
    expected_index: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    """Remuestrea barras de 1 minuto a 5 minutos.

    Reglas:
    - Intervalos semiabiertos a la izquierda: [09:30:00, 09:35:00) se etiqueta como 09:35:00
      usando closed='left', label='right'.
    - Agregación OHLCV: open=first, high=max, low=min, close=last, volume=sum.
    - VWAP: ponderado por volumen. Si el volumen total es 0, fallback al último precio de cierre.
    - Inyección de barras sintéticas si fill_missing=True o expected_index está provisto.
    """
    output_cols = [
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "trade_count",
        "is_synthetic",
    ]

    if df_1m.empty:
        return pd.DataFrame(columns=output_cols)

    df = df_1m.copy()

    # Normalizar índice temporal
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp")
        else:
            raise ValueError("DataFrame must contain 'timestamp' column or a DatetimeIndex")
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    # Columnas requeridas
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Missing required price column: '{col}'")
    if "volume" not in df.columns:
        df["volume"] = 0.0

    # Determinar columna para ponderar VWAP
    vwap_source = df["vwap"] if "vwap" in df.columns else df["close"]
    df["_dollar_vol"] = df["volume"] * vwap_source

    has_trade_count = "trade_count" in df.columns
    has_symbol = "symbol" in df.columns

    symbols = df["symbol"].unique() if has_symbol else [None]
    all_resampled: list[pd.DataFrame] = []

    for sym in symbols:
        sym_df = df[df["symbol"] == sym] if sym is not None else df
        sym_df = sym_df.sort_index()

        # Resample a 5m con closed='left', label='right'
        # Intervalo [09:30, 09:35) -> etiqueta 09:35:00
        resampler = sym_df.resample("5min", closed="left", label="right")

        agg_rules: dict[str, Any] = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "_dollar_vol": "sum",
        }
        if has_trade_count:
            agg_rules["trade_count"] = "sum"

        agg_df = resampler.agg(agg_rules).dropna(subset=["close"])
        if agg_df.empty:
            continue

        # Cálculo de VWAP con fallback a close en volumen 0
        zero_vol = agg_df["volume"] == 0
        agg_df["vwap"] = np.where(
            zero_vol,
            agg_df["close"],
            agg_df["_dollar_vol"] / np.where(zero_vol, 1.0, agg_df["volume"]),
        )
        agg_df = agg_df.drop(columns=["_dollar_vol"])

        if not has_trade_count:
            agg_df["trade_count"] = 0

        agg_df["is_synthetic"] = False
        if sym is not None:
            agg_df["symbol"] = sym

        # Inyección sintética si corresponde
        target_index = expected_index
        if target_index is None and fill_missing and len(agg_df) > 1:
            target_index = pd.date_range(
                start=agg_df.index.min(),
                end=agg_df.index.max(),
                freq="5min",
                tz="UTC",
            )

        if target_index is not None:
            # Reindex y rellenar faltantes
            reindexed = agg_df.reindex(target_index)
            synthetic_mask = reindexed["close"].isna() | (reindexed["is_synthetic"].isna())

            reindexed["close"] = reindexed["close"].ffill()
            reindexed["open"] = reindexed["open"].combine_first(reindexed["close"])
            reindexed["high"] = reindexed["high"].combine_first(reindexed["close"])
            reindexed["low"] = reindexed["low"].combine_first(reindexed["close"])
            reindexed["vwap"] = reindexed["vwap"].combine_first(reindexed["close"])

            reindexed.loc[synthetic_mask, "volume"] = 0.0
            reindexed.loc[synthetic_mask, "trade_count"] = 0
            reindexed.loc[synthetic_mask, "is_synthetic"] = True
            if sym is not None:
                reindexed["symbol"] = sym

            agg_df = reindexed.dropna(subset=["close"])

        out_sym = agg_df.reset_index().rename(columns={"index": "timestamp"})
        all_resampled.append(out_sym)

    if not all_resampled:
        return pd.DataFrame(columns=output_cols)

    result = pd.concat(all_resampled, ignore_index=True)

    # Asegurar orden y tipos de columnas
    for c in output_cols:
        if c not in result.columns:
            if c == "symbol":
                result[c] = ""
            elif c == "trade_count":
                result[c] = 0
            elif c == "is_synthetic":
                result[c] = False

    return result[output_cols].sort_values(by=["symbol", "timestamp"]).reset_index(drop=True)


# Alias para interoperabilidad
aggregate_1m_to_5m = resample_1m_to_5m
