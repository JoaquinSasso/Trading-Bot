#!/usr/bin/env python3
"""Descarga datos de mercado diarios reales (2019-09-01 a 2023-01-15) para los 14 activos del universo.

Asegura el historial previo necesario para el calentamiento de indicadores (EMA50, SMA200 y Momentum a 45 días)
desde el primer día hábil de 2020 hasta el último de 2022.
"""

from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "historical_2020_2022"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]

# Rango temporal: Septiembre 2019 a Enero 2023
START_DT = datetime.datetime(2019, 9, 1, tzinfo=datetime.timezone.utc)
END_DT = datetime.datetime(2023, 1, 15, tzinfo=datetime.timezone.utc)

P1 = int(START_DT.timestamp())
P2 = int(END_DT.timestamp())

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def download_symbol(symbol: str) -> pd.DataFrame | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&period1={P1}&period2={P2}"
    req = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        res = data["chart"]["result"][0]
        ts_list = res.get("timestamp", [])
        indicators = res["indicators"]["quote"][0]

        dates = [datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).date() for t in ts_list]

        df = pd.DataFrame({
            "date": dates,
            "open": [round(x, 4) if x is not None else None for x in indicators.get("open", [])],
            "high": [round(x, 4) if x is not None else None for x in indicators.get("high", [])],
            "low": [round(x, 4) if x is not None else None for x in indicators.get("low", [])],
            "close": [round(x, 4) if x is not None else None for x in indicators.get("close", [])],
            "volume": indicators.get("volume", []),
            "symbol": symbol,
        }).dropna()

        out_path = OUTPUT_DIR / f"{symbol}_daily.csv"
        df.to_csv(out_path, index=False)
        print(f"[{symbol}] Descargadas {len(df)} barras ({df['date'].min()} a {df['date'].max()}) -> {out_path.name}")
        return df

    except Exception as e:  # noqa: BLE001
        print(f"[{symbol}] Error al descargar: {e}")
        return None


def main() -> None:
    print("=" * 70)
    print(" DESCARGA DE DATOS HISTÓRICOS 2020-2022 (CON CALENTAMIENTO DESDE 2019)")
    print(f" Universo ({len(SYMBOLS)} activos): {SYMBOLS}")
    print(f" Directorio destino: {OUTPUT_DIR}")
    print("=" * 70)

    for sym in SYMBOLS:
        download_symbol(sym)
        time.sleep(0.5)

    print("\n[OK] Descarga completada exitosamente.")


if __name__ == "__main__":
    main()
