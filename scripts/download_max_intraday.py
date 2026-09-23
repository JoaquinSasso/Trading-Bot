# -*- coding: utf-8 -*-
"""
download_max_intraday.py
========================
Descarga el MAXIMO historial posible de datos intradía 5min y 1min de forma
completamente gratuita, usando múltiples fuentes en cascada de mayor a menor
historia disponible.

FUENTES (por historia descendente):
  1. Alpha Vantage  -- hasta 2 años de 5min (API key gratuita, mes a mes)
  2. Yahoo Finance  -- ~85 dias de 5min, ~7 dias de 1min (sin API key)
  3. Stooq         -- varios meses de 5min via URL directa (sin API key)

Salida:
  data/intraday_5min/  → {TICKER}_5min_FUENTE.csv
  data/intraday_1min/  → {TICKER}_1min_FUENTE.csv
  data/firstrate_raw/  → ZIPs originales de FirstRate (ya descargados)

Uso:
  python scripts/download_max_intraday.py
  python scripts/download_max_intraday.py --av-key TU_CLAVE_AQUI
"""

import io
import os
import sys
import time
import argparse
import datetime
import traceback
from pathlib import Path

# Forzar UTF-8 en Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import requests

# -- Configuracion ---------------------------------------------------------------
BASE_DIR  = Path(__file__).resolve().parent.parent
DIR_5MIN  = BASE_DIR / "data" / "intraday_5min"
DIR_1MIN  = BASE_DIR / "data" / "intraday_1min"
DIR_OTHER = BASE_DIR / "data" / "intraday_other"

for d in [DIR_5MIN, DIR_1MIN, DIR_OTHER]:
    d.mkdir(parents=True, exist_ok=True)

# Tickers objetivo (estrategia S5 Dual Momentum + cash yield)
TICKERS = [
    "AAPL", "MSFT", "AMZN", "GOOG", "META", "NFLX", "TSLA",  # Momentum stocks
    "SPY",  "DIA",  "IWM",                                     # Broad market ETFs
    "GLD",  "SLV",                                             # Custodia fisica (GEMINI.md)
    "XOM",                                                     # Energia integrada (GEMINI.md)
    "SGOV",                                                    # Cash yield T-Bills
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ================================================================================
# UTILIDADES COMUNES
# ================================================================================

def normalize_ohlcv(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Estandariza columnas a [DateTime, Open, High, Low, Close, Volume]."""
    df = df.copy()

    # Si el indice es un DatetimeIndex, convertirlo a columna
    if isinstance(df.index, pd.DatetimeIndex):
        df.reset_index(inplace=True)

    # Mapeo flexible de nombres
    rename = {}
    for col in df.columns:
        cl = str(col).strip().lower()
        if cl in ("datetime", "date", "time", "timestamp", "index", "datetime (us/eastern)"):
            rename[col] = "DateTime"
        elif cl in ("open",):
            rename[col] = "Open"
        elif cl in ("high",):
            rename[col] = "High"
        elif cl in ("low",):
            rename[col] = "Low"
        elif cl in ("close", "adj close", "adjusted close"):
            rename[col] = "Close"
        elif cl in ("volume", "vol"):
            rename[col] = "Volume"
    df.rename(columns=rename, inplace=True)

    # Conservar solo columnas OHLCV estandar
    keep = [c for c in ["DateTime", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].copy()

    if "DateTime" in df.columns:
        df["DateTime"] = pd.to_datetime(df["DateTime"], errors="coerce")
        df.dropna(subset=["DateTime"], inplace=True)
        df.sort_values("DateTime", inplace=True)
        df.drop_duplicates(subset=["DateTime"], inplace=True)
        df.reset_index(drop=True, inplace=True)

    return df


def save_and_report(df: pd.DataFrame, ticker: str, interval: str, source: str) -> dict:
    """Guarda el CSV y devuelve estadisticas."""
    out_dir = DIR_5MIN if "5" in interval else (DIR_1MIN if "1m" in interval else DIR_OTHER)
    filename = f"{ticker}_{interval}_{source}.csv"
    out_path = out_dir / filename

    df.to_csv(out_path, index=False)

    rows = len(df)
    date_start = str(df["DateTime"].min())[:19] if "DateTime" in df.columns and rows > 0 else "N/A"
    date_end   = str(df["DateTime"].max())[:19] if "DateTime" in df.columns and rows > 0 else "N/A"

    print(f"    [GUARDADO] {filename}")
    print(f"    Filas: {rows:,} | Inicio: {date_start} | Fin: {date_end}")

    return {
        "ticker": ticker, "interval": interval, "source": source,
        "file": filename, "rows": rows, "date_start": date_start, "date_end": date_end,
    }


# ================================================================================
# FUENTE 1: Yahoo Finance via yfinance
# Historia: ~85 dias para 5min, ~7 dias para 1min
# ================================================================================

def fetch_yfinance(ticker: str) -> list:
    """Descarga el maximo historial posible de Yahoo Finance."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [SKIP yfinance] Modulo no instalado.")
        return []

    results = []
    print(f"  [Yahoo Finance] Descargando {ticker}...")

    for interval, period, label in [
        ("5m",  "60d", "5min"),
        ("1m",  "7d",  "1min"),
        ("30m", "60d", "30min"),
        ("1h",  "730d","1hour"),
    ]:
        try:
            t = yf.Ticker(ticker)
            df = t.history(period=period, interval=interval, auto_adjust=True, prepost=True)
            if df.empty:
                print(f"    [VACIO] {ticker} {interval}")
                continue
            df = normalize_ohlcv(df, ticker)
            if len(df) > 0:
                r = save_and_report(df, ticker, label, "yfinance")
                results.append(r)
        except Exception as exc:
            print(f"    [ERROR yfinance] {ticker} {interval}: {exc}")
        time.sleep(0.3)  # Cortesia con Yahoo

    return results


# ================================================================================
# FUENTE 2: Alpha Vantage (API key gratuita — 25 requests/dia)
# Historia: hasta 2 años mes a mes para 5min
# Registro gratuito: https://www.alphavantage.co/support/#api-key
# ================================================================================

AV_BASE = "https://www.alphavantage.co/query"

def fetch_alpha_vantage(ticker: str, api_key: str, months_back: int = 24) -> list:
    """
    Descarga datos 5min de Alpha Vantage usando el parametro month=YYYY-MM.
    Con una clave gratuita: 25 requests/dia. Cada mes = 1 request.
    months_back=24 consume 24 calls (se recomienda ejecutar en varias sesiones).
    """
    if not api_key or api_key == "DEMO":
        print(f"  [SKIP Alpha Vantage] Sin API key valida para {ticker}.")
        return []

    print(f"  [Alpha Vantage] Descargando {ticker} ({months_back} meses)...")
    all_frames = []
    today = datetime.date.today()

    for i in range(months_back):
        target = today.replace(day=1) - datetime.timedelta(days=i * 30)
        month_str = target.strftime("%Y-%m")
        params = {
            "function":   "TIME_SERIES_INTRADAY",
            "symbol":     ticker,
            "interval":   "5min",
            "month":      month_str,
            "outputsize": "full",
            "datatype":   "csv",
            "apikey":     api_key,
        }
        try:
            r = requests.get(AV_BASE, params=params, timeout=30, headers=HEADERS)
            if r.status_code != 200:
                print(f"    [HTTP {r.status_code}] {month_str}")
                continue
            if "Information" in r.text[:200] or "limit" in r.text[:200].lower():
                print(f"    [LIMITE API] Pausando 60s...")
                time.sleep(60)
                continue
            if "timestamp" not in r.text[:100].lower():
                print(f"    [AVISO] Respuesta inesperada para {month_str}: {r.text[:80]!r}")
                continue
            df = pd.read_csv(io.StringIO(r.text))
            if len(df) > 0:
                all_frames.append(df)
                print(f"    {month_str}: {len(df):,} velas")
            time.sleep(12)  # Respetar limite: 5 calls/min en tier gratuito
        except Exception as exc:
            print(f"    [ERROR AV] {month_str}: {exc}")

    if not all_frames:
        return []

    combined = pd.concat(all_frames, ignore_index=True)
    combined = normalize_ohlcv(combined, ticker)
    combined.drop_duplicates(subset=["DateTime"], inplace=True)
    combined.sort_values("DateTime", inplace=True)
    combined.reset_index(drop=True, inplace=True)

    result = save_and_report(combined, ticker, "5min", "alphavantage")
    return [result]


# ================================================================================
# FUENTE 3: Stooq via URL directa CSV
# Historia: variable, tipicamente 1-6 meses de intradía 5min
# URL: https://stooq.com/q/d/l/?s=AAPL.US&i=5
# ================================================================================

STOOQ_INTERVAL_MAP = {
    "5min":  "5",
    "1min":  "1",
    "1hour": "60",
}

def fetch_stooq(ticker: str) -> list:
    """
    Intenta descargar datos intradía desde Stooq.
    Stooq usa el sufijo .US para acciones americanas.
    """
    stooq_ticker = f"{ticker}.US"
    results = []
    print(f"  [Stooq] Descargando {ticker}...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://stooq.com/",
    })

    for label, interval_code in STOOQ_INTERVAL_MAP.items():
        url = f"https://stooq.com/q/d/l/?s={stooq_ticker}&i={interval_code}"
        try:
            r = session.get(url, timeout=20, allow_redirects=True)
            if r.status_code != 200:
                print(f"    [HTTP {r.status_code}] {label}")
                continue
            content = r.text.strip()
            if "No data" in content or len(content) < 50 or "Date" not in content:
                print(f"    [SIN DATOS] Stooq {ticker} {label}")
                continue
            df = pd.read_csv(io.StringIO(content))
            df = normalize_ohlcv(df, ticker)
            if len(df) > 0:
                r2 = save_and_report(df, ticker, label, "stooq")
                results.append(r2)
        except Exception as exc:
            print(f"    [ERROR Stooq] {ticker} {label}: {exc}")
        time.sleep(1)

    return results


# ================================================================================
# FUENTE 4: Consolidacion inteligente (merge y dedup)
# Combina yfinance + AV + Stooq eliminando duplicados por DateTime
# ================================================================================

def consolidate_ticker(ticker: str, interval: str) -> dict | None:
    """Busca todos los CSVs del ticker/intervalo y genera un archivo consolidado."""
    out_dir  = DIR_5MIN if "5" in interval else DIR_1MIN
    pattern  = f"{ticker}_{interval}_"
    files    = sorted(out_dir.glob(f"{pattern}*.csv"))

    if len(files) < 2:
        return None  # Nada que consolidar

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, parse_dates=["DateTime"])
            frames.append(df)
        except Exception:
            pass

    if not frames:
        return None

    combined = pd.concat(frames, ignore_index=True)
    combined = normalize_ohlcv(combined, ticker)
    combined.drop_duplicates(subset=["DateTime"], inplace=True)
    combined.sort_values("DateTime", inplace=True)
    combined.reset_index(drop=True, inplace=True)

    out_path = out_dir / f"{ticker}_{interval}_CONSOLIDATED.csv"
    combined.to_csv(out_path, index=False)

    rows = len(combined)
    d_start = str(combined["DateTime"].min())[:19]
    d_end   = str(combined["DateTime"].max())[:19]
    print(f"  [CONSOLIDADO] {out_path.name}: {rows:,} filas | {d_start} -> {d_end}")
    return {
        "ticker": ticker, "interval": interval, "source": "CONSOLIDATED",
        "file": out_path.name, "rows": rows, "date_start": d_start, "date_end": d_end,
    }


# ================================================================================
# MAIN
# ================================================================================

def print_summary(results: list) -> None:
    sep  = "=" * 95
    dash = "-" * 93
    print(f"\n{sep}")
    print("  RESUMEN FINAL -- DATOS INTRADAY MAXIMOS DESCARGADOS")
    print(sep)
    fmt = "  {:<8} {:<7} {:<18} {:<10} {:<22} {:<22}"
    print(fmt.format("TICKER", "INTV", "FUENTE", "FILAS", "INICIO", "FIN"))
    print(f"  {dash}")
    for r in sorted(results, key=lambda x: (x["ticker"], x["interval"], x["source"])):
        print(fmt.format(
            r["ticker"], r["interval"], r["source"],
            f"{r['rows']:,}", r["date_start"], r["date_end"]
        ))
    print(sep)
    print(f"  Total archivos: {len(results)}")

    summary_path = BASE_DIR / "data" / "intraday_max_summary.csv"
    pd.DataFrame(results).to_csv(summary_path, index=False)
    print(f"  Resumen guardado: {summary_path}")


def main():
    parser = argparse.ArgumentParser(description="Descarga maximo historial intradía gratuito")
    parser.add_argument(
        "--av-key", default=os.environ.get("ALPHA_VANTAGE_KEY", ""),
        help="API key de Alpha Vantage (gratuita en alphavantage.co)"
    )
    parser.add_argument(
        "--av-months", type=int, default=24,
        help="Meses hacia atras a descargar de Alpha Vantage (max 24, default 24)"
    )
    parser.add_argument(
        "--sources", nargs="+", default=["yfinance", "stooq", "alphavantage"],
        help="Fuentes a usar: yfinance stooq alphavantage"
    )
    parser.add_argument(
        "--tickers", nargs="+", default=TICKERS,
        help="Lista de tickers a procesar"
    )
    args = parser.parse_args()

    print("+" + "=" * 70 + "+")
    print("|  Descargador MAXIMO de intradía 5min/1min -- Multi-fuente        |")
    print("+" + "=" * 70 + "+")
    print(f"  Fuentes activas : {args.sources}")
    print(f"  Tickers         : {args.tickers}")
    print(f"  Alpha Vantage   : {'key configurada' if args.av_key else 'SIN KEY (se omite)'}")
    print(f"  Meses AV        : {args.av_months}")
    print()

    all_results = []

    for ticker in args.tickers:
        sep60 = "=" * 60
        print(f"\n{sep60}")
        print(f"  Ticker: {ticker}")
        print(sep60)

        # Fuente 1: Yahoo Finance (mas rapido, sin key)
        if "yfinance" in args.sources:
            try:
                all_results.extend(fetch_yfinance(ticker))
            except Exception as exc:
                print(f"  [ERROR yfinance] {ticker}: {exc}")

        # Fuente 2: Stooq (sin key)
        if "stooq" in args.sources:
            try:
                all_results.extend(fetch_stooq(ticker))
            except Exception as exc:
                print(f"  [ERROR Stooq] {ticker}: {exc}")

        # Fuente 3: Alpha Vantage (requiere key gratuita)
        if "alphavantage" in args.sources and args.av_key:
            try:
                all_results.extend(fetch_alpha_vantage(ticker, args.av_key, args.av_months))
            except Exception as exc:
                print(f"  [ERROR Alpha Vantage] {ticker}: {exc}")

    # Consolidar por ticker x intervalo
    print("\n" + "=" * 60)
    print("  Consolidando archivos por ticker...")
    print("=" * 60)
    for ticker in args.tickers:
        for interval in ["5min", "1min"]:
            try:
                r = consolidate_ticker(ticker, interval)
                if r:
                    all_results.append(r)
            except Exception as exc:
                print(f"  [ERROR consolidar] {ticker} {interval}: {exc}")

    print_summary(all_results)


if __name__ == "__main__":
    main()
