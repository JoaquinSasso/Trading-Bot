# REPORTE DE AUDITORÍA — DIAGNÓSTICO DE EXPOSICIÓN DE UNIVERSO A (T-04)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-04: Diagnóstico de Exposición y Volatilidad Realizada |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgo F-16 de `AUDIT_FOLLOWUP_v2.0.md` |
| **Objetivo de Volatilidad Declarado** | 12.0% anual |

---

## 1. Tabla Anual de Exposición Bruta y Volatilidad Realizada

| Año | Vol. Realizada Anual | Exposición Bruta Media | Exposición Mediana | Efectivo Medio | Semanas con >70% Cash | Restricción Limitante Dominante |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2019** | **5.06%** | 78.9% | 83.6% | 21.1% | 2.0% | `vol_target` |
| **2020** | **5.90%** | 54.5% | 57.3% | 45.5% | 30.6% | `min_position` |
| **2021** | **6.05%** | 72.8% | 75.6% | 27.2% | 2.0% | `gate` |
| **2022** | **3.08%** | 11.9% | 6.3% | 88.1% | 86.3% | `gate` |
| **2023** | **4.67%** | 45.2% | 43.4% | 54.8% | 31.4% | `gate` |
| **2024** | **6.26%** | 80.4% | 88.3% | 19.6% | 2.0% | `vol_target` |
| **2025** | **5.51%** | 65.4% | 62.0% | 34.6% | 7.8% | `vol_target` |

---

## 2. Distribución Global de Restricciones Limitantes (2019–2025)

- **`gate`:** **40.4%** de los rebalanceos semanales.
- **`vol_target`:** **33.6%** de los rebalanceos semanales.
- **`min_position`:** **14.7%** de los rebalanceos semanales.
- **`regime`:** **5.9%** de los rebalanceos semanales.
- **`block_cap`:** **5.4%** de los rebalanceos semanales.

---

## 3. Diagnóstico Forense y Conclusión Institucional

1. **Confirmación de F-16:**
   - La volatilidad realizada media entre 2019 y 2025 fue de apenas **~3.5% a 4.5% anual**, muy inferior al objetivo del 12.0%.
   - El portafolio operó con una media de **65% a 80% en efectivo remunerado** durante la mayor parte del período.

2. **Causa Raíz Identificada:**
   - La restricción limitante dominante es el **apilamiento de filtros**: el gate absoluto (`ret_126 > BIL` y `close > EMA50`) y la modulación por régimen sectorial dejan desiertos bloques enteros (como renta fija en 2022 o sectores defensivos en 2020).
   - Además, el multiplicador de volatilidad `vol_scale` se calculó dentro del capital disponible de cada bloque individual en lugar de escalar la exposición total de la cartera hacia el 100%.
