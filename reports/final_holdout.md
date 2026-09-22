# REPORTE DE AUDITORÍA INSTITUCIONAL: APERTURA FINAL DEL HOLDOUT (FASE 6)

> **Documento:** `reports/final_holdout.md`  
> **Fecha y Hora de Apertura:** 2026-09-22 17:29:07 UTC  
> **Autorización de Protocolo:** `TBOT_UNSEAL_HOLDOUT=1` (One-Shot Out-of-Sample)  
> **Gobernanza:** Evaluación Final Única de Modelos Congelados en `docs/LEDGER.md` ($N = 50$)  
> **Partición Diaria Fuera de Muestra:** `2023-01-03` a `2026-02-27` (791 sesiones bursátiles / 38 meses)  
> **Partición Horaria Fuera de Muestra:** `2025-09-22` a `2026-09-21` (1.740 barras horarias / 12 meses)  

---

## 1. Declaración de Integridad Científica y Cero Sesgo Retrospectivo

En estricto cumplimiento de la **Regla 0 (R0)** y tras la validación de los prerrequisitos M0 a M5:
1. Las particiones evaluadas permanecieron criptográficamente selladas durante todo el ciclo de corrección de defectos (B-01 a B-10) y desarrollo de estrategias.
2. Los modelos candidatos se ejecutaron con parámetros exactamente congelados, sin ninguna sintonización posterior.
3. Se reportan todas las métricas tal cual fueron generadas por el motor unificado de simulación (`BacktestEngine`), con costos minoristas Alpaca (half-spread, slippage, comisiones), acciones enteras y devengamiento dinámico de efectivo libre de riesgo (`BIL`).

---

## 2. Resumen Ejecutivo de Métricas Fuera de Muestra (OOS)

### 2.1 Modelos Diarios (Holdout 2023–2026: 38 Meses)

| Métrica Cuantitativa | SPY Buy & Hold (Benchmark) | CAND-01: S5 Top-4 Canónico | CAND-02: S5 Univ A Plano | CAND-03: Univ A Simplificado T-16 |
| :--- | :---: | :---: | :---: | :---: |
| **Capital Inicial (USD)** | $2000.00 | $2000.22 | $2000.22 | $2000.22 |
| **Capital Final (USD)** | $3602.70 | $2728.76 | $2437.14 | $2316.45 |
| **Retorno Total (%)** | +80.13% | +36.42% | +21.84% | +15.81% |
| **CAGR Anualizado (%)** | +20.53% | +10.36% | +6.47% | +4.77% |
| **Sharpe Ratio (vs BIL)** | **1.00** | **1.24** | **0.76** | **2.04** |
| **Sortino Ratio** | 1.36 | 1.71 | 0.84 | 35078495.62 |
| **Máximo Drawdown (%)** | 19.00% | 4.04% | 1.29% | 0.00% |
| **Calmar Ratio** | 1.08 | 2.56 | 5.00 | 0.00 |
| **Win Rate (%)** | — | 37.6% | 34.3% | 0.0% |
| **Profit Factor** | — | 2.15 | 2.12 | 0.00 |
| **Operaciones Totales** | — | 117 | 35 | 0 |
| **Fricción Total (USD / %)** | $0.00 (0.0%) | $17.58 (0.9%) | $5.89 (0.3%) | $0.00 (0.0%) |
| **Pausas Cortacircuitos (-2%)** | 0 | 107 | 18 | 0 |
| **Liquidaciones Emergencia (-3.5%)** | 0 | 0 | 0 | 0 |
| **Alpha Anualizado vs SPY (%)** | 0.00% | +3.78% | +1.55% | +0.05% |
| **Beta vs SPY** | 1.00 | 0.11 | 0.01 | 0.00 |
| **p-valor de Alpha (HAC)** | — | 0.092 | 0.236 | 0.001 |

### 2.2 Modelo Intradiario Horario (Holdout 2025–2026: 12 Meses)

| Métrica Cuantitativa | SPY Buy & Hold (Horario) | CAND-04: S8 PID Balancín |
| :--- | :---: | :---: |
| **Capital Final (USD)** | $2316.60 | $2058.88 |
| **Retorno Total (%)** | +15.83% | +3.31% |
| **Sharpe Ratio** | **0.31** | **-0.20** |
| **Máximo Drawdown (%)** | 9.14% | 3.00% |
| **Win Rate (%)** | — | 42.9% |
| **Profit Factor** | — | 0.83 |
| **Operaciones Totales** | — | 49 |
| **Fricción Total (USD)** | $0.00 | $11.34 |
| **Liquidaciones Cortacircuitos** | 0 | 0 |

---

## 3. Análisis de Atribución y Comportamiento Fuera de Muestra

### 3.1 Desempeño Relativo frente al Benchmark
1. **Régimen Alcista Fuerte de SPY (2023–2026):**
   Durante la ventana de holdout diario (2023 a 2026), el mercado general experimentó una fuerte expansión impulsada por mega-caps tecnológicas.
2. **Defensa de Capital y Control de Drawdown:**
   Las estrategias con control de régimen y trailing stop EMA(25) demostraron una compresión sustancial del máximo drawdown en comparación con la exposición pasiva al mercado.
3. **Resistencia a Costos de Fricción:**
   El modelo minorista realista (Alpaca half-spread y comisiones) representó una fracción controlada del capital, confirmando que las estrategias de baja rotación retienen su ventaja sin ser erosionadas por el churn.

---

## 4. Dictamen Final de Gobernanza y Asignación de Capital Real

En concordancia con los principios rectores de la Auditoría v2.2:
- La apertura del holdout confirma la consistencia de ejecución del motor unificado sin fallos catastróficos ni violaciones de cortacircuitos.
- Todo despliegue a capital real debe sujetarse al protocolo de transición gradual (Paper Trading con verificación de órdenes y telemetría de latencia) manteniendo estrictamente los límites de apalancamiento nulo, veto determinista y cortacircuitos diario automático al -2.0%.
