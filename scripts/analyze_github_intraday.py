# -*- coding: utf-8 -*-
"""
analyze_github_intraday.py  --  Re-analiza los CSVs descargados de GitHub
con deteccion correcta de separador TAB y columna de fecha.
"""
import io, sys, warnings, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

import pandas as pd
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "data" / "github_intraday"

FILE_MAP = {
    "AAPL_M5.csv":    ("AAPL",    "5min"),
    "AAPL_M1.csv":    ("AAPL",    "1min"),
    "AAPL_M15.csv":   ("AAPL",   "15min"),
    "AAPL_M30.csv":   ("AAPL",   "30min"),
    "AAPL_H1.csv":    ("AAPL",     "1h"),
    "NFLX_M5.csv":    ("NFLX",    "5min"),
    "NFLX_M1.csv":    ("NFLX",    "1min"),
    "SP500_M5.csv":   ("SP500",   "5min"),
    "SP500_M1.csv":   ("SP500",   "1min"),
    "SP500_M15.csv":  ("SP500",  "15min"),
    "SP500_H1.csv":   ("SP500",    "1h"),
    "NDX100_M5.csv":  ("NDX100",  "5min"),
    "NDX100_M1.csv":  ("NDX100",  "1min"),
    "NDX100_M15.csv": ("NDX100", "15min"),
    "NDX100_H1.csv":  ("NDX100",   "1h"),
}

OHLCV_COLS = {"open", "high", "low", "close", "volume"}

def load_tsv_csv(fp: Path) -> pd.DataFrame:
    """Lee el archivo detectando separador automaticamente."""
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        header = f.readline()
    sep = "\t" if "\t" in header else ","
    return pd.read_csv(fp, sep=sep, low_memory=False)


def find_dt_col(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if col.strip().lower() in ("time", "datetime", "date", "timestamp", "localtime"):
            return col
    return df.columns[0] if len(df.columns) > 0 else None


def years_between(d0: str, d1: str) -> float:
    try:
        a = datetime.datetime.fromisoformat(d0[:19])
        b = datetime.datetime.fromisoformat(d1[:19])
        return round((b - a).days / 365.25, 1)
    except Exception:
        return 0.0


def analyze(fp: Path, ticker: str, interval: str) -> dict:
    df = load_tsv_csv(fp)
    dt_col = find_dt_col(df)

    df[dt_col] = pd.to_datetime(df[dt_col], errors="coerce")
    df = df.dropna(subset=[dt_col]).sort_values(dt_col).reset_index(drop=True)

    rows = len(df)
    d0 = str(df[dt_col].min())[:19]
    d1 = str(df[dt_col].max())[:19]
    yrs = years_between(d0, d1)

    # Verificar columnas OHLCV presentes
    present = {c.lower().strip() for c in df.columns}
    ohlcv_ok = OHLCV_COLS.issubset(present)

    return {
        "ticker": ticker, "interval": interval, "file": fp.name,
        "rows": rows, "date_start": d0, "date_end": d1,
        "years": yrs, "ohlcv_ok": ohlcv_ok,
        "columns": list(df.columns),
        "head": df.head(5), "tail": df.tail(5),
    }


def main():
    LINE = "=" * 100
    DASH = "-" * 98

    print(LINE)
    print("  REPORTE COMPLETO -- DATOS INTRADAY HISTORICOS (GitHub / TheSnowGuru)")
    print(LINE)

    results = []
    for fname, (ticker, interval) in sorted(FILE_MAP.items(),
                                            key=lambda x: (x[1][0], x[1][1])):
        fp = DIR / fname
        if not fp.exists():
            print(f"\n  [NO DESCARGADO] {fname}")
            continue

        print(f"\n{'='*70}")
        print(f"  {ticker}  ({interval})  --  {fname}")
        print(f"{'='*70}")

        try:
            info = analyze(fp, ticker, interval)
        except Exception as exc:
            print(f"  [ERROR] {exc}")
            continue

        ohlcv_str = "SI" if info["ohlcv_ok"] else "PARCIAL"
        print(f"  Columnas     : {', '.join(info['columns'])}")
        print(f"  OHLCV valido : {ohlcv_str}")
        print(f"  Total velas  : {info['rows']:,}")
        print(f"  Fecha inicio : {info['date_start']}")
        print(f"  Fecha fin    : {info['date_end']}")
        print(f"  Historia     : ~{info['years']} anos")

        print(f"\n  --- Primeros 5 registros ---")
        head_str = info["head"].to_string(index=False)
        for line in head_str.splitlines():
            print(f"  {line}")

        print(f"\n  --- Ultimos 5 registros ---")
        tail_str = info["tail"].to_string(index=False)
        for line in tail_str.splitlines():
            print(f"  {line}")

        results.append(info)

    # Tabla resumen
    print(f"\n\n{LINE}")
    print("  RESUMEN CONSOLIDADO")
    print(LINE)
    fmt = "  {:<10} {:<7} {:<22} {:<10} {:<20} {:<20} {:<6} {:<8}"
    print(fmt.format("TICKER","INTV","ARCHIVO","VELAS","INICIO","FIN","ANOS","OHLCV"))
    print(f"  {DASH}")
    for r in sorted(results, key=lambda x: (x["ticker"], x["interval"])):
        print(fmt.format(
            r["ticker"], r["interval"], r["file"],
            f"{r['rows']:,}", r["date_start"], r["date_end"],
            str(r["years"]), "OK" if r["ohlcv_ok"] else "PARC"
        ))
    print(LINE)
    total_velas = sum(r["rows"] for r in results)
    print(f"  Total archivos   : {len(results)}")
    print(f"  Total velas      : {total_velas:,}")
    print(f"  Espacio en disco : ~{sum((DIR/r['file']).stat().st_size for r in results) // (1024*1024)} MB")

    # Guardar indice
    out = DIR.parent / "github_intraday_report.csv"
    pd.DataFrame([{
        k: v for k, v in r.items()
        if k not in ("head", "tail", "columns")
    } for r in results]).to_csv(out, index=False)
    print(f"\n  Reporte guardado : {out}")


if __name__ == "__main__":
    main()
