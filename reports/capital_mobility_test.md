# REPORTE DE AUDITORÍA — TEST DE MOVILIDAD DE CAPITAL EN S5 (T-15)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-15: Test de Movilidad de Capital vs Amplitud de Posiciones |
| **Fecha** | 2026-09-21 |
| **Responde a** | Hallazgos F-20, F-13 y Mandato §3 T-15 de `AUDIT_FOLLOWUP_v2.2.md` |
| **Modelo de Costos** | Costos reales activos (spread 3-5 bps, slippage 2-3 bps, acciones enteras $2.000) |
| **Cortacircuitos** | Activos (-2.0% pausa / -3.5% liquidación intradiaria) |

---

## 1. Motivación y Formulación de Hipótesis (F-20)

> *'Sobre la "trampa de movilidad de capital": si Top-4 supera a Top-2 porque rota más rápido y no porque seleccione mejor, la ventaja proviene de la movilidad del capital, no del ranking. Es directamente testeable (T-15) y la respuesta cambia qué componente del diseño hay que conservar.'*

### Criterio Falsable del Auditor:
- **Hipótesis A (Movilidad de Capital):** Si acelerar la rotación en Top-2 (hold=10d o reemplazo semanal cuando cae del Top-4) iguala el desempeño de Top-4, la ventaja reside en no atrapar capital en posiciones rezagadas, y debe conservarse el mecanismo de movilidad.
- **Hipótesis B (Amplitud / Diversificación):** Si solo Top-4 funciona y las variantes de rotación rápida en Top-2 siguen destruidas por costos o whipsaws, la ventaja proviene de la amplitud transversal (reducir el peso de 50% a 25% para no detonar cortacircuitos en correcciones normales).

---

## 2. Tabla Comparativa General (Trienio y Muestra Completa)

| Configuración | Retorno Trienio (2020–22) | Sharpe Trienio | Retorno Muestra (2020–25) | Sharpe Muestra | Max Drawdown | Trades | Días Tenencia Promedio | Liquidaciones CB (2020–25) | Fricción ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Top-2 (hold=30d, Baseline)** | **-1.15%** | 0.07 | **+145.43%** | **0.66** | 30.06% | 215 | 14.5d | **28** | $264.58 |
| **2. Top-2 (hold=10d, Rotación rápida)** | **+3.91%** | 0.15 | **+136.69%** | **0.63** | 23.12% | 335 | 9.2d | **32** | $464.62 |
| **3. Top-2 (Reeval. Semanal, reemplazo si rank > 4)** | **+2.51%** | 0.13 | **+127.68%** | **0.60** | 24.04% | 237 | 13.1d | **31** | $297.90 |
| **4. Top-4 (hold=30d, Amplitud propuesta)** | **+40.85%** | 0.72 | **+230.97%** | **1.11** | 20.34% | 401 | 15.6d | **9** | $311.18 |

---

## 3. Desglose Anual de Rendimientos

| Configuración | 2020 | 2021 | 2022 | 2025 |
| :--- | :---: | :---: | :---: | :---: |
| **1. Top-2 (hold=30d, Baseline)** | -10.46% | +15.81% | -1.38% | +53.59% |
| **2. Top-2 (hold=10d, Rotación rápida)** | -13.77% | +30.17% | -7.03% | +23.57% |
| **3. Top-2 (Reeval. Semanal, reemplazo si rank > 4)** | -11.03% | +18.99% | -8.62% | +37.00% |
| **4. Top-4 (hold=30d, Amplitud propuesta)** | +22.57% | +20.63% | -1.81% | +32.90% |

---

## 4. Dictamen Institucional y Conclusión (Resolución F-20)

### Análisis de Resultados Empíricos:
1. **Baseline Top-2 (hold=30d):** Retorno **+145.43%**, Sharpe **0.66**, con **28 liquidaciones forzosas** por cortacircuito.
2. **Top-2 con Rotación Forzada (hold=10d):** Retorno **+136.69%**, Sharpe **0.63**, 335 operaciones. Multiplicar la rotación incrementa el arrastre por fricción a $464.62 y sufre **32 liquidaciones**.
3. **Top-2 con Reevaluación Semanal (reemplazo rank > 4):** Retorno **+127.68%**, Sharpe **0.60**, con **31 liquidaciones**.
4. **Top-4 (hold=30d):** Retorno **+230.97%**, Sharpe **1.11**, con solo **9 liquidaciones**.

### Veredicto F-20:
- **La Hipótesis B (Amplitud / Diversificación Transversal) QUEDA CONFIRMADA; Hipótesis de Movilidad PURA DESCARTADA:**
  Rotar más rápido dentro de Top-2 NO rescata el sistema: genera un arrastre severo de fricción y NO resuelve el problema central de riesgo idiosincrático (con 50% de asignación por activo, una corrección intradiaria normal de -7% en una sola acción activa de inmediato el cortacircuito de -3.5% del portafolio, provocando liquidaciones forzosas destructivas).
  **Conclusión de Arquitectura:** La superioridad de Top-4 proviene de la **amplitud transversal (cap del 25%)**, que amortigua la volatilidad idiosincrática de acciones individuales por debajo del umbral de liquidación del portafolio. Se ratifica **Top-4 como la estructura cuantitativa que debe conservarse**.
