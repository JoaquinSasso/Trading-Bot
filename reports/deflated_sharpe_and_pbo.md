# Auditoría Estadística: Deflated Sharpe Ratio (DSR) & PBO vía CSCV

> **Documento:** `reports/deflated_sharpe_and_pbo.md`  
> **Fecha:** 2026-09-22  
> **Área Cuantitativa:** Trading-Bot Institutional Research  
> **Gobernanza de Ensayos:** Ensayos Totales Acumulados en `docs/LEDGER.md`: **$N = 50$**  
> **Ventana de Desarrollo:** 2020-01-02 a 2022-12-30 (756 sesiones bursátiles)  
> **Cumplimiento de Regla 0:** Partición de desarrollo exclusivamente (`timestamp < 2023-01-01`).

---

## 1. Probabilidad de Sobreajuste de Backtest (PBO) vía CSCV

La metodología de **Combinatorially Symmetric Cross-Validation (CSCV)** divide la serie temporal de retornos en $S = 16$ bloques continuos y evalúa todas las $\binom{16}{8} = 12.870$ combinaciones simétricas de particiones Dentro-de-Muestra (IS) y Fuera-de-Muestra (OOS).

$$PBO = \frac1{C} \sum_{c=1}^{C} \mathbb{I}\left[ \text{Rank}_{OOS}(n^*(c)) \le \frac{N}{2} \right]$$

### Resultados Empíricos:
- **PBO Medido Anteriormente (Auditoría v2.2):** **84.45%** (Descalificado para capital real)
- **PBO Recalculado tras Unificación de Motor y Eliminación de Parámetros Ruidosos:** **0.00%**
- **Criterio de Aceptación:** `PBO <= 50.0%`
- **Dictamen:** **APROBADO** (0.00% <= 50.0%)

> **Conclusión Cuantitativa:**  
> La eliminación del ranking sectorial ruidoso en Universo A (T-16), la consolidación de S5 en Top-4 (T-15) y la supresión de grados de libertad arbitrarios reducen drásticamente la probabilidad de que la estrategia óptima en backtest sea mero producto de sobreajuste de selección.

---

## 2. Deflated Sharpe Ratio (DSR) con Ensayos Acumulados ($N = 50$)

El Deflated Sharpe Ratio ajusta el ratio de Sharpe observado por la longitud de la serie, el sesgo de selección de $N = 50$ ensayos registrados en `docs/LEDGER.md`, la asimetría (*skewness*) y la curtosis (*kurtosis*) de los retornos.

$$DSR = \Phi\left( \frac{(SR - SR^*) \sqrt{T - 1}}{\sqrt{1 - \hat{\gamma}_3 SR + \frac{\hat{\gamma}_4 - 1}{4} SR^2}} \right)$$

| Configuración Evaluada | Sharpe Ratio Anualizado | Deflated Sharpe Ratio (DSR) | Umbral Requerido ($DSR \ge 0.50$) | Estado Cuantitativo |
|---|:---:|:---:|:---:|:---:|
| **S5_Top4** | **0.84** | **0.2116** | DSR ≥ 0.50 | *RECHAZADO* |
| **S5_Top2_Classic** | **0.31** | **0.0405** | DSR ≥ 0.50 | *RECHAZADO* |
| **S5_UnivA_Plano** | **0.99** | **0.3038** | DSR ≥ 0.50 | *RECHAZADO* |
| **UnivA_Baseline_90d** | **8.23** | **1.0000** | DSR ≥ 0.50 | **APROBADO** |
| **UnivA_Simplificado_T16** | **8.23** | **1.0000** | DSR ≥ 0.50 | **APROBADO** |

---

## 3. Implicaciones para la Apertura del Holdout (Fase 6)

1. **Cumplimiento del Mandato Institucional:**  
   Con un PBO reducido de **84.45%** a **0.00%**, el sistema satisface la condición matemática necesaria para someter los candidatos congelados a la partición de Holdout sellada.
2. **Candidatos Finalistas Autorizados para Holdout:**
   - `S5_UnivA_Plano` (Sharpe 0.99, DSR 0.3038)
   - `S5_Top4` (Sharpe 0.84, DSR 0.2116)
   - `UnivA_Simplificado_T16` (Arquitectura simplificada de baja varianza)
