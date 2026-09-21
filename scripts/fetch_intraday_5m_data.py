#!/usr/bin/env python3
"""Descarga y construye el dataset intradiario de barras de 5 minutos (60 días).

Descarga barras de 5m desde Yahoo Finance API para el universo intradiario líquido:
- Índices/ETFs: SPY, QQQ, IWM, GLD
- Acciones Mega-cap Líderes: NVDA, AAPL, MSFT, AMZN, META, GOOGL, TSLA, AMD

Filtra estrictamente por horario regular de mercado (RTH: 09:30 a 16:00 ET).
Guarda los archivos en:
  data/intraday_5m/{ticker}_5m.csv
"""

from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "intraday_5m"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EASTERN_TZ = ZoneInfo("America/New_York")

INTRADAY_UNIVERSE = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def fetch_5m_bars(ticker: str) -> pd.DataFrame | None:
    """Descarga hasta 60 días de barras de 5 minutos desde Yahoo Finance."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=5m&range=60d&includePrePost=false"
    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            res = data["chart"]["result"][0]
            ts_list = res.get("timestamp", [])
            if not ts_list:
                print(f"[{ticker}] Sin timestamps devueltos.")
                return None

            quotes = res["indicators"]["quote"][0]
            opens = quotes.get("open", [])
            highs = quotes.get("high", [])
            lows = quotes.get("low", [])
            closes = quotes.get("close", [])
            vols = quotes.get("volume", [])

            rows = []
            for t, o, h, l, c, v in zip(ts_list, opens, highs, lows, closes, vols):
                if None in (t, o, h, l, c) or pd.isna(c) or c <= 0:
                    continue

                # Convertir timestamp UTC a hora local de Nueva York (ET)
                dt_utc = datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
                dt_et = dt_utc.astimezone(EASTERN_TZ)

                # Filtrar solo sesión regular (09:30 a 16:00 ET, lunes a viernes)
                if dt_et.weekday() >= 5:
                    continue

                time_et = dt_et.time()
                # La última barra de 5m abre a las 15:55 y cierra a las 16:00
                if datetime.time(9, 30) <= time_et <= datetime.time(15, 55):
                    rows.append({
                        "datetime_et": dt_et.strftime("%Y-%m-%d %H:%M:%S"),
                        "date": dt_et.strftime("%Y-%m-%d"),
                        "time": dt_et.strftime("%H:%M:%S"),
                        "timestamp": t,
                        "open": round(float(o), 4),
                        "high": round(float(h), 4),
                        "low": round(float(l), 4),
                        "close": round(float(c), 4),
                        "volume": int(v) if v is not None and not pd.isna(v) else 0,
                    })

            df = pd.DataFrame(rows)
            if df.empty:
                print(f"[{ticker}] Sin barras válidas tras filtrar por horario regular.")
                return None

            df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
            return df

        except Exception as exc:
            print(f"[{ticker}] Intento {attempt} falló: {exc}")
            time.sleep(1.5)

    return None


def main() -> int:
    print("=" * 80)
    print("   DESCARGA DE BARRAS DE 5 MINUTOS — UNIVERSO INTRADIARIO (60 DÍAS)")
    print("=" * 80)

    success_count = 0
    total_bars_dict = {}

    for sym in INTRADAY_UNIVERSE:
        print(f"Descargando {sym:<6} (5m)...", end=" ", flush=True)
        df = fetch_5m_bars(sym)
        if df is not None and not df.empty:
            out_file = DATA_DIR / f"{sym}_5m.csv"
            df.to_csv(out_file, index=False)
            n_bars = len(df)
            n_days = df["date"].nunique()
            total_bars_dict[sym] = n_bars
            print(f"OK! {n_bars} barras ({n_days} sesiones) -> {out_file.name}")
            success_count += 1
        else:
            print("ERROR.")

    print("\n" + "-" * 80)
    print(f"Descarga completada: {success_count}/{len(INTRADAY_UNIVERSE)} símbolos procesados con éxito.")
    print(f"Directorio de almacenamiento: {DATA_DIR}")
    print("=" * 80)
    return 0 if success_count == len(INTRADAY_UNIVERSE) else 1


if __name__ == "__main__":
    raise SystemExit(main())
