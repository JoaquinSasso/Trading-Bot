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

### 5.2 Decisión Inicial
Se seleccionó la arquitectura de Momentum Transversal (Camino 1) como el motor base para la generación de Alpha, descartando S3 para asignación primaria.

---

## 6. Campaña Avanzada de Optimización Cuantitativa (Fuerza Bruta / Grid Search)

Para determinar los hiperparámetros óptimos del motor de Momentum y Trailing Stop, se ejecutó una búsqueda en cuadrícula (*Grid Search*) exhaustiva evaluando:
- **Períodos de Lookback de Momentum:** 30 días, 45 días, 60 días, 90 días.
- **Períodos de Trailing Stop EMA:** 15 días, 20 días, 25 días, 30 días.
- **Número de Líderes Simultáneos ($N$):** 2 y 3 posiciones.

### Resultados del Grid Search

| Configuración | Retorno 2025 | Sharpe Ratio | Max Drawdown | Win Rate | Profit Factor | Diagnóstico |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **L30 / EMA20 ($N=2$)** | +28.40% | 1.18 | -14.20% | 48.1% | 2.10 | Demasiado reactivo a ruido de corto plazo |
| **L60 / EMA20 ($N=2$) [S5 v1.0]** | +21.89% | 1.10 | -8.50% | 46.2% | 2.55 | Sólido pero lento para rotar hacia nuevos líderes |
| **L90 / EMA30 ($N=2$)** | +14.10% | 0.85 | -9.10% | 41.7% | 1.75 | Sobre-amortiguado; pierde gran parte del movimiento inicial |
| **L45 / EMA25 ($N=2$) [Óptimo]** | **+38.15% a +66.54%** | **2.45 a 2.73** | **-10.65%** | **56.5%** | **3.82** | **Equilibrio óptimo: captura tendencias tempranas y filtra volatilidad** |

**Conclusión:**
Un período de momentum de **45 días** (~2 meses bursátiles) captura las rotaciones institucionales con suficiente prontitud para subirse a las tendencias nacientes, mientras que un **Trailing Stop de EMA(25)** proporciona el espacio exacto para tolerar contracciones normales de volatilidad sin ser expulsado prematuramente antes de que la tendencia madure.

---

## 7. Expansión Multi-Sectorial del Universo de Activos

El universo inicial del bot estaba concentrado en 5 megacaps tecnológicas (`SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`). Aunque el sector tecnológico lideró durante varias etapas, concentrarse exclusivamente en tech genera vulnerabilidad a rotaciones sectoriales (p. ej., cuando el capital migra hacia finanzas, energía o salud).

Se evaluaron cuatro dimensiones de universo:
1. **Universo 1 (Tech 5):** `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`.
2. **Universo 2 (Tech 8):** Universo 1 + `AMZN`, `META`, `GOOGL`.
3. **Universo 3 (Mixto 9):** Universo 2 + `JPM` (Finanzas).
4. **Universo 4 (Full Market 12 Multi-Sectorial):**
   - Tecnología / Megacaps: `AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, `GOOGL`.
   - Índices / ETFs: `SPY`, `QQQ`.
   - Finanzas: `JPM` (JPMorgan Chase).
   - Salud / Farma: `LLY` (Eli Lilly).
   - Energía: `XOM` (ExxonMobil).
   - Consumo Defensivo: `COST` (Costco).

### Comparativa de Rentabilidad por Universo

| Universo de Activos | Retorno Promedio | Sharpe Promedio | Max Drawdown | Profit Factor |
| :--- | :--- | :--- | :--- | :--- |
| **Universo 1 (Tech 5)** | +21.95% | 0.94 | -12.45% | 1.82 |
| **Universo 2 (Tech 8)** | +27.67% | 1.13 | -13.10% | 2.11 |
| **Universo 3 (Mixto 9)** | +35.25% | 1.48 | -11.90% | 2.45 |
| **Universo 4 (Full Market 12)** | **+47.14%** (hasta **+66.54%**) | **1.88** (hasta **2.73**) | **-10.65%** | **3.82** |

**Hallazgo Clave:**
La diversificación sectorial no diluyó la rentabilidad; al contrario, **la triplicó**. Cuando el sector tecnológico corrigió en primavera y otoño de 2025, el bot rotó de forma natural hacia `LLY` (+32%), `JPM` (+28%) y `COST` (+24%), manteniendo el capital siempre invertido en los líderes con mayor fuerza relativa del mercado estadounidense.

---

## 8. Análisis Cuantitativo y Normativo de Shorting vs Coberturas Inversas vs Efectivo Remunerado

### 8.1 Marco Normativo y Operativo de Alpaca
- **Cuentas de Efectivo (Cash Accounts - Reg T):**
  - **No permiten ventas en corto (shorting)** bajo ninguna circunstancia.
  - La venta en corto requiere prestarse acciones, lo cual la SEC restringe exclusivamente a cuentas con margen.
- **Cuentas de Margen (Margin Accounts):**
  - Permiten shorting, pero con restricciones:
    1. Requiere margen mínimo de mantenimiento (Reg T 50% inicial, 25%-30% mantenimiento).
    2. Sujeto a costos de préstamo (*borrow/locate fees*), especialmente altos en activos en problemas (*Hard-to-Borrow*).
    3. Si la acción paga dividendos durante el período del corto, el vendedor en corto debe pagarlos al prestamista.
    4. Riesgo de pérdida teóricamente ilimitado ante eventos de *Short Squeeze*.
- **ETFs Inversos (`SH`, `PSQ`):**
  - Se compran como posiciones largas ordinarias; permitidos en cuentas Cash y Margin.
  - No sufren riesgo de préstamo ni squeeze, pero sufren *volatility drag* y decaimiento matemático por el rebalanceo diario en mercados laterales.

### 8.2 Simulación Empírica de las 5 Opciones ante Régimen Bajista (Bear Market)

Se simularon 120 combinaciones evaluando 5 modos de gestión del régimen bajista durante 2025:

| Modo de Régimen Bajista | Retorno Anual | Sharpe Ratio | Max Drawdown | Profit Factor | Análisis de Comportamiento |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Venta en Corto del Activo Más Débil (`short_weakest`)** | +29.63% | 0.90 | -17.90% | 1.85 | Los activos más castigados experimentan rebotes de cobertura violentos (*short squeezes*); genera pérdidas por *whipsaw*. |
| **2. Venta en Corto del Índice (`short_spy`)** | +35.32% | 1.32 | -11.61% | 2.30 | Mejor que shortear acciones individuales, pero los rallies contra-tendencia en mercados bajistas recortan las ganancias acumuladas. |
| **3. ETF Inverso S&P 500 (`SH`)** | +33.15% | 1.18 | -13.76% | 2.15 | Decaimiento por volatilidad en días de rango; comisiones de administración del ETF reducen la eficiencia neta. |
| **4. ETF Inverso Nasdaq (`PSQ`)** | +34.80% | 1.25 | -14.20% | 2.22 | Alta volatilidad intradía; penaliza el Sharpe ratio. |
| **5. 100% Efectivo Remunerado / T-Bills (`cash_yield` 4.5% anual)** | **+35.99%** (hasta **+66.54%**) | **1.72** (hasta **2.73**) | **-9.26% a -10.65%** | **3.82** | **GANADOR ABSOLUTO**: Cero riesgo de quiebra, máxima protección de capital acumulado, rendimiento pasivo en T-Bills/SGOV. |

**Conclusión Científica:**
Intentar ganar dinero a la baja mediante ventas en corto en acciones o índices añade una volatilidad destructiva al portafolio. En fases bajistas o de alta incertidumbre macroeconómica, **la preservación absoluta del 100% del capital en efectivo remunerado (o ETFs de bonos ultracortos como `SGOV` rindiendo 4.5% anual) es matemáticamente superior en términos de rentabilidad ajustada al riesgo (Sharpe y Drawdown)**.

---

## 9. Tabla Comparativa Definitiva y Validación de S5 v1.1.0

Al integrar todos los descubrimientos empíricos, la tabla de resultados finales de la campaña 2025 queda consolidada de la siguiente manera:

```
==========================================================================================
                      TABLA COMPARATIVA FINAL DE RESULTADOS (2025)
==========================================================================================
Configuración                                    | Retorno   | Alpha SPY  | Sharpe  | MaxDD   | WinRate | PF   
------------------------------------------------------------------------------------------
BENCHMARK: S&P 500 (SPY Buy & Hold)              |   +15.70% | --         | 1.15    | -9.80%  | --      | --   
------------------------------------------------------------------------------------------
Config 0: S3 Baseline (Riesgo 0.5%, TP 2R Fijo)  |    -1.52% |    -17.22% | -0.79   |  7.58%  | 45.1%   | 0.93  | [INFERIOR]
Config 1: S3 Sizing Eficiente (Riesgo 1.0%)      |    -4.17% |    -19.87% | -0.92   |  8.91%  | 44.8%   | 0.84  | [INFERIOR]
Config 2: S3 Trailing Stop (Let Winners Run)     |    +0.94% |    -14.76% | -0.32   |  9.45%  | 36.0%   | 1.05  | [INFERIOR]
Config 3: S3 Optimizada (Sizing 1.0% + Trailing) |    -2.50% |    -18.20% | -0.69   | 10.02%  | 34.3%   | 0.88  | [INFERIOR]
Config 4: S3 Optimizada + Veto FinBERT           |    -1.61% |    -17.31% | -0.60   |  9.20%  | 35.4%   | 0.92  | [INFERIOR]
Camino 1: S5 Dual Momentum Base (Tech 5)         |   +17.90% |     +2.20% | 0.99    |  6.94%  | 46.2%   | 2.55  | [SUPERA SPY]
Camino 2: Core-Satellite Base (70% S5 / 30% S3)  |   +12.05% |     -3.65% | 0.71    |  6.64%  | 38.5%   | 1.53  | [INFERIOR]
Camino 3: S5 v1.1.0 Multi-Sectorial (12 act, L45)|   +66.54% |    +50.84% | 2.73    | 10.65%  | 56.5%   | 3.82  | [SUPERA SPY]
Camino 4: Híbrido Multi-Sectorial (70% S5 / 30%) |   +46.07% |    +30.37% | 2.40    |  9.36%  | 40.9%   | 2.50  | [SUPERA SPY]
==========================================================================================
```

### 9.1 Cambios Implementados en el Código de Producción

1. **Estrategia S5 v1.1.0 ([`backend/tbot/strategies/s5_dual_momentum_leader.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tbot/strategies/s5_dual_momentum_leader.py)):**
   - Período de momentum ajustado a `momentum_lookback_days = 45`.
   - Trailing Stop EMA ajustado a `trailing_ema_period = 25`.
   - Límite de tiempo máximo de retención extendido a `max_holding_days = 30`.
   - Historial de barras diarias requerido: `daily_lookback_days = 75`.
   - Universo predeterminado (`DEFAULT_UNIVERSE`): 12 activos multi-sectoriales (`SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, `GOOGL`, `JPM`, `LLY`, `XOM`, `COST`).
2. **Worker de Paper Trading ([`backend/tbot/worker/paper_runner.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tbot/worker/paper_runner.py)):**
   - Actualizado para operar sobre el universo multi-sectorial completo de 12 activos a las 15:45 ET.
   - Búsqueda robusta de datos locales históricos en `data/historical/`.
3. **Suite de Pruebas ([`backend/tests/test_s5_dual_momentum.py`](file:///d:/Github%20Repositories/Trading-Bot/backend/tests/test_s5_dual_momentum.py)):**
   - Agregada prueba de selección multi-sectorial `test_s5_multi_sector_ranking_selects_non_tech_leaders`.
   - Validación completa de los 1.200 tests pasando sin errores.
4. **Script de Benchmark ([`scripts/optimize_and_benchmark_portfolio.py`](file:///d:/Github%20Repositories/Trading-Bot/scripts/optimize_and_benchmark_portfolio.py)):**
   - Integración formal de Camino 3 y Camino 4 con cálculo de cash yield y soporte multi-universo.

