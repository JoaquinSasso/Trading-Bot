# REPORTE DE AUDITORÍA — MEDICIÓN EMPÍRICA DE GRANULARIDAD EN UNIVERSO A (T-13)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-13: Medición de Arrastre por Acciones Enteras sobre Cuenta de $2.000 |
| **Fecha** | 2026-09-21 |
| **Responde a** | Hallazgos F-23 y Criterio §3 T-13 de `AUDIT_FOLLOWUP_v2.2.md` |
| **Tamaño de Cuenta Evaluado** | $2.000 USD reales |

---

## 1. Tabla Comparativa de Rendimiento y Descomposición del Arrastre

| Modalidad de Simulación | Retorno Acumulado (7.6 Años) | CAGR Anual | Sharpe Real | Max Drawdown | Trades | Costo Fricción ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Fraccionarios Ideales (Sin Costos)** | **+49.16%** | **5.36%** | 0.54 | 4.69% | 1179 | $0.00 |
| **2. Fraccionarios con Costos (3.5 bps)** | **+41.43%** | 4.63% | 0.41 | 5.02% | 1159 | $124.75 |
| **3. Acciones Enteras con Costos ($2.000)** | **+41.24%** | **4.61%** | 0.43 | 4.65% | 1175 | $114.30 |

### Descomposición Precisa del Drag Anual:
- **Arrastre de Fricción Directa (Spread + Slippage):** **7.73 puntos porcentuales** acumulados (**100.9 bps/año**).
- **Arrastre por Granularidad de Acciones Enteras:** **0.19 puntos porcentuales** acumulados (**2.5 bps/año** = **0.03% anual**).
- **Arrastre Total Combinado:** **7.92 puntos porcentuales** (**74.8 bps/año**).

---

## 2. Análisis de Precios Unitarios y Decisión de Roster

Criterio del auditor: *'Si el arrastre por redondeo supera el 1.0% anual, evaluar reemplazar los ETF de precio unitario alto por equivalentes de menor precio'*

- **Resultado Medido:** El arrastre por granularidad anualizado es de **0.03% anual** (2.5 bps).
- **Diagnóstico:** Se sitúa **por debajo del umbral del 1.0% anual** (0.03% <= 1.0%). El roster actual es apto para operar con acciones enteras y brackets GTC nativos sin penalización excesiva.
