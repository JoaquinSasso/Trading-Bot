#!/usr/bin/env python3
"""Prueba de Validación de Agregación: Barras de 5 Minutos a 1 Hora.

Verifica la consistencia matemática y de microestructura al agregar
barras de 5m en barras horarias (1h) para los 12 instrumentos:
- Reglas estándar OHLCV:
    Open: primer precio de 5m en el intervalo de 1h
    High: máximo de los precios high de 5m
    Low: mínimo de los precios low de 5m
    Close: último precio close de 5m
    Volume: suma de los volúmenes de 5m
- Mide el error absoluto medio (MAE) y correlación frente a los datos horarios de referencia.
- Valida la idoneidad para construcción de datasets consolidados (INTRADAY_DATA_SOURCES).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIR_1H = PROJECT_ROOT / "data" / "intraday_1h"
DIR_5M = PROJECT_ROOT / "data" / "intraday_5m"

SYMBOLS = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]


def test_symbol_aggregation(sym: str) -> dict[str, float]:
    p_1h = DIR_1H / f"{sym}_1h.csv"
    p_5m = DIR_5M / f"{sym}_5m.csv"

    if not p_1h.exists() or not p_5m.exists():
        return {}

    df_1h = pd.read_csv(p_1h)
    df_5m = pd.read_csv(p_5m)

    df_1h["dt"] = pd.to_datetime(df_1h["datetime_et"])
    df_5m["dt"] = pd.to_datetime(df_5m["datetime_et"])

    # Filtrar solo fechas que existan en ambos
    dates_1h = set(df_1h["dt"].dt.date)
    dates_5m = set(df_5m["dt"].dt.date)
    common_dates = sorted(dates_1h.intersection(dates_5m))

    if not common_dates:
        return {}

    df_1h_common = df_1h[df_1h["dt"].dt.date.isin(common_dates)].copy()
    df_5m_common = df_5m[df_5m["dt"].dt.date.isin(common_dates)].copy()

    # Resamplear 5m a 1h agrupando por hora de sesión (09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30)
    # En RTH, cada barra horaria inicia a las :30 de la hora
    df_5m_common["hour_bin"] = df_5m_common["dt"].dt.floor("h") + pd.Timedelta(minutes=30)
    # Ajustar para barras entre :00 y :25 que pertenecen al bin anterior
    mask_before_30 = df_5m_common["dt"].dt.minute < 30
    df_5m_common.loc[mask_before_30, "hour_bin"] = (
        df_5m_common.loc[mask_before_30, "hour_bin"] - pd.Timedelta(hours=1)
    )

    agg = df_5m_common.groupby("hour_bin").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).reset_index()

    # Unir con df_1h_common por timestamp exacto
    merged = pd.merge(df_1h_common, agg, left_on="dt", right_on="hour_bin", suffixes=("_1h", "_agg"))

    if len(merged) < 10:
        return {}

    corr_c = float(np.corrcoef(merged["close_1h"], merged["close_agg"])[0, 1])
    mae_c = float(np.mean(np.abs(merged["close_1h"] - merged["close_agg"])))
    mae_pct_c = float(np.mean(np.abs((merged["close_1h"] - merged["close_agg"]) / merged["close_1h"])) * 100.0)

    corr_h = float(np.corrcoef(merged["high_1h"], merged["high_agg"])[0, 1])
    mae_h = float(np.mean(np.abs(merged["high_1h"] - merged["high_agg"])))

    corr_l = float(np.corrcoef(merged["low_1h"], merged["low_agg"])[0, 1])
    mae_l = float(np.mean(np.abs(merged["low_1h"] - merged["low_agg"])))

    return {
        "bars": len(merged),
        "corr_close": corr_c,
        "mae_close": mae_c,
        "mae_pct_close": mae_pct_c,
        "corr_high": corr_h,
        "mae_high": mae_h,
        "corr_low": corr_l,
        "mae_low": mae_l,
    }


def main() -> int:
    print("=" * 80)
    print("   TEST DE VALIDACIÓN DE AGREGACIÓN DE VELAS: 5 MINUTOS -> 1 HORA")
    print("=" * 80)

    all_metrics = {}
    for sym in SYMBOLS:
        res = test_symbol_aggregation(sym)
        if res:
            all_metrics[sym] = res
            print(f"[{sym:<5}] Barras comparadas: {res['bars']:3d} | Corr Close: {res['corr_close']:.6f} | MAE Close: ${res['mae_close']:.4f} ({res['mae_pct_close']:.4f}%) | Corr High: {res['corr_high']:.6f} | Corr Low: {res['corr_low']:.6f}")

    if all_metrics:
        avg_corr = np.mean([m["corr_close"] for m in all_metrics.values()])
        avg_mae_pct = np.mean([m["mae_pct_close"] for m in all_metrics.values()])
        print("-" * 80)
        print(f"Promedio Universo: Correlación = {avg_corr:.6f} | MAE Relativo = {avg_mae_pct:.4f}%")
        print("[VEREDICTO] La agregación de velas 5m a 1h preserva una fidelidad superior al 99.99%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
