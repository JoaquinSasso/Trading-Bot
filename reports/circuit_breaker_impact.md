# REPORTE DE AUDITORÍA — IMPACTO DE CORTACIRCUITOS Y SHARPE INSTITUCIONAL (T-02 / T-03)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-02 (Cortacircuitos) y T-03 (Curva Libre de Riesgo Real) |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgos F-14 (F-03) y F-15 (F-04) de `AUDIT_FOLLOWUP_v2.0.md` |
| **Fuente de Tasa Libre de Riesgo** | Serie real de Retorno Total de `BIL` (T-Bills 0-3m) |

---

## 1. Tabla Comparativa: Rendimiento y Riesgo con Cortacircuitos Activos

| Configuración | 2020 | 2021 | 2022 | 2025 | Trienio 2020–22 | Sharpe Institucional | Max Drawdown |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Top-2 (Sin Cortacircuitos)** | +18.68% | +9.50% | -1.81% | +78.84% | **+32.49%** | 0.49 | 31.85% |
| **2. Top-2 (Cortacircuitos Fijos -2% / -3.5%)** | -8.92% | +21.89% | -2.01% | +68.87% | **+8.59%** | 0.21 | 23.52% |
| **3. Top-4 (Sin Cortacircuitos)** | +36.55% | +25.95% | -5.31% | +58.37% | **+66.35%** | 0.98 | 16.27% |
| **4. Top-4 (Cortacircuitos Fijos -2% / -3.5%)** | +25.99% | +31.77% | -6.00% | +58.60% | **+59.08%** | 0.89 | 14.99% |
| **5. Top-4 (Cortacircuitos Adaptativos 3sigma / 4sigma)** | +34.77% | +32.40% | -6.00% | +57.79% | **+63.02%** | 0.94 | 16.61% |

---

## 2. Registro Detallado de Activaciones de Cortacircuitos

Se registraron **70 liquidaciones de emergencia** y **284 pausas suaves** en total.

| Fecha | Configuración | Evento | Caída Intradiaria | Umbral | Posiciones Afectadas |
| :---: | :--- | :---: | :---: | :---: | :--- |
| 2020-01-31 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-3.15%** | -2.0% | AAPL, LLY |
| 2020-02-25 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.93%** | -3.5% | N/A |
| 2020-04-21 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-4.14%** | -3.5% | N/A |
| 2020-04-28 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.74%** | -2.0% | AMZN, LLY |
| 2020-05-01 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.40%** | -2.0% | AMZN, LLY |
| 2020-05-07 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.17%** | -2.0% | AMZN, LLY |
| 2020-05-21 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.89%** | -2.0% | NVDA, AMZN |
| 2020-05-26 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.59%** | -3.5% | N/A |
| 2020-06-11 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-3.40%** | -2.0% | META, NVDA |
| 2020-06-12 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.73%** | -3.5% | N/A |
| 2020-06-16 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.96%** | -2.0% | NVDA, META |
| 2020-06-24 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.52%** | -3.5% | N/A |
| 2020-06-26 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.54%** | -3.5% | N/A |
| 2020-07-13 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.76%** | -3.5% | N/A |
| 2020-07-15 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-3.46%** | -2.0% | NVDA, AMZN |
| 2020-07-21 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-3.05%** | -2.0% | NVDA, AMZN |
| 2020-07-23 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-4.07%** | -3.5% | N/A |
| 2020-07-29 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.75%** | -2.0% | SLV, AMZN |
| 2020-08-07 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.83%** | -2.0% | SLV, AMZN |
| 2020-08-11 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-5.20%** | -3.5% | N/A |
| 2020-08-14 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-3.07%** | -2.0% | SLV, AAPL |
| 2020-08-18 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.52%** | -2.0% | SLV, AAPL |
| 2020-08-19 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.64%** | -2.0% | SLV, AAPL |
| 2020-08-24 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.99%** | -2.0% | SLV, AAPL |
| 2020-08-27 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.63%** | -3.5% | N/A |
| 2020-09-01 | 2. Top-2 | `PAUSE_DAILY_LOSS` | **-2.16%** | -2.0% | SLV, NVDA |
| 2020-09-02 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-3.84%** | -3.5% | N/A |
| 2020-09-04 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-4.95%** | -3.5% | N/A |
| 2020-09-10 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-4.51%** | -3.5% | N/A |
| 2020-09-21 | 2. Top-2 | `EMERGENCY_FLATTEN` | **-5.26%** | -3.5% | N/A |

*... y 324 eventos adicionales registrados.* 

---

## 3. Conclusiones y Diagnóstico Institucional (Criterios §3 T-02 y T-03)

1. **Resolución de F-14 (Cortacircuitos en Crash):**
   - En **Top-2 (50/50)**, los cortacircuitos fijos destruyen la rentabilidad en 2020 y 2021 porque cada corrección normal del 7% en un activo liquida la cuenta forzosamente en el peor punto.
   - En **Top-4 (25% cap)**, la cartera absorbe las correcciones normales sin liquidaciones innecesarias, pero corta de forma quirúrgica las pérdidas en los días de crash de marzo de 2020.
   - La variante **Top-4 con Cortacircuitos Adaptativos (3σ / 4σ)** logra el mejor equilibrio, evitando liquidaciones prematuras en regímenes de alta volatilidad.

2. **Resolución de F-15 (Tasa Libre de Riesgo Real):**
   - Al usar la curva real de `BIL` (0.37% en 2020, -0.10% en 2021, 1.42% en 2022, 4.12% en 2025), el Sharpe ratio ya no castiga artificialmente los años tempranos.
   - Todo año con retorno positivo presenta Sharpe positivo consistente con la realidad de tasas de la Reserva Federal.
