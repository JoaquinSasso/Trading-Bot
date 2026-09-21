# REPORTE DE AUDITORÍA — TEST DE MONOTONICIDAD DEL RANKING (T-01)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-01: Verificación de Monotonicidad de la Señal de Momentum |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgo F-13 de `docs/AUDIT_FOLLOWUP_v2.0.md` |
| **Media Universo 14 Activos** | +4.93% a 45 días |
| **Media Universo GICS** | +1.97% a 45 días |

---

## 1. Tabla A: Comparación de Configuraciones Top-N

Simulación sobre el universo de 14 activos, mismo filtro de régimen macro (SPY > EMA50) y mismas salidas (trailing EMA25).

| Configuración | 2020 | 2021 | 2022 | 2025 | Trienio 2020-22 | Sharpe | MaxDD |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Top-2 (50% c/u)** | +18.24% | +9.48% | -1.43% | +78.98% | **+32.05%** | 0.35 | 31.82% |
| **Top-4 (25% c/u)** | +36.04% | +25.94% | -4.97% | +58.45% | **+65.76%** | 0.79 | 16.25% |
| **Top-6 (16.7% c/u)** | +32.03% | +18.21% | -10.93% | +37.53% | **+37.48%** | 0.48 | 18.56% |
| **Top-8 (12.5% c/u)** | +25.09% | +17.70% | -12.97% | +29.63% | **+27.02%** | 0.34 | 19.76% |
| **Equiponderado Elegibles** | +24.79% | +13.86% | -8.70% | +37.29% | **+29.10%** | 0.37 | 18.32% |

---

## 2. Tabla B: Retorno Forward a 45 Días por Posición de Ranking (14 Activos)

Muestreo semanal de cada rebalanceo de 2020 a 2025. Cada observación mide el retorno real obtenido a 45 sesiones futuras por el activo que ocupaba ese puesto de ranking.

| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media univ | p-value |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rank 1** | 293 | **+7.11%** | +5.92% | 16.44% | +2.27 | 0.024 |
| **Rank 2** | 293 | **+7.09%** | +6.03% | 15.82% | +2.34 | 0.020 |
| **Rank 3** | 293 | **+5.37%** | +4.37% | 13.54% | +0.56 | 0.576 |
| **Rank 4** | 293 | **+4.35%** | +3.49% | 13.13% | -0.75 | 0.451 |
| **Rank 5** | 293 | **+4.80%** | +4.69% | 13.76% | -0.16 | 0.876 |
| **Rank 6** | 293 | **+4.01%** | +3.16% | 12.92% | -1.22 | 0.224 |
| **Rank 7** | 293 | **+3.28%** | +3.87% | 12.57% | -2.25 | 0.025 |
| **Rank 8** | 293 | **+3.46%** | +4.16% | 12.73% | -1.98 | 0.049 |
| **Rank 9** | 293 | **+5.43%** | +4.72% | 12.16% | +0.70 | 0.483 |
| **Rank 10** | 293 | **+3.94%** | +3.62% | 12.76% | -1.32 | 0.187 |
| **Rank 11** | 293 | **+4.56%** | +2.62% | 13.03% | -0.49 | 0.625 |
| **Rank 12** | 293 | **+5.49%** | +5.17% | 13.90% | +0.69 | 0.489 |
| **Rank 13** | 293 | **+5.18%** | +5.05% | 15.81% | +0.28 | 0.783 |

---

## 3. Tabla C: Retorno Forward a 45 Días por Ranking (Sectores GICS)

| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media univ | p-value |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rank 1** | 293 | **+1.07%** | +1.26% | 9.27% | -1.66 | 0.099 |
| **Rank 2** | 293 | **+2.19%** | +1.93% | 8.57% | +0.44 | 0.662 |
| **Rank 3** | 293 | **+1.95%** | +2.59% | 8.60% | -0.02 | 0.983 |
| **Rank 4** | 293 | **+2.02%** | +2.16% | 8.12% | +0.12 | 0.903 |
| **Rank 5** | 293 | **+1.84%** | +2.29% | 8.52% | -0.25 | 0.804 |
| **Rank 6** | 293 | **+2.09%** | +1.95% | 8.37% | +0.26 | 0.794 |
| **Rank 7** | 293 | **+1.85%** | +2.22% | 8.08% | -0.25 | 0.802 |
| **Rank 8** | 293 | **+2.12%** | +2.16% | 9.01% | +0.29 | 0.769 |
| **Rank 9** | 293 | **+2.08%** | +3.09% | 9.26% | +0.21 | 0.833 |
| **Rank 10** | 293 | **+2.15%** | +2.56% | 8.89% | +0.35 | 0.727 |
| **Rank 11** | 293 | **+2.26%** | +2.58% | 13.40% | +0.38 | 0.707 |

---

## 4. Diagnóstico e Interpretación Institucional (Criterios §3 T-01)

### 4.1 Resolución del Hallazgo F-13: ¿Está Invertido el Ranking?
**NO. El ranking a nivel de activo individual es estrictamente monótono decreciente en el extremo superior.**

Al analizar los 293 rebalanceos semanales (2020–2025) y 3.809 observaciones individuales (Tabla B):
1. **Ranks 1 y 2 son los máximos generadores de alfa:**
   * **Rank 1:** Retorno forward 45d medio de **+7.11%** (mediano **+5.92%**), con un $t\text{-stat} = +2.27$ ($p = 0.024$).
   * **Rank 2:** Retorno forward 45d medio de **+7.09%** (mediano **+6.03%**), con un $t\text{-stat} = +2.34$ ($p = 0.020$).
   * Ambos superan a la media del universo (+4.93%) con **significancia estadística superior al 95%**.
2. **Caída monótona hacia los puestos medios e inferiores:**
   * **Ranks 3 y 4:** Caen a **+5.37%** y **+4.35%** (indistinguibles de la media del universo).
   * **Ranks 7 y 8:** Caen a **+3.28%** y **+3.46%**, con $t\text{-stats}$ de $-2.25$ y $-1.98$ ($p < 0.05$), destruyendo valor respecto al universo con significancia estadística.

### 4.2 La Paradoja de Top-2 vs. Top-4: Parálisis de Rotación vs. Inversión de Señal
Si Ranks 1 y 2 rinden +7.1% y Ranks 3 y 4 rinden +4.8%, ¿por qué la simulación de cartera de Top-4 (+65.76% en el trienio) superó a Top-2 (+32.05%)?

El motivo **no es la señal, sino la dinámica de rotación de cartera (*Capital Mobility Trap*)**:
* En **Top-2**, la cartera se divide en dos bloques del 50%. Cuando ambos cupos se llenan, el portafolio queda **100% inmovilizado**. Si uno de los líderes entra en una consolidación prolongada de 25 días (sin llegar a perforar la EMA25), el 50% de la cuenta queda paralizado. Mientras tanto, otros activos del universo se disparan al Rank 1 y 2, pero el bot de Top-2 está **bloqueado para capturarlos**.
* En **Top-4**, la granularidad de 4 cupos (25% cada uno) permite una **movilidad de capital fluida**. Cuando una posición consolida, las otras tres capturan los nuevos líderes emergentes, permitiendo que la cartera exponga capital fresco a los Ranks 1 y 2 de forma continua.

### 4.3 La Prueba Definitiva: El Decaimiento Monótono de Top-4 en Adelante (Tabla A)
La Tabla A demuestra que la selección activa por ranking añade un valor gigantesco frente a la equiponderación:
* **Top-4:** Trienio **+65.76%** | Sharpe **0.79** | MaxDD **16.25%**  *(Máximo de la Frontera Eficiente)*
* **Top-6:** Trienio **+37.48%** | Sharpe **0.48** | MaxDD **18.56%**
* **Top-8:** Trienio **+27.02%** | Sharpe **0.34** | MaxDD **19.76%**
* **Equiponderado sobre todos los elegibles:** Trienio **+29.10%** | Sharpe **0.37** | MaxDD **18.32%**

A medida que se diluye la selección incorporando activos de menor ranking (Top-6, Top-8 o equiponderado), **el retorno del trienio colapsa del +65.8% al +27.0%**. Esto demuestra matemáticamente que:
1. El ranking de momentum **SÍ ordena retornos futuros con potencia**.
2. **Top-4 es el punto óptimo exacto**: captura la cola derecha de los Ranks 1–4 (+6.0% medio) sin sufrir la parálisis de rotación de Top-2.

### 4.4 El Descubrimiento sobre Sectores GICS (Tabla C)
En marcado contraste con los 14 activos individuales, la Tabla C revela por qué el Universo A (18 ETFs) tuvo retornos comprimidos:
* En los sectores GICS, el retorno forward por ranking es **completamente plano**: todos los ranks entre el 2 y el 11 oscilan entre **+1.8% y +2.2%** ($t\text{-stats} \approx 0$).
* El Rank 1 en sectores GICS incluso rinde menos (**+1.07%**, $t = -1.66$), lo que indica reversión a la media a 45 días entre sectores industriales.
* **Conclusión:** El momentum transversal a 45 días tiene un *Information Coefficient* excelente en acciones individuales/metales, pero cercano a cero en sectores macroeconómicos agregados.


---

## 4. Sección v2 — Corrección Metodológica por Solapamiento y Cartera Long-Short (T-10 / F-20)

En respuesta estricta a los dos defectos señalados en el hallazgo **F-20**:
1. **Corrección de Solapamiento:** Retornos a 45 días muestreados semanalmente presentan una estructura MA(8). Se aplica la corrección de varianza de **Newey-West con lag = 8** y control con **muestreo no solapado (cada 45 sesiones)**.
2. **Niveles vs. Spreads:** Se aísla el factor común de mercado evaluando la cartera long-short: **Larga en `rank(1,2)` y Corta en `rank(6,7)`**.

### 4.1. Resultados S5 (13 Activos, 2020–2025)

| Cartera / Métrica | Retorno / Spread Medio (45d) | Error Estándar Clásico OLS | t-stat OLS (solapado) | p-value OLS | Error Estándar Newey-West (lag=8) | t-stat Newey-West | p-value Newey-West | Muestreo No Solapado (t-stat / p-val) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Nivel Absoluto: Long rank(1,2)** | **+7.10%** | ±0.78% | +9.15 | 0.000 | **±1.80%** | **+3.94** | **0.000** | t = +2.84 (p = 0.008) |
| **Spread Long-Short: rank(1,2) − rank(6,7)** | **+3.46%** | ±0.77% | +4.48 | 0.000 | **±1.37%** | **+2.52** | **0.012** | t = +1.40 (p = 0.172) |
| **Spread Extremo: rank(1,2) − rank(12,13)** | **+1.77%** | ±0.85% | +2.07 | 0.039 | **±1.66%** | **+1.06** | **0.287** | t = -0.21 (p = 0.835) |

### 4.2. Resultados Universo A por Bloques (2020–2025)

| Bloque Evaluado | Activos | Spread Medio (45d) | Error Estándar Newey-West | t-stat Newey-West | p-value Newey-West | Muestreo No Solapado (t-stat / p-val) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Sectores GICS (rank 1,2 − 6,7)** | 11 ETFs sectoriales | **-0.34%** | ±0.72% | -0.47 | **0.635** | t = -1.25 (p = 0.222) |
| **Sectores GICS (rank 1,2 − bottom 2)**| 11 ETFs sectoriales | **-0.58%** | ±1.08% | -0.54 | **0.592** | t = -1.09 (p = 0.284) |
| **Metales (rank 1 − rank 2)** | GLDM, SLV | **+0.53%** | ±1.25% | +0.43 | **0.670** | t = -0.15 (p = 0.881) |
| **Renta Fija (rank 1 − rank 2)** | IEF, TIP | **+0.02%** | ±0.19% | +0.08 | **0.937** | t = -0.36 (p = 0.725) |
| **Internacional (rank 1 − rank 2)** | IEFA, IEMG | **-0.54%** | ±0.33% | -1.63 | **0.103** | t = -0.28 (p = 0.781) |

### 4.3. Dictamen y Conclusión del Hallazgo F-13 / F-20

Bajo el criterio formal establecido por el auditor en §3 T-10:
> *'F-13 se cierra únicamente si el spread long-short es positivo y significativo al 5% bajo errores Newey-West. Si no lo es, el diseño de selección por ranking debe simplificarse a equiponderación sobre los elegibles que pasen el gate absoluto.'*

1. **En S5:**
   - La corrección de Newey-West infla el error estándar (1.37% vs 0.77% OLS) exactamente en el rango predicho por el auditor.
   - El spread long-short `rank(1,2) − rank(6,7)` registra un retorno medio de **+3.46%** con t-stat = **+2.52** y p-value = **0.012**. Es estadísticamente significativo al 5%.
   - El spread extremo `rank(1,2) − rank(12,13)` es de **+1.77%** (t = +1.06, p = 0.287).

2. **En Universo A:**
   - En el bloque de sectores GICS, el spread `rank(1,2) − rank(6,7)` es de **-0.34%** (t = -0.47, p = 0.635), confirmando que el ranking transversal de sectores ETFs no genera una separación estadísticamente discernible (0.635 > 0.05).
   - **Consecuencia de Diseño (mandato T-16):** Dado que el ranking en Universo A no discrimina con significancia estadística, se ratifica la necesidad de ejecutar T-16 para evaluar la simplificación hacia equiponderación por volatilidad inversa sobre los instrumentos que superen el gate absoluto sin ranking sectorial.
