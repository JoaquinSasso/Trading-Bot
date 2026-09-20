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

Ejecuta el script indicando los activos que deseas analizar, el rango de fechas y la aceleración:

### Comando Recomendado (Universo Principal 2024–2026 en GPU):
```bash
python scripts/extract_and_process_historical_news.py --symbols AAPL,NVDA,MSFT,SPY,QQQ,AMZN,META,TSLA --start 2024-09-01 --device auto
```

### Opciones y Parámetros Disponibles:
| Parámetro | Valor por Defecto | Descripción |
| :--- | :--- | :--- |
| `--symbols` | `AAPL,NVDA,MSFT,SPY,QQQ,AMZN,META,TSLA` | Lista de tickers separados por comas. |
| `--start` | `2024-09-01` | Fecha inicial en formato `YYYY-MM-DD`. |
| `--end` | Hoy | Fecha final en formato `YYYY-MM-DD`. |
| `--device` | `auto` | `cuda` (fuerza GPU), `cpu` o `auto` (detecta GPU si existe). |
| `--batch-size` | `32` | Tamaño del lote para inferencia de FinBERT (puedes subir a `64` o `128` si tu GPU tiene más de 8 GB VRAM). |
| `--synthetic-fallback` | `False` | Genera noticias sintéticas si no se encuentran claves de Alpaca. |
| `--output-parquet` | `data/news_features/historical_news_features.parquet` | Destino del archivo binario comprimido Parquet. |
| `--output-csv` | `data/news_features/historical_news_features.csv` | Destino del archivo tabular CSV. |

---

## 5. Salida Generada

El script realiza 4 pasos automáticamente:
1. **Descarga y Caché:** Guarda las noticias crudas en `data/news/raw/{symbol}_raw_news.json` (las descargas sucesivas se leen del disco en 1 segundo sin consumir peticiones de API).
2. **Inferencia Batch FinBERT:** Ejecuta el modelo `ProsusAI/finbert` en tu GPU calculando probabilidades de sentimiento positivo, negativo y neutral.
3. **Agregación Temporal (24h):** Alinea los titulares a las **15:45 ET** de cada sesión de mercado para garantizar **CERO sesgo de futuro** (*lookahead bias*).
4. **Almacenamiento:**
   - `data/news_features/historical_news_features.csv`
   - `data/news_features/historical_news_features.parquet`

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
