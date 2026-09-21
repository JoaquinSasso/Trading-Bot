# REPORTE DE AUDITORÍA — ATRIBUCIÓN MULTIFACTORIAL DEL ALPHA (T-12 / F-22)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-12: Regresión Multifactorial de Rendimientos y Atribución Factorial |
| **Fecha** | 2026-09-21 |
| **Responde a** | Hallazgo F-22 y Mandato §3 T-12 de `AUDIT_FOLLOWUP_v2.2.md` |
| **Modelos Evaluados** | 1 Factor (CAPM SPY), 2 Factores (SPY + GLD), 3 Factores (SPY + GLD + MTUM/UMD) |
| **Errores Estándar** | Robustos a Heterocedasticidad y Autocorrelación (HAC / Newey-West) |

---

## 1. Motivación y Predicción del Auditor (F-22)

> *'La regresión de un solo factor contra SPY, aplicada a una cartera que puede tener hasta el 50% en GLD y SLV, atribuye al intercepto todo el retorno de los metales... β = 0.31 y R² = 0.15... Predicción registrada antes del test: la mayor parte del alpha se traslada a cargas sobre el factor oro y el factor momentum, y el intercepto queda cerca de cero sin significancia.'*

---

## 2. Tabla Comparativa de Modelos Factoriales (Muestra Completa 2020–2025)

| Configuración | Modelo | Alpha Anualizado (α) | Error Estándar (SE) | t-stat | p-value | β_SPY | β_GLD | β_MOM | R² | R² Aj. |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Top-4 (25% cap, CB Fijo)** | **1F (CAPM SPY)** | **+15.13%** | ±6.31% | +2.40 | 0.016 | 0.31 | — | — | **0.15** | 0.15 |
| **Top-4 (25% cap, CB Fijo)** | **2F (SPY + GLD)** | **+12.29%** | ±6.42% | +1.92 | 0.055 | 0.30 | 0.17 | — | **0.17** | 0.17 |
| **Top-4 (25% cap, CB Fijo)** | **3F (SPY + GLD + MTUM)** | **+12.31%** | ±6.41% | +1.92 | 0.055 | 0.28 | 0.17 | 0.01 | **0.17** | 0.17 |
| **Top-4 (25% cap, CB Fijo)** | **3F (SPY + GLD + UMD)** | **+12.16%** | ±6.39% | +1.90 | 0.057 | 0.31 | 0.16 | 0.07 | **0.18** | 0.18 |
| **Top-4 (25% cap, CB Adaptativo)** | **1F (CAPM SPY)** | **+15.98%** | ±6.19% | +2.58 | 0.010 | 0.33 | — | — | **0.16** | 0.16 |
| **Top-4 (25% cap, CB Adaptativo)** | **2F (SPY + GLD)** | **+13.11%** | ±6.29% | +2.08 | 0.037 | 0.31 | 0.17 | — | **0.19** | 0.19 |
| **Top-4 (25% cap, CB Adaptativo)** | **3F (SPY + GLD + MTUM)** | **+13.14%** | ±6.27% | +2.09 | 0.036 | 0.28 | 0.17 | 0.03 | **0.19** | 0.19 |
| **Top-4 (25% cap, CB Adaptativo)** | **3F (SPY + GLD + UMD)** | **+12.96%** | ±6.26% | +2.07 | 0.038 | 0.32 | 0.16 | 0.08 | **0.20** | 0.20 |
| **Top-2 (50% cap, CB Fijo)** | **1F (CAPM SPY)** | **+10.83%** | ±8.44% | +1.28 | 0.199 | 0.35 | — | — | **0.10** | 0.10 |
| **Top-2 (50% cap, CB Fijo)** | **2F (SPY + GLD)** | **+7.63%** | ±8.70% | +0.88 | 0.380 | 0.33 | 0.19 | — | **0.12** | 0.12 |
| **Top-2 (50% cap, CB Fijo)** | **3F (SPY + GLD + MTUM)** | **+7.69%** | ±8.65% | +0.89 | 0.374 | 0.28 | 0.19 | 0.05 | **0.12** | 0.12 |
| **Top-2 (50% cap, CB Fijo)** | **3F (SPY + GLD + UMD)** | **+7.42%** | ±8.66% | +0.86 | 0.392 | 0.35 | 0.17 | 0.11 | **0.13** | 0.13 |

---

## 3. Desglose Detallado por Períodos (Top-4 CB Adaptativo)

| Período | Retorno Neto | Modelo 1F (Alpha / R²) | Modelo 2F SPY+GLD (Alpha / R²) | Modelo 3F SPY+GLD+MTUM (Alpha / R²) | β_SPY (p-val) | β_GLD (p-val) | β_MTUM (p-val) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Muestra Completa 2020–2025 | +252.42% | **+15.98%** (R²=0.16) | **+13.11%** (R²=0.19) | **+13.14%** (R²=0.19) | 0.28 (0.003) | 0.17 (0.000) | 0.03 (0.704) |
| Trienio 2020–2022 | +53.03% | **+12.92%** (R²=0.16) | **+11.93%** (R²=0.19) | **+11.93%** (R²=0.19) | 0.22 (0.010) | 0.17 (0.004) | 0.03 (0.646) |
| 2020 | +30.22% | **+24.03%** (R²=0.13) | **+19.15%** (R²=0.17) | **+18.10%** (R²=0.17) | 0.06 (0.680) | 0.19 (0.069) | 0.15 (0.277) |
| 2021 | +21.21% | **+3.11%** (R²=0.32) | **+3.81%** (R²=0.32) | **+0.63%** (R²=0.33) | 0.86 (0.000) | 0.07 (0.311) | -0.16 (0.039) |
| 2022 | -1.81% | **+1.70%** (R²=0.18) | **+1.00%** (R²=0.22) | **+0.90%** (R²=0.26) | 0.38 (0.000) | 0.12 (0.003) | -0.20 (0.015) |
| 2025 | +33.62% | **+23.78%** (R²=0.07) | **+14.31%** (R²=0.17) | **+14.39%** (R²=0.18) | 0.24 (0.142) | 0.19 (0.008) | -0.08 (0.358) |

---

## 4. Dictamen Institucional y Conclusión (Criterio F-22)

Criterio del auditor: *'Si el R² sube sustancialmente y el α pierde significancia, el retorno del sistema es exposición factorial y no habilidad.'*

- **Evolución del R²:** Pasa de **0.16** (1 factor SPY) a **0.19** en el modelo 3 factores.
- **Evolución del Alpha Anualizado:** Pasa de **+15.98%** (t=+2.58, p=0.010) a **+13.14%** (t=+2.09, p=0.036).
- **Cargas Factoriales (Betas):**
  * **Beta SPY:** 0.28 (t=2.98, p=0.003)
  * **Beta GLD:** 0.17 (t=4.23, p=0.000)
  * **Beta MTUM:** 0.03 (t=0.38, p=0.704)

### Veredicto F-22: PARCIALMENTE CONFIRMADO.
El R² aumenta a 0.19 pero el alpha residual conserva significancia marginal (+13.14%, p=0.036).
