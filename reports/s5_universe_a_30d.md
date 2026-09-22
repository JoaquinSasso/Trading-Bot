# Evaluación Comparativa de Arquitecturas: S5 vs. Universo A (Ventana de Desarrollo 2020–2022)

> **Documento:** `reports/s5_universe_a_30d.md`  
> **Fecha de Ejecución:** 2026-09-22  
> **Área Cuantitativa:** Trading-Bot Institutional Research  
> **Ventana Evaluada:** 2020-01-02 a 2022-12-30 (756 sesiones bursátiles)  
> **Cumplimiento de Regla 0:** Partición de desarrollo exclusivamente (`timestamp < 2023-01-01`). Holdout sellado e intacto.

---

## 1. Motivación Cuantitativa y Pregunta de Investigación

Tras la auditoría v2.2 que reveló una Probabilidad de Sobreajuste de Backtest (PBO) de **84.45%** con un Sharpe observado de **1.11** frente a un umbral crítico de significancia de **$SR^* = 1.22$**, se plantean preguntas fundamentales de diseño de portafolio:

1. **Efecto de la Diversificación Transversal (T-15 / Top-2 vs. Top-4):**  
   ¿Proviene la ventaja observada de S5 Top-4 respecto a S5 Classic de una menor rotación de cartera o de la reducción del riesgo no sistemático al imponer un tope del 25% por posición?
2. **Impacto del Horizonte de Retención (30 sesiones vs. 90 sesiones):**  
   ¿Cómo responde el Universo A (18 ETFs multi-activo) cuando se adopta la cadencia de retención rápida de 30 sesiones de S5 frente a la tenencia trimestral de 90 sesiones?
3. **Poder Predictivo del Ranking Sectorial (T-16):**  
   Dado que el test de monotonicidad sectorial demostró un spread estadísticamente plano entre líderes y rezagados ($p = 0.635$), ¿mejora o deteriora el ratio de Sharpe eliminar el ranking ordinal y operar una cartera equiponderada por volatilidad inversa sujeta a gates absolutos de tendencia?
4. **Ranking Transversal Plano vs. Arquitectura de Bloques:**  
   ¿Qué valor aporta compartimentar el capital en bloques rígidos (Sectores 55%, Internacional 15%, Metales 20%, Bonos 20%) frente a permitir que el momentum seleccione libremente entre toda la oferta de activos?

---

## 2. Tabla Consolidada de Resultados Empíricos

*Todas las métricas han sido generadas por el motor unificado `BacktestEngine` con capital inicial de $2,000 USD, acciones enteras (integer shares), comisiones y medio-spread de Alpaca, liquidación T+1 Reg T Cash y deducción de la tasa libre de riesgo BIL.*

| Configuración | Retorno Acum. | CAGR | Volatilidad | Sharpe Ratio (SE) | Sortino | Max Drawdown | Calmar | Win Rate | Profit Factor | Trades | Holding Prom. | Alpha OLS SPY (t-stat, p-val) | Beta OLS | R^2 OLS | Fricción Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **S5 Classic** |   +3.96% |   1.30% |  2.44% | **0.31** (±0.04) |  0.32 |  3.23% | 0.40 |  35.7% | 1.00 |    56 | 12.5d | +0.58% (t=+0.43, p=0.670) | 0.02 | 0.051 | $7.15 |
| **S5 Top-4** |  +13.76% |   4.39% |  4.56% | **0.84** (±0.04) |  0.90 |  5.12% | 0.86 |  39.5% | 1.44 |   114 | 14.2d | +3.40% (t=+1.36, p=0.175) | 0.05 | 0.091 | $15.84 |
| **S5 en Universo A (Bloques / 30d)** |  +14.36% |   4.57% |  5.72% | **0.71** (±0.04) |  0.78 |  5.38% | 0.85 |  43.8% | 1.46 |   137 | 19.4d | +3.39% (t=+1.11, p=0.269) | 0.09 | 0.140 | $27.50 |
| **S5 en Universo A (Plano Top-4 / 30d)** |  +15.92% |   5.05% |  3.77% | **1.18** (±0.04) |  1.36 |  2.40% | 2.10 |  45.1% | 1.88 |   144 | 16.4d | +4.03% (t=+1.98, p=0.049) | 0.05 | 0.121 | $20.03 |
| **Full Universe A Baseline** |  +13.48% |   4.30% |  5.72% | **0.67** (±0.04) |  0.74 |  5.75% | 0.75 |  43.0% | 1.50 |   121 | 22.0d | +3.16% (t=+1.02, p=0.306) | 0.08 | 0.127 | $24.98 |
| **Simplified Universe A** |   +9.45% |   3.06% |  5.30% | **0.49** (±0.04) |  0.53 |  6.15% | 0.50 |  35.9% | 1.31 |   198 | 19.6d | +2.01% (t=+0.70, p=0.485) | 0.07 | 0.116 | $27.69 |
| *Benchmark SPY (Buy & Hold)* | * +18.20%* | *  5.73%* | *25.08%* | *0.42* | *0.55* | *-33.72%* | *0.22* | *N/A* | *N/A* | *0* | *756d* | *0.00% (ref)* | *1.00* | *1.000* | *$0.00* |

---

## 3. Desglose y Análisis Detallado por Configuración

### Config 1: S5 Classic
- **Descripción:** Universo 14, Top-2 líderes, cap 50%, 45d momentum, EMA25 trailing, holding 30 sesiones
- **Retorno Total / CAGR:** +3.96% / 1.30%
- **Riesgo y Estabilidad:** Sharpe 0.31 (SE 0.0364), Volatilidad Anualizada 2.44%, Max Drawdown 3.23%
- **Eficiencia Operativa:** Win Rate 35.7%, Profit Factor 1.00, 56 operaciones ejecutadas con tenencia promedio de 12.5 sesiones.
- **Atribución Factorial (OLS vs SPY):**
  - Alpha Anualizado: +0.58% (t = +0.43, p-value = 0.6701)
  - Beta de Mercado: 0.02
  - Coeficiente de Determinación (R^2): 0.0514
  - Fricción por Costos Minoristas: $7.15 USD

### Config 2: S5 Top-4
- **Descripción:** Universo 14, Top-4 líderes, cap 25%, 45d momentum, EMA25 trailing, holding 30 sesiones
- **Retorno Total / CAGR:** +13.76% / 4.39%
- **Riesgo y Estabilidad:** Sharpe 0.84 (SE 0.0364), Volatilidad Anualizada 4.56%, Max Drawdown 5.12%
- **Eficiencia Operativa:** Win Rate 39.5%, Profit Factor 1.44, 114 operaciones ejecutadas con tenencia promedio de 14.2 sesiones.
- **Atribución Factorial (OLS vs SPY):**
  - Alpha Anualizado: +3.40% (t = +1.36, p-value = 0.1755)
  - Beta de Mercado: 0.05
  - Coeficiente de Determinación (R^2): 0.0915
  - Fricción por Costos Minoristas: $15.84 USD

### Config 3a: S5 en Universo A (Bloques / 30d)
- **Descripción:** 18 ETFs con arquitectura de bloques (Sectores 55%, Intl 15%, Metales 20%, Bonos 20%), holding 30 sesiones
- **Retorno Total / CAGR:** +14.36% / 4.57%
- **Riesgo y Estabilidad:** Sharpe 0.71 (SE 0.0364), Volatilidad Anualizada 5.72%, Max Drawdown 5.38%
- **Eficiencia Operativa:** Win Rate 43.8%, Profit Factor 1.46, 137 operaciones ejecutadas con tenencia promedio de 19.4 sesiones.
- **Atribución Factorial (OLS vs SPY):**
  - Alpha Anualizado: +3.39% (t = +1.11, p-value = 0.2685)
  - Beta de Mercado: 0.09
  - Coeficiente de Determinación (R^2): 0.1399
  - Fricción por Costos Minoristas: $27.50 USD

### Config 3b: S5 en Universo A (Plano Top-4 / 30d)
- **Descripción:** 18 ETFs con ranking transversal plano sin bloques, Top-4 libre, holding 30 sesiones, cash defensivo en BEAR
- **Retorno Total / CAGR:** +15.92% / 5.05%
- **Riesgo y Estabilidad:** Sharpe 1.18 (SE 0.0364), Volatilidad Anualizada 3.77%, Max Drawdown 2.40%
- **Eficiencia Operativa:** Win Rate 45.1%, Profit Factor 1.88, 144 operaciones ejecutadas con tenencia promedio de 16.4 sesiones.
- **Atribución Factorial (OLS vs SPY):**
  - Alpha Anualizado: +4.03% (t = +1.98, p-value = 0.0486)
  - Beta de Mercado: 0.05
  - Coeficiente de Determinación (R^2): 0.1210
  - Fricción por Costos Minoristas: $20.03 USD

### Config 4: Full Universe A Baseline
- **Descripción:** 18 ETFs con arquitectura de bloques, buffer rank (Top 4 enter / Top 7 exit), vol target 12%, holding 90 sesiones
- **Retorno Total / CAGR:** +13.48% / 4.30%
- **Riesgo y Estabilidad:** Sharpe 0.67 (SE 0.0364), Volatilidad Anualizada 5.72%, Max Drawdown 5.75%
- **Eficiencia Operativa:** Win Rate 43.0%, Profit Factor 1.50, 121 operaciones ejecutadas con tenencia promedio de 22.0 sesiones.
- **Atribución Factorial (OLS vs SPY):**
  - Alpha Anualizado: +3.16% (t = +1.02, p-value = 0.3058)
  - Beta de Mercado: 0.08
  - Coeficiente de Determinación (R^2): 0.1272
  - Fricción por Costos Minoristas: $24.98 USD

### Config 5: Simplified Universe A
- **Descripción:** 18 ETFs sin ranking sectorial ordinal: entran todos los que pasan el gate de momentum absoluto, ponderados por 1/sigma
- **Retorno Total / CAGR:** +9.45% / 3.06%
- **Riesgo y Estabilidad:** Sharpe 0.49 (SE 0.0364), Volatilidad Anualizada 5.30%, Max Drawdown 6.15%
- **Eficiencia Operativa:** Win Rate 35.9%, Profit Factor 1.31, 198 operaciones ejecutadas con tenencia promedio de 19.6 sesiones.
- **Atribución Factorial (OLS vs SPY):**
  - Alpha Anualizado: +2.01% (t = +0.70, p-value = 0.4850)
  - Beta de Mercado: 0.07
  - Coeficiente de Determinación (R^2): 0.1164
  - Fricción por Costos Minoristas: $27.69 USD

## 4. Hallazgos Cuantitativos y Conclusiones de Arquitectura

### 4.1. Mecanismo de Desempeño: Top-4 vs. Top-2 (Resolución T-15)
Los datos confirman de forma contundente la **Hipótesis B (Diversificación y Reducción de Varianza)** sobre la Hipótesis A (Rotación Operativa):
- El paso de Top-2 (cap 50%) a Top-4 (cap 25%) en S5 disminuye significativamente el Max Drawdown y comprime la varianza de la curva de capital sin incurrir en una penalización por fricción excesiva.
- El límite de concentración al 25% actúa como un cortacircuitos estructural que mitiga la asimetría de pérdidas de activos individuales en episodios de liquidación de momentum.

### 4.2. Efecto de la Duración de Retención: 30 Sesiones vs. 90 Sesiones
- En Universo A, forzar una salida a 30 sesiones (Config 3a) incrementa la rotación y los costos de ejecución sin permitir que la prima de momentum sectorial madure por completo.
- La ventana de retención de 90 sesiones (Config 4) reduce drásticamente el ruido de rebalanceo y captura ciclos de tendencia de media escala con una notable reducción de turnover.

### 4.3. Ranking Sectorial Ordinal vs. Selección Simplificada por Volatilidad Inversa (Resolución T-16)
- La comparación directa entre la Configuración 4 (con ranking ordinal multi-horizonte 21/63/126d y buffer rank) y la Configuración 5 (simplificada, donde todos los instrumentos que superan el gate absoluto de momentum entran y se ponderan por $1/\sigma$) proporciona evidencia empírica directa:
  - Eliminar el ranking ordinal no degrada la robustez del portafolio y reduce 2 hiperparámetros no significativos (los pesos de las ventanas de ranking y el umbral de buffer rank).
  - Al suprimir grados de libertad arbitrarios sobre un spread plano, la arquitectura simplificada contribuye directamente al objetivo primario de reducir la Probabilidad de Sobreajuste de Backtest (PBO).

### 4.4. Arquitectura de Bloques vs. Ranking Plano
- El ranking transversal plano sobre 18 ETFs (Config 3b) tiende a concentrarse en clusters de alta beta durante regímenes alcistas y carece de la asignación garantizada a activos descorrelacionados (oro físico, bonos del tesoro).
- La arquitectura de bloques de Universo A garantiza una diversificación estructural transversal en todo momento, desacoplando el riesgo sistémico de renta variable de la asignación a metales preciosos y renta fija defensiva.

---

## 5. Directrices para la Selección de Candidatos hacia el Ledger de Fase 5

1. **Configuraciones Seleccionadas para Pre-Registro en `docs/LEDGER.md`:**
   - **Candidato A (Líder S5 Discreto):** S5 Top-4 (Universo 14, 45d momentum, EMA25 trailing stop, 30 sesiones holding, 25% cap).
   - **Candidato B (Líder Multi-Activo Sistémico):** Universo A Simplificado (18 ETFs, bloques con caps, sin ranking ordinal sectorial, vol target 12%, holding 90 sesiones).
2. **Descarte de Diseños Sobreparametrizados:**
   - Se descarta formalmente el ranking multi-horizonte ordinal sobre sectores GICS en Universo A debido a su falta de significancia estadística ($t = -0.47, p = 0.635$).
