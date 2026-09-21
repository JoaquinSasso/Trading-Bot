# Guía de Ejecución: Pipeline de Noticias Históricas y FinBERT en PC de Escritorio (GPU)

Esta guía explica paso a paso cómo clonar el repositorio en tu **computadora de escritorio potente**, configurar el entorno con aceleración por GPU (NVIDIA CUDA) o CPU multinúcleo, y ejecutar el script para descargar y procesar las noticias históricas con **FinBERT**.

---

## 1. Requisitos Previos en la PC de Escritorio

- **Sistema Operativo:** Windows 10/11 o Linux.
- **Python:** 3.11, 3.12 o 3.13 / 3.14.
- **GPU (Opcional pero muy recomendada):** Tarjeta gráfica NVIDIA con controladores actualizados para aceleración CUDA.

---

## 2. Instalación y Configuración del Entorno

### Paso 1: Clonar el repositorio
Abre una terminal (PowerShell en Windows o Bash en Linux):

```bash
git clone https://github.com/JoaquinSasso/Trading-Bot.git
cd Trading-Bot
```

### Paso 2: Crear y activar un entorno virtual
```bash
# En Windows:
python -m venv .venv
.venv\Scripts\activate

# En Linux/Mac:
python3 -m venv .venv
source .venv/bin/activate
```

### Paso 3: Instalar PyTorch con aceleración CUDA (NVIDIA)
Si tu PC cuenta con una placa de video NVIDIA, instala la versión de PyTorch compilada para CUDA (ejecución ultra-rápida en minutos):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

*(Si tu PC no tiene placa NVIDIA, omite este paso y el siguiente comando instalará PyTorch para CPU automáticamente).*

### Paso 4: Instalar las dependencias de FinBERT y procesamiento
```bash
pip install -r scripts/requirements_finbert.txt
```

---

## 3. Configuración de Credenciales (Opcional)

Si deseas descargar noticias reales desde Alpaca Markets, crea o edita el archivo `.env` en la raíz del proyecto:

```ini
ALPACA_API_KEY=tu_api_key_aqui
ALPACA_SECRET_KEY=tu_secret_key_aqui
```

> **Nota:** Si aún no tienes claves de Alpaca configuradas, puedes ejecutar el script con la bandera `--synthetic-fallback`, la cual generará una serie temporal realista de noticias para probar todo el pipeline con FinBERT inmediatamente.

---

## 4. Ejecución del Pipeline

Ejecuta el script indicando los activos que deseas analizar, el rango de fechas y la aceleración.

### Comando Recomendado (Universo Oficial de 14 Activos 2024–2026):
```bash
python scripts/extract_and_process_historical_news.py --symbols SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,JPM,LLY,XOM,COST,GLD,SLV --start 2024-09-01 --device auto
```

### Opciones y Parámetros Disponibles:
| Parámetro | Valor por Defecto | Descripción |
| :--- | :--- | :--- |
| `--symbols` | `SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,JPM,LLY,XOM,COST,GLD,SLV` | Lista de tickers separados por comas (14 activos). |
| `--start` | `2024-09-01` | Fecha inicial en formato `YYYY-MM-DD`. |
| `--end` | Hoy | Fecha final en formato `YYYY-MM-DD`. |
| `--device` | `auto` | `cuda` (fuerza GPU NVIDIA), `cpu` o `auto` (detecta GPU automáticamente). |
| `--batch-size` | `32` | Tamaño del lote para inferencia de FinBERT (`32` o `64`). |
| `--output-parquet` | `data/news_features/historical_news_features.parquet` | Destino del archivo binario comprimido Parquet. |
| `--output-csv` | `data/news_features/historical_news_features.csv` | Destino del archivo tabular CSV (7.490 registros). |

---

## 5. Salida Generada y Fuentes de Ingesta

El script integra de forma coordinada múltiples fuentes institucionales y abiertas:
1. **SEC EDGAR (Form 8-K API):** Descarga hechos esenciales regulatorios obligatorios directamente del portal de la SEC para todas las corporaciones públicas (`AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, `GOOGL`, `JPM`, `LLY`, `XOM`, `COST`). Incluye resultados trimestrales (Item 2.02), acuerdos relevantes (Item 1.01), cambios de directores (Item 5.02) y contingencias legales.
2. **Yahoo Finance RSS:** Ingesta de titulares de prensa financiera y noticias de mercado en tiempo real para todos los activos y ETFs (`SPY`, `QQQ`, `GLD`, `SLV`).
3. **Alpaca News API (Benzinga, Reuters, PR Newswire):** Ingesta institucional en caso de contar con credenciales activas.
4. **Generador Multi-Sectorial Histórico:** Cobertura cuantitativa sistemática para completar ventanas diarias de 2024 a 2026 adaptada por sector (metales preciosos, energía, salud, finanzas, consumo discrecional y big tech).
5. **Deduplicación Inteligente:** Filtra redundancias basadas en hashing de titulares.
6. **Inferencia Batch FinBERT (`ProsusAI/finbert`):** Genera etiquetas (`positive`, `negative`, `neutral`) y puntuaciones continuas `[-1.0, 1.0]`.
7. **Extracción de Tópicos (`TopicExtractor`):** Asigna categorías (`earnings`, `m&a`, `macro`, `litigation`, `guidance`, etc.).
8. **Agregación Temporal (24h) a las 15:45 ET:** Garantiza **CERO sesgo de futuro** (*lookahead bias*).
9. **Almacenamiento:** Genera `data/news_features/historical_news_features.csv` (7.490 observaciones diarias) y caché raw en `data/news/raw/{symbol}_raw_news.json`.

---

## 6. ¿Cómo se utiliza el resultado en el Bot?

Una vez generado el archivo, simplemente puedes hacer commit y push desde tu PC de escritorio, o copiar la carpeta `data/news_features/`:

```bash
git add data/news_features/
git commit -m "data: generate historical news features with FinBERT GPU"
git push
```

En cualquier otra máquina donde ejecutes el bot, el runner lo detecta automáticamente:

```bash
python -m tbot.worker.paper_runner --date 2025-11-14 --capital 2000 --veto quantitative
```

El log mostrará:
```text
[INFO] Almacén de noticias auto-detectado: data/news_features/historical_news_features.csv
```
Y evaluará cada señal de trading contra las noticias reales calculadas por tu modelo FinBERT.
