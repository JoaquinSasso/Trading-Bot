#!/usr/bin/env python3
"""Descarga y construye la curva diaria histórica de Tasa Libre de Riesgo (T-03).

Utiliza la serie completa de Retorno Total Ajustado de BIL (SPDR Bloomberg 1-3 Month T-Bill ETF)
desde junio de 2018 hasta 2026.
Genera 'data/risk_free_rate_bil.csv' con:
- date
- close
- adjclose (incluye dividendos reinvertidos de letras del tesoro)
- daily_rf (retorno diario real del activo libre de riesgo)
- annualized_rf (tasa anualizada equivalente)
"""

from __future__ import annotations

import datetime
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil.csv"
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

START_DT = datetime.datetime(2018, 6, 1, tzinfo=datetime.timezone.utc)
END_DT = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)
P1 = int(START_DT.timestamp())
P2 = int(END_DT.timestamp())

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def main() -> int:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/BIL?interval=1d&period1={P1}&period2={P2}"
    req = urllib.request.Request(url, headers=HEADERS)

    print("Descargando serie histórica de retorno total de BIL (T-Bills 0-3m)...")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    res = data["chart"]["result"][0]
    ts_list = res.get("timestamp", [])
    quotes = res["indicators"]["quote"][0]
    adjclose_list = res["indicators"]["adjclose"][0]["adjclose"]

    dates = [datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).date() for t in ts_list]

    df = pd.DataFrame({
        "date": dates,
        "close": quotes.get("close", []),
        "adjclose": adjclose_list,
    }).dropna()

    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["daily_rf"] = df["adjclose"].pct_change().fillna(0.0)
    # Tasa anualizada móvil a 20 días
    df["annualized_rf"] = ((1.0 + df["daily_rf"]) ** 252.0 - 1.0).clip(lower=0.0)

    df.to_csv(OUTPUT_FILE, index=False)
    print(f"[OK] Guardadas {len(df)} observaciones diarias en {OUTPUT_FILE}")

    # Muestra representativa anual
    df["year"] = pd.to_datetime(df["date"]).dt.year
    for y, grp in df.groupby("year"):
        tot_ret = (grp["adjclose"].iloc[-1] - grp["adjclose"].iloc[0]) / grp["adjclose"].iloc[0] * 100.0
        print(f"  Año {y}: Retorno Total T-Bills (BIL) = {tot_ret:+.2f}%")

    return 0


if __name__ == "__main__":
    main()
