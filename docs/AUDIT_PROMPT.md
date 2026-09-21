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

### 5. Resultados Empíricos del Benchmark (Año 2025 Completo)
- **S&P 500 Buy & Hold (Benchmark):** +15.70% Retorno | 1.15 Sharpe | -9.80% MaxDD.
- **Estrategia S3 Baseline (Pullbacks a EMA20 con TP 2R):** +5.16% Retorno | 1.05 Sharpe | -2.59% MaxDD (sufrió severo *cash drag* con 70% en efectivo y corte prematuro de ganadores).
- **S5 v1.2.0 (Multi-Sectorial + Metales Preciosos + Veto FinBERT):**
  - **Retorno Neto Anual:** **+81.85%** (**+66.15% de Alpha**).
  - **Ratio de Sharpe:** **2.91**.
  - **Máximo Drawdown (MaxDD):** **-7.57%** (inferior al -9.80% del mercado).
  - **Tasa de Aciertos (Win Rate):** **71.4%**.
  - **Factor de Beneficio (Profit Factor):** **5.28**.
  - **Operaciones Totales:** 21 trades en el año (baja rotación, alta convicción).

---

## MANDATO DE AUDITORÍA: TU MISIÓN

Debes realizar un análisis exhaustivo, crítico y estructurado del sistema, respondiendo con rigor técnico a cada una de las siguientes 5 secciones:

### SECCIÓN 1: OPINIÓN SOBRE EL SISTEMA DE TOMA DE DECISIONES
1. Evalúa la validez conceptual de la transición de **S3 (Trend Pullback)** a **S5 (Dual Momentum Leader)**. ¿Es una mejora estructural fundamentada en la literatura financiera (Jegadeesh & Titman, Gary Antonacci, Asness) o un simple ajuste de conveniencia sobre un año alcista?
2. Analiza la interacción entre el **Filtro de Régimen Macro de SPY** y el **Ranking de Momentum a 45 días**. ¿Qué fortalezas y debilidades matemáticas observas en esta combinación?
3. Evalúa la regla de salida mediante **Trailing Stop en EMA(25)** y límite duro de **30 días de retención**. ¿Cómo afecta a la esperanza matemática (*expected payoff*) frente a salidas fijas por múltiplos de ATR?

### SECCIÓN 2: IDENTIFICACIÓN DE RIESGOS OCULTOS Y VULNERABILIDADES
Identifica y califica la severidad (Baja, Media, Alta, Crítica) de los siguientes riesgos en la arquitectura actual:
1. **Riesgo de Sobreajuste (Overfitting / Curve Fitting):** ¿Hasta qué punto la combinación de parámetros (Lookback 45d, EMA 25, 30d holding, universo de 14 activos) podría estar sobreoptimizada para la dinámica de 2024–2025 (mega-rally de Nvidia, oro y plata)? ¿Cómo se comportaría este sistema en un mercado como 2022 (inflación agresiva y caída simultánea de bonos y acciones) o 2018 (mercado lateral picado / *choppy market*)?
2. **Riesgo de Concentración Extrema (50% por activo en Top 2):** ¿Cuál es el peor escenario matemático si uno de los dos líderes sufre un evento de cola o cisne negro (*gap down* nocturno de -25% por fraude contable, fallo de FDA o quiebra) antes de que el trailing stop diario pueda ejecutarse a las 15:45 ET?
3. **Riesgo de Fricción Operativa y Deslizamiento (Slippage):** En la práctica real con Alpaca, ¿qué impacto tendrá el retraso entre la señal calculada a las 15:45 ET y la ejecución de órdenes Market/Limit previas al cierre de las 16:00 ET?
4. **Vulnerabilidades del Pipeline NLP / FinBERT:**
   - La ventana rodante es de 24h a las 15:45 ET. ¿Qué ocurre si un evento de quiebre se publica a las 15:50 ET o durante el fin de semana?
   - ¿Qué riesgo de falsos positivos (vetar a un líder que publica un titular alarmista que el mercado digiere favorablemente) o falsos negativos (un 8-K ambiguo que FinBERT clasifica como neutral) identificas?

### SECCIÓN 3: EVALUACIÓN DE DILEMAS CRÍTICOS
1. **Política de Shorting:** ¿Estás de acuerdo con prohibir el shorting y rotar al 100% en Efectivo Remunerado / T-Bills (~4.5% en SGOV) durante regímenes bajistas, o consideras que el sistema debería incorporar coberturas (*hedging*) activas? Justifica matemáticamente.
2. **Política de Commodities:** ¿Respaldas la distinción entre metales físicos (`GLD`/`SLV`) y futuros sintéticos (`USO`/`UNG`) basada en el contango y roll decay? ¿Recomiendas añadir alguna otra clase de activo?

### SECCIÓN 4: PROPUESTAS DE MEJORA Y PRÓXIMOS PASOS CUANTITATIVOS
Presenta recomendaciones concretas, accionables y priorizadas para robustecer el sistema:
1. **Sizing Dinámico y Volatility Targeting:** ¿Cómo se podría sustituir la asignación fija del 50/50 por un dimensionamiento basado en volatilidad inversa (ATR o varianza) para evitar que activos volátiles como `SLV` dominen el riesgo del portafolio?
2. **Momentum Multi-Horizonte:** ¿Recomiendas promediar o combinar ventanas de momentum (ej. 20d, 45d, 90d) para reducir el riesgo de seleccionar un pico temporal de momentum?
3. **Mecanismos de Protección Intradiaria:** ¿Cómo mitigar el riesgo de *overnight gap* en posiciones concentradas?

### SECCIÓN 5: SCORECARD INSTITUCIONAL Y VEREDICTO FINAL
Asigna una calificación de 1 a 10 (donde 10 es nivel fondo institucional) a cada uno de los siguientes pilares:
- **Rigor Matemático y Conceptual de la Estrategia:** [ /10]
- **Arquitectura de Riesgo y Seguridad Operativa (Guardian, Fail-Closed):** [ /10]
- **Ingesta de Datos y Procesamiento de Sentimiento (FinBERT, SEC 8-K):** [ /10]
- **Robustez Frente a Condiciones Adversas del Mercado:** [ /10]
- **Facilidad de Implementación y Mantenibilidad:** [ /10]

**Veredicto Final:** Emite una conclusión ejecutiva dictaminando si el sistema está listo para operar en **Paper Trading continuo** y cuáles son los **3 requisitos sine qua non** que debe cumplir antes de que el usuario deposite capital real en Alpaca.
```
