# REPORTE DE AUDITORÍA — SIMPLIFICACIÓN DE UNIVERSO A (T-16)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-16: Variante Simplificada de Universo A sin Ranking Sectorial |
| **Fecha** | 2026-09-21 |
| **Responde a** | Hallazgos F-20, F-13 y Mandato §3 T-16 de `AUDIT_FOLLOWUP_v2.2.md` |
| **Modelo de Ejecución** | Cuenta $2.000 USD, acciones enteras (`floor`), costos 3.5 bps one-way |

---

## 1. Motivación y Principio de Parsimonia (F-20 / T-16)

> *'El propio test mostró que el ranking sectorial es plano (~+2.0% en todos los ranks, spread long-short t=-0.47, p=0.635). Un parámetro que no discrimina es superficie de sobreajuste sin contrapartida, y el PBO de 84.45% indica que sobra exactamente eso.'*

### Criterio Falsable del Auditor:
> *'Si el desempeño es equivalente, adoptar la variante simplificada como candidato primario. Menos parámetros libres reducen directamente el PBO en la próxima medición, que es el obstáculo real para capital.'*

---

## 2. Tabla Comparativa por Períodos de Simulación

| Período | Variante | Retorno Acumulado | CAGR Anual | Sharpe Real | Max Drawdown | Trades | Exposición Media | Fricción ($) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Muestra Completa 2018–2026 (7.6a) | **Con Ranking (Baseline)** | **+38.55%** | 4.35% | 0.38 | 4.71% | 1182 | 43.7% | $115.55 |
| Muestra Completa 2018–2026 (7.6a) | **Simplificado (Sin Ranking)** | **+42.50%** | 4.74% | 0.57 | 3.37% | 1130 | 34.1% | $88.61 |
| | *Diferencia (Simplificado - Ranking)* | *+3.95%* | *+0.38%* | *+0.19* | *-1.34%* | | | |
| Trienio 2020–2022 | **Con Ranking (Baseline)** | **+11.93%** | 3.83% | 0.70 | 4.40% | 354 | 36.6% | $31.53 |
| Trienio 2020–2022 | **Simplificado (Sin Ranking)** | **+8.66%** | 2.81% | 0.71 | 3.44% | 250 | 22.8% | $19.35 |
| | *Diferencia (Simplificado - Ranking)* | *-3.28%* | *-1.02%* | *+0.01* | *-0.96%* | | | |
| 2020 | **Con Ranking (Baseline)** | **+7.84%** | 7.81% | 1.37 | 3.45% | 112 | 43.5% | $9.73 |
| 2020 | **Simplificado (Sin Ranking)** | **+5.75%** | 5.72% | 1.29 | 3.44% | 80 | 30.1% | $5.67 |
| | *Diferencia (Simplificado - Ranking)* | *-2.09%* | *-2.08%* | *-0.08* | *-0.01%* | | | |
| 2021 | **Con Ranking (Baseline)** | **+3.04%** | 3.04% | 0.59 | 4.37% | 200 | 55.6% | $17.70 |
| 2021 | **Simplificado (Sin Ranking)** | **+2.19%** | 2.19% | 0.81 | 2.53% | 126 | 29.9% | $10.18 |
| | *Diferencia (Simplificado - Ranking)* | *-0.84%* | *-0.84%* | *+0.21* | *-1.84%* | | | |
| 2022 | **Con Ranking (Baseline)** | **+1.79%** | 1.79% | 0.16 | 2.56% | 40 | 8.0% | $2.80 |
| 2022 | **Simplificado (Sin Ranking)** | **+2.94%** | 2.95% | 0.74 | 2.15% | 22 | 4.9% | $1.72 |
| | *Diferencia (Simplificado - Ranking)* | *+1.15%* | *+1.16%* | *+0.59* | *-0.41%* | | | |
| 2025 | **Con Ranking (Baseline)** | **+6.48%** | 6.54% | 0.47 | 3.76% | 184 | 48.7% | $17.47 |
| 2025 | **Simplificado (Sin Ranking)** | **+6.82%** | 6.87% | 0.61 | 3.00% | 180 | 38.4% | $12.49 |
| | *Diferencia (Simplificado - Ranking)* | *+0.33%* | *+0.34%* | *+0.15* | *-0.76%* | | | |

---

## 3. Eliminación de Parámetros Libres y Reducción de Complejidad

| Componente de Arquitectura | Versión con Ranking (Baseline) | Versión Simplificada (T-16) | Impacto en PBO |
| :--- | :--- | :--- | :--- |
| **Ventanas de Momentum** | 3 ventanas (21d, 63d, 126d) | **Ninguna** (0 ventanas) | Elimina 3 hiperparámetros |
| **Skip Period** | 5 días de omisión | **Ninguno** | Elimina 1 hiperparámetro |
| **Ponderación de Ranks** | Promedio de ordinales | **Ninguno** | Elimina regla de ordenamiento |
| **Truncamiento Top-N** | Top 4 sectores, Top 1 intl, etc. | **Ninguno** (todos los calificados) | Elimina 4 umbrales arbitrarios |
| **Buffer de Salida** | Buffer rank 7 (sectores) | **Ninguno** (salida si falla gate) | Elimina 3 buffers asimétricos |
| **Total Parámetros Eliminados** | Baseline | **11 Parámetros Libres Eliminados** | **Reducción directa de PBO** |

---

## 4. Dictamen Institucional y Decisión de Candidato Primario

- **Desempeño Muestra Completa (2018–2026):**
  * Versión con Ranking    : **+38.55%** (CAGR: 4.35%, Sharpe: 0.38, MaxDD: 4.71%)
  * Versión Simplificada   : **+42.50%** (CAGR: 4.74%, Sharpe: 0.57, MaxDD: 3.37%)
  * Diferencia de Retorno : **+3.95 puntos porcentuales** (Diferencia Sharpe: +0.19)

### Veredicto T-16: ADOPCIÓN RECOMENDADA.
El desempeño de la variante simplificada es cuantitativamente equivalente (o superior) al de la versión con ranking, pero **con 11 parámetros libres menos**.
Al no existir ventaja estadística en ordenar los sectores GICS (confirmado por F-20), el ranking es sobreajuste puro que infló el PBO al 84.45%.
Se adopta formalmente la **Variante Simplificada T-16** (Gate Absoluto + Volatilidad Inversa sobre todos los calificados) como la especificación del Candidato Primario para la Fase 3 de Paper Trading.
