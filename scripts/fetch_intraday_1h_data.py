#!/usr/bin/env python3
"""Descarga dataset intradiario de barras de 1 hora (730 días / 2 años).

Descarga barras horarias desde Yahoo Finance para los 12 activos líquidos:
- SPY, QQQ, IWM, GLD
- NVDA, AAPL, MSFT, AMZN, META, GOOGL, TSLA, AMD
Almacena los resultados en: data/intraday_1h/{ticker}_1h.csv
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "intraday_1h"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EASTERN_TZ = ZoneInfo("America/New_York")

INTRADAY_UNIVERSE = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def fetch_1h_bars(ticker: str) -> pd.DataFrame | None:
    """Descarga hasta 730 días de barras de 1 hora."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1h&range=730d&includePrePost=false"
    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
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
            volumes = quotes.get("volume", [])

            records = []
            for ts, o, h, l, c, v in zip(ts_list, opens, highs, lows, closes, volumes):
                if any(x is None for x in (o, h, l, c)):
                    continue
                dt_utc = pd.to_datetime(ts, unit="s", utc=True)
                dt_et = dt_utc.tz_convert(EASTERN_TZ)

                # RTH horario regular de mercado
                records.append({
                    "timestamp": ts,
                    "datetime_et": dt_et.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": dt_et.strftime("%Y-%m-%d"),
                    "time": dt_et.strftime("%H:%M:%S"),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v) if v is not None else 0.0,
                })

            df = pd.DataFrame(records)
            return df
        except Exception as exc:
            print(f"[{ticker}] Intento {attempt} falló: {exc}")
            time.sleep(1.5)

    return None


def main() -> int:
    print(f"Descargando barras de 1 hora (2 años / 730 días) para {len(INTRADAY_UNIVERSE)} activos...")
    for sym in INTRADAY_UNIVERSE:
        print(f"  Descargando {sym}...", end="", flush=True)
        df = fetch_1h_bars(sym)
        if df is not None and not df.empty:
            out_p = DATA_DIR / f"{sym}_1h.csv"
            df.to_csv(out_p, index=False)
            print(f" OK: {len(df)} barras ({df['date'].min()} a {df['date'].max()})")
        else:
            print(f" ERROR al descargar {sym}")
        time.sleep(0.5)

    print("\nDescarga intradiaria 1h completada exitosamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
