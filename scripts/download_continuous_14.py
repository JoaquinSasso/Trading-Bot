#!/usr/bin/env python3
"""Descarga datos continuos (2019 a 2026) para los 14 activos del universo oficial."""

from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "historical_14"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]

START_DT = datetime.datetime(2019, 1, 1, tzinfo=datetime.timezone.utc)
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

        df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df
    except Exception as exc:
        print(f"Error {symbol}: {exc}")
        return None


def main() -> int:
    print(f"Descargando 14 activos continuos (2019-2026) a {OUTPUT_DIR}...")
    for s in SYMBOLS:
        df = download_symbol(s)
        if df is not None and not df.empty:
            df.to_csv(OUTPUT_DIR / f"{s}_daily.csv", index=False)
            print(f"  [OK] {s}: {len(df)} barras ({df['date'].iloc[0]} a {df['date'].iloc[-1]})")
        time.sleep(0.3)
    return 0


if __name__ == "__main__":
    main()
