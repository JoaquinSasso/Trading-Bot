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
| Trienio 2020–2022 | **Con Ranking (Baseline)** | **+13.97%** | 4.45% | 0.69 | 5.51% | 120 | 75.0% | $24.87 |
| Trienio 2020–2022 | **Simplificado (Sin Ranking)** | **+9.45%** | 3.06% | 0.49 | 6.15% | 198 | 75.0% | $27.69 |
| | *Diferencia (Simplificado - Ranking)* | *-4.52%* | *-1.40%* | *-0.20* | *+0.64%* | | | |
| 2020 (Crash COVID + Rebote) | **Con Ranking (Baseline)** | **+11.50%** | 11.45% | 1.58 | 4.14% | 42 | 75.0% | $9.58 |
| 2020 (Crash COVID + Rebote) | **Simplificado (Sin Ranking)** | **+5.33%** | 5.30% | 0.81 | 6.15% | 73 | 75.0% | $10.45 |
| | *Diferencia (Simplificado - Ranking)* | *-6.17%* | *-6.15%* | *-0.77* | *+2.01%* | | | |
| 2021 (Mercado Alcista) | **Con Ranking (Baseline)** | **-2.78%** | -2.78% | 0.00 | 4.62% | 56 | 75.0% | $9.94 |
| 2021 (Mercado Alcista) | **Simplificado (Sin Ranking)** | **+1.20%** | 1.20% | 0.00 | 3.46% | 88 | 75.0% | $11.79 |
| | *Diferencia (Simplificado - Ranking)* | *+3.99%* | *+3.99%* | *+0.00* | *-1.16%* | | | |
| 2022 (Mercado Bajista Severo) | **Con Ranking (Baseline)** | **-1.20%** | -1.20% | 0.00 | 3.17% | 28 | 75.0% | $4.14 |
| 2022 (Mercado Bajista Severo) | **Simplificado (Sin Ranking)** | **+0.08%** | 0.08% | 0.00 | 3.05% | 34 | 75.0% | $4.07 |
| | *Diferencia (Simplificado - Ranking)* | *+1.28%* | *+1.28%* | *+0.00* | *-0.12%* | | | |

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

- **Desempeño Ventana Desarrollo (2020–2022):**
  * Versión con Ranking    : **+13.97%** (CAGR: 4.45%, Sharpe: 0.69, MaxDD: 5.51%)
  * Versión Simplificada   : **+9.45%** (CAGR: 3.06%, Sharpe: 0.49, MaxDD: 6.15%)
  * Diferencia de Retorno : **-4.52 puntos porcentuales** (Diferencia Sharpe: -0.20)

### Veredicto T-16: DESVIACIÓN MATERIAL.
La variante simplificada exhibe un comportamiento divergente que requiere análisis adicional antes de sustituir la arquitectura base.
