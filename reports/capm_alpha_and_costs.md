# REPORTE DE AUDITORÍA — JENSEN'S ALPHA, BETA Y MODELO DE COSTOS (T-05 / T-07)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-05 (Alpha por Regresión CAPM) y T-07 (Modelo Institucional de Costos) |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgos F-14, F-15, F-18 y tareas T-05/T-07 de `AUDIT_FOLLOWUP_v2.0.md` |
| **Modelo de Costos Activo** | Spread: 3-5 bps, Slippage: 2-3 bps, Granularidad de Acciones Enteras ($2.000) |
| **Cortacircuitos Activos** | Sí (-2.0% pausa / -3.5% liquidación intradiaria) |
| **Tasa Libre de Riesgo** | Serie dinámica diaria de `BIL` (T-Bills 0-3m) |

---

## 1. Tabla de Alpha por Regresión CAPM con Error Estándar (T-05)

Modelo de regresión: `(r_p - r_f) = alpha + beta * (r_SPY - r_f) + epsilon`.
Sustituye formalmente cualquier diferencia simple de retornos acumulados (F-18).

| Configuración | Período | Retorno Neto | Sharpe Real | Alpha Anualizado (α) | Error Estándar (SE) | t-stat | p-value | Beta (β) | R² |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Top-2** | 2020 | -10.42% | -0.24 | **-11.23%** | ±28.27% | -0.40 | 0.692 | 0.22 | 0.06 |
| **1. Top-2** | 2021 | +15.87% | 0.75 | **-2.66%** | ±21.66% | -0.12 | 0.902 | 0.77 | 0.18 |
| **1. Top-2** | 2022 | -1.38% | -0.10 | **+3.33%** | ±14.76% | +0.23 | 0.822 | 0.23 | 0.13 |
| **1. Top-2** | Trienio 2020-2022 | -1.15% | 0.07 | **-0.52%** | ±12.45% | -0.04 | 0.966 | 0.27 | 0.09 |
| **1. Top-2** | 2025 | +53.65% | 2.18 | **+38.53%** | ±18.64% | +2.07 | 0.040 | 0.20 | 0.04 |
| **1. Top-2** | Muestra Completa 2020-2025 | +145.53% | 0.66 | **+10.83%** | ±8.85% | +1.22 | 0.221 | 0.35 | 0.10 |
| **3. Top-4** | 2020 | +22.63% | 1.01 | **+18.74%** | ±21.39% | +0.88 | 0.382 | 0.20 | 0.09 |
| **3. Top-4** | 2021 | +20.68% | 1.33 | **+3.27%** | ±12.86% | +0.25 | 0.799 | 0.64 | 0.30 |
| **3. Top-4** | 2022 | -1.81% | -0.22 | **+1.70%** | ±10.58% | +0.16 | 0.872 | 0.20 | 0.18 |
| **3. Top-4** | Trienio 2020-2022 | +40.85% | 0.72 | **+10.35%** | ±9.31% | +1.11 | 0.267 | 0.26 | 0.14 |
| **3. Top-4** | 2025 | +32.95% | 2.16 | **+23.36%** | ±11.50% | +2.03 | 0.043 | 0.15 | 0.07 |
| **3. Top-4** | Muestra Completa 2020-2025 | +231.11% | 1.11 | **+15.13%** | ±6.45% | +2.35 | 0.019 | 0.31 | 0.15 |
| **4. Top-4** | 2020 | +30.22% | 1.33 | **+24.03%** | ±20.06% | +1.20 | 0.232 | 0.23 | 0.13 |
| **4. Top-4** | 2021 | +21.21% | 1.34 | **+3.11%** | ±12.88% | +0.24 | 0.810 | 0.67 | 0.32 |
| **4. Top-4** | 2022 | -1.81% | -0.22 | **+1.70%** | ±10.58% | +0.16 | 0.872 | 0.20 | 0.18 |
| **4. Top-4** | Trienio 2020-2022 | +53.03% | 0.89 | **+12.92%** | ±9.04% | +1.43 | 0.153 | 0.27 | 0.16 |
| **4. Top-4** | 2025 | +33.62% | 2.20 | **+23.78%** | ±11.48% | +2.07 | 0.039 | 0.16 | 0.07 |
| **4. Top-4** | Muestra Completa 2020-2025 | +252.42% | 1.18 | **+15.98%** | ±6.30% | +2.53 | 0.011 | 0.33 | 0.16 |

---

## 2. Descomposición del Modelo de Costos y Drag de Fricción (T-07)

| Configuración | Total Operaciones | Costo Spread ($) | Costo Slippage ($) | Fricción Total ($) | Arrastre Total (% cuenta) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Top-2 (50% cap, CB Fijo, Costos)** | 213 | $118.65 | $143.86 | $262.52 | **13.13%** |
| **3. Top-4 (25% cap, CB Fijo, Costos)** | 397 | $138.88 | $169.38 | $308.26 | **15.41%** |
| **4. Top-4 (25% cap, CB Adaptativo 3s/4s, Costos)** | 388 | $147.11 | $179.46 | $326.57 | **16.33%** |

### Cuantificación del Arrastre por Granularidad de Acciones Enteras ($2.000)
- Al comparar Top-4 operando acciones enteras (`floor`) versus acciones fraccionarias continuas en el Trienio 2020-2022:
  - Retorno con Fraccionarios sin fricción: **+52.73%**
  - Retorno con Acciones Enteras y Costos: **+40.85%**
  - **Arrastre combinado de Granularidad + Fricción:** **11.88 puntos porcentuales** en el trienio (~3.96% anual).

---

## 3. Medición Empírica de Granularidad en Universo A (T-13 / F-23)

En respuesta al hallazgo **F-23** de la auditoría v2.2, se ejecutó la simulación sobre el roster real de 18 ETFs con cuenta de $2.000 para el período 2018–2026 (7.6 años, 1.916 sesiones):

| Modalidad de Simulación | Retorno Acumulado | CAGR Anual | Sharpe Real | Max Drawdown | Trades | Costo Fricción ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Fraccionarios Ideales (Sin Costos)** | **+49.16%** | **5.36%** | 0.54 | 4.69% | 1.179 | $0.00 |
| **2. Fraccionarios con Costos (3.5 bps)** | **+41.43%** | 4.63% | 0.41 | 5.02% | 1.159 | $124.75 |
| **3. Acciones Enteras con Costos ($2.000)** | **+41.24%** | **4.61%** | 0.43 | 4.65% | 1.175 | $114.30 |

### Descomposición del Arrastre Anualizado en Universo A:
- **Arrastre por Fricción de Mercado (Spread + Slippage):** **7.73% acumulado** (**100.9 bps/año**).
- **Arrastre por Granularidad de Acciones Enteras (`floor`):** **0.19% acumulado** (**2.5 bps/año** = **0.03% anual**).
- **Arrastre Total Combinado:** **7.92% acumulado** (**74.8 bps/año**).

### Conclusión F-23:
El arrastre por redondeo en Universo A sobre una cuenta de $2.000 es de solo **2.5 bps anuales (0.03% anual)**, ampliamente inferior al umbral crítico del **1.0% anual**. A diferencia de S5 (donde el volumen de operaciones es diario y las acciones unitarias superan los $500), en Universo A la cadencia semanal y los precios unitarios moderados ($40–$177) permiten la ejecución de **acciones enteras con brackets GTC nativos** sin penalización por granularidad. No se requiere sustitución de ETFs en el roster primario.

