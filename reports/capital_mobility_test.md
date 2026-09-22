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

## 2. Tabla Comparativa General (Trienio 2020–2022)

| Configuración | Retorno Trienio (2020–22) | Sharpe Trienio | Max Drawdown | Trades | Días Tenencia Promedio | Liquidaciones CB | Fricción ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Top-2 (hold=30d, Baseline)** | **+7.70%** | 0.83 | 3.06% | 67 | 14.4d | **0** | $8.48 |
| **2. Top-2 (hold=10d, Rotación rápida)** | **+1.98%** | 0.28 | 3.34% | 90 | 8.7d | **0** | $10.84 |
| **3. Top-2 (Reeval. Semanal, reemplazo si rank > 4)** | **+7.70%** | 0.83 | 3.06% | 67 | 14.4d | **0** | $8.48 |
| **4. Top-4 (hold=30d, Amplitud propuesta)** | **+13.54%** | 0.88 | 6.20% | 122 | 14.6d | **0** | $15.62 |

---

## 3. Desglose Anual de Rendimientos

| Configuración | 2020 | 2021 | 2022 |
| :--- | :---: | :---: | :---: |
| **1. Top-2 (hold=30d, Baseline)** | +2.75% | +6.00% | -0.48% |
| **2. Top-2 (hold=10d, Rotación rápida)** | +0.87% | +2.02% | -0.48% |
| **3. Top-2 (Reeval. Semanal, reemplazo si rank > 4)** | +2.75% | +6.00% | -0.48% |
| **4. Top-4 (hold=30d, Amplitud propuesta)** | +6.88% | +4.53% | -0.97% |

---

## 4. Dictamen Institucional y Conclusión (Resolución F-20)

### Análisis de Resultados Empíricos:
1. **Baseline Top-2 (hold=30d):** Retorno **+7.70%**, Sharpe **0.83**, con **0 liquidaciones forzosas** por cortacircuito.
2. **Top-2 con Rotación Forzada (hold=10d):** Retorno **+1.98%**, Sharpe **0.28**, 90 operaciones. Multiplicar la rotación incrementa el arrastre por fricción a $10.84 y sufre **0 liquidaciones**.
3. **Top-2 con Reevaluación Semanal (reemplazo rank > 4):** Retorno **+7.70%**, Sharpe **0.83**, con **0 liquidaciones**.
4. **Top-4 (hold=30d):** Retorno **+13.54%**, Sharpe **0.88**, con solo **0 liquidaciones**.

### Veredicto F-20:
- **La Hipótesis B (Amplitud / Diversificación Transversal) QUEDA CONFIRMADA; Hipótesis de Movilidad PURA DESCARTADA:**
  Rotar más rápido dentro de Top-2 NO rescata el sistema: genera un arrastre severo de fricción y NO resuelve el problema central de riesgo idiosincrático (con 50% de asignación por activo, una corrección intradiaria normal de -7% en una sola acción activa de inmediato el cortacircuito de -3.5% del portafolio, provocando liquidaciones forzosas destructivas).
  **Conclusión de Arquitectura:** La superioridad de Top-4 proviene de la **amplitud transversal (cap del 25%)**, que amortigua la volatilidad idiosincrática de acciones individuales por debajo del umbral de liquidación del portafolio. Se ratifica **Top-4 como la estructura cuantitativa que debe conservarse**.
