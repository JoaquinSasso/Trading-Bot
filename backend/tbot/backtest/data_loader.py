"""Cargador y gestor de datos históricos para el motor de replay y backtesting.

Soporta:
1. Carga desde archivos CSV locales (con normalización de columnas, zonas horarias y guardas de holdout).
2. Descarga y cache de barras SIP vía AlpacaProvider.
3. Generador de series históricas realistas para pruebas deterministas offline.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytz

from tbot.backtest.guards import (
    DEV_DAILY_END,
    DEV_HOURLY_END,
    HOLDOUT_DAILY_END,
    HOLDOUT_DAILY_START,
    HOLDOUT_HOURLY_END,
    HOLDOUT_HOURLY_START,
    SAMPLE_5M_END,
    SAMPLE_5M_START,
    HoldoutViolationError,
    _to_date,
    assert_not_holdout,
    is_holdout_unsealed,
    is_synthetic_bypassed,
    normalize_resolution,
)

logger = logging.getLogger(__name__)


def _is_in_sealed_historical_dir(path: Path) -> bool:
    """Verifica si la ruta reside dentro del directorio de holdout sellado 'data/historical'.

    Distingue rigurosamente 'data/historical' de los directorios legítimos de desarrollo y spanning:
    - 'data/historical_14'
    - 'data/historical_2010_2026'
    - 'data/historical_2020_2022'
    """
    # 1. Comprobación directa del nombre del directorio padre
    if path.parent.name.lower() == "historical":
        return True

    # 2. Comprobación en la jerarquía de partes de la ruta (secuencia exacta 'data' -> 'historical')
    parts = [p.lower() for p in path.parts]
    for i in range(len(parts) - 1):
        if parts[i] == "data" and parts[i + 1] == "historical":
            return True

    # 3. Comprobación con ruta resuelta absoluta
    try:
        resolved_parts = [p.lower() for p in path.resolve().parts]
        for i in range(len(resolved_parts) - 1):
            if resolved_parts[i] == "data" and resolved_parts[i + 1] == "historical":
                return True
    except Exception:
        pass

    return False


class HistoricalDataLoader:
    """Carga y normaliza barras históricas diarias e intradía para backtesting con guardas de holdout."""

    DEFAULT_DATA_DIR = Path("data/historical_2020_2022")

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else self.DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def detect_file_resolution(file_path: Path | str) -> str | None:
        """Detecta la resolución temporal inherente a partir del nombre de archivo o directorio.

        Retorna 'daily', 'hourly', '5m', o None si es indeterminado.
        """
        path = Path(file_path)
        stem = path.stem.lower()

        # 1. Detección por sufijo o patrón en el nombre del archivo
        if stem.endswith(("_daily", "_1d", "_day")) or "_daily" in stem:
            return "daily"
        if stem.endswith(("_1h", "_1hour", "_hourly")) or "_1h" in stem or "_1hour" in stem or "_hourly" in stem:
            return "hourly"
        if stem.endswith(("_5m", "_5min")) or "_5m" in stem or "_5min" in stem:
            return "5m"

        # 2. Heurística contextual por directorio padre si el nombre no contiene indicador
        parent = path.parent.name.lower()
        if parent in ("intraday_1h", "1h", "hourly"):
            return "hourly"
        if parent in ("intraday_5m", "5m"):
            return "5m"
        if parent in ("historical_14", "historical_2010_2026", "historical_2020_2022", "universe_a", "daily_2026"):
            return "daily"

        return None

    def load_from_csv(
        self,
        file_path: Path | str,
        symbol: str,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        resolution: str | None = None,
        allow_synthetic: bool = False,
    ) -> pd.DataFrame:
        """Carga barras desde un archivo CSV con guardas de holdout y compatibilidad regresiva."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Archivo de datos históricos no encontrado: {path}")

        # --- GUARDA DURA DE DIRECTORIO: CUARENTENA TOTAL DE data/historical ---
        # data/historical es una partición 100% sellada (contiene tanto archivos diarios
        # 2024-2026 como barras 5m no autorizadas). Debe fallar incondicionalmente salvo en Fase 6.
        if not (allow_synthetic or is_synthetic_bypassed() or is_holdout_unsealed()) and _is_in_sealed_historical_dir(path):
            logger.error(
                "HOLDOUT_VIOLATION_TRIGGERED: Acceso a directorio sellado data/historical denegado para %s",
                path,
            )
            raise HoldoutViolationError(
                f"El archivo '{path}' reside en el directorio 'data/historical', el cual es una "
                f"partición de holdout 100% sellada bajo la Regla 0 (R0) y data/HOLDOUT_LOCK.md. "
                f"El acceso a este directorio está estrictamente prohibido hasta el desellado formal de Fase 6."
            )

        # Determinar resolución inherente del archivo
        file_res = self.detect_file_resolution(path)

        if resolution is not None:
            req_res = normalize_resolution(resolution)
            if file_res is not None and req_res != file_res:
                raise ValueError(
                    f"Resolution mismatch for '{path.name}': caller requested resolution '{resolution}' "
                    f"(normalized: '{req_res}'), but inherent file resolution is '{file_res}'. "
                    f"Resolution spoofing is strictly prohibited to maintain holdout integrity."
                )
            norm_res = req_res
        else:
            # Si el llamador no especificó resolución, adoptar la resolución inherente (default: 'daily')
            norm_res = file_res if file_res is not None else "daily"

        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]

        # Mapear nombres comunes de columnas sin colisiones
        col_renames: dict[str, str] = {}
        if "vol" in df.columns and "volume" not in df.columns:
            col_renames["vol"] = "volume"
        if "adj close" in df.columns and "close" not in df.columns:
            col_renames["adj close"] = "close"
        if "adj_close" in df.columns and "close" not in df.columns:
            col_renames["adj_close"] = "close"

        # Estandarizar columna temporal sin generar columnas duplicadas
        if "datetime_et" in df.columns:
            if "timestamp" in df.columns:
                df = df.drop(columns=["timestamp"])
            col_renames["datetime_et"] = "timestamp"
        elif "datetime" in df.columns and "timestamp" not in df.columns:
            col_renames["datetime"] = "timestamp"
        elif "time" in df.columns and "timestamp" not in df.columns and "date" not in df.columns:
            col_renames["time"] = "timestamp"

        if col_renames:
            df = df.rename(columns=col_renames)

        if "symbol" not in df.columns:
            df["symbol"] = symbol

        time_col = (
            "timestamp" if "timestamp" in df.columns else ("date" if "date" in df.columns else None)
        )
        if time_col is None:
            raise ValueError(
                f"El archivo {path} debe contener una columna de fecha/hora ('timestamp' o 'date')"
            )

        # Convertir a America/New_York naive para sincronización con rueda (9:30-16:00 ET)
        if pd.api.types.is_numeric_dtype(df[time_col]):
            ts_parsed = (
                pd.to_datetime(df[time_col], unit="s", utc=True)
                .dt.tz_convert("America/New_York")
                .dt.tz_localize(None)
            )
        else:
            ts_parsed = pd.to_datetime(df[time_col])
            if ts_parsed.dt.tz is not None:
                ts_parsed = ts_parsed.dt.tz_convert("America/New_York").dt.tz_localize(None)
        df[time_col] = ts_parsed

        required_cols = [time_col, "open", "high", "low", "close", "volume", "symbol"]
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                raise ValueError(f"Columna requerida '{col}' faltante en {path}")

        if df.empty:
            return df[required_cols]

        df = df.sort_values(by=time_col).reset_index(drop=True)
        parsed_dates = df[time_col].dt.date

        # Verificación de frecuencia a nivel de datos (defensa en profundidad)
        if len(df) >= 2:
            deltas = df[time_col].diff().dropna()
            if not deltas.empty:
                median_delta = deltas.median()
                if norm_res in ("hourly", "5m") and median_delta >= pd.Timedelta(hours=20):
                    raise ValueError(
                        f"Data frequency mismatch for '{path.name}': file contents show daily frequency "
                        f"(median interval: {median_delta}), but requested/configured resolution is '{norm_res}'. "
                        f"Resolution spoofing is prohibited."
                    )
                elif norm_res == "daily" and median_delta <= pd.Timedelta(hours=2):
                    raise ValueError(
                        f"Data frequency mismatch for '{path.name}': file contents show intraday frequency "
                        f"(median interval: {median_delta}), but requested/configured resolution is 'daily'."
                    )

        # --- REGLA 0: GUARDAS DE HOLDOUT Y FILTRADO TEMPORAL ---
        # Caso 1: El llamador especificó fechas explícitas (Backtest acotado)
        if start_date is not None or end_date is not None:
            req_start = _to_date(start_date) if start_date is not None else parsed_dates.min()
            req_end = _to_date(end_date) if end_date is not None else parsed_dates.max()

            # Guarda dura: Lanza HoldoutViolationError si el rango solicitado invade el holdout
            assert_not_holdout(
                start=req_start,
                end=req_end,
                resolution=norm_res,
                allow_synthetic=allow_synthetic,
            )

            mask = (parsed_dates >= req_start) & (parsed_dates <= req_end)
            df = df[mask].reset_index(drop=True)
            return df[required_cols]

        # Caso 2: Compatibilidad hacia atrás (llamada sin fechas)
        if allow_synthetic or is_synthetic_bypassed() or is_holdout_unsealed():
            return df[required_cols]

        # Determinar límites de la ventana de desarrollo según resolución
        if norm_res == "daily":
            dev_end = DEV_DAILY_END
            holdout_start = HOLDOUT_DAILY_START
            holdout_end = HOLDOUT_DAILY_END
        elif norm_res == "hourly":
            dev_end = DEV_HOURLY_END
            holdout_start = HOLDOUT_HOURLY_START
            holdout_end = HOLDOUT_HOURLY_END
        elif norm_res == "5m":
            file_min = parsed_dates.min()
            file_max = parsed_dates.max()
            if file_min >= SAMPLE_5M_START and file_max <= SAMPLE_5M_END:
                dev_end = None
                holdout_start = None
                holdout_end = None
            else:
                assert_not_holdout(start=file_min, end=file_max, resolution="5m")
                dev_end = DEV_DAILY_END
                holdout_start = HOLDOUT_DAILY_START
                holdout_end = HOLDOUT_DAILY_END
        else:  # Muestras fijas no identificadas sin holdout sellado
            dev_end = None
            holdout_start = None
            holdout_end = None

        if dev_end is not None and holdout_start is not None:
            file_min = parsed_dates.min()
            file_max = parsed_dates.max()

            # Si el archivo está 100% dentro del rango de holdout, bloquear carga
            if file_min >= holdout_start:
                raise HoldoutViolationError(
                    f"El archivo '{path}' contiene exclusivamente datos de holdout ({file_min} a {file_max}). "
                    f"El holdout para resolución '{norm_res}' está estrictamente sellado ({holdout_start} a {holdout_end})."
                )

            # Si el archivo abarca ventana de desarrollo pero se extiende al holdout, clamplear a desarrollo
            if file_max > dev_end:
                logger.info(
                    "Filtrando dataset '%s' a la ventana de desarrollo (%s <= %s). Filas de holdout descartadas.",
                    path,
                    norm_res,
                    dev_end,
                )
                df = df[parsed_dates <= dev_end].reset_index(drop=True)

        return df[required_cols]

    def fetch_and_save_real_data(
        self,
        symbol: str,
        interval: str = "1d",
        range_: str = "2y",
    ) -> pd.DataFrame:
        """Descarga barras históricas reales del mercado (vía Yahoo Finance API pública) y las guarda en CSV."""
        if not is_holdout_unsealed():
            raise HoldoutViolationError(
                "La descarga de datos de mercado en vivo (Yahoo Finance) está inhabilitada durante el desarrollo "
                "para preservar el sellado del holdout. Utilice los datasets locales de desarrollo "
                "('data/historical_2020_2022' o 'data/historical_2010_2026')."
            )

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
        df_daily.attrs["is_synthetic"] = True
        df_intraday.attrs["is_synthetic"] = True
        return df_daily, df_intraday
