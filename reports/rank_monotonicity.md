# REPORTE DE AUDITORÍA — TEST DE MONOTONICIDAD DEL RANKING (T-01)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-01: Verificación de Monotonicidad de la Señal de Momentum |
| **Fecha** | 2026-09-20 |
| **Responde a** | Hallazgo F-13 de `docs/AUDIT_FOLLOWUP_v2.0.md` |
| **Media Universo 14 Activos** | +2.95% a 45 días |
| **Media Universo GICS** | +1.68% a 45 días |

---

## 1. Tabla A: Comparación de Configuraciones Top-N

Simulación sobre el universo de 14 activos, mismo filtro de régimen macro (SPY > EMA50) y mismas salidas (trailing EMA25).

| Configuración | 2020 | 2021 | 2022 | Trienio 2020-22 | Sharpe | MaxDD |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Top-2 (50% c/u)** | +3.11% | +6.21% | -0.72% | **+8.24%** | -0.36 | 3.87% |
| **Top-4 (25% c/u)** | +9.00% | +6.26% | -1.54% | **+15.51%** | 0.19 | 6.28% |
| **Top-6 (16.7% c/u)** | +6.97% | +13.11% | -2.08% | **+20.54%** | 0.42 | 6.57% |
| **Top-8 (12.5% c/u)** | +8.02% | +12.20% | -2.40% | **+20.26%** | 0.39 | 6.69% |
| **Equiponderado Elegibles** | +9.68% | +10.77% | +0.20% | **+20.60%** | 0.45 | 5.28% |

---

## 2. Tabla B: Retorno Forward a 45 Días por Posición de Ranking (14 Activos)

Muestreo semanal de cada rebalanceo de 2020 a 2022. Cada observación mide el retorno real obtenido a 45 sesiones futuras por el activo que ocupaba ese puesto de ranking.

| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media univ | p-value |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rank 1** | 143 | **+4.50%** | +3.21% | 15.63% | +1.19 | 0.237 |
| **Rank 2** | 143 | **+4.90%** | +5.42% | 13.40% | +1.74 | 0.084 |
| **Rank 3** | 143 | **+4.59%** | +4.48% | 13.20% | +1.49 | 0.139 |
| **Rank 4** | 143 | **+3.85%** | +3.52% | 15.09% | +0.72 | 0.474 |
| **Rank 5** | 143 | **+3.76%** | +3.58% | 15.53% | +0.62 | 0.533 |
| **Rank 6** | 143 | **+2.82%** | +2.84% | 14.64% | -0.10 | 0.917 |
| **Rank 7** | 143 | **+1.08%** | +1.21% | 14.65% | -1.53 | 0.129 |
| **Rank 8** | 143 | **+0.66%** | +1.85% | 14.91% | -1.83 | 0.069 |
| **Rank 9** | 143 | **+4.46%** | +4.40% | 13.17% | +1.37 | 0.173 |
| **Rank 10** | 143 | **+1.73%** | +1.13% | 14.30% | -1.02 | 0.309 |
| **Rank 11** | 143 | **+2.51%** | +0.23% | 14.48% | -0.36 | 0.720 |
| **Rank 12** | 143 | **+1.00%** | +1.91% | 13.26% | -1.75 | 0.081 |
| **Rank 13** | 143 | **+2.47%** | +2.04% | 18.49% | -0.31 | 0.755 |

---

## 3. Tabla C: Retorno Forward a 45 Días por Ranking (Sectores GICS)

| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media univ | p-value |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Rank 1** | 143 | **+1.19%** | +1.37% | 11.26% | -0.52 | 0.605 |
| **Rank 2** | 143 | **+2.34%** | +2.32% | 10.34% | +0.76 | 0.450 |
| **Rank 3** | 143 | **+1.22%** | +2.40% | 10.57% | -0.52 | 0.603 |
| **Rank 4** | 143 | **+2.49%** | +3.87% | 9.97% | +0.97 | 0.331 |
| **Rank 5** | 143 | **+2.15%** | +3.18% | 10.16% | +0.56 | 0.580 |
| **Rank 6** | 143 | **+2.38%** | +2.26% | 10.21% | +0.82 | 0.412 |
| **Rank 7** | 143 | **+1.38%** | +1.08% | 9.98% | -0.36 | 0.719 |
| **Rank 8** | 143 | **+1.23%** | +1.88% | 11.16% | -0.49 | 0.626 |
| **Rank 9** | 143 | **+1.05%** | +2.75% | 11.74% | -0.65 | 0.520 |
| **Rank 10** | 143 | **+1.28%** | +2.84% | 10.21% | -0.47 | 0.638 |
| **Rank 11** | 143 | **+1.78%** | +3.86% | 17.84% | +0.07 | 0.946 |

---

## 4. Diagnóstico e Interpretación Institucional (Criterios §3 T-01)

- **Top Ranks (1-2) vs Mid Ranks (3-4):** Rank 1 (+4.50%) y Rank 2 (+4.90%) vs Rank 3 (+4.59%) y Rank 4 (+3.85%).
- **Top Ranks vs Fondo de Tabla (Ranks 10-13):** Media Ranks 1-4 (+4.46%) vs Fondo (+1.93%).


---

## 4. Sección v2 — Corrección Metodológica por Solapamiento y Cartera Long-Short (T-10 / F-20)

En respuesta estricta a los dos defectos señalados en el hallazgo **F-20**:
1. **Corrección de Solapamiento:** Retornos a 45 días muestreados semanalmente presentan una estructura MA(8). Se aplica la corrección de varianza de **Newey-West con lag = 8** y control con **muestreo no solapado (cada 45 sesiones)**.
2. **Niveles vs. Spreads:** Se aísla el factor común de mercado evaluando la cartera long-short: **Larga en `rank(1,2)` y Corta en `rank(6,7)`**.

### 4.1. Resultados S5 (13 Activos, 2020–2025)

| Cartera / Métrica | Retorno / Spread Medio (45d) | Error Estándar Clásico OLS | t-stat OLS (solapado) | p-value OLS | Error Estándar Newey-West (lag=8) | t-stat Newey-West | p-value Newey-West | Muestreo No Solapado (t-stat / p-val) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Nivel Absoluto: Long rank(1,2)** | **+4.70%** | ±0.99% | +4.76 | 0.000 | **±2.03%** | **+2.32** | **0.021** | t = +1.49 (p = 0.156) |
| **Spread Long-Short: rank(1,2) − rank(6,7)** | **+2.75%** | ±1.02% | +2.70 | 0.008 | **±1.35%** | **+2.04** | **0.042** | t = +0.79 (p = 0.444) |
| **Spread Extremo: rank(1,2) − rank(12,13)** | **+2.96%** | ±1.14% | +2.59 | 0.011 | **±2.01%** | **+1.47** | **0.140** | t = +0.01 (p = 0.992) |

### 4.2. Resultados Universo A por Bloques (2020–2025)

| Bloque Evaluado | Activos | Spread Medio (45d) | Error Estándar Newey-West | t-stat Newey-West | p-value Newey-West | Muestreo No Solapado (t-stat / p-val) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Sectores GICS (rank 1,2 − 6,7)** | 11 ETFs sectoriales | **-0.12%** | ±1.06% | -0.11 | **0.912** | t = -1.23 (p = 0.238) |
| **Sectores GICS (rank 1,2 − bottom 2)**| 11 ETFs sectoriales | **+0.23%** | ±1.81% | +0.13 | **0.897** | t = -0.93 (p = 0.369) |
| **Metales (rank 1 − rank 2)** | GLDM, SLV | **+2.03%** | ±2.02% | +1.00 | **0.316** | t = +0.19 (p = 0.853) |
| **Renta Fija (rank 1 − rank 2)** | IEF, TIP | **+0.23%** | ±0.33% | +0.70 | **0.486** | t = -0.15 (p = 0.880) |
| **Internacional (rank 1 − rank 2)** | IEFA, IEMG | **-0.70%** | ±0.44% | -1.61 | **0.108** | t = -0.32 (p = 0.754) |

### 4.3. Dictamen y Conclusión del Hallazgo F-13 / F-20

Bajo el criterio formal establecido por el auditor en §3 T-10:
> *'F-13 se cierra únicamente si el spread long-short es positivo y significativo al 5% bajo errores Newey-West. Si no lo es, el diseño de selección por ranking debe simplificarse a equiponderación sobre los elegibles que pasen el gate absoluto.'*

1. **En S5:**
   - La corrección de Newey-West infla el error estándar (1.35% vs 1.02% OLS) exactamente en el rango predicho por el auditor.
   - El spread long-short `rank(1,2) − rank(6,7)` registra un retorno medio de **+2.75%** con t-stat = **+2.04** y p-value = **0.042**. Es estadísticamente significativo al 5%.
   - El spread extremo `rank(1,2) − rank(12,13)` es de **+2.96%** (t = +1.47, p = 0.140).

2. **En Universo A:**
   - En el bloque de sectores GICS, el spread `rank(1,2) − rank(6,7)` es de **-0.12%** (t = -0.11, p = 0.912), confirmando que el ranking transversal de sectores ETFs no genera una separación estadísticamente discernible (0.912 > 0.05).
   - **Consecuencia de Diseño (mandato T-16):** Dado que el ranking en Universo A no discrimina con significancia estadística, se ratifica la necesidad de ejecutar T-16 para evaluar la simplificación hacia equiponderación por volatilidad inversa sobre los instrumentos que superen el gate absoluto sin ranking sectorial.
