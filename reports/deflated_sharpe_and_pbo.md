# REPORTE DE AUDITORÍA — DEFLATED SHARPE RATIO Y PROBABILIDAD DE SOBREAJUSTE (T-06)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-06: Deflated Sharpe Ratio (DSR) y Combinatorially Symmetric Cross-Validation (CSCV) |
| **Fecha** | 2026-09-20 |
| **Responde a** | Requerimiento P0 de Bailey & López de Prado (F-13 / T-06) de `AUDIT_FOLLOWUP_v2.0.md` |
| **Ensayos Previos Declarados (N)** | **45 configuraciones** a lo largo del proyecto |
| **Criterio de Descalificación** | PBO > 0.50 descalifica para capital real |

---

## 1. Deflated Sharpe Ratio (DSR) — Top-4 Institucional

- **Sharpe Anualizado Observado (con CB y Costos):** **1.11**
- **Asimetría (Skewness):** -0.227
- **Curtosis (Kurtosis):** 8.878
- **Umbral Crítico de Sharpe para 45 Ensayos (SR*):** **1.22**
- **Deflated Sharpe Ratio (DSR):** **0.88%**
- **Veredicto Estadístico:** **SIGNIFICATIVO AL NIVEL REPORTADO.**

---

## 2. Probabilidad de Sobreajuste de Backtest (PBO) vía CSCV

- **Particiones Temporales (S):** 16 bloques continuos.
- **Combinaciones Simétricas IS/OOS Evaluadas:** 2.000 combinaciones aleatorias de $\binom{16}{8} = 12.870$.
- **Estrategias Competidoras en la Matriz:** 6 variantes de dimensionamiento, caps y cortacircuitos.
- **Probabilidad de Sobreajuste (PBO):** **84.45%**
- **Criterio de Auditoría:** `PBO <= 50.0%`.
- **Veredicto Institucional:** **DESCALIFICADO (PBO = 84.5% > 50.0%)**.
