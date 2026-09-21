# Directrices y Reglas del Trading Bot Cuantitativo

## 1. Principios No Negociables de Riesgo y Arquitectura
- **Fail-Closed y Veto Determinista:** Ninguna orden de mercado se envía sin stop loss activo. Las decisiones de trading son 100% de código determinista basado en precio y volumen; tras la auditoría v2.0 (Decisión D-01), los modelos de NLP (FinBERT) quedan desacoplados del camino crítico de ejecución en producción y archivados como telemetría offline de investigación.
- **Cero Sesgo de Futuro (Lookahead Bias):** Cualquier cálculo de señal, backtest o agregado de datos debe respetar estrictamente `timestamp <= as_of` (15:45 ET de cada sesión).
- **Límites de Seguridad:** Cortacircuitos diario automático al -2.0%, liquidación de emergencia al -3.5%, máximo 4 posiciones abiertas simultáneas y exclusividad de 1 dueño por ticker (bot vs manual).
- **Condición de Capital Real (Auditoría v2.2):** Con PBO = 84.45% y Sharpe observado de 1.11 por debajo del umbral crítico SR* = 1.22, seis meses impecables de paper trading NO habilitan capital real. El paper trading demuestra que el motor funciona (condición necesaria y no suficiente). La existencia de ventaja estadística se responde con T-10 a T-12 más evidencia forward. Las pruebas 2010–2026 de S5 no son punto-en-el-tiempo y no constituyen validación fuera de muestra (F-19).

## 2. Invariantes Cuantitativas de Estrategia y Portafolio
- **Estrategia Principal (S5 Dual Momentum Leader):** Selección transversal basada en momentum a 45 días, salida con Trailing Stop EMA(25), y límite de 30 días de retención.
- **Régimen de Mercado y Cash Yield:** Ante régimen bajista (`SPY` bajo EMA50/SMA200), la cartera rota al 100% en efectivo remunerado o instrumentos libres de riesgo (~4.5% anual en T-Bills / `SGOV`). **No realizar ventas en corto (shorting)** en el mercado de renta variable debido a restricciones normativas (Reg T en cuentas Cash), comisiones de préstamo y riesgo asimétrico de *short squeeze*.
- **Commodities Físicas vs Futuros Sintéticos:** 
  - Solo operar materias primas con custodia física en bóvedas (`GLD`/`GLDM` Oro, `SLV` Plata), que no sufren contango ni desgaste por renovación de contratos (*roll decay*).
  - **No operar fondos de futuros sintéticos a corto plazo (`USO`, `UNG`)**, cuyo contango estructural destruye capital de forma acumulativa. La exposición al ciclo energético debe mantenerse mediante productores integrados de gran capitalización (`XOM`).

## 3. Ingesta de Noticias y FinBERT
- **SEC EDGAR API:** Todas las peticiones a `data.sec.gov` DEBEN incluir un encabezado `User-Agent` descriptivo (ej. `TradingBot-Research/1.0 (admin@tradingbot.local)`), o la SEC bloqueará la solicitud con HTTP 403 Forbidden.
- **Form 8-K como Hecho Esencial:** Tratar las presentaciones 8-K (ítems 1.02, 2.02, 4.02, 5.02) como señales de veto prioritarias por riesgo de evento.
- **Almacenamiento Resiliente:** Soportar tanto formato CSV como Parquet en `HistoricalNewsFeatureStore`, priorizando CSV si no están instalados `pyarrow` o `fastparquet`.

## 4. Ejecución Remota e Infraestructura (Windows OpenSSH)
- **Modo No Interactivo:** Al ejecutar comandos remotos vía SSH hacia la PC de escritorio (`JOAPC` / `192.168.0.108`), usar siempre la bandera `ssh -n` para evitar que el cliente OpenSSH de Windows quede colgado esperando fin de entrada estándar (EOF).
- **Sintaxis de Comandos PowerShell:** Evitar cadenas complejas con comillas anidadas en `python -c` sobre `ssh powershell -Command`. Preferir ejecutar scripts directamente con argumentos o invocar funciones en archivos.
- **Transferencia de Código:** Cuando el nodo remoto no cuente con llaves SSH directas a GitHub, sincronizar el repositorio y datasets utilizando `scp` y `scp -r` bidireccionalmente.
