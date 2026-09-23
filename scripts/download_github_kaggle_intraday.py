# -*- coding: utf-8 -*-
"""
download_github_kaggle_intraday.py
===================================
Descarga series historicas largas (multi-anio) de velas intraday 5min/1min
desde dos fuentes gratuitas:

  OPCION A - GitHub (TheSnowGuru/Stocks-Futures-Financial-Time-series):
    AAPL      M5 / M1  (~5-6 MB cada uno, varios anios de historia)
    S&P500    M5 / M1  (~11 MB, varios anios de historia)
    NASDAQ100 M5 / M1  (~12 MB, varios anios de historia)
    NFLX      M5 / M1  (si disponible)
    TSLA      M5 / M1  (si disponible)

  OPCION B - Kaggle (via kaggle CLI / kagglehub):
    maged2/spy-intraday-ohlc         -> SPY 1min
    themisr/aapl-historical-intraday -> AAPL 1min 2010-2019
    Descarga en ./data/kaggle_intraday/

Salida:
  data/github_intraday/   -> CSVs del repo GitHub
  data/kaggle_intraday/   -> CSVs de Kaggle

Reporte final: primeros 5 y ultimos 5 registros + estadisticas por archivo.

Uso:
  python scripts/download_github_kaggle_intraday.py
  python scripts/download_github_kaggle_intraday.py --skip-kaggle   # sin Kaggle
  python scripts/download_github_kaggle_intraday.py --skip-github   # sin GitHub
"""

import io
import os
import sys
import time
import zipfile
import argparse
import subprocess
from pathlib import Path

# Forzar UTF-8 en consola Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import requests

# ============================================================================
# CONFIGURACION
# ============================================================================
BASE_DIR        = Path(__file__).resolve().parent.parent
DIR_GITHUB      = BASE_DIR / "data" / "github_intraday"
DIR_KAGGLE      = BASE_DIR / "data" / "kaggle_intraday"
for d in [DIR_GITHUB, DIR_KAGGLE]:
    d.mkdir(parents=True, exist_ok=True)

RAW_BASE = "https://raw.githubusercontent.com/TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data/main"

# Mapa de archivos GitHub a descargar: (ticker_label, raw_url, output_filename)
GITHUB_FILES = [
    # AAPL
    ("AAPL", "5min",  f"{RAW_BASE}/stock/aapl/AAPLUSUSD_M5.csv",     "AAPL_M5.csv"),
    ("AAPL", "1min",  f"{RAW_BASE}/stock/aapl/AAPLUSUSD_M1.csv",     "AAPL_M1.csv"),
    ("AAPL", "15min", f"{RAW_BASE}/stock/aapl/AAPLUSUSD_M15.csv",    "AAPL_M15.csv"),
    ("AAPL", "30min", f"{RAW_BASE}/stock/aapl/AAPLUSUSD_M30.csv",    "AAPL_M30.csv"),
    ("AAPL", "1h",    f"{RAW_BASE}/stock/aapl/AAPLUSUSD_H1.csv",     "AAPL_H1.csv"),
    # NFLX
    ("NFLX", "5min",  f"{RAW_BASE}/stock/nflx/NFLXUSUSD_M5.csv",    "NFLX_M5.csv"),
    ("NFLX", "1min",  f"{RAW_BASE}/stock/nflx/NFLXUSUSD_M1.csv",    "NFLX_M1.csv"),
    # TSLA
    ("TSLA", "5min",  f"{RAW_BASE}/stock/tsla/TSLAUSAUSD_M5.csv",   "TSLA_M5.csv"),
    ("TSLA", "1min",  f"{RAW_BASE}/stock/tsla/TSLAUSAUSD_M1.csv",   "TSLA_M1.csv"),
    # S&P 500 Index (proxy para SPY)
    ("SP500", "5min", f"{RAW_BASE}/indices/s%26p500/USA500IDXUSD_M5.csv",  "SP500_M5.csv"),
    ("SP500", "1min", f"{RAW_BASE}/indices/s%26p500/USA500IDXUSD_M1.csv",  "SP500_M1.csv"),
    ("SP500","15min", f"{RAW_BASE}/indices/s%26p500/USA500IDXUSD_M15.csv", "SP500_M15.csv"),
    ("SP500", "1h",   f"{RAW_BASE}/indices/s%26p500/USA500IDXUSD_H1.csv",  "SP500_H1.csv"),
    # NASDAQ 100 (proxy para QQQ)
    ("NDX100","5min", f"{RAW_BASE}/indices/nasdaq100/USATECHIDXUSD_M5.csv",  "NDX100_M5.csv"),
    ("NDX100","1min", f"{RAW_BASE}/indices/nasdaq100/USATECHIDXUSD_M1.csv",  "NDX100_M1.csv"),
    ("NDX100","15min",f"{RAW_BASE}/indices/nasdaq100/USATECHIDXUSD_M15.csv", "NDX100_M15.csv"),
    ("NDX100","1h",   f"{RAW_BASE}/indices/nasdaq100/USATECHIDXUSD_H1.csv",  "NDX100_H1.csv"),
]

# Datasets de Kaggle: (dataset_slug, target_subdir_label)
KAGGLE_DATASETS = [
    ("maged2/spy-intraday-ohlc",          "spy_intraday_ohlc"),
    ("themisr/aapl-historical-intraday",  "aapl_historical_intraday"),
    ("abdullahalturki/5-year-data-for-sp-500-nasdaq-100", "sp500_ndx_5year"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingBot-Research/1.0)",
    "Accept": "*/*",
}

# ============================================================================
# UTILIDADES
# ============================================================================

def download_file(url: str, dest: Path, label: str = "") -> bool:
    """Descarga un archivo con barra de progreso simple."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        r = session.get(url, stream=True, timeout=120)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct and "<html" in r.text[:200].lower():
            print(f"  [ERROR] Respuesta HTML inesperada para {label}")
            return False
        size = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
                downloaded += len(chunk)
        kb = downloaded / 1024
        print(f"  [OK] {dest.name}  ({kb:,.0f} KB)")
        return True
    except requests.HTTPError as e:
        print(f"  [HTTP {e.response.status_code}] {url}")
        return False
    except Exception as e:
        print(f"  [ERROR] {url} -> {e}")
        return False


def detect_datetime_col(df: pd.DataFrame):
    """Detecta la columna de fecha/hora."""
    for col in df.columns:
        cl = str(col).strip().lower()
        if cl in ("datetime", "date", "time", "timestamp", "date time",
                  "date/time", "<date>", "localtime"):
            return col
    # Intentar la primera columna si parece una fecha
    if len(df.columns) > 0:
        try:
            pd.to_datetime(df.iloc[:3, 0], errors="raise")
            return df.columns[0]
        except Exception:
            pass
    return None


def analyze_file(filepath: Path, ticker: str, interval: str) -> dict:
    """Lee el CSV y retorna estadisticas + head/tail."""
    try:
        df = pd.read_csv(filepath, low_memory=False)
        if df.empty:
            return {"ok": False, "error": "vacio"}

        dt_col = detect_datetime_col(df)
        if dt_col:
            df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")
            df = df.dropna(subset=[dt_col])
            df = df.sort_values(dt_col).reset_index(drop=True)
            date_start = str(df[dt_col].min())[:19]
            date_end   = str(df[dt_col].max())[:19]
        else:
            date_start = date_end = "N/A"

        return {
            "ok":         True,
            "ticker":     ticker,
            "interval":   interval,
            "file":       filepath.name,
            "rows":       len(df),
            "cols":       list(df.columns),
            "date_start": date_start,
            "date_end":   date_end,
            "head":       df.head(5),
            "tail":       df.tail(5),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "file": filepath.name}


def print_file_report(info: dict) -> None:
    """Imprime el reporte detallado de un archivo."""
    if not info.get("ok"):
        print(f"  [ERROR analisis] {info.get('file','?')}: {info.get('error','?')}")
        return

    sep = "-" * 70
    print(f"\n{sep}")
    print(f"  TICKER    : {info['ticker']}  ({info['interval']})")
    print(f"  ARCHIVO   : {info['file']}")
    print(f"  COLUMNAS  : {', '.join(str(c) for c in info['cols'])}")
    print(f"  FILAS     : {info['rows']:,}")
    print(f"  INICIO    : {info['date_start']}")
    print(f"  FIN       : {info['date_end']}")
    # Calcular anios aproximados de historia
    try:
        from datetime import datetime
        d0 = datetime.fromisoformat(info["date_start"][:19])
        d1 = datetime.fromisoformat(info["date_end"][:19])
        years = round((d1 - d0).days / 365, 1)
        print(f"  HISTORIA  : ~{years} anios")
    except Exception:
        pass
    print(f"  --- Primeros 5 registros ---")
    print(info["head"].to_string(index=False))
    print(f"  --- Ultimos 5 registros ---")
    print(info["tail"].to_string(index=False))
    print(sep)


# ============================================================================
# OPCION A: GITHUB
# ============================================================================

def download_github(files_to_skip: set) -> list:
    """Descarga todos los archivos del repo TheSnowGuru."""
    print("\n" + "=" * 70)
    print("  OPCION A: GitHub - TheSnowGuru/Stocks-Futures-Financial-Time-series")
    print("=" * 70)
    results = []

    for ticker, interval, url, filename in GITHUB_FILES:
        dest = DIR_GITHUB / filename
        if dest.name in files_to_skip:
            print(f"  [CACHE] {filename}")
        elif dest.exists() and dest.stat().st_size > 1000:
            print(f"  [CACHE] {filename} (ya existe, {dest.stat().st_size//1024} KB)")
        else:
            print(f"  -> Descargando {filename} ({ticker} {interval})...")
            ok = download_file(url, dest, filename)
            if not ok:
                continue
            time.sleep(0.5)

        if dest.exists() and dest.stat().st_size > 100:
            info = analyze_file(dest, ticker, interval)
            if info.get("ok"):
                results.append(info)
            else:
                print(f"  [AVISO] No se pudo analizar {filename}: {info.get('error')}")

    return results


# ============================================================================
# OPCION B: KAGGLE
# ============================================================================

def check_kaggle_credentials() -> bool:
    """Verifica que existan credenciales de Kaggle."""
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    env_user = os.environ.get("KAGGLE_USERNAME", "")
    env_key  = os.environ.get("KAGGLE_KEY", "")
    return kaggle_json.exists() or (bool(env_user) and bool(env_key))


def download_kaggle_dataset(slug: str, dest_dir: Path) -> list:
    """Descarga un dataset de Kaggle via CLI y devuelve los CSV encontrados."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n  -> Kaggle: {slug}")

    # Intentar primero kagglehub (mas moderno)
    try:
        import kagglehub
        path = kagglehub.dataset_download(slug)
        print(f"  [kagglehub] Descargado en: {path}")
        # Copiar/enlazar los archivos al destino
        src = Path(path)
        csvs = list(src.rglob("*.csv"))
        for csv in csvs:
            dst = dest_dir / csv.name
            import shutil
            shutil.copy2(csv, dst)
            print(f"  [COPIADO] {dst.name}")
        return [dest_dir / csv.name for csv in csvs]
    except Exception as e1:
        pass

    # Fallback: kaggle CLI
    try:
        cmd = [
            sys.executable, "-m", "kaggle",
            "datasets", "download", "-d", slug,
            "-p", str(dest_dir), "--unzip"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            print(f"  [kaggle CLI] OK: {result.stdout.strip()}")
        else:
            print(f"  [kaggle CLI] ERROR: {result.stderr.strip()[:300]}")
            return []
        return list(dest_dir.glob("*.csv"))
    except Exception as e2:
        print(f"  [ERROR kaggle] {slug}: {e2}")
        return []


def download_kaggle(skip: bool) -> list:
    """Descarga todos los datasets de Kaggle."""
    print("\n" + "=" * 70)
    print("  OPCION B: Kaggle datasets")
    print("=" * 70)

    if skip:
        print("  [SKIP] --skip-kaggle activado.")
        return []

    has_creds = check_kaggle_credentials()
    if not has_creds:
        print("  [AVISO] No se encontraron credenciales de Kaggle.")
        print("  Para habilitarlo:")
        print("    1. Crea cuenta en kaggle.com")
        print("    2. Ve a Settings -> API -> Create New Token")
        print("    3. Guarda kaggle.json en: %USERPROFILE%\\.kaggle\\kaggle.json")
        print("    O define env vars: KAGGLE_USERNAME y KAGGLE_KEY")
        return []

    results = []
    for slug, label in KAGGLE_DATASETS:
        dest_subdir = DIR_KAGGLE / label
        csvs = download_kaggle_dataset(slug, dest_subdir)
        for csv_path in csvs:
            # Inferir ticker e intervalo del nombre del slug/archivo
            slug_parts = slug.split("/")[-1]
            ticker_guess = slug_parts.split("-")[0].upper() if "-" in slug_parts else "UNK"
            info = analyze_file(csv_path, ticker_guess, "?min")
            if info.get("ok"):
                results.append(info)
    return results


# ============================================================================
# RESUMEN FINAL
# ============================================================================

def print_summary(all_results: list) -> None:
    """Tabla resumen de todos los archivos descargados."""
    sep = "=" * 95
    dash = "-" * 93
    print(f"\n{sep}")
    print("  RESUMEN FINAL -- DATOS DESCARGADOS (GitHub + Kaggle)")
    print(sep)
    fmt = "  {:<10} {:<7} {:<28} {:<10} {:<20} {:<20}"
    print(fmt.format("TICKER", "INTV", "ARCHIVO", "VELAS", "INICIO", "FIN"))
    print(f"  {dash}")
    for r in sorted(all_results, key=lambda x: (x.get("ticker",""), x.get("interval",""))):
        if not r.get("ok"):
            continue
        fname = r["file"]
        fname_s = (fname[:26] + "..") if len(fname) > 28 else fname
        print(fmt.format(
            r["ticker"], r["interval"], fname_s,
            f"{r['rows']:,}", r["date_start"], r["date_end"]
        ))
    print(sep)
    print(f"  Total archivos con datos: {sum(1 for r in all_results if r.get('ok'))}")

    # Guardar CSV de indice
    idx_path = BASE_DIR / "data" / "github_kaggle_summary.csv"
    rows = [{
        "ticker": r["ticker"], "interval": r["interval"], "file": r["file"],
        "rows": r["rows"], "date_start": r["date_start"], "date_end": r["date_end"],
        "source": "github" if "github" in str(DIR_GITHUB / r["file"]) else "kaggle"
    } for r in all_results if r.get("ok")]
    pd.DataFrame(rows).to_csv(idx_path, index=False)
    print(f"  Indice guardado en: {idx_path}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Descarga intraday historico GitHub + Kaggle")
    parser.add_argument("--skip-github", action="store_true", help="No descargar de GitHub")
    parser.add_argument("--skip-kaggle", action="store_true", help="No descargar de Kaggle")
    parser.add_argument("--only", nargs="+", help="Solo descargar estos tickers, ej: AAPL SP500")
    args = parser.parse_args()

    print("+" + "=" * 70 + "+")
    print("|  Descargador GitHub + Kaggle -- Series Intraday Historicas         |")
    print("+" + "=" * 70 + "+")
    print(f"  Destino GitHub : {DIR_GITHUB}")
    print(f"  Destino Kaggle : {DIR_KAGGLE}")

    # Filtrar por ticker si se especifica --only
    files = GITHUB_FILES
    if args.only:
        only_upper = {t.upper() for t in args.only}
        files = [(t, i, u, f) for t, i, u, f in GITHUB_FILES if t.upper() in only_upper]
        print(f"  Filtro tickers : {only_upper}")

    all_results = []

    # Opcion A: GitHub
    if not args.skip_github:
        gh_results = download_github(set())
        # Imprimir reporte detallado por archivo
        for info in gh_results:
            print_file_report(info)
        all_results.extend(gh_results)

    # Opcion B: Kaggle
    kg_results = download_kaggle(skip=args.skip_kaggle)
    for info in kg_results:
        print_file_report(info)
    all_results.extend(kg_results)

    print_summary(all_results)


if __name__ == "__main__":
    main()
