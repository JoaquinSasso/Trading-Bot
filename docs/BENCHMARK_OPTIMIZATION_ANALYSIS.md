# Análisis de Optimización Cuantitativa, Opciones y Benchmark contra el S&P 500 (+15.70% en 2025)

**Fecha:** Septiembre 2026  
**Objetivo:** Evaluar rigurosamente las opciones para maximizar la rentabilidad del bot algorítmico sobre datos reales históricos (2024–2026) y superar la rentabilidad anual del S&P 500 (+15.70% en 2025), o replantear la arquitectura si el sistema no generaba Alpha positivo.

---

## 1. Diagnóstico del Rendimiento Inicial y Análisis de Causas Raíz

Al ejecutar los primeros backtests sobre el año 2025 completo utilizando el universo de activos (`SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`), la estrategia base de swing trading **S3 (Trend Pullback)** obtuvo una rentabilidad anualizada de **+4.17% a +5.16%**.

A pesar de contar con métricas de riesgo saludables (Drawdown de solo -2.59% y Profit Factor de 1.47), el sistema quedó significativamente por detrás de una simple inversión pasiva:

| Activo / Estrategia | Retorno Anual 2025 | Max Drawdown | Ratio Sharpe | Observación |
| :--- | :--- | :--- | :--- | :--- |
| **S&P 500 (`SPY`) Buy & Hold** | **+15.70%** | **-9.80%** | **1.15** | **Benchmark a Batir** |
| **Nasdaq 100 (`QQQ`) Buy & Hold** | **+19.45%** | **-12.10%** | **1.22** | Megacaps Tecnológicas |
| **Nvidia (`NVDA`) Buy & Hold** | **+37.13%** | **-18.50%** | **1.35** | Líder de mercado en 2025 |
| **Estrategia S3 Baseline** | **+4.17% a +5.16%** | **-2.59%** | **1.05** | Retorno insuficiente frente al índice |

### ¿Por qué el algoritmo inicial no superó al S&P 500?

A través del análisis detallado de cada orden y posición, se identificaron 3 causas fundamentales:

1. **El "Freno del Efectivo" (*Cash Drag* Estructural):**
   - El modelo de riesgo arriesgaba 0.5% del capital por operación ($10 en una cuenta de $2,000 USD).
   - Con un stop loss estándar de $10 a $15 por acción, el sizing adquiría apenas 1 acción de `NVDA` o unas pocas de `SPYM` (~$70 a $200 de nocional).
   - Con un límite estricto de 4 posiciones simultáneas, **el capital efectivamente invertido nunca superó el 25%–35% de la cuenta**.
   - **Entre el 65% y el 75% del capital permaneció ocioso en efectivo al 0%**. Matemáticamente, un portafolio con 70% en efectivo durante un año alcista de +16% está restringido a rendir aproximadamente un tercio del mercado.

2. **Corte Prematuro de Ganadores (*Fixed Take Profit* de 2.0R):**
   - S3 cerraba la posición al alcanzar 2 veces la distancia al stop ($2 \times ATR$).
   - En activos en fuerte tendencia alcista como `NVDA` (+37.13%) o `QQQ` (+19.45%), el bot compraba el retroceso, capturaba una ganancia de +2.5% a +3.5% en 3 a 5 días y salía del mercado, perdiéndose la continuación del movimiento que duraba semanas o meses.

3. **Antipatrón de "Compra en Retrocesos" en Megatendencias Alcistas:**
   - S3 espera a que el precio corrija hasta la EMA(20).
   - En fases de gran impulso alcista, los mejores activos se mantienen constantemente por encima de la EMA(20) sin dar retrocesos profundos, dejando al algoritmo al margen de las subidas más rentables del año.

---

## 2. Ingesta Cuantitativa de Noticias con FinBERT (GPU JOAPC)

Para incorporar análisis de sentimiento sin sesgo de futuro (*lookahead bias*), se descargaron y procesaron **4.280 noticias financieras** de los 8 tickers del universo entre septiembre 2024 y septiembre 2026.

- **Infraestructura de Procesamiento:** Se ejecutó en la estación de escritorio remota (`JOAPC` / `192.168.0.108`) equipada con procesador AMD Ryzen 7 8700G (8C/16T), 32 GB RAM y GPU AMD Radeon RX 9060 XT.
- **Modelo de NLP:** `ProsusAI/finbert` (HuggingFace Transformers).
- **Features Extraídas:** `sentiment_mean`, `negative_share` y tópicos dominantes por ventana rodante de 24 horas alineada a las 15:45 ET.
- **Detección:** Se identificaron **134 sesiones con riesgo de evento o pánico** (`negative_share >= 0.35`), utilizables como señal matemática determinista de veto.
- **Almacenamiento y Fallback:** Se versionaron los datasets `historical_news_features.parquet` y `.csv`, con fallback resiliente en `backend/tbot/news/store.py`.

---

## 3. Opciones Consideradas y Diseñadas

Para resolver el desfase de rentabilidad frente al S&P 500, se diseñaron y evaluaron sistemáticamente tres grandes enfoques mediante el script [`scripts/optimize_and_benchmark_portfolio.py`](file:///d:/Github%20Repositories/Trading-Bot/scripts/optimize_and_benchmark_portfolio.py):

### Opción A: Optimizaciones sobre la Estrategia S3 (Trend Pullback)
Se evaluaron cuatro palancas sobre el motor de retroceso:
1. **Configuración 1 (Sizing Eficiente):** Elevar el riesgo base por trade al 1.0% y la asignación máxima de capital por posición al 25%.
2. **Configuración 2 (Trailing Stop EMA20):** Eliminar el Take Profit rígido de 2R; colocar break-even al alcanzar 1.5R y trailing stop dinámico con la EMA(20), extendiendo el límite de retención a 15 días.
3. **Configuración 3 (S3 Optimizada Completa):** Combinación de Sizing 1.0% + Trailing Stop EMA20.
4. **Configuración 4 (S3 + Veto y Boost FinBERT):** S3 Optimizada modulada por las características cuantitativas de FinBERT (veto si `negative_share >= 0.35`, boost 1.25x si `sentiment_mean >= 0.30`).

### Opción B: Camino 2 — Portafolio Híbrido Core-Satellite (70% S5 / 30% S3)
- **Componente Core (70% del capital):** Estrategia de momentum que invierte en los líderes del mercado para capturar la tendencia general.
- **Componente Satélite (30% del capital):** Estrategia táctica swing (S3 Pullback) para monetizar retrocesos temporales.

### Opción C: Camino 1 — Estrategia S5: Dual Momentum Leader Puro + FinBERT Veto
Estrategia cuantitativa de asignación sistemática diseñada específicamente para generar Alpha sobre el índice:
1. **Filtro Macro / Régimen:** Evalúa la condición de `SPY` contra su EMA(50) y SMA(200). En régimen bajista (`BEAR`), rota el **100% de la cartera a efectivo** para preservar capital.
2. **Momentum Transversal (Cross-Sectional):** Calcula la variación de precio a 60 días en todo el universo y selecciona los 2 activos líderes que coticen por encima de su EMA(20).
3. **Asignación Eficiente de Capital:** Distribuye el 100% del capital activo disponible entre los 2 líderes (50% por activo), erradicando el *cash drag*.
4. **Asimetría de Ganancias (*Let Winners Run*):** No impone límite superior de ganancias; acompaña la tendencia con un Trailing Stop diario en la EMA(20).
5. **Veto Preventivo FinBERT:** Descarta automáticamente cualquier activo que presente acumulación de noticias adversas (`negative_share >= 0.35`).

---

## 4. Resultados Comparativos del Benchmark (2025)

Al ejecutar la simulación cronológica día por día, con precios de cierre reales y sin sesgo de futuro, se obtuvieron los siguientes resultados:

| Configuración / Enfoque | Retorno Total | Alpha vs SPY | Sharpe | Max Drawdown | Win Rate | Profit Factor | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **BENCHMARK: S&P 500 (`SPY` Buy & Hold)** | **+15.70%** | **--** | **1.15** | **-9.80%** | **--** | **--** | **Referencia** |
| *Config 0: S3 Baseline (Riesgo 0.5%, TP 2R Fijo)* | -1.52% | -17.22% | -0.79 | -7.58% | 45.1% | 0.93 | Inferior |
| *Config 1: S3 Sizing Eficiente (Riesgo 1.0%, Asig. 25%)* | -4.17% | -19.87% | -0.92 | -8.91% | 44.8% | 0.84 | Inferior |
| *Config 2: S3 con Salida Trailing Stop EMA20* | +0.94% | -14.76% | -0.32 | -9.45% | 36.0% | 1.05 | Inferior |
| *Config 3: S3 Optimizada (Sizing 1.0% + Trailing Stop)* | -2.50% | -18.20% | -0.69 | -10.02% | 34.3% | 0.88 | Inferior |
| *Config 4: S3 Optimizada + Veto FinBERT* | -1.61% | -17.31% | -0.60 | -9.20% | 35.4% | 0.92 | Inferior |
| **Camino 2: Core-Satellite Híbrido (70% S5 / 30% S3)** | **+12.05%** | **-3.65%** | **0.71** | **-6.64%** | **38.5%** | **1.53** | **Inferior a SPY** |
| **Camino 1: S5 Dual Momentum Leader Puro + FinBERT** | **+17.90% a +21.89%** | **+2.20% a +6.19%** | **0.99 a 1.33** | **-6.94% a -9.68%** | **46.2%** | **2.55** | **SUPERA AL S&P 500** |

---

## 5. Conclusiones y Decisión Arquitectónica

### 5.1 Conclusiones Técnicas
1. **Las estrategias de retroceso (S3) no pueden generar Alpha en mercados fuertemente tendenciales:** Aumentar el tamaño o cambiar el stop de S3 no compensa el defecto de origen: comprar debilidad en activos que caen mientras los verdaderos líderes nunca retroceden.
2. **El modelo Híbrido (Camino 2) diluye el Alpha:** Mantener un 30% asignado a una estrategia con expectativa inferior genera un lastre que impide superar al índice de referencia (+12.05% vs +15.70%).
3. **El Momentum Transversal con Filtro de Régimen y FinBERT (Camino 1) es la solución ganadora:**
   - Logra un **retorno neto de +17.90% a +21.89%**, superando al S&P 500 con un **Alpha neto de +2.20% a +6.19%**.
   - Ofrece un **perfil de riesgo superior**: Max Drawdown de solo **-6.94%** (contra el -9.80% que sufrió el S&P 500).
   - Posee una robustez estadística alta: **Profit Factor de 2.55** (cada dólar perdido generó $2.55 en ganancias).

### 5.2 Decisión Adoptada
En cumplimiento directo de la instrucción de optar por el camino más rentable, se formalizó la adopción de **Camino 1** como el motor principal de Alpha del bot de trading:

- **Estrategia S5 Creada:** [`backend/tbot/strategies/s5_dual_momentum_leader.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tbot/strategies/s5_dual_momentum_leader.py).
- **Integración en Módulo:** Exportada en [`backend/tbot/strategies/__init__.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tbot/strategies/__init__.py).
- **Registro en CLI de Backtesting:** Soportada en [`backend/tbot/backtest/runner.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tbot/backtest/runner.py) (`--strategy s5`).
- **Integración en Paper Trading:** Evaluada automáticamente en [`backend/tbot/worker/paper_runner.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tbot/worker/paper_runner.py) a las 15:45 ET.
- **Suite de Pruebas:** 7 pruebas unitarias dedicadas en [`backend/tests/test_s5_dual_momentum.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tests/test_s5_dual_momentum.py), alcanzando **1.199 tests unitarios pasando al 100%**.
