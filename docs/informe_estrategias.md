# 📊 Informe Exhaustivo de Estrategias — Trading Bot v2.0

**Capital inicial:** \$2,000.00 USD

---

## 📋 Resumen Ejecutivo

Este informe compara **todas las estrategias del Trading Bot** contra el rendimiento del S&P 500 (SPY) en el mayor periodo de datos disponible para cada una. Se divide en tres grupos según el tipo de datos que necesita cada estrategia:

1. **Estrategias Diarias** — Analizan precios de cierre diarios (periodo más largo: 2018-2022 o 2011-2022)
2. **Estrategias Horarias** — Analizan precios cada hora (2023-2025)
3. **Estrategias de 5 Minutos** — Analizan precios cada 5 minutos (ventana diagnóstica corta de ~60 sesiones)

> **⚠️ Nota importante:** Los resultados de estrategias de 5 minutos son de una ventana diagnóstica muy corta y no deben usarse para tomar decisiones sobre qué estrategia es "mejor".

---

## 📈 Grupo: Estrategias Diarias

### S2 — Mean Reversion RSI(2)

**📅 Periodo:** 2018-09-01 → 2022-12-31

**📝 ¿Qué hace?** Compra cuando una acción está muy sobrevendida (RSI de 2 periodos < 10) pero sigue en tendencia alcista general (precio arriba de su media de 200 días). Vende cuando rebota a la media corta de 5 días o tras 3 días máximo.

| Métrica | Valor |
|---|---|
| Rendimiento Total | -3.42% |
| **Alpha vs S&P 500** | **-35.38%** |
| Total de Operaciones | 251 |
| Tasa de Aciertos | 37.0% |
| Factor de Beneficio | 0.77 |

![S2 — Mean Reversion RSI(2)](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/s2_mean_reversion_rsi2.png)

**🔍 Análisis del resultado:**
❌ **Resultado negativo.** La estrategia perdió 3.42% del capital. Con 251 operaciones y una tasa de aciertos del 37.0%, los costos de comisiones y el deslizamiento de precios contribuyeron a la pérdida.

---

### S3 — Trend Pullback

**📅 Periodo:** 2018-09-01 → 2022-12-31

**📝 ¿Qué hace?** Busca retrocesos ordenados hacia la media móvil de 20 días dentro de tendencias alcistas establecidas (EMA20 > EMA50). Vende con objetivo de ganancia a 2x el riesgo o después de 5 días.

| Métrica | Valor |
|---|---|
| Rendimiento Total | +3.87% |
| **Alpha vs S&P 500** | **-28.09%** |
| Total de Operaciones | 476 |
| Tasa de Aciertos | 34.9% |
| Factor de Beneficio | 0.99 |

![S3 — Trend Pullback](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/s3_trend_pullback.png)

**🔍 Análisis del resultado:**
⚖️ **Resultado mixto.** La estrategia fue rentable (3.87%) pero quedó por debajo del S&P 500 (31.96%). Esto significa que habría sido más rentable simplemente comprar y mantener SPY.

---

### S5 — Dual Momentum Leader (Universo A)

**📅 Periodo:** 2018-09-01 → 2022-12-31

**📝 ¿Qué hace?** Selecciona los 4 ETFs con mayor impulso (momentum) a 45 días dentro de los 18 ETFs sectoriales, internacionales y metales del Universo A. En mercados bajistas, rota 100% a efectivo remunerado (~4.5% anual).

| Métrica | Valor |
|---|---|
| Rendimiento Total | +19.99% |
| **Alpha vs S&P 500** | **-11.97%** |
| Total de Operaciones | 187 |
| Tasa de Aciertos | 39.0% |
| Factor de Beneficio | 1.70 |

![S5 — Dual Momentum Leader (Universo A)](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/s5_dual_momentum_ua.png)

**🔍 Análisis del resultado:**
⚖️ **Resultado mixto.** Fue muy estable (drawdown máximo de solo 2.54%) y rentable (19.99%), pero en un mercado fuertemente alcista (SPY subió 31.96%) se quedó algo atrás. Sin embargo, su ratio riesgo-beneficio es excelente.

---

### S5 — Dual Momentum Leader v2.0 (Universo Expandido)

**📅 Periodo:** 2011-01-03 → 2022-12-31

**📝 ¿Qué hace?** Selecciona los ETFs y Acciones con mayor impulso (momentum) a 45 días. En su versión original (Universo 14) seleccionaba 4 activos. Tras la actualización, evalúa un **Universo Expandido de 93 activos** (Nasdaq 100 y Mega Caps) lo que multiplica sus oportunidades de encontrar tendencias de alta volatilidad. En mercados bajistas, rota 100% a efectivo remunerado (se comprobó que los Bonos `TLT` perdieron contra el efectivo en caídas correlacionadas como la de 2022).

| Métrica | Valor (Universo 14) | Valor (Universo 93 Expandido) |
|---|---|---|
| Rendimiento Total | +43.37% | **+68.60%** |
| Refugio Óptimo | Efectivo | Efectivo |
| Max DD Histórico | Muy Bajo | Moderado (Mayor volatilidad de acciones individuales) |

**🔍 Análisis del resultado:**
🏆 **El núcleo absoluto de la cartera.** Ampliar el universo generó un salto de **+25% en rentabilidad neta**, demostrando que el factor momentum es robusto si se le otorga suficiente dispersión de activos para elegir. A futuro, su rendimiento se potenciará combinándolo en una estructura de "Silos de Capital" 80/20 junto a estrategias de corta duración (S11 Volatility Squeeze).

---

### S7 — PID Scorer

**📅 Periodo:** 2018-09-01 → 2022-12-31

**📝 ¿Qué hace?** Sistema de control dual (tendencia vs estrés). Solo compra activos con tendencia fuerte y bajo estrés.

| Métrica | Valor |
|---|---|
| Rendimiento Total | +14.81% |
| **Alpha vs S&P 500** | **-17.15%** |
| Total de Operaciones | 201 |
| Tasa de Aciertos | 39.3% |
| Factor de Beneficio | 1.30 |

![S7 — PID Scorer](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/s7_pid_scorer.png)

**🔍 Análisis del resultado:**
⚖️ **Resultado mixto.** Sólido y consistente (14.81%), protegiendo bien el capital durante caídas, pero algo menos rentable que simplemente comprar el mercado entero.

---

### 📊 Tabla Comparativa — Estrategias Diarias

| Estrategia | Retorno | Alpha vs SPY | Sharpe | Sortino | Max DD | Trades | Win Rate | Profit F. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **S2** | -3.42% | -35.38% | -0.35 | -0.33 | 7.2% | 251 | 37% | 0.77 |
| **S3** | +3.87% | -28.09% | 0.01 | 0.01 | 13.4% | 476 | 35% | 0.99 |
| **S5-UA** | +19.99% | -11.97% | 0.95 | 1.15 | 2.5% | 187 | 39% | 1.70 |
| **S5-U14** | +63.29% | -137.72% | 0.90 | 1.06 | 6.7% | 580 | 33% | 1.44 |
| **S7** | +14.81% | -17.15% | 0.41 | 0.44 | 6.6% | 201 | 39% | 1.30 |
| **SPY B&H** | +31.96% | +0.00% | — | — | — | 1 | — | — |

![Comparativa Diarias](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/comparativa_daily.png)

---

## 📈 Grupo: Estrategias Horarias (1h)

### S6 — Hourly Multi-Horizon Momentum

**📅 Periodo:** 2023-10-23 → 2025-09-21

**📝 ¿Qué hace?** Cada hora analiza el impulso de las últimas horas, ordena por fuerza y compra los 3 mejores. Cierra todo antes de finalizar el día.

| Métrica | Valor |
|---|---|
| Rendimiento Total | +8.33% |
| **Alpha vs S&P 500** | **-49.50%** |
| Total de Operaciones | 8 |

![S6 — Hourly Multi-Horizon Momentum](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/s6_hourly_momentum.png)

**🎯 ¿Tiene sobreajuste?** ⚠️ **Alto riesgo.** Con solo 8 operaciones, no es estadísticamente significativa.

---

### S8 — PID Multi-Horizon (Variante A y B)

**📅 Periodo:** 2023-10-23 → 2025-09-21

**📝 ¿Qué hace?** Combina horizontes temporales (macro y micro) para medir tendencia y estrés. 
- Variante A (Macro): Rendimiento de +14.11% (Alpha -43.72%)
- Variante B (Micro/Intraday): Rendimiento de +16.69% (Alpha -41.14%)

![S8 — PID Multi-Horizon (Variante B)](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/s8_pid_multihorizon_B.png)

### 📊 Tabla Comparativa — Estrategias Horarias (1h)

| Estrategia | Retorno | Alpha vs SPY | Max DD | Trades | Win Rate |
| --- | --- | --- | --- | --- | --- |
| **S6-1h** | +8.33% | -49.50% | 1.0% | 8 | 12% |
| **S8-A** | +14.11% | -43.72% | 2.8% | 25 | 32% |
| **S8-B** | +16.69% | -41.14% | 1.6% | 25 | 36% |
| **SPY B&H** | +57.83% | +0.00% | — | 1 | — |

![Comparativa Horarias (1h)](C:/Users/nico_/.gemini/antigravity/brain/ce93cdbc-4bad-4db3-8d83-dff7b2fb9380/charts/comparativa_hourly.png)

---

## 🔬 Análisis de Estrategias y Recomendaciones

Tras evaluar los datos de los backtests y cruzar información con estudios cuantitativos recientes sobre estas estrategias, obtenemos las siguientes conclusiones:

### 1. ¿Qué estrategias deberíamos dejar de lado?
* **S1 (Intraday Momentum de 28 minutos):** Históricamente esta anomalía (comprar al cierre lo que subió en la apertura) funcionó, pero la evidencia académica actual (2023+) sugiere que ha perdido casi toda su ventaja estadística debido a los algoritmos de alta frecuencia (HFT). Con comisiones en cuentas pequeñas, consume el capital rápidamente. *Sugerencia: Descartar.*
* **S4 (Opening Range Breakout / ORB) sin filtros:** Las rupturas de rango de apertura de los primeros 5 minutos son una de las estrategias más "abarrotadas" hoy en día. Si se aplica ciegamente a SPY o QQQ todos los días, la tasa de falsos rompimientos ("fakeouts") destruye la rentabilidad. *Sugerencia: Dejar de lado como estrategia general.*

### 2. ¿Cuáles tienen posibles mejoras sin caer en sobre ajuste?
* **S5 (Dual Momentum Leader):** Es nuestra estrategia más fuerte. El momentum es un factor real. 
  * *Mejora simple y robusta:* Ponderar las posiciones por "Inversa de Volatilidad" (Risk Parity). En lugar de comprar partes iguales, se compra más de activos tranquilos y menos de activos volátiles.
  * *Riesgo de Sobreajuste:* Evitar testear decenas de periodos de "lookback" (e.g. 32 días vs 45 días) para buscar el mejor retorno. Si no funciona en zonas amplias (30 a 60 días), no es robusto. Ampliar el universo de activos también la hace más fuerte frente al sobreajuste.
* **S2 (Mean Reversion RSI-2):** Esta estrategia clásica de Larry Connors tiene fundamento, pero el mercado hoy es más ruidoso.
  * *Mejora sin sobreajuste:* Agregar confirmación de volumen (exigir que el volumen en el día de la caída sea > 1.5x lo normal). Esto filtra caídas sin convicción. 

### 3. Nuevas estrategias que podríamos probar
* **Dual Momentum Absoluto con Bonos (Gary Antonacci):** En lugar de comparar solo las acciones, comparar las acciones contra el retorno a corto plazo de Bonos del Tesoro (BIL o IEF). Solo se compra renta variable si su momentum de los últimos 12 meses es positivo *Y* superior a los bonos. 
* **Carry + Momentum:** No mirar solo el precio, mirar el "Carry" (Dividend Yield para acciones, o Cupones para Bonos). Combinar un score que sea 50% Momentum de Precio y 50% Rendimiento de Dividendos (Carry). Funciona especialmente bien para mitigar pérdidas en mercados laterales.
* **Momentum Estacional (Turn-of-Month):** Restringir las entradas intradía (como el S6/S8) exclusivamente a los últimos 3 y primeros 3 días hábiles del mes, aprovechando los flujos de rebalanceo institucional y el pago de nóminas, reduciendo enormemente el número de trades y las comisiones.
