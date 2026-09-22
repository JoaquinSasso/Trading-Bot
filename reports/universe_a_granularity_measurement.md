# REPORTE DE AUDITORÍA — MEDICIÓN EMPÍRICA DE GRANULARIDAD EN UNIVERSO A (T-13)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-13: Medición de Arrastre por Acciones Enteras sobre Cuenta de $2.000 |
| **Fecha** | 2026-09-21 |
| **Responde a** | Hallazgos F-23 y Criterio §3 T-13 de `AUDIT_FOLLOWUP_v2.2.md` |
| **Tamaño de Cuenta Evaluado** | $2.000 USD reales |

---

## 1. Tabla Comparativa de Rendimiento y Descomposición del Arrastre

| Modalidad de Simulación | Retorno Acumulado (Trienio 2020–2022) | CAGR Anual | Sharpe Real | Max Drawdown | Trades | Costo Fricción ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Fraccionarios Ideales (Sin Costos)** | **+22.04%** | **6.87%** | 1.02 | 5.08% | 125 | $0.00 |
| **2. Fraccionarios con Costos (3.5 bps)** | **+15.36%** | 4.88% | 0.72 | 5.78% | 119 | $27.09 |
| **3. Acciones Enteras con Costos ($2.000)** | **+14.54%** | **4.63%** | 0.72 | 5.20% | 119 | $25.10 |

### Descomposición Precisa del Drag Anual:
- **Arrastre de Fricción Directa (Spread + Slippage):** **6.69 puntos porcentuales** acumulados (**222.8 bps/año**).
- **Arrastre por Granularidad de Acciones Enteras:** **0.82 puntos porcentuales** acumulados (**27.4 bps/año** = **0.27% anual**).
- **Arrastre Total Combinado:** **7.51 puntos porcentuales** (**223.8 bps/año**).

---

## 2. Análisis de Precios Unitarios y Decisión de Roster

Criterio del auditor: *'Si el arrastre por redondeo supera el 1.0% anual, evaluar reemplazar los ETF de precio unitario alto por equivalentes de menor precio'*

- **Resultado Medido:** El arrastre por granularidad anualizado es de **0.27% anual** (27.4 bps).
- **Diagnóstico:** Se sitúa **por debajo del umbral del 1.0% anual** (0.27% <= 1.0%). El roster actual es apto para operar con acciones enteras y brackets GTC nativos sin penalización excesiva.
