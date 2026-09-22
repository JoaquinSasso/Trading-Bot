#!/usr/bin/env python3
"""Descarga y construye el dataset histórico diario (2010–2026) para la prueba multiciclo.

Descarga las 4.063 sesiones diarias oficiales de:
- Activos: SPY, QQQ, AAPL, MSFT, NVDA, AMZN, META (post-IPO), GOOGL, JPM, LLY, XOM, COST, GLD, SLV
- Activo libre de riesgo: BIL (T-Bills 0-3m)

Genera:
- 'data/historical_2010_2026/{ticker}_daily.csv'
- 'data/risk_free_rate_bil_2010_2026.csv'
"""

from __future__ import annotations

import datetime
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "historical_2010_2026"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RF_OUTPUT_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil_2010_2026.csv"

START_DT = datetime.datetime(2010, 1, 1, tzinfo=datetime.timezone.utc)
END_DT = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)
P1 = int(START_DT.timestamp())
P2 = int(END_DT.timestamp())

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

ALL_TICKERS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV", "BIL"
]


def fetch_ticker_data(ticker: str) -> pd.DataFrame | None:
    """Descarga datos diarios desde Yahoo Finance API y formatea a estándar OHLCV."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&period1={P1}&period2={P2}"
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
            adjclose_list = res["indicators"].get("adjclose", [{}])[0].get("adjclose", [])

            dates = [datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d") for t in ts_list]

            df = pd.DataFrame({
                "date": dates,
                "open": quotes.get("open", []),
                "high": quotes.get("high", []),
                "low": quotes.get("low", []),
                "close": quotes.get("close", []),
                "volume": quotes.get("volume", []),
                "adjclose": adjclose_list if adjclose_list else quotes.get("close", []),
                "symbol": ticker,
            })

            # Limpiar filas con valores nulos
            df = df.dropna(subset=["open", "high", "low", "close"]).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            print(f"[{ticker}] Intento {attempt}/3 falló: {e}")
            time.sleep(1.5)

    return None


def main() -> int:
    print("=" * 80)
    print("  DESCARGA DE DATOS HISTÓRICOS MULTICICLO (2010–2026)")
    print("=" * 80)

    bil_df: pd.DataFrame | None = None

    for ticker in ALL_TICKERS:
        print(f"Descargando {ticker:<5}...", end=" ", flush=True)
        df = fetch_ticker_data(ticker)
        if df is None or df.empty:
            print("[ERROR] No se pudo obtener datos.")
            continue

        d_start = df["date"].iloc[0]
        d_end = df["date"].iloc[-1]
        n_bars = len(df)
        print(f"[OK] {n_bars:>5} barras ({d_start} a {d_end})")

        # Guardar archivo estándar de mercado (sin adjclose para alimentar HistoricalDataLoader)
        market_df = df[["date", "open", "high", "low", "close", "volume", "symbol"]].copy()
        out_path = OUTPUT_DIR / f"{ticker}_daily.csv"
        market_df.to_csv(out_path, index=False)

        if ticker == "BIL":
            bil_df = df.copy()

        time.sleep(0.3)

    # Generar serie extendida de tasa libre de riesgo BIL
    if bil_df is not None and not bil_df.empty:
        print("\nGenerando serie histórica extendida de Tasa Libre de Riesgo (BIL)...")
        rf_df = bil_df[["date", "close", "adjclose"]].copy()
        rf_df["daily_rf"] = rf_df["adjclose"].pct_change().fillna(0.0)
        rf_df["annualized_rf"] = (1.0 + rf_df["daily_rf"]) ** 252 - 1.0
        rf_df.to_csv(RF_OUTPUT_FILE, index=False)
        print(f"[OK] Tasa libre de riesgo guardada en: {RF_OUTPUT_FILE} ({len(rf_df)} días)")

    print(f"\n[ÉXITO] Todos los datasets 2010–2026 preparados en: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
