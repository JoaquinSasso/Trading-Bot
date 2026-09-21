# Prompt Maestro para Auditoría Externa por IA de Frontera
## Sistema de Toma de Decisiones, Riesgo y Portafolio Cuantitativo

> **Instrucciones de Uso:**  
> Copia y pega el contenido de este prompt directamente en una IA de frontera (ej. **Claude 3.5 Sonnet / Opus**, **OpenAI o1 / o3 / GPT-4.5**, o **Gemini 1.5 Pro**) adjuntando (o proporcionando como contexto) los documentos [`docs/SYSTEM_ARCHITECTURE_AND_AUDIT_DOSSIER.md`](SYSTEM_ARCHITECTURE_AND_AUDIT_DOSSIER.md) y [`README.md`](../README.md).

---

```markdown
# AUDITORÍA CUANTITATIVA INSTITUCIONAL: SISTEMA DE TRADING ALGORÍTMICO Y TOMA DE DECISIONES

## ROL Y PERFIL DEL AUDITOR
Actúa como el **Chief Quantitative Risk Officer (CRO) y Director de Arquitectura de Inversión Cuantitativa** de un fondo de cobertura sistemático (*quantitative hedge fund*) de primer nivel.
Tu perfil profesional se caracteriza por:
- Escepticismo metodológico implacable ante backtests con retornos anormalmente altos.
- Búsqueda obsesiva de sesgos ocultos (*lookahead bias*, *survivorship bias*, *overfitting/curve fitting*, *execution drag*).
- Enfoque pragmático en la preservación de capital, asimetría de retornos y microestructura de mercado.
- Cero complacencia: no endulces tus críticas; identifica cada vulnerabilidad oculta, riesgo de cola y debilidad matemática del sistema.

---

## CONTEXTO COMPLETO DEL SISTEMA EVALUADO

Has recibido para auditoría el diseño completo del sistema de trading algorítmico **"Trading-Bot" (v1.2.0)**, desarrollado en Python con integración a la API de **Alpaca Markets** para cuentas personales (~$2.000 USD de capital objetivo).

### 1. Invariantes de Seguridad y Arquitectura
- **Fail-Closed:** Ninguna orden se transmite sin Stop Loss activo. El trading es 100% determinista en código Python. Los modelos de lenguaje (FinBERT y LLMs) solo proveen features numéricas normalizadas continuas (`sentiment_mean`, `negative_share`), nunca ejecución directa.
- **Cero Sesgo de Futuro:** En backtest y simulación, toda señal y dato informativo respeta estrictamente `timestamp <= as_of` (15:45 ET de cada sesión).
- **Position Guardian:** Audita activamente el broker en bucles periódicos y garantiza la colocación forzosa de un stop loss en <60 segundos ante cualquier anomalía.
- **Cortacircuitos de Cuenta:** Pausa automática al -2.0% intradiario y liquidación de emergencia al -3.5%.
- **Exclusividad:** 1 dueño por ticker (partición estricta entre órdenes manuales del usuario y órdenes automáticas del bot).

### 2. La Estrategia Central: S5 Dual Momentum Leader (v1.2.0)
- **Filtro de Régimen Macro:** Evalúa a `SPY` al cierre diario. Si `SPY > EMA50` y `SPY > SMA200` $\to$ Régimen `BULL`. Si no $\to$ Régimen `BEAR`.
- **Comportamiento en BEAR:** Rota el **100% de la cartera a Efectivo Remunerado / T-Bills (~4.5% anual en SGOV)**. No realiza ventas en corto (*shorting*).
- **Ranking Transversal de Momentum:** En régimen alcista, calcula la rentabilidad a 45 días hábiles sobre el universo oficial de 14 activos. Filtra los activos con retorno positivo que coticen por encima de su $\text{EMA}_{25}$.
- **Selección y Sizing:** Selecciona los **Top 2 activos líderes** y asigna el **50% del capital a cada uno**, erradicando el *cash drag*.
- **Salida Asimétrica (Let Winners Run):** Sin Take Profit fijo. Mantiene la posición mientras el precio cierre por encima de la $\text{EMA}_{25}$. Si cruza a la baja o si transcurren 30 días hábiles (`max_holding_days = 30`), la posición se liquida en la apertura siguiente.

### 3. El Universo Oficial de Activos (14 Instrumentos)
- **Índices Macro:** `SPY`, `QQQ`.
- **Megacap Tech:** `AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, `GOOGL`.
- **Diversificación Multi-Sectorial:** `JPM` (Finanzas), `LLY` (Farmacéutica/Salud), `XOM` (Energía integrada), `COST` (Consumo básico).
- **Metales Preciosos Físicos:** `GLD` (Oro con respaldo en bóvedas, proxy `GLDM` para cuentas pequeñas), `SLV` (Plata física).
- **Exclusiones Explícitas:** Se prohibieron los fondos de futuros sintéticos a corto plazo (`USO`, `UNG`) debido al desgaste estructural por contango (*Negative Roll Yield*). Se prohibió el *shorting* en acciones por restricciones normativas (Reg T en Cash), costos de préstamo y riesgo de *short squeeze*.

### 4. Pipeline de Noticias y FinBERT
- **Ingesta Multi-Fuente:** SEC EDGAR Form 8-K (hechos esenciales regulatorios CIK oficiales), Yahoo Finance RSS en tiempo real y cables históricos.
- **Inferencia FinBERT:** Modelo `ProsusAI/finbert` ejecutado en PC de escritorio (`JOAPC`), produciendo **7.490 observaciones diarias** (2024–2026) alineadas a las 15:45 ET.
- **Regla de Veto:** Si `negative_share >= 0.35` en la ventana rodante de 24h, la entrada en ese activo queda vetada automáticamente.

### 5. Resultados Empíricos del Benchmark (2020–2022 y 2025)

#### A. Prueba de Estrés Fuera de Muestra (Out-of-Sample: 2020 a 2022 con 10.962 noticias FinBERT)
- **Año 2020 (Crash COVID + Rebote Explosivo):**
  - SPY Buy & Hold: +15.56%
  - S3 Baseline: +9.83%
  - **S5 v1.2.0 Oficial: +18.24%** (Alpha vs SPY: **+2.68%** | Sharpe: 0.60 | MaxDD: 24.10% | WinRate: 48.1%)
- **Año 2021 (Mercado Alcista de Renta Variable):**
  - SPY Buy & Hold: +26.55%
  - S3 Baseline: -2.75%
  - **S5 v1.2.0 Oficial: +14.08%** (Ganancia neta positiva | Sharpe: 0.51 | MaxDD: 23.41% | WinRate: 45.2%)
- **Año 2022 (Mercado Bajista Severo - Inflación, Suba de Tasas y Guerra):**
  - SPY Buy & Hold: **-19.71%** (Desplome bursátil general)
  - S3 Baseline: -7.33%
  - **S5 v1.2.0 Oficial: -1.43%** (Alpha vs SPY: **+18.28%** | Sharpe: -0.22 | MaxDD: 10.44% | WinRate: 55.9%)
- **Trienio 2020–2022 Acumulado Compuesto (3 Años Continuos):**
  - SPY Buy & Hold: **+18.20%**
  - S3 Baseline: **-0.33%** (Pérdida acumulada)
  - **S5 v1.2.0 Oficial: +37.58%** (Alpha vs SPY: **+19.38%** — ¡Más del doble que el mercado!)

#### B. Período 2025 (Año Completo con 7.490 noticias FinBERT)
- **S&P 500 Buy & Hold:** +15.70% Retorno | 1.15 Sharpe | -9.80% MaxDD.
- **S3 Baseline (Pullbacks a EMA20 con TP 2R):** +5.16% Retorno | 1.05 Sharpe | -2.59% MaxDD.
- **S5 v1.2.0 (Multi-Sectorial + Metales Preciosos + Veto FinBERT):**
  - **Retorno Neto Anual:** **+81.85%** (**+66.15% de Alpha**).
  - **Ratio de Sharpe:** **2.91**.
  - **Máximo Drawdown (MaxDD):** **-7.57%** (menor que el mercado).
  - **Tasa de Aciertos (Win Rate):** **71.4%**.
  - **Factor de Beneficio (Profit Factor):** **5.28**.
  - **Operaciones Totales:** 21 trades en el año.

---

## MANDATO DE AUDITORÍA: TU MISIÓN

Debes realizar un análisis exhaustivo, crítico y estructurado del sistema, respondiendo con rigor técnico a cada una de las siguientes 5 secciones:

### SECCIÓN 1: OPINIÓN SOBRE EL SISTEMA DE TOMA DE DECISIONES
1. Evalúa la validez conceptual de la transición de **S3 (Trend Pullback)** a **S5 (Dual Momentum Leader)**. ¿Es una mejora estructural fundamentada en la literatura financiera cuantitativa (Jegadeesh & Titman, Gary Antonacci, Asness/AQR) o un simple ajuste sobre datos históricos?
2. Analiza la interacción entre el **Filtro de Régimen Macro de SPY (EMA50/SMA200)** y el **Ranking de Momentum a 45 días**. ¿Qué fortalezas y debilidades matemáticas observas en esta arquitectura de doble filtro?
3. Evalúa la regla de salida mediante **Trailing Stop en EMA(25)** y límite duro de **30 días de retención**. ¿Cómo afecta a la esperanza matemática (*expected payoff*) frente a salidas fijas por múltiplos de ATR o metas de beneficio rígidas?
4. **Evaluación de la Asimetría Multianual (+37.58% vs +18.20% en 2020–2022, y +81.85% vs +15.70% en 2025):** ¿Consideras que la capacidad del sistema de batir al índice en 3 de los 4 años evaluados (y acumular más del doble que el mercado en el trienio de COVID e inflación) demuestra solidez estadística estructural, o existen sesgos de dispersión transversal que favorecen temporalmente a este universo de 14 activos?

### SECCIÓN 2: IDENTIFICACIÓN DE RIESGOS OCULTOS Y VULNERABILIDADES
Identifica y califica la severidad (Baja, Media, Alta, Crítica) de los siguientes riesgos en la arquitectura actual:
1. **Evaluación Anti-Sobreajuste a la Luz de los Datos (2020, 2021, 2022 y 2025):**
   - Habiendo analizado los resultados empíricos reales de 2020 (+18.24% vs +15.56%), 2021 (+14.08% vs +26.55%), 2022 (-1.43% vs -19.71%) y 2025 (+81.85% vs +15.70%):
   - ¿Consideras que la hipótesis de sobreajuste (*curve fitting*) queda suficientemente mitigada, o detectas aún sesgos de selección (*survivorship bias*) en el universo de 14 activos?
   - ¿Qué lectura haces de 2021, donde el mercado alcista ganó +26.55% y S5 rindió +14.08%? ¿Es esa captura parcial en rallies alcistas parabólicos el coste estructural inevitable (y aceptable) para lograr la protección casi total observada en el mercado bajista de 2022 (-1.43% vs -19.71%)?
2. **Análisis del Perfil de Drawdown:**
   - En 2020 (MaxDD -24.10%) y 2021 (MaxDD -23.41%) las correcciones intermedias fueron notables, mientras que en 2022 (MaxDD -10.44%) y 2025 (MaxDD -7.57%) se mantuvieron fuertemente controladas.
   - ¿A qué atribuyes esta dispersión del drawdown? ¿Qué mecanismos recomiendas para amortiguar las caídas transitorias durante choques repentinos de volatilidad (como el flash crash de marzo de 2020)?
3. **Riesgo de Concentración Extrema (50% por activo en Top 2):** ¿Cuál es el peor escenario matemático si uno de los dos líderes sufre un cisne negro (*overnight gap down* de -20% por litigio, fraude o fallo clínico) antes de que el trailing stop diario pueda ejecutarse a las 15:45 ET?
4. **Fricción Operativa y Deslizamiento (Slippage):** En la operativa real con Alpaca en una cuenta pequeña (~$2.000 USD), ¿qué impacto real estimas entre la señal calculada a las 15:45 ET y la ejecución de órdenes Market-On-Close o limit brackets?
5. **Vulnerabilidades del Pipeline NLP / FinBERT:**
   - La ventana rodante es de 24h a las 15:45 ET. ¿Qué ocurre si un evento de quiebre se publica a las 15:50 ET o durante el fin de semana?
   - ¿Qué riesgo de falsos positivos (vetar a un líder que publica un titular alarmista que el mercado digiere favorablemente) o falsos negativos (un 8-K ambiguo que FinBERT clasifica como neutral) identificas?

### SECCIÓN 3: EVALUACIÓN DE DILEMAS CRÍTICOS
1. **Política de Shorting:** ¿Respaldas la decisión de prohibir el shorting y rotar al 100% en Efectivo Remunerado / T-Bills (~4.5% en SGOV) durante regímenes bajistas (que en 2022 protegió el capital con un retorno de -1.43% vs -19.71% de SPY), o consideras que el sistema debería incorporar coberturas activas con ETFs inversos? Justifica matemáticamente.
2. **Política de Commodities:** ¿Respaldas la distinción entre metales físicos (`GLD`/`SLV`) y fondos de futuros sintéticos (`USO`/`UNG`) basada en el contango y roll decay? ¿Recomiendas añadir alguna otra clase de activo descorrelacionada?

### SECCIÓN 4: PROPUESTAS DE MEJORA Y PRÓXIMOS PASOS CUANTITATIVOS
Presenta recomendaciones concretas, accionables y priorizadas para robustecer el sistema:
1. **Sizing Dinámico y Volatility Targeting:** ¿Cómo sustituir la asignación fija del 50/50 por un dimensionamiento basado en volatilidad inversa (ATR o varianza) para balancear activos de alta volatilidad (como `SLV` o `NVDA`) frente a instrumentos estables (como `GLD` o `COST`)?
2. **Momentum Multi-Horizonte (Ensemble):** ¿Recomiendas promediar o combinar ventanas de momentum (ej. 21d, 45d, 90d) para suavizar la rotación y mejorar la captura en años tendenciales continuos como 2021?
3. **Mecanismos de Protección Intradiaria:** ¿Cómo mitigar el riesgo de *overnight gap* en posiciones concentradas sin destruir el rendimiento con paradas prematuras?

### SECCIÓN 5: SCORECARD INSTITUCIONAL Y VEREDICTO FINAL
Asigna una calificación de 1 a 10 (donde 10 es nivel fondo sistemático de élite) a cada uno de los siguientes pilares:
- **Rigor Matemático y Conceptual de la Estrategia:** [ /10]
- **Arquitectura de Riesgo y Seguridad Operativa (Guardian, Fail-Closed):** [ /10]
- **Ingesta de Datos y Procesamiento de Sentimiento (FinBERT, SEC 8-K):** [ /10]
- **Robustez Frente a Condiciones Adversas del Mercado (Prueba 2020-2022):** [ /10]
- **Facilidad de Implementación y Mantenibilidad del Código:** [ /10]

**Veredicto Final:** Emite una conclusión ejecutiva dictaminando si el sistema está listo para operar en **Paper Trading continuo** y cuáles son los **3 requisitos sine qua non** que debe cumplir antes de que el usuario deposite capital real en Alpaca.
```
