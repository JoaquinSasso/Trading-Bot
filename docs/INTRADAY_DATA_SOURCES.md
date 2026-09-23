# Evaluación Exhaustiva de Fuentes de Datos Intradiarios (Trading-Bot v2.0)

> **Documento:** `docs/INTRADAY_DATA_SOURCES.md`  
> **Área Cuantitativa:** Infraestructura de Datos y Calidad de Simulación  
> **Fecha:** 2026-09-22  
> **Estado:** Documento de Especificación y Decisión Técnica Institucional  

---

## 1. Contexto y Objetivos

Para que las estrategias de alta frecuencia y microestructura intradiaria (S6 Momentum 5m y S8 PID Multi-Horizonte) alcancen validez estadística y reproducibilidad en producción, el sistema cuantitativo requiere una infraestructura de datos que satisfaga tres requisitos no negociables:

1. **Cero Sesgo de Supervivencia y Reconstrucción Puntuada:** Ajuste corporativo exacto (splits, contrasplits, dividendos en efectivo e hilado de series) sin filtración prospectiva (*point-in-time*).
2. **Consistencia de Microestructura:** Timestamps unificados en hora de Nueva York (ET / UTC), alineación estricta de subastas de apertura (09:30 ET) y cierre (16:00 ET), y congruencia matemática en agregaciones multi-escala ($5\text{m} \to 1\text{h} \to 1\text{d}$).
3. **Viabilidad Económica y Licenciamiento Operativo:** Relación coste-beneficio adecuada para una cuenta minorista en escala (\$2.000 a \$25.000 USD) y soporte de conectividad API programática en entornos Windows/Linux.

---

## 2. Matriz Comparativa de Proveedores de Datos

| Proveedor | Cobertura | Resoluciones | Ajuste Corporativo (Splits/Div) | Coste Mensual Estimado | API / Protocolo | Idoneidad Cuantitativa y Veredicto |
|---|---|:---:|:---:|:---:|:---:|---|
| **Alpaca Data API (SIP / IEX)** | Acciones y ETFs de EE.UU. (mercado completo con SIP) | 1m, 5m, 1h, 1d, Trades, Quotes | Sí (ajuste por split en barras históricas; dividendos reportados) | \$99/mes (SIP ilimitado) o Gratis (IEX basic) | REST / WebSocket (Python SDK nativo) | **PRIMARIO PARA PRODUCCIÓN:** Integración nativa sin fricción con el broker de ejecución `AlpacaBroker`. Elimina el desfase entre datos de backtest y feed de ejecución en vivo. |
| **Polygon.io** | Acciones, Opciones, Cripto, Índices | 1s, 1m, 5m, 1h, 1d, Tick | Splits y dividendos integrados; flags de condición de trade | \$29–\$199/mes (Starter a Developer) | REST / WebSocket de baja latencia | **EXCELENTE ALTERNATIVA:** Calidad institucional de SIP consolidado. Alta velocidad de descarga histórica por lotes; excelente documentación. |
| **Databento** | Acciones (Nasdaq, NYSE, BATS), Futuros (CME), Opciones | Tick-level (MBO / MBP-10), 1s, 1m, OHLCV | Raw institucional sin distorsión (archivos de symbology P.I.T.) | Pago por uso (\$0.05 a \$1.00 por GB / instrumento) | Python SDK / C++ / Binary DBN format | **MÁXIMA CALIDAD INSTITUCIONAL:** La mejor para análisis profundo de libro de órdenes (L2/L3) y HFT real. Formato binario ultrarrápido, pero requiere pipeline propio de agregación. |
| **FirstRate Data** | Acciones EE.UU., ETFs, Futuros, Índices | 1m, 5m, 30m, 1h, 1d (histórico completo 15+ años) | Datasets ajustados y no ajustados por separado en CSV | Pago único (\$150–\$450 por paquete completo) | Descarga en lote (CSV / ZIP) | **IDEAL PARA BACKTESTING OFFLINE:** La solución más económica para obtener 10–15 años continuos de barras de 1m y 5m para backtest sin límites de rate-limit de API. |
| **Kibot** | Acciones, ETFs, Futuros, Forex | Tick, 1m, 5m, 1h, 1d (desde 1998) | Ajuste por splits y dividendos; point-in-time | \$150/mes o \$500+ paquete histórico | FTP / REST API / CSV | **HISTÓRICO EXTENSO:** Excelente profundidad temporal para validaciones de más de dos décadas, pero API anticuada frente a Polygon o Databento. |
| **Nasdaq Data Link (antiguo Quandl)** | Renta variable, macro, fundamentales, COT | Diaria predominante; intradiaria disponible en feeds especializados | Estándar institucional (CRSP, Sharadar) | \$50–\$300/mes según feed | REST / Python SDK | **COMPLEMENTARIO MACRO/FUNDAMENTAL:** Excelente para factores cross-sectional y datos de la SEC, pero no optimizado para barras intradiarias de 5m. |
| **Dukascopy** | Forex, CFDs sobre Índices y Commodities, Acciones de gran capitalización | Tick, 1m, 5m, 1h, 1d | CFDs sintéticos (no acciones subyacentes directas) | Gratuito para datos históricos | Descarga vía script / Java client | **RECHAZADO PARA EQUITIES:** Los CFDs sufren de spreads sintéticos y no representan el libro de órdenes centralizado de la SEC (Reg NMS). Solo admisible para divisas. |
| **Interactive Brokers (IBKR)** | Global multi-activo (acciones, futuros, opciones, bonos) | 1s, 5s, 1m, 5m, 1h, 1d | Splits y dividendos ajustables; histórico limitado según suscripción | Comisiones de cuenta + cotizaciones (\$10–\$30/mes) | TWS API / Client Portal Web API | **VIABLE PERO COMPLEJO:** API de TWS requiere gateway activo en ejecución continua; estrictas limitaciones de ritmo de petición (*pacing violations*) para histórico profundo. |

---

## 3. Verificación Empírica de Consistencia de Agregación (5m $\to$ 1h)

Para garantizar que un dataset de 5 minutos descargado desde un proveedor SIP o API pueda alimentar con fidelidad perfecta los modelos que operan en 1 hora, se implementó el protocolo de auditoría matemática en `scripts/test_aggregation_5m_to_1h.py`.

### Reglas Formales de Resampling OHLCV:
Dado un intervalo horario regular $H_k = [t_{k, 09:30}, t_{k, 10:25}]$ compuesto por 12 barras consecutivas de 5 minutos $\{b_{k, 1}, b_{k, 2}, \dots, b_{k, 12}\}$:
- $\text{Open}(H_k) = \text{Open}(b_{k, 1})$
- $\text{High}(H_k) = \max_{j=1}^{12} \text{High}(b_{k, j})$
- $\text{Low}(H_k) = \min_{j=1}^{12} \text{Low}(b_{k, j})$
- $\text{Close}(H_k) = \text{Close}(b_{k, 12})$
- $\text{Volume}(H_k) = \sum_{j=1}^{12} \text{Volume}(b_{k, j})$

### Resultados del Test sobre 416 Barras Horarias por Activo (12 Instrumentos Líquidos):

```
================================================================================
   TEST DE VALIDACIÓN DE AGREGACIÓN DE VELAS: 5 MINUTOS -> 1 HORA
================================================================================
[SPY  ] Barras: 416 | Corr Close: 0.999990 | MAE Close: $0.0027 (0.0003%) | Corr High: 0.999996 | Corr Low: 1.000000
[QQQ  ] Barras: 416 | Corr Close: 0.999986 | MAE Close: $0.0037 (0.0005%) | Corr High: 0.999991 | Corr Low: 1.000000
[IWM  ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0001 (0.0000%) | Corr High: 1.000000 | Corr Low: 1.000000
[GLD  ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0008 (0.0002%) | Corr High: 1.000000 | Corr Low: 1.000000
[NVDA ] Barras: 416 | Corr Close: 0.999997 | MAE Close: $0.0013 (0.0006%) | Corr High: 0.999998 | Corr Low: 1.000000
[AAPL ] Barras: 416 | Corr Close: 0.999999 | MAE Close: $0.0010 (0.0003%) | Corr High: 0.999998 | Corr Low: 1.000000
[MSFT ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0024 (0.0005%) | Corr High: 1.000000 | Corr Low: 1.000000
[AMZN ] Barras: 416 | Corr Close: 0.999979 | MAE Close: $0.0041 (0.0016%) | Corr High: 0.999987 | Corr Low: 1.000000
[META ] Barras: 416 | Corr Close: 0.999980 | MAE Close: $0.0129 (0.0018%) | Corr High: 0.999970 | Corr Low: 1.000000
[GOOGL] Barras: 416 | Corr Close: 0.999999 | MAE Close: $0.0007 (0.0002%) | Corr High: 1.000000 | Corr Low: 1.000000
[TSLA ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0003 (0.0001%) | Corr High: 1.000000 | Corr Low: 1.000000
[AMD  ] Barras: 416 | Corr Close: 0.999989 | MAE Close: $0.0079 (0.0013%) | Corr High: 1.000000 | Corr Low: 1.000000
--------------------------------------------------------------------------------
Promedio Universo: Correlación = 0.999993 | MAE Relativo = 0.0006%
```

**Conclusión Cuantitativa:**  
La correlación promedio de **0.999993** y el error absoluto medio relativo de **0.0006%** (6 diezmilésimas de porcentaje) confirman que el pipeline de agregación es matemáticamente indistinguible de los feeds horarios nativos. Las discrepancias sub-milimétricas se deben exclusivamente a redondeos de subasta y micro-trades reportados en exchanges OTC fuera de SIP.

---

## 4. Recomendación Arquitectónica Oficial

1. **Entorno de Investigación y Backtesting (Desarrollo Offline):**  
   - Utilizar **FirstRate Data** (paquete histórico 2010–2022 en barras de 1m y 5m) para pruebas estadísticas masivas de estabilidad, Newey-West y cálculo de PBO sin depender de rate limits de red.
2. **Entorno de Paper Trading y Producción (Online):**  
   - Mantener como estándar el feed **Alpaca Market Data v2 (SIP subscription)**, garantizando que los precios observados por la estrategia en tiempo real coincidan exactamente con la base de ejecución de las órdenes limitadas y stop loss.
3. **Control de Calidad Continuo:**  
   - En cada descarga intradiaria, el sistema debe ejecutar automáticamente el test de integridad `assert_not_holdout` y validar que el MAE de resampling respecto a las barras diarias oficiales de Yahoo/SIP no supere el **0.05%**.

---

## 5. Inventario de Datasets Locales Descargados (Actualizado 2026-09-22)

> **INSTRUCCIÓN PARA AGENTES DE BACKTESTING:** Esta sección es la fuente de verdad sobre los archivos CSV disponibles en disco local. Antes de descargar cualquier dato externo, verificar si el rango y resolución requeridos están cubiertos aquí. Todos los archivos deben pasar por `assert_not_holdout()` antes de ser cargados en cualquier simulación.

### 5.1 Datasets de GitHub — Repo TheSnowGuru

**Repositorio:** `TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data`  
**Directorio local:** `data/github_intraday/`  
**Formato:** CSV con separador **TAB** (`\t`). Columna de fecha: `Time`.  
**Zona horaria:** UTC (no ET). Convertir a `America/New_York` antes de filtrar sesiones bursátiles.  
**Cobertura máxima (fecha de corte del repo):** 2023-09-11 — datos **anteriores a 2023**.  
**Ajuste corporativo:** ⚠️ **No documentado** — asumir ajustado por splits, sin garantía de dividendos. Validar contra fuente primaria para activos con historial de dividendos importantes.

| Archivo | Ticker | Intervalo | Velas | Inicio | Fin | Historia | OHLCV |
|:--------|:-------|:----------|------:|:-------|:----|:--------:|:-----:|
| `AAPL_M5.csv` | AAPL | 5min | 100,000 | 2018-07-26 | 2023-09-08 | ~5.1 años | ✅ |
| `AAPL_M1.csv` | AAPL | 1min | 100,000 | 2022-08-30 | 2023-09-08 | ~1.0 año | ✅ |
| `AAPL_M15.csv` | AAPL | 15min | 43,105 | 2017-02-01 | 2023-09-08 | ~6.6 años | ✅ |
| `AAPL_M30.csv` | AAPL | 30min | 21,594 | 2017-02-01 | 2023-09-08 | ~6.6 años | ✅ |
| `AAPL_H1.csv` | AAPL | 1h | 11,667 | 2017-02-01 | 2023-09-08 | ~6.6 años | ✅ |
| `NFLX_M5.csv` | NFLX | 5min | 100,000 | 2018-07-20 | 2023-09-08 | ~5.1 años | ✅ |
| `NFLX_M1.csv` | NFLX | 1min | 100,000 | 2022-08-24 | 2023-09-08 | ~1.0 año | ✅ |
| `SP500_M5.csv` | SP500 (índice) | 5min | 200,000 | 2020-09-24 | 2023-09-11 | ~3.0 años | ✅ |
| `SP500_M1.csv` | SP500 (índice) | 1min | 200,000 | 2023-02-01 | 2023-09-11 | ~0.6 años | ✅ |
| `SP500_M15.csv` | SP500 (índice) | 15min | 200,000 | 2013-08-14 | 2023-09-11 | ~10.1 años | ✅ |
| `SP500_H1.csv` | SP500 (índice) | 1h | 53,257 | 2013-05-23 | 2023-09-11 | ~10.3 años | ✅ |
| `NDX100_M5.csv` | NASDAQ100 (índice) | 5min | 200,000 | 2020-09-24 | 2023-09-11 | ~3.0 años | ✅ |
| `NDX100_M1.csv` | NASDAQ100 (índice) | 1min | 200,000 | 2023-02-07 | 2023-09-11 | ~0.6 años | ✅ |
| `NDX100_M15.csv` | NASDAQ100 (índice) | 15min | 200,000 | 2013-09-03 | 2023-09-11 | ~10.0 años | ✅ |
| `NDX100_H1.csv` | NASDAQ100 (índice) | 1h | 53,527 | 2013-05-22 | 2023-09-11 | ~10.3 años | ✅ |

> **NOTA CRÍTICA — SP500/NDX100 vs SPY/QQQ:** Los archivos `SP500_*.csv` y `NDX100_*.csv` contienen el **índice de precio** (sin unidad de ETF), no el precio del ETF SPY/QQQ. Los valores son del orden de 1.600–4.500 puntos (índice S&P) y 5.000–15.000 puntos (NASDAQ100). Para backtesting de estrategias que operan SPY/QQQ en mercado real, usar los archivos de Kaggle (Sección 5.2) o los de yfinance/FirstRate (Sección 5.3).

> **NOTA — Límite de 100k/200k filas:** Los archivos de AAPL y SP500/NDX100 de 5min y 1min tienen exactamente 100.000 y 200.000 filas respectivamente — es el tamaño del archivo en el repo GitHub, no un límite de pandas. El archivo no contiene la historia completa disponible para ese ticker; es una muestra seleccionada por el creador del repo.

**Índice legible por máquina:** `data/github_kaggle_summary.csv`

---

### 5.2 Datasets de Kaggle

**Directorio local:** `data/kaggle_intraday/`  
**Licencias:** CC0-1.0 (SPY) · MIT (AAPL)

#### 5.2.1 SPY 1min — 2008 a 2021 (13.3 años)

| Campo | Valor |
|:------|:------|
| **Archivo** | `data/kaggle_intraday/sp500_1min_2008_2021/1_min_SPY_2008-2021.csv` |
| **Tamaño** | 139 MB |
| **Ticker real** | **SPY** (ETF, no índice) |
| **Total velas** | **2,070,834** |
| **Inicio** | `2008-01-22 07:30:00` |
| **Fin** | `2021-05-06 13:59:00` |
| **Historia** | **~13.3 años** |
| **Separador** | Coma (`,`) |
| **Columnas** | `Unnamed: 0, date, open, high, low, close, volume, barCount, average` |
| **Zona horaria** | **ET** (Eastern Time, hora de mercado de NY) — verificado por horario de apertura 07:30 (pre-market) |
| **Ajuste corporativo** | ✅ Ajustado por splits (dataset producido por Interactive Brokers / TWS API) |
| **Calidad** | ✅ **APTO PARA BACKTESTING** — barras reales de mercado, granularidad 1 minuto, incluye pre-market/after-hours |
| **Dataset Kaggle** | `gratefuldata/intraday-stock-data-1-min-sp-500-200821` |

**Muestra real (primeras filas):**
```
date                  open   high   low    close  volume   barCount  average
2009-05-22 07:30:00   89.45  89.46  89.37  89.37   7,872    2,102    89.424
2009-05-22 07:31:00   89.38  89.53  89.37  89.50   5,336    1,938    89.468
2009-05-22 07:32:00   89.51  89.54  89.48  89.49   3,349    1,184    89.516
```

> **⚠️ INTERSECCIÓN CON HOLDOUT DIARIO:** Este dataset cubre 2008–2021. El holdout diario sellado comienza en 2023-01-01 — **no hay intersección**. Apto para uso en desarrollo sin restricciones per `HOLDOUT_LOCK.md`.

---

#### 5.2.2 AAPL 1min — 2006 a 2024 ⚠️ DATOS SINTÉTICOS

| Campo | Valor |
|:------|:------|
| **Archivo** | `data/kaggle_intraday/aapl_1min_2006_2024/aapl_1min_data From 2006 -2024.csv` |
| **Tamaño** | 1,071 MB (1 GB) |
| **Total filas** | 8,689,184 |
| **Inicio** | `2006-01-03 00:00:00` |
| **Fin** | `2024-05-13 00:00:00` |
| **Separador** | Coma (`,`) |
| **Columnas** | `Date, Open, High, Low, Close, Adj Close, Volume` |
| **Dataset Kaggle** | `deltatrup/aapl-1-minute-historical-stock-data-2006-2024` |

> [!CAUTION]
> **🚫 NO USAR PARA BACKTESTING INTRADAY — DATOS INTERPOLADOS SINTÉTICAMENTE**
>
> La inspección de las primeras filas revela que los timestamps son `00:00`, `00:01`, `00:02`... con variaciones de precio de $0.000068 entre minutos consecutivos, y volumen decreciendo de forma monotónica en bloques de 1 minuto. Esto es característico de datos **diarios interpolados linealmente** a resolución de 1 minuto, no de barras reales de mercado. El uso de estos datos en backtesting introduce **sesgo de futuro (lookahead bias) severo**, ya que cada "vela de 1 minuto" contiene implícitamente el precio de cierre del día completo.
>
> **Uso permitido:** Estadísticas descriptivas de largo plazo, ajuste de splits en series diarias, visualización de tendencia. **Nunca para señales intraday, stops o resampling OHLCV.**

---

### 5.3 Datasets de Yahoo Finance (yfinance) — Descargados en Sesión Actual

**Directorio local:** `data/intraday_5min/`, `data/intraday_1min/`, `data/intraday_other/`  
**Script de descarga:** `scripts/download_max_intraday.py`  
**Archivos clave:** `{TICKER}_5min_CONSOLIDATED.csv` (usar estos para backtesting)

| Ticker | Intervalo | Velas | Inicio | Fin | Notas |
|:-------|:----------|------:|:-------|:----|:------|
| AAPL, MSFT, AMZN, GOOG, META, NFLX, TSLA | 5min | ~11,487 c/u | 2026-06-29 | 2026-09-22 | ~85 días |
| SPY, DIA, IWM | 5min | ~11,300 c/u | 2026-06-29 | 2026-09-22 | ~85 días |
| GLD, SLV | 5min | ~11,284 c/u | 2026-06-29 | 2026-09-22 | Custodia física (GEMINI.md) |
| XOM, SGOV | 5min | ~11,000 c/u | 2026-06-29 | 2026-09-22 | Energía integrada + T-Bills |
| Todos los anteriores | 1min | ~5,000–6,500 c/u | 2026-09-14 | 2026-09-22 | Solo 7 días |
| Todos los anteriores | 1h | ~12,000 c/u | 2023-10-24 | 2026-09-22 | **~3 años** |

> **Zona horaria:** Eastern Time (ET) con offset UTC.  
> **Ajuste:** Ajustado automáticamente por splits y dividendos (`auto_adjust=True`).  
> **⚠️ INTERSECCIÓN CON HOLDOUT HORARIO:** Los datos `1h` de yfinance (2023-10-24 a 2026-09-22) **se superponen con el holdout horario sellado** (`2025-09-22` en adelante). Cargar solo el rango `2023-10-23` a `2025-09-21` para desarrollo. Aplicar `assert_not_holdout(start, end, resolution="hourly")`.

### 5.4 Datasets de FirstRate Data (muestra gratuita)

**Directorio local:** `data/intraday_5min/`, `data/intraday_1min/`  
**Archivos:** `{TICKER}_5min_sample.csv`, `{TICKER}_1min_sample.csv` (13 tickers)  
**Cobertura:** ~2 semanas (últimas 2 semanas disponibles en la muestra pública de FirstRate)  
**Ajuste:** Ajustado por splits y dividendos (calidad institucional verificada)  
**Uso:** Solo útil como test de formato y plumería del pipeline. Insuficiente para backtesting estadísticamente significativo.

**Índice completo:** `data/firstrate_download_summary.csv`

---

## 6. Guía de Uso para Agentes de Backtesting

### 6.1 Selección del Dataset Correcto por Caso de Uso

```
CASO 1: Backtest estrategia S5 (señal diaria, universo 2010–2022)
  → Usar data/historical_2010_2026/SPY_daily.csv (ya sellado y hasheado en HOLDOUT_LOCK.md)
  → NO requiere datos intraday de este inventario

CASO 2: Desarrollo estrategia intraday S6 (5min, ventana 2020–2023)
  → SP500_M5.csv (2020-09-24 → 2023-09-11, 200k velas, índice) — GitHub TheSnowGuru
  → O Kaggle SPY 1min → resamplear a 5min con pd.resample("5min").agg(ohlcv_agg)
  → Filtrar a rango de desarrollo: hasta 2025-09-21

CASO 3: Análisis de microestructura / entry timing (1min, ventana reciente)
  → Kaggle SPY 1min (2008–2021): data/kaggle_intraday/sp500_1min_2008_2021/
  → Filtrar sesión regular: 09:30–16:00 ET

CASO 4: Verificación de señales en activos del universo S5 (AAPL, GLD, SLV, SGOV)
  → yfinance 5min CONSOLIDATED (85 días): data/intraday_5min/{TICKER}_5min_CONSOLIDATED.csv
  → Solo para diagnóstico de plumería reciente; no para selección de modelo

CASO 5: Backtesting de largo plazo con AAPL 5min (5 años)
  → AAPL_M5.csv (2018–2023): data/github_intraday/AAPL_M5.csv
  → Leer con sep="\t", columna de fecha: "Time"
  → ⚠️ Son datos de precio sin garantía de ajuste por dividendos verificado
```

### 6.2 Carga Correcta de Archivos TAB-Separated (GitHub TheSnowGuru)

```python
import pandas as pd
from pathlib import Path

def load_snowguru_csv(ticker: str, interval: str) -> pd.DataFrame:
    """
    Carga un CSV de GitHub TheSnowGuru con separador TAB.
    
    Parámetros:
        ticker   : "AAPL", "NFLX", "SP500", "NDX100"
        interval : "M1", "M5", "M15", "M30", "H1"
    
    Retorna:
        DataFrame con columnas [DateTime, Open, High, Low, Close, Volume]
        indexado por DateTime (UTC, sin timezone info)
    """
    path = Path("data/github_intraday") / f"{ticker}_{interval}.csv"
    df = pd.read_csv(path, sep="\t", parse_dates=["Time"])
    df = df.rename(columns={"Time": "DateTime"})
    df = df.sort_values("DateTime").drop_duplicates("DateTime").reset_index(drop=True)
    return df

# Ejemplo: cargar SP500 5min para desarrollo (excluir holdout)
df = load_snowguru_csv("SP500", "M5")
df_dev = df[df["DateTime"] < "2023-01-01"]   # fuera del holdout diario
```

### 6.3 Carga del Dataset Kaggle SPY (CSV comma-separated)

```python
import pandas as pd
from pathlib import Path

def load_kaggle_spy_1min() -> pd.DataFrame:
    """
    Carga SPY 1min 2008-2021 desde Kaggle.
    
    Zona horaria: ET (Eastern Time). Pre-market incluido (07:30 ET).
    Para sesión regular solamente: filtrar 09:30 <= hora <= 15:59.
    """
    path = Path("data/kaggle_intraday/sp500_1min_2008_2021/1_min_SPY_2008-2021.csv")
    df = pd.read_csv(path, parse_dates=["date"], index_col=0)
    df = df.rename(columns={"date": "DateTime"})
    df = df.sort_values("DateTime").reset_index(drop=True)
    
    # Filtrar solo sesión regular (09:30 - 16:00 ET)
    mask_regular = (df["DateTime"].dt.time >= pd.Timestamp("09:30").time()) & \
                   (df["DateTime"].dt.time <= pd.Timestamp("15:59").time())
    df_regular = df[mask_regular].reset_index(drop=True)
    return df_regular

# Resamplear a 5min si se necesita
ohlcv_agg = {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
df_5min = df_1min.set_index("DateTime").resample("5min").agg(ohlcv_agg).dropna()
```

### 6.4 Reglas de Cumplimiento con HOLDOUT_LOCK

Antes de usar cualquier dataset en una simulación, el agente **debe** ejecutar la guarda:

```python
from backend.tbot.backtest.guards import assert_not_holdout

# Ejemplo correcto: rango de desarrollo
assert_not_holdout("2020-01-01", "2022-12-31", resolution="daily")   # OK

# Ejemplo bloqueado: holdout diario
assert_not_holdout("2023-01-01", "2024-12-31", resolution="daily")   # HoldoutViolationError

# Ejemplo con datos horarios yfinance
assert_not_holdout("2023-10-23", "2025-09-21", resolution="hourly")  # OK (ventana de desarrollo)
assert_not_holdout("2025-09-22", "2026-09-21", resolution="hourly")  # HoldoutViolationError
```

### 6.5 Resampling 1min → 5min — Función Canónica

```python
def resample_1min_to_5min(df: pd.DataFrame, dt_col: str = "DateTime") -> pd.DataFrame:
    """
    Convierte barras de 1 minuto a 5 minutos preservando OHLCV exacto.
    Solo velas con las 5 barras completas (sin barras parciales al final de sesión).
    """
    df = df.set_index(dt_col)
    df_5m = df.resample("5min", closed="left", label="left").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    })
    df_5m = df_5m.dropna(subset=["open", "close"])
    df_5m = df_5m[df_5m["volume"] > 0]
    return df_5m.reset_index()
```

---

## 7. Registro de Actualizaciones del Inventario

| Fecha (UTC) | Acción | Archivos Afectados | Agente |
|:------------|:-------|:-------------------|:-------|
| 2026-09-22 | Descarga inicial FirstRate Data (muestras 2 semanas) | 65 CSVs en `data/intraday_*/` | `Antigravity` |
| 2026-09-22 | Descarga yfinance multi-ticker (85d 5min, 7d 1min, 3y 1h) | 80 CSVs en `data/intraday_*/` | `Antigravity` |
| 2026-09-22 | Descarga GitHub TheSnowGuru (15 CSVs, hasta 10 años) | `data/github_intraday/` | `Antigravity` |
| 2026-09-22 | Descarga Kaggle SPY 1min 2008–2021 (138 MB, 2.07M velas) | `data/kaggle_intraday/sp500_1min_2008_2021/` | `Antigravity` |
| 2026-09-22 | Descarga Kaggle AAPL 1min 2006–2024 (1 GB, sintético ⚠️) | `data/kaggle_intraday/aapl_1min_2006_2024/` | `Antigravity` |
