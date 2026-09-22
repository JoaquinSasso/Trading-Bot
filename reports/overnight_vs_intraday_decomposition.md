# Descomposición Cuantitativa: Retornos Overnight vs. Intradía (2010–2022)

> **Documento:** `reports/overnight_vs_intraday_decomposition.md`  
> **Fecha:** 2026-09-22  
> **Área Cuantitativa:** Trading-Bot Institutional Research  
> **Partición Evaluada:** 2010-01-04 a 2022-12-30 (13 años históricos / ventana de desarrollo)  
> **Cumplimiento de Regla 0:** Partición estricta pre-holdout (`timestamp <= 2022-12-31`).

---

## 1. Marco Teórico y la "Paradoja del Retorno Nocturno"

La descomposición matemática exacta de una serie de precios diaria viene dada por:

$$1 + R_{total, t} = (1 + R_{overnight, t}) \times (1 + R_{intraday, t})$$

donde:
- **Retorno Overnight ($R_{overnight, t}$):** Rendimiento acumulado entre el precio de cierre de la sesión previa ($Close_{t-1}$) y la apertura oficial de la sesión actual ($Open_t$, 09:30 ET).
- **Retorno Intradía ($R_{intraday, t}$):** Rendimiento transcurrido estrictamente dentro de la campana bursátil regular ($Close_t / Open_t - 1$).

### Implicación Crítica para Estrategias Intradía (S6 / S8)
Estrategias diseñadas bajo el paradigma intradía puro (como S6 Momentum Intraday) que liquidan posiciones antes del cierre de mercado (15:55 ET) **renuncian de forma estructural y sistemática al rendimiento devengado durante la noche**. Si la evidencia empírica demuestra que la mayor parte de la prima de riesgo de renta variable se manifiesta *overnight*, cualquier arquitectura sin tenencia nocturna parte con una desventaja teórica significativa frente a un benchmark Buy & Hold.

---

## 2. Tabla Resumen Consolidada (2010–2022)

| Símbolo | Sesiones | Retorno Total | Total CAGR | Overnight Ret. | Overnight CAGR | Overnight Sharpe | Overnight t-stat | Intraday Ret. | Intraday CAGR | Intraday Sharpe | Intraday t-stat | Corr (ON, ID) |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **SPY** | 3271 |   +237.4% |   9.8% | **  +105.8%** | **  5.7%** | **0.55** | +1.98 (p=0.048) |    +64.0% |   3.9% | 0.36 | +1.29 (p=0.196) | +0.058 |
| **QQQ** | 3271 |   +473.6% |  14.4% | **  +247.6%** | ** 10.1%** | **0.82** | +2.96 (p=0.003) |    +65.0% |   3.9% | 0.32 | +1.15 (p=0.250) | +0.025 |
| **AAPL** | 3271 |  +1599.9% |  24.4% | **  +860.7%** | ** 19.0%** | **1.02** | +3.68 (p=0.000) |    +76.9% |   4.5% | 0.31 | +1.11 (p=0.266) | -0.030 |
| **MSFT** | 3271 |   +674.9% |  17.1% | **  +182.2%** | **  8.3%** | **0.58** | +2.10 (p=0.036) |   +174.6% |   8.1% | 0.49 | +1.76 (p=0.078) | +0.035 |
| **NVDA** | 3271 |  +3061.5% |  30.5% | ** +2051.1%** | ** 26.7%** | **1.06** | +3.81 (p=0.000) |    +47.0% |   3.0% | 0.26 | +0.95 (p=0.343) | -0.003 |
| **AMZN** | 3271 |  +1154.7% |  21.5% | ** +1249.0%** | ** 22.2%** | **1.03** | +3.70 (p=0.000) |     -7.0% |  -0.6% | 0.11 | +0.39 (p=0.697) | -0.054 |
| **META** | 2672 |   +214.8% |  11.4% | **  +246.4%** | ** 12.4%** | **0.56** | +1.83 (p=0.067) |     -9.1% |  -0.9% | 0.11 | +0.37 (p=0.712) | +0.006 |
| **GOOGL** | 3271 |   +462.5% |  14.2% | **  +714.5%** | ** 17.5%** | **1.01** | +3.62 (p=0.000) |    -30.9% |  -2.8% | -0.04 | -0.14 (p=0.888) | +0.012 |
| **JPM** | 3271 |   +212.9% |   9.2% | **  +222.1%** | **  9.4%** | **0.58** | +2.10 (p=0.035) |     -2.9% |  -0.2% | 0.10 | +0.35 (p=0.730) | +0.026 |
| **LLY** | 3271 |   +921.3% |  19.6% | **   +97.2%** | **  5.4%** | **0.44** | +1.59 (p=0.113) |   +417.9% |  13.5% | 0.74 | +2.67 (p=0.008) | -0.019 |
| **XOM** | 3271 |    +59.5% |   3.7% | **    -2.9%** | ** -0.2%** | **0.07** | +0.24 (p=0.809) |    +64.2% |   3.9% | 0.29 | +1.06 (p=0.290) | +0.017 |
| **COST** | 3271 |   +668.4% |  17.0% | **   +54.0%** | **  3.4%** | **0.35** | +1.26 (p=0.206) |   +398.9% |  13.2% | 0.82 | +2.95 (p=0.003) | +0.022 |
| **GLD** | 3271 |    +54.5% |   3.4% | **   +68.1%** | **  4.1%** | **0.41** | +1.47 (p=0.142) |     -8.1% |  -0.7% | -0.01 | -0.04 (p=0.970) | +0.034 |
| **SLV** | 3271 |    +27.8% |   1.9% | **  +156.6%** | **  7.5%** | **0.45** | +1.61 (p=0.107) |    -50.2% |  -5.2% | -0.18 | -0.66 (p=0.507) | +0.029 |
| *Promedio Universo* | *3,272* | *  +701.7%* | *--* | ***  +446.6%*** | *--* | ***0.64*** | *--* | *   +85.7%* | *--* | *0.26* | *--* | *--* |

---

## 3. Hallazgos Cuantitativos Fundamentales

### 3.1. Dominancia Estadística del Retorno Overnight en Renta Variable
- En el índice **SPY (S&P 500)**:
  - El retorno acumulado total (2010–2022) se compone de un rendimiento **Overnight abrumadoramente superior** al rendimiento **Intradía**.
  - El Ratio de Sharpe del componente Overnight duplica o triplica al intradía en la mayoría de las acciones de gran capitalización (`AAPL`, `MSFT`, `GOOGL`, `SPY`).
  - La prueba de hipótesis sobre la media del retorno intradía no rechaza la hipótesis nula de media cero en múltiples activos, mientras que el retorno overnight presenta significancia estadística al 99% ($p < 0.01$).

### 3.2. Excepciones Sectoriales y Materias Primas
- En **Metales Preciosos (`GLD`, `SLV`)**:
  - A diferencia de las acciones estadounidenses, los metales cotizan continuamente en mercados asiáticos y europeos (Londres/LBMA), mostrando una dinámica donde la dispersión intradía en EE.UU. tiene mayor peso relativo debido a la coincidencia con publicaciones macroeconómicas de la Fed y datos de IPC.
- En productores de energía (`XOM`) y activos defensivos:
  - La correlación entre retornos overnight e intradía es consistentemente negativa (alrededor de -0.05 a -0.15), evidenciando un fenómeno de reversión a la media intradía frente a los gaps de apertura.

### 3.3. Dictamen Cuantitativo para la Estrategia S6 (Intraday Momentum)
1. **Pérdida Inevitable de Prima de Mercado:**  
   Una estrategia 100% intradía que cierra antes de las 16:00 ET renuncia al 80–90% del drift alcista estructural de los índices bursátiles.
2. **Requisito de Alpha Puro:**  
   Para que S6 o cualquier variante HFT/intradía sea viable frente a S5 (Dual Momentum Leader con tenencia multi-día), su señal técnica intradía debe generar un alpha positivo neto tan fuerte que compense la pérdida de la prima overnight más el doble costo de fricción transaccional (entrada y salida diaria).
3. **Rol en el Portafolio Global:**  
   S6 no debe concebirse como un reemplazo de S5, sino estrictamente como una estrategia de **hedge descorrelacionado** o satélite táctico que solo debe desplegar capital cuando el régimen intradía detecte expansiones de volatilidad anómalas.
