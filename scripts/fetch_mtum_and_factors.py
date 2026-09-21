#!/usr/bin/env python3
"""Descarga datos de MTUM para la regresión multifactorial (T-12 / F-22)."""

import datetime
import json
import urllib.request
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = PROJECT_ROOT / "data" / "MTUM_daily.csv"

START_DT = datetime.datetime(2013, 1, 1, tzinfo=datetime.timezone.utc)
END_DT = datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc)
P1 = int(START_DT.timestamp())
P2 = int(END_DT.timestamp())

url = f"https://query1.finance.yahoo.com/v8/finance/chart/MTUM?interval=1d&period1={P1}&period2={P2}"
headers = {"User-Agent": "Mozilla/5.0"}
req = urllib.request.Request(url, headers=headers)

with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read().decode("utf-8"))

res = data["chart"]["result"][0]
ts = res["timestamp"]
quotes = res["indicators"]["quote"][0]
adj_close = res["indicators"].get("adjclose", [{}])[0].get("adjclose", quotes["close"])

dates = [datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d") for t in ts]
df = pd.DataFrame({
    "date": dates,
    "open": quotes["open"],
    "high": quotes["high"],
    "low": quotes["low"],
    "close": quotes["close"],
    "adj_close": adj_close,
    "volume": quotes["volume"],
})
df = df.dropna(subset=["close"]).reset_index(drop=True)
df.to_csv(OUT_FILE, index=False)
print(f"Descargadas {len(df)} barras de MTUM en: {OUT_FILE}")
