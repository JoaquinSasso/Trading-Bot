"""Cargador y gestor de datos históricos para el motor de replay y backtesting.

Soporta:
1. Carga desde archivos CSV locales (con normalización de columnas y zonas horarias).
2. Descarga y cache de barras SIP vía AlpacaProvider.
3. Generador de series históricas realistas para pruebas deterministas offline.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytz


class HistoricalDataLoader:
    """Carga y normaliza barras históricas diarias e intradía para backtesting."""

    DEFAULT_DATA_DIR = Path("data/historical")

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else self.DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load_from_csv(self, file_path: Path | str, symbol: str) -> pd.DataFrame:
        """Carga barras desde un archivo CSV y estandariza los nombres de columnas."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Archivo de datos históricos no encontrado: {path}")

        df = pd.read_csv(path)
        # Normalizar nombres de columnas a minúsculas
        df.columns = [c.strip().lower() for c in df.columns]

        # Mapear nombres comunes
        rename_map = {
            "datetime": "timestamp",
            "time": "timestamp",
            "vol": "volume",
            "adj close": "close",
            "adj_close": "close",
        }
        df = df.rename(columns=rename_map)

        if "symbol" not in df.columns:
            df["symbol"] = symbol

        time_col = (
            "timestamp" if "timestamp" in df.columns else ("date" if "date" in df.columns else None)
        )
        if time_col is None:
            raise ValueError(
                f"El archivo {path} debe contener una columna de fecha/hora ('timestamp' o 'date')"
            )

        # Convertir a America/New_York naive para sincronización con horarios de rueda (9:30-16:00 ET)
        ts_parsed = pd.to_datetime(df[time_col])
        if ts_parsed.dt.tz is not None:
            df[time_col] = ts_parsed.dt.tz_convert("America/New_York").dt.tz_localize(None)
        else:
            df[time_col] = ts_parsed
        required_cols = [time_col, "open", "high", "low", "close", "volume", "symbol"]
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                raise ValueError(f"Columna requerida '{col}' faltante en {path}")

        df = df.sort_values(by=time_col).reset_index(drop=True)
        return df[required_cols]

    def fetch_and_save_real_data(
        self,
        symbol: str,
        interval: str = "1d",
        range_: str = "2y",
    ) -> pd.DataFrame:
        """Descarga barras históricas reales del mercado (vía Yahoo Finance API pública) y las guarda en CSV."""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_}"

        try:
            resp = httpx.get(url, headers=headers, timeout=20.0)
            resp.raise_for_status()
            data = resp.json()
            result = data["chart"]["result"][0]
            raw_ts = result["timestamp"]
            quote = result["indicators"]["quote"][0]

            if interval == "1d":
                dates = [
                    datetime.fromtimestamp(ts, tz=pytz.timezone("America/New_York")).date()
                    for ts in raw_ts
                ]
                time_key = "date"
                out_filename = f"{symbol}_daily.csv"
            else:
                dates = [
                    datetime.fromtimestamp(ts, tz=pytz.timezone("America/New_York"))
                    for ts in raw_ts
                ]
                time_key = "timestamp"
                out_filename = f"{symbol}_{interval}.csv"

            df = pd.DataFrame(
                {
                    time_key: dates,
                    "open": [round(x, 4) if x is not None else None for x in quote["open"]],
                    "high": [round(x, 4) if x is not None else None for x in quote["high"]],
                    "low": [round(x, 4) if x is not None else None for x in quote["low"]],
                    "close": [round(x, 4) if x is not None else None for x in quote["close"]],
                    "volume": quote["volume"],
                    "symbol": symbol,
                }
            ).dropna()

            target_path = self.data_dir / out_filename
            df.to_csv(target_path, index=False)
            return df
        except Exception as e:
            raise RuntimeError(
                f"Error al descargar datos históricos reales para {symbol}: {e}"
            ) from e

    def save_to_parquet(self, df: pd.DataFrame, file_name: str) -> Path:
        """Guarda un DataFrame de barras en archivo Parquet en el directorio de cache."""
        target = self.data_dir / file_name
        try:
            df.to_parquet(target, index=False)
        except Exception:
            # Fallback a CSV si pyarrow no está instalado
            csv_target = target.with_suffix(".csv")
            df.to_csv(csv_target, index=False)
            return csv_target
        return target

    def generate_synthetic_history(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        initial_price: float = 500.0,
        daily_drift: float = 0.0004,
        daily_vol: float = 0.012,
        seed: int = 42,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Genera series históricas sintéticas (diarias y de 5m) estadísticamente realistas.

        Útil para pruebas unitarias, verificación de determinismo y escenarios controlados.
        """
        rng = np.random.default_rng(seed)

        # Generar días hábiles
        current = start_date
        trading_days: list[date] = []
        while current <= end_date:
            if current.weekday() < 5:  # Lunes a Viernes
                trading_days.append(current)
            current += timedelta(days=1)

        daily_records: list[dict[str, Any]] = []
        intraday_records: list[dict[str, Any]] = []

        cur_price = initial_price

        for t_day in trading_days:
            day_open = cur_price * (1.0 + rng.normal(0, daily_vol * 0.3))
            daily_ret = rng.normal(daily_drift, daily_vol)
            day_close = day_open * (1.0 + daily_ret)
            intra_range = abs(day_close - day_open) + cur_price * daily_vol
            day_high = max(day_open, day_close) + rng.uniform(0, intra_range * 0.5)
            day_low = min(day_open, day_close) - rng.uniform(0, intra_range * 0.5)
            volume = int(rng.uniform(40_000_000, 80_000_000))

            daily_records.append(
                {
                    "symbol": symbol,
                    "date": t_day,
                    "open": round(day_open, 2),
                    "high": round(day_high, 2),
                    "low": round(day_low, 2),
                    "close": round(day_close, 2),
                    "volume": volume,
                    "adjusted": True,
                    "feed": "sip_delayed",
                }
            )

            # Generar barras de 5m de 9:30 a 16:00 ET (78 barras)
            session_start = datetime.combine(t_day, time(9, 30))
            intra_p = day_open
            for bar_idx in range(78):
                bar_time = session_start + timedelta(minutes=bar_idx * 5)
                # Trayectoria hacia el cierre del día
                weight_to_close = (bar_idx + 1) / 78.0
                target_p = day_open + (day_close - day_open) * weight_to_close
                step_noise = rng.normal(0, daily_vol * 0.15 * cur_price)
                b_open = intra_p
                b_close = b_open * 0.6 + (target_p + step_noise) * 0.4
                b_high = max(b_open, b_close) + abs(rng.normal(0, daily_vol * 0.05 * cur_price))
                b_low = min(b_open, b_close) - abs(rng.normal(0, daily_vol * 0.05 * cur_price))
                b_vol = int(volume / 78 * rng.uniform(0.7, 1.4))

                intraday_records.append(
                    {
                        "symbol": symbol,
                        "timestamp": bar_time,
                        "open": round(b_open, 2),
                        "high": round(b_high, 2),
                        "low": round(b_low, 2),
                        "close": round(b_close, 2),
                        "volume": b_vol,
                    }
                )
                intra_p = b_close

            cur_price = day_close

        df_daily = pd.DataFrame(daily_records)
        df_intraday = pd.DataFrame(intraday_records)
        return df_daily, df_intraday
