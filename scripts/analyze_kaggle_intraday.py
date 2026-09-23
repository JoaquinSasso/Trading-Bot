# -*- coding: utf-8 -*-
"""
analyze_kaggle_intraday.py
Analiza los CSVs descargados de Kaggle y genera reporte head/tail + estadisticas.
"""
import io, sys, warnings, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import pandas as pd
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "data" / "kaggle_intraday"

def detect_sep(fp):
    with open(fp, encoding="utf-8", errors="replace") as f:
        line = f.readline()
    return "\t" if "\t" in line else ","

def find_dt_col(df):
    for col in df.columns:
        lc = col.strip().lower()
        if lc in ("date", "datetime", "time", "timestamp", "date time"):
            return col
    return df.columns[0]

def years_between(d0, d1):
    try:
        a = datetime.datetime.fromisoformat(str(d0)[:19])
        b = datetime.datetime.fromisoformat(str(d1)[:19])
        return round((b - a).days / 365.25, 1)
    except Exception:
        return 0.0

def analyze_csv(fp: Path, ticker: str, interval: str) -> dict:
    sep = detect_sep(fp)
    # Para archivos grandes, leer en chunks para obtener primero/ultimo
    df_head = pd.read_csv(fp, sep=sep, nrows=5, low_memory=False)
    # Contar filas totales eficientemente
    with open(fp, "rb") as f:
        total_rows = sum(1 for _ in f) - 1  # -1 para header

    # Leer tail (ultimas 5 filas)
    df_full = pd.read_csv(fp, sep=sep, low_memory=False,
                          skiprows=range(1, max(1, total_rows - 5 + 1)))

    # Leer columna de fecha de todo el dataset para inicio/fin
    dt_col = find_dt_col(df_head)

    # Leer solo columna fecha para rango
    try:
        df_dates = pd.read_csv(fp, sep=sep, usecols=[dt_col], low_memory=False)
        df_dates[dt_col] = pd.to_datetime(df_dates[dt_col], errors="coerce")
        df_dates = df_dates.dropna()
        d0 = str(df_dates[dt_col].min())[:19]
        d1 = str(df_dates[dt_col].max())[:19]
    except Exception:
        d0 = d1 = "N/A"

    yrs = years_between(d0, d1)

    # Normalizar head/tail para display
    df_head[dt_col] = pd.to_datetime(df_head[dt_col], errors="coerce")
    df_full[dt_col] = pd.to_datetime(df_full[dt_col], errors="coerce")

    return {
        "ticker": ticker, "interval": interval, "file": fp.name,
        "rows": total_rows, "date_start": d0, "date_end": d1,
        "years": yrs, "columns": list(df_head.columns),
        "head": df_head, "tail": df_full,
    }

def report(info):
    LINE = "=" * 80
    print(f"\n{LINE}")
    print(f"  {info['ticker']}  ({info['interval']})  --  {info['file']}")
    print(LINE)
    print(f"  Columnas   : {', '.join(str(c) for c in info['columns'])}")
    print(f"  Total velas: {info['rows']:,}")
    print(f"  Inicio     : {info['date_start']}")
    print(f"  Fin        : {info['date_end']}")
    print(f"  Historia   : ~{info['years']} anos")
    print(f"\n  --- Primeros 5 registros ---")
    for line in info["head"].to_string(index=False).splitlines():
        print(f"  {line}")
    print(f"\n  --- Ultimos 5 registros ---")
    for line in info["tail"].to_string(index=False).splitlines():
        print(f"  {line}")

def main():
    datasets = [
        # (glob_pattern, ticker, interval, subdir)
        ("sp500_1min_2008_2021/*.csv",  "SPY",  "1min", None),
        ("aapl_1min_2006_2024/*.csv",   "AAPL", "1min", None),
        ("aapl_1min_2006_2024/**/*.csv","AAPL", "1min", None),
    ]

    results = []
    seen = set()

    print("=" * 80)
    print("  REPORTE KAGGLE -- DATOS INTRADAY HISTORICOS")
    print("=" * 80)

    for pattern, ticker, interval, _ in datasets:
        for fp in sorted(BASE.glob(pattern)):
            if fp in seen or not fp.is_file():
                continue
            seen.add(fp)
            print(f"\n  Analizando: {fp.name} ({fp.stat().st_size // (1024*1024)} MB)...")
            try:
                info = analyze_csv(fp, ticker, interval)
                report(info)
                results.append(info)
            except Exception as e:
                print(f"  [ERROR] {e}")

    # Resumen
    if results:
        print("\n\n" + "=" * 90)
        print("  RESUMEN KAGGLE")
        print("=" * 90)
        fmt = "  {:<8} {:<7} {:<45} {:<12} {:<12} {:<6}"
        print(fmt.format("TICKER","INTV","ARCHIVO","VELAS","HISTORIA","FIN"))
        print("  " + "-" * 88)
        for r in results:
            fname = r["file"][:43] + ".." if len(r["file"]) > 45 else r["file"]
            print(fmt.format(r["ticker"], r["interval"], fname,
                             f"{r['rows']:,}", f"{r['years']} anos", r["date_end"][:10]))
        total = sum(r["rows"] for r in results)
        print(f"\n  Total archivos : {len(results)}")
        print(f"  Total velas    : {total:,}")

        out = BASE.parent / "kaggle_intraday_report.csv"
        pd.DataFrame([{k: v for k, v in r.items()
                       if k not in ("head","tail","columns")} for r in results]
                    ).to_csv(out, index=False)
        print(f"  Reporte CSV    : {out}")

if __name__ == "__main__":
    main()
