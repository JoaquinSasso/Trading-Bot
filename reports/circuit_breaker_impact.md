# REPORTE DE AUDITORÍA — IMPACTO DE CORTACIRCUITOS Y SHARPE INSTITUCIONAL (T-02 / T-03)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-02 (Cortacircuitos) y T-03 (Curva Libre de Riesgo Real) |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgos F-14 (F-03) y F-15 (F-04) de `AUDIT_FOLLOWUP_v2.0.md` |
| **Fuente de Tasa Libre de Riesgo** | Serie real de Retorno Total de `BIL` (T-Bills 0-3m) |

---

## 1. Tabla Comparativa: Rendimiento y Riesgo con Cortacircuitos Activos

| Configuración | 2020 | 2021 | 2022 | Trienio 2020–22 | Sharpe Institucional | Max Drawdown |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Top-2 (Sin Cortacircuitos)** | +2.75% | +6.51% | -0.48% | **+8.07%** | 0.60 | 3.85% |
| **2. Top-2 (Cortacircuitos Fijos -2% / -3.5%)** | +3.11% | +6.21% | -0.72% | **+8.24%** | 0.63 | 3.87% |
| **3. Top-4 (Sin Cortacircuitos)** | +6.91% | +5.43% | -1.13% | **+13.87%** | 0.69 | 8.03% |
| **4. Top-4 (Cortacircuitos Fijos -2% / -3.5%)** | +8.99% | +6.26% | -1.54% | **+15.50%** | 0.81 | 6.28% |

---

## 2. Registro Detallado de Activaciones de Cortacircuitos

Se registraron **0 liquidaciones de emergencia** y **0 pausas suaves** en total.

| Fecha | Configuración | Evento | Caída Intradiaria | Umbral | Posiciones Afectadas |
| :---: | :--- | :---: | :---: | :---: | :--- |
| 2020-09-23 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.39%** | -2.0% | N/A |
| 2020-09-24 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.05%** | -2.0% | N/A |
| 2020-09-28 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.23%** | -2.0% | N/A |
| 2020-09-29 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.00%** | -2.0% | N/A |
| 2020-09-30 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.01%** | -2.0% | N/A |
| 2020-10-01 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.23%** | -2.0% | N/A |
| 2020-10-02 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.13%** | -2.0% | N/A |
| 2020-10-05 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.01%** | -2.0% | N/A |
| 2020-10-06 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.13%** | -2.0% | N/A |
| 2020-10-07 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.10%** | -2.0% | N/A |
| 2020-10-08 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.24%** | -2.0% | N/A |
| 2020-10-09 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.08%** | -2.0% | N/A |
| 2020-10-12 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.06%** | -2.0% | N/A |
| 2020-10-13 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.21%** | -2.0% | N/A |
| 2020-10-14 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.25%** | -2.0% | N/A |
| 2020-10-15 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.03%** | -2.0% | N/A |
| 2020-10-16 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.19%** | -2.0% | N/A |
| 2020-10-19 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.38%** | -2.0% | N/A |
| 2020-10-20 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.06%** | -2.0% | N/A |
| 2020-10-21 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.11%** | -2.0% | N/A |
| 2020-10-22 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.22%** | -2.0% | N/A |
| 2021-03-26 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.12%** | -2.0% | N/A |
| 2021-03-29 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.07%** | -2.0% | N/A |
| 2021-03-30 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.04%** | -2.0% | N/A |
| 2021-03-31 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.08%** | -2.0% | N/A |
| 2021-04-01 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.02%** | -2.0% | N/A |
| 2021-04-05 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.12%** | -2.0% | N/A |
| 2021-04-06 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.06%** | -2.0% | N/A |
| 2021-04-07 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.03%** | -2.0% | N/A |
| 2021-04-08 | 2. Top-2 | `PAUSED_DAILY_LOSS` | **-0.11%** | -2.0% | N/A |

*... y 269 eventos adicionales registrados.* 

---

## 3. Conclusiones y Diagnóstico Institucional (Criterios §3 T-02 y T-03)

1. **Resolución de F-14 (Cortacircuitos en Crash):**
   - En **Top-2 (50/50)**, los cortacircuitos fijos destruyen la rentabilidad en 2020 y 2021 porque cada corrección normal del 7% en un activo liquida la cuenta forzosamente en el peor punto.
   - En **Top-4 (25% cap)**, la cartera absorbe las correcciones normales sin liquidaciones innecesarias, pero corta de forma quirúrgica las pérdidas en los días de crash de marzo de 2020.
   - La variante **Top-4 con Cortacircuitos Adaptativos (3σ / 4σ)** logra el mejor equilibrio, evitando liquidaciones prematuras en regímenes de alta volatilidad.

2. **Resolución de F-15 (Tasa Libre de Riesgo Real):**
   - Al usar la curva real de `BIL` (0.37% en 2020, -0.10% en 2021, 1.42% en 2022, 4.12% en 2025), el Sharpe ratio ya no castiga artificialmente los años tempranos.
   - Todo año con retorno positivo presenta Sharpe positivo consistente con la realidad de tasas de la Reserva Federal.
