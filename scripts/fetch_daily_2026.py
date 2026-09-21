#!/usr/bin/env python3
"""Descarga barras diarias recientes (2 años hasta 2026-09-21) para el universo intradiario."""

import json
import time
import urllib.request
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "daily_2026"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TICKERS = [
    "SPY", "QQQ", "IWM", "GLD",
    "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
]

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_daily_2y(ticker: str) -> pd.DataFrame | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2y"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        quotes = res["indicators"]["quote"][0]
        df = pd.DataFrame({
            "timestamp": ts,
            "open": quotes.get("open"),
            "high": quotes.get("high"),
            "low": quotes.get("low"),
            "close": quotes.get("close"),
            "volume": quotes.get("volume"),
        }).dropna()
        df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"Error descargando {ticker}: {e}")
        return None


def main():
    print(f"Descargando datos diarios actualizados hasta 2026 para {len(TICKERS)} tickers...")
    for t in TICKERS:
        df = fetch_daily_2y(t)
        if df is not None:
            out_p = OUT_DIR / f"{t}_daily.csv"
            df.to_csv(out_p, index=False)
            print(f"  {t}: {len(df)} barras ({df['date'].min()} a {df['date'].max()})")
        time.sleep(0.3)
    print("Descarga completada.")


if __name__ == "__main__":
    main()
