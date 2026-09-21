#!/usr/bin/env python3
"""Descarga datos de mercado históricos diarios para el Universo A (18 ETFs + SPY).

Cubre el período completo desde junio de 2018 hasta la actualidad (2026):
- 11 Sectores GICS: XLK, XLC, XLY, XLP, XLV, XLF, XLI, XLE, XLU, XLB, XLRE
- 2 Internacionales: IEFA, IEMG
- 2 Metales Físicos: GLDM, SLV
- 3 Renta Fija / Defensivos: IEF, TIP, BIL
- Benchmark / Régimen: SPY
"""

from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "universe_a"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_A_TICKERS = [
    # Benchmark & Régimen
    "SPY",
    # Sectores GICS
    "XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE",
    # Internacional
    "IEFA", "IEMG",
    # Metales
    "GLDM", "SLV",
    # Renta Fija
    "IEF", "TIP", "BIL",
]

# Rango: 1 de junio de 2018 (inicio de cotización de XLC y GLDM) hasta la fecha
START_DT = datetime.datetime(2018, 6, 1, tzinfo=datetime.timezone.utc)
END_DT = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)

P1 = int(START_DT.timestamp())
P2 = int(END_DT.timestamp())

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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

        # Filtrar registros duplicados y ordenar por fecha
        df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df

    except Exception as exc:
        print(f"  [ERROR] Fallo al descargar {symbol}: {exc}")
        return None


def main() -> int:
    print("=" * 80)
    print(" DESCARGA DE DATOS HISTÓRICOS — UNIVERSO A (18 ETFs + SPY)")
    print(f" Destino: {OUTPUT_DIR}")
    print(f" Rango: {START_DT.date()} a {END_DT.date()}")
    print(f" Activos ({len(UNIVERSE_A_TICKERS)}): {', '.join(UNIVERSE_A_TICKERS)}")
    print("=" * 80)

    success_count = 0

    for i, symbol in enumerate(UNIVERSE_A_TICKERS, 1):
        target_path = OUTPUT_DIR / f"{symbol}_daily.csv"
        print(f"[{i:02d}/{len(UNIVERSE_A_TICKERS):02d}] Descargando {symbol:<5} ...", end=" ", flush=True)

        df = download_symbol(symbol)
        if df is not None and not df.empty:
            df.to_csv(target_path, index=False)
            print(f"[OK] {len(df)} barras guardadas ({df['date'].iloc[0]} a {df['date'].iloc[-1]})")
            success_count += 1
        else:
            print("[FALLO]")

        time.sleep(0.5)

    print("-" * 80)
    print(f"Descarga completada con éxito: {success_count}/{len(UNIVERSE_A_TICKERS)} activos.")
    return 0 if success_count == len(UNIVERSE_A_TICKERS) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
