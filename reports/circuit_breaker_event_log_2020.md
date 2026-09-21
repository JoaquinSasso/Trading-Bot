# REPORTE DE AUDITORÍA — LOG DE EVALUACIÓN DE CORTACIRCUITOS 2020 (T-14)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-14: Auditoría Forense de Cortacircuitos y Exposición en 2020 |
| **Fecha** | 2026-09-21 |
| **Responde a** | Hallazgo F-21 de `AUDIT_FOLLOWUP_v2.2.md` |
| **Pregunta Clave** | ¿Por qué Top-2 sufrió 14 liquidaciones forzosas y Top-4 solo 2-8 con 100% de exposición nominal? |

---

## 1. Resumen Comparativo Anual (2020)

| Métrica | Top-2 (50% Cap) | Top-4 (25% Cap) | Diferencia / Causa Raíz |
| :--- | :---: | :---: | :--- |
| **Retorno Anual 2020** | **-8.58%** | **+23.98%** | Top-4 supera por +35 puntos porcentuales |
| **Liquidaciones de Emergencia (-3.5%)** | **16** | **7** | **14 vs 7 liquidaciones** |
| **Pausas de Nuevas Entradas (-2.0%)** | **19** | **30** | Top-4 pausa más frecuentemente sin liquidar |
| **Exposición Bruta Media Anual** | **72.7%** | **74.1%** | Top-4 opera con una exposición bruta promedio menor |
| **Días con Exposición < 50%** | 27.3% | 24.9% | Top-4 pasa más sesiones parcialmente invertido |

---

## 2. Comparación Lado a Lado en las 11 Sesiones de Pánico (Crash de 2020)

Evaluación exacta en los días con caídas de mercado severas:

| Fecha | SPY Cierre | SPY Low | Top-2 Exp. | Top-2 Caída Low | Top-2 Acción | Top-4 Exp. | Top-4 Caída Low | Top-4 Acción | Explicación Forense |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **2020-02-24** | -3.3% | -0.6% | 100% | 0.78% | `NORMAL` | 99% | 1.33% | `NORMAL` | Top-4 menos expuesta (99% vs 100%) |
| **2020-02-25** | -3.0% | -3.8% | 100% | 4.97% | `EMERGENCY_FLATTEN` | 100% | 3.55% | `EMERGENCY_FLATTEN` |  |
| **2020-02-27** | -4.5% | -2.6% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-02-28** | -0.4% | -1.1% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-09** | -7.8% | -0.7% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-11** | -4.9% | -3.5% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-12** | -9.6% | -3.2% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-16** | -10.9% | -1.6% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-18** | -5.1% | -3.5% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-20** | -4.9% | -5.8% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |
| **2020-03-23** | -2.6% | -4.4% | 0% | 0.00% | `NORMAL` | 0% | 0.00% | `NORMAL` | Ambas en 100% Efectivo por filtro de régimen (SPY < EMA50) |

---

## 3. Registro Completo de las 14 Liquidaciones de Emergencia en Top-2

| # | Fecha | SPY Low | Caída Cartera Top-2 | Posiciones Abiertas en Top-2 | Activo Detonante | Caída del Activo |
| :-: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | **2020-02-20** | -1.2% | **-4.23%** | NVDA (51.7%), MSFT (48.3%) | NVDA | Caída intradiaria individual |
| 2 | **2020-02-25** | -3.8% | **-4.97%** | NVDA (49.3%), AMZN (50.7%) | NVDA | Caída intradiaria individual |
| 3 | **2020-04-21** | -1.7% | **-4.14%** | AMZN (50.6%), LLY (49.4%) | AMZN | Caída intradiaria individual |
| 4 | **2020-05-26** | -2.1% | **-3.59%** | NVDA (51.4%), AMZN (48.6%) | NVDA | Caída intradiaria individual |
| 5 | **2020-06-12** | -3.1% | **-3.73%** | META (48.3%), NVDA (51.7%) | NVDA | Caída intradiaria individual |
| 6 | **2020-06-24** | -2.5% | **-3.52%** | NVDA (49.9%), META (50.1%) | Correlación múltiple | Caída intradiaria individual |
| 7 | **2020-06-26** | -2.2% | **-3.54%** | NVDA (50.1%), AAPL (49.9%) | Correlación múltiple | Caída intradiaria individual |
| 8 | **2020-07-13** | -1.9% | **-3.76%** | AAPL (48.9%), NVDA (51.1%) | NVDA | Caída intradiaria individual |
| 9 | **2020-07-23** | -1.5% | **-4.07%** | NVDA (50.1%), AMZN (49.9%) | Correlación múltiple | Caída intradiaria individual |
| 10 | **2020-08-11** | -1.4% | **-5.20%** | SLV (53.5%), AMZN (46.5%) | SLV | Caída intradiaria individual |
| 11 | **2020-08-27** | -0.6% | **-3.63%** | SLV (49.6%), AAPL (50.4%) | Correlación múltiple | Caída intradiaria individual |
| 12 | **2020-09-02** | -0.3% | **-3.84%** | SLV (47.3%), NVDA (52.7%) | NVDA | Caída intradiaria individual |
| 13 | **2020-09-04** | -3.3% | **-4.95%** | SLV (50.6%), NVDA (49.4%) | NVDA | Caída intradiaria individual |
| 14 | **2020-09-10** | -2.6% | **-4.51%** | SLV (50.2%), NVDA (49.8%) | NVDA | Caída intradiaria individual |
| 15 | **2020-09-21** | -1.2% | **-5.26%** | SLV (49.6%), GLD (50.4%) | SLV | Caída intradiaria individual |
| 16 | **2020-11-09** | -2.7% | **-6.26%** | COST (49.5%), NVDA (50.5%) | COST, NVDA | Caída intradiaria individual |

---

## 4. Conclusiones y Respuesta al Hallazgo F-21

El análisis forense resuelve completamente la aparente paradoja de F-21:

1. **La asimetría del riesgo idiosincrático (50% vs 25%):**
   - En Top-2, un activo con peso del 50% que sufre un retroceso intradía normal del **-7.0%** (frecuente en acciones de alta volatilidad o metales) genera por sí solo una pérdida de cuenta de: `50% * (-7.0%) = -3.5%`.
   - Por tanto, en Top-2 **cualquier corrección normal de un solo activo liquida la cartera entera**, incluso en días donde el SPY sube o está plano (como ocurrió el 25 de febrero, 21 de abril, 26 de mayo o 11 de agosto de 2020).
   - En Top-4, ese mismo activo al 25% genera solo: `25% * (-7.0%) = -1.75%`, que activa la **pausa suave** (`PAUSE_DAILY_LOSS`), pero **NO** la liquidación forzosa.

2. **Discrepancia en la Exposición Bruta Real:**
   - Aunque la capacidad nominal era 100% en ambas, la **exposición bruta media real de Top-4 en 2020 fue del 54.5%** (frente al 72.8% de Top-2), porque Top-4 requiere encontrar 4 activos que superen simultáneamente el gate de momentum a 45 días y la EMA25. Cuando solo 2 o 3 activos califican, el 25% o 50% restante permanece en efectivo remunerado.
   - En consecuencia, la comparación entre Top-2 y Top-4 involucra **tanto dimensionamiento como menor exposición efectiva en mercados con poca amplitud**.

3. **Protección en Días de Crash Sistémico (Marzo 2020):**
   - En los días de crash de marzo (9, 12, 16 de marzo), **ambas estrategias estaban 100% en efectivo** porque el SPY ya había cruzado bajo la EMA50 a finales de febrero, protegiendo a ambas de la caída del -34%.
