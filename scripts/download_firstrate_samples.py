# -*- coding: utf-8 -*-
"""
FirstRate Data -- Descargador de muestras gratuitas intraday
=============================================================
Usa Playwright para navegar las paginas de cada ticker, extraer los
enlaces "Download Sample", descargar los ZIP, descomprimirlos y generar
un resumen OHLCV con rango de fechas y numero de filas.

Directorios de salida:
  data/firstrate_raw/       -> ZIPs originales descargados
  data/intraday_5min/       -> CSV de 5 minutos extraidos
  data/intraday_1min/       -> CSV de 1 minuto extraidos
  data/intraday_other/      -> Otras resoluciones

Uso:
  python scripts/download_firstrate_samples.py
"""

import asyncio
import io
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Forzar salida UTF-8 en Windows (evita cp1252 UnicodeEncodeError)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
import requests

# -- Playwright (opcional: fallback a requests si no esta instalado) ----------
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[AVISO] playwright no esta disponible -- se usara requests puro.")

# -- Configuracion ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # raiz del repo

RAW_DIR   = BASE_DIR / "data" / "firstrate_raw"
DIR_5MIN  = BASE_DIR / "data" / "intraday_5min"
DIR_1MIN  = BASE_DIR / "data" / "intraday_1min"
DIR_OTHER = BASE_DIR / "data" / "intraday_other"

for d in [RAW_DIR, DIR_5MIN, DIR_1MIN, DIR_OTHER]:
    d.mkdir(parents=True, exist_ok=True)

# Tickers prioritarios (stocks + ETFs relevantes para S5 Dual Momentum)
TICKERS_STOCK = [
    ("stock", "AAPL"),
    ("stock", "MSFT"),
    ("stock", "AMZN"),
    ("stock", "GOOG"),
    ("stock", "META"),
    ("stock", "NFLX"),
    ("stock", "TSLA"),
]

TICKERS_ETF = [
    ("etf", "SPY"),
    ("etf", "DIA"),
    ("etf", "IWM"),
    ("etf", "VXX"),
    ("etf", "GLD"),    # Oro con custodia fisica
    ("etf", "SLV"),    # Plata con custodia fisica
    ("etf", "SGOV"),   # T-Bills / Cash yield
]

ALL_TICKERS = TICKERS_STOCK + TICKERS_ETF

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# -- Utilidades ---------------------------------------------------------------

def classify_resolution(filename: str) -> str:
    """Clasifica el archivo segun su resolucion temporal."""
    fn = filename.lower()
    if "1min" in fn or "1-min" in fn or "_1m" in fn or "1minute" in fn:
        return "1min"
    if "5min" in fn or "5-min" in fn or "_5m" in fn or "5minute" in fn:
        return "5min"
    return "other"


def get_target_dir(resolution: str) -> Path:
    return {
        "1min":  DIR_1MIN,
        "5min":  DIR_5MIN,
        "other": DIR_OTHER,
    }[resolution]


def analyze_csv(filepath: Path) -> dict:
    """Lee un CSV y devuelve estadisticas basicas OHLCV."""
    try:
        df_peek = pd.read_csv(filepath, header=None, nrows=5)
        # Detectar si tiene cabecera
        has_header = any(
            str(df_peek.iloc[0, c]).strip().lower() in
            {"datetime", "date", "time", "open", "high", "low", "close", "volume"}
            for c in range(min(df_peek.shape[1], 6))
        )
        df = pd.read_csv(filepath, header=0 if has_header else None)

        # Normalizar nombres de columnas
        col_map = {}
        for col in df.columns:
            cl = str(col).strip().lower()
            if cl in ("datetime", "date", "time", "timestamp"):
                col_map[col] = "DateTime"
            elif cl in ("open", "o"):
                col_map[col] = "Open"
            elif cl in ("high", "h"):
                col_map[col] = "High"
            elif cl in ("low", "l"):
                col_map[col] = "Low"
            elif cl in ("close", "c"):
                col_map[col] = "Close"
            elif cl in ("volume", "vol", "v"):
                col_map[col] = "Volume"
        df.rename(columns=col_map, inplace=True)

        if "DateTime" in df.columns:
            df["DateTime"] = pd.to_datetime(df["DateTime"], errors="coerce")
            df.dropna(subset=["DateTime"], inplace=True)
            date_start = df["DateTime"].min()
            date_end   = df["DateTime"].max()
        else:
            date_start = date_end = "N/A"

        return {
            "rows":       len(df),
            "date_start": str(date_start)[:19] if date_start != "N/A" else "N/A",
            "date_end":   str(date_end)[:19]   if date_end   != "N/A" else "N/A",
            "columns":    list(df.columns),
            "ok":         True,
        }
    except Exception as exc:
        return {
            "rows": 0, "date_start": "ERR", "date_end": "ERR",
            "columns": [], "ok": False, "error": str(exc)
        }


def extract_zip(zip_path: Path, ticker: str) -> list:
    """Extrae un ZIP y clasifica cada CSV en el directorio correcto."""
    extracted = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if not member.lower().endswith(".csv"):
                    continue
                fname = Path(member).name
                resolution = classify_resolution(fname) or classify_resolution(zip_path.name)
                target_dir = get_target_dir(resolution)
                out_name = f"{ticker}_{fname}" if not fname.lower().startswith(ticker.lower()) else fname
                out_path = target_dir / out_name
                with zf.open(member) as src, open(out_path, "wb") as dst:
                    dst.write(src.read())
                extracted.append(out_path)
    except zipfile.BadZipFile:
        print(f"  [ERROR] {zip_path.name} no es un ZIP valido (probablemente HTML de redireccion).")
    except Exception as exc:
        print(f"  [ERROR] Al extraer {zip_path.name}: {exc}")
    return extracted


# -- Extraccion de URLs con Playwright ----------------------------------------

async def get_sample_urls_playwright(asset_type: str, ticker: str) -> list:
    """Navega la pagina del ticker y extrae todos los href de 'Download Sample'."""
    url = f"https://firstratedata.com/i/{asset_type}/{ticker}"
    urls = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"User-Agent": HEADERS["User-Agent"]})

        try:
            print(f"  -> Navegando: {url}")
            await page.goto(url, wait_until="networkidle", timeout=30_000)

            # Esperar a que cargue contenido dinamico
            await page.wait_for_timeout(2000)

            # Extraer todos los <a href> con "sample" o ".zip"
            links = await page.evaluate("""() => {
                const anchors = Array.from(document.querySelectorAll('a'));
                return anchors
                    .filter(a => /sample|download/i.test(a.textContent) || /\\.zip/i.test(a.href) || /sample/i.test(a.href))
                    .map(a => a.href)
                    .filter(h => h && h.startsWith('http'));
            }""")
            for href in links:
                if href:
                    urls.append(href)

            # Buscar tambien en onclick y atributos data-*
            btn_scripts = await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('[onclick],[data-url],[data-href]'));
                return els.flatMap(e => [
                    e.getAttribute('onclick') || '',
                    e.getAttribute('data-url') || '',
                    e.getAttribute('data-href') || ''
                ]);
            }""")
            for script_text in btn_scripts:
                if script_text:
                    found = re.findall(r'https?://[^\s\'"<>]+\.zip', script_text)
                    urls.extend(found)

            # Capturar el HTML para depuracion si no hay URLs
            if not urls:
                html_snippet = await page.evaluate(
                    "() => document.body.innerText.substring(0, 500)"
                )
                print(f"  [DEBUG] Sin URLs. Texto visible: {html_snippet[:200]!r}")

        except Exception as exc:
            print(f"  [AVISO] Playwright error en {ticker}: {exc}")
        finally:
            await browser.close()

    unique = list(dict.fromkeys(urls))  # deduplicar manteniendo orden
    return unique


# -- Fallback: Heuristica de URL directa --------------------------------------

CANDIDATE_URL_PATTERNS = [
    # Google Storage buckets conocidos de FirstRate
    "https://storage.googleapis.com/frdbucket1/{ticker}_1min_sample.zip",
    "https://storage.googleapis.com/frdbucket1/{ticker}_5min_sample.zip",
    "https://storage.googleapis.com/frdbucket1/samples/{ticker}_1min.zip",
    "https://storage.googleapis.com/frdbucket1/samples/{ticker}_5min.zip",
    "https://storage.googleapis.com/frdbucket1/free/{ticker}_5min.zip",
    "https://storage.googleapis.com/frdbucket1/free/{ticker}_1min.zip",
    "https://storage.googleapis.com/frdbucket1/{ticker_upper}_1min_sample.zip",
    "https://storage.googleapis.com/frdbucket1/{ticker_upper}_5min_sample.zip",
    "https://storage.googleapis.com/frdbucket1/samples/{ticker_upper}_1min.zip",
    "https://storage.googleapis.com/frdbucket1/samples/{ticker_upper}_5min.zip",
    # Patrones alternativos
    "https://firstratedata.com/static/samples/{ticker_upper}_5min.zip",
    "https://firstratedata.com/static/samples/{ticker_upper}_1min.zip",
]


def probe_candidate_urls(ticker: str) -> list:
    """Prueba URLs candidatas para encontrar descargas directas."""
    found = []
    session = requests.Session()
    session.headers.update(HEADERS)
    for pattern in CANDIDATE_URL_PATTERNS:
        url = pattern.format(ticker=ticker.lower(), ticker_upper=ticker.upper())
        try:
            r = session.head(url, timeout=8, allow_redirects=True)
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                if "zip" in ct or "octet-stream" in ct or not ct:
                    found.append(url)
                    print(f"  [OK] URL directa: {url}")
        except Exception:
            pass
    return found


# -- Descarga -----------------------------------------------------------------

def download_file(url: str, dest_path: Path) -> bool:
    """Descarga un archivo y lo guarda en dest_path."""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        with session.get(url, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if "text/html" in content_type:
                print(f"  [AVISO] URL retorno HTML (requiere login?): {url}")
                return False
            downloaded = 0
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
            size_kb = downloaded / 1024
            print(f"  [OK] Descargado: {dest_path.name} ({size_kb:.1f} KB)")
            return True
    except requests.HTTPError as e:
        print(f"  [ERROR HTTP {e.response.status_code}] {url}")
        return False
    except Exception as e:
        print(f"  [ERROR] {url} -> {e}")
        return False


# -- Flujo principal ----------------------------------------------------------

async def process_ticker(asset_type: str, ticker: str) -> list:
    """Procesa un ticker: extrae URLs, descarga, descomprime y analiza."""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  Procesando: {ticker.upper()} ({asset_type})")
    print(sep)

    sample_urls = []

    # 1. Intentar con Playwright para obtener URLs reales de la pagina
    if PLAYWRIGHT_AVAILABLE:
        sample_urls = await get_sample_urls_playwright(asset_type, ticker)
        if sample_urls:
            print(f"  [OK] {len(sample_urls)} enlace(s) encontrado(s) via Playwright")
            for u in sample_urls:
                print(f"       {u}")

    # 2. Fallback: probar URLs candidatas directas
    if not sample_urls:
        print("  -> Sin resultados de Playwright. Probando URLs directas...")
        sample_urls = probe_candidate_urls(ticker)

    if not sample_urls:
        print(f"  [SKIP] No se encontraron enlaces de descarga para {ticker}")
        return []

    results = []
    for url in sample_urls:
        filename = Path(urlparse(url).path).name
        if not filename or "." not in filename:
            filename = f"{ticker}_sample.zip"
        zip_path = RAW_DIR / filename

        if zip_path.exists() and zip_path.stat().st_size > 100:
            print(f"  [CACHE] {filename} ya existe, usando version local.")
        else:
            ok = download_file(url, zip_path)
            if not ok:
                continue
            # Verificar que el archivo es realmente un ZIP
            if not zipfile.is_zipfile(zip_path):
                print(f"  [ERROR] {filename} no es un ZIP valido. Eliminando.")
                zip_path.unlink(missing_ok=True)
                continue

        # Extraer ZIP
        print(f"  -> Extrayendo {filename}...")
        csv_files = extract_zip(zip_path, ticker)
        if not csv_files:
            print(f"  [AVISO] No se encontraron CSV en {filename}")
            continue

        for csv_path in csv_files:
            resolution = classify_resolution(csv_path.name)
            print(f"  -> Analizando: {csv_path.name} [{resolution}]")
            stats = analyze_csv(csv_path)
            result = {
                "ticker":      ticker.upper(),
                "asset_type":  asset_type,
                "file":        csv_path.name,
                "resolution":  resolution,
                "rows":        stats["rows"],
                "date_start":  stats["date_start"],
                "date_end":    stats["date_end"],
                "columns":     ", ".join(str(c) for c in stats["columns"]),
                "source_url":  url,
            }
            results.append(result)
            if stats["ok"]:
                print(f"     Filas: {stats['rows']:,} | "
                      f"Desde: {stats['date_start']} | "
                      f"Hasta: {stats['date_end']}")
            else:
                print(f"     [AVISO] Error al analizar: {stats.get('error', '?')}")

    return results


def print_summary(all_results: list) -> None:
    """Imprime tabla resumen en consola."""
    sep = "=" * 90
    dash = "-" * 88

    if not all_results:
        print("\n[!] No se descargo ningun archivo exitosamente.")
        print("    Posibles causas:")
        print("    1. FirstRate Data requiere login para descargar muestras.")
        print("    2. Las URLs del bucket GCS no son publicas.")
        print("    3. El sitio usa un flujo de descarga con token temporal.")
        print("\n    Recomendacion: ejecutar el script con sesion autenticada")
        print("    o descargar manualmente desde firstratedata.com/i/{etf|stock}/TICKER")
        return

    print(f"\n{sep}")
    print("  RESUMEN DE DATOS DESCARGADOS")
    print(sep)
    fmt = "  {:<10} {:<7} {:<28} {:<9} {:<20} {:<20}"
    print(fmt.format("TICKER", "TIPO", "ARCHIVO", "FILAS", "FECHA INICIO", "FECHA FIN"))
    print(f"  {dash}")
    for r in all_results:
        fname = r["file"]
        fname_short = (fname[:26] + "..") if len(fname) > 28 else fname
        print(fmt.format(
            r["ticker"],
            r["resolution"],
            fname_short,
            f"{r['rows']:,}",
            r["date_start"],
            r["date_end"],
        ))
    print(sep)
    print(f"  Total archivos procesados: {len(all_results)}")
    tickers_ok = {r["ticker"] for r in all_results}
    print(f"  Tickers con datos: {', '.join(sorted(tickers_ok))}")

    # Guardar resumen CSV
    summary_path = BASE_DIR / "data" / "firstrate_download_summary.csv"
    pd.DataFrame(all_results).to_csv(summary_path, index=False)
    print(f"\n  Resumen guardado en: {summary_path}")


async def main():
    print("+" + "=" * 63 + "+")
    print("|   FirstRate Data -- Descargador de muestras intraday         |")
    print("+" + "=" * 63 + "+")
    print(f"  Playwright disponible : {PLAYWRIGHT_AVAILABLE}")
    print(f"  Directorio RAW        : {RAW_DIR}")
    print(f"  Directorio 5min       : {DIR_5MIN}")
    print(f"  Directorio 1min       : {DIR_1MIN}")
    print(f"  Tickers a procesar    : {len(ALL_TICKERS)}")

    all_results = []
    for asset_type, ticker in ALL_TICKERS:
        try:
            results = await process_ticker(asset_type, ticker)
            all_results.extend(results)
        except Exception as exc:
            print(f"  [ERROR CRITICO] {ticker}: {exc}")
            continue

    print_summary(all_results)


if __name__ == "__main__":
    asyncio.run(main())
