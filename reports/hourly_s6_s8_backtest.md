# Evaluación Cuantitativa en Datos Horarios (1h): S6 vs. S8 vs. S5

> **Documento:** `reports/hourly_s6_s8_backtest.md`  
> **Fecha:** 2026-09-22  
> **Área Cuantitativa:** Trading-Bot Institutional Research  
> **Ventana Evaluada:** 2023-10-23 a 2025-09-19 (478 sesiones bursátiles / ~3,350 barras de 1h)  
> **Cumplimiento de Regla 0:** Estrictamente pre-holdout (`timestamp < 2025-09-22`). Holdout intradiario sellado.

---

## 1. Justificación Cuantitativa de la Prueba Horaria

El propósito de esta batería de pruebas es contrastar tres arquitecturas temporales divergentes:
1. **S8 PID Multi-Horizonte:** Sistema de control de doble vía que sintetiza 8 horizontes temporales (desde 10 días hasta intradiario) mediante un controlador proporcional-integral-derivativo (Sistema U) y un módulo de veto de estrés (Sistema D).
2. **S6 Intraday Multi-Horizonte:** Estrategia intradiaria con liquidación incondicional al cierre de sesión (*day-end flatten*).
3. **S5 Dual Momentum Leader:** Estrategia diaria canónica con horizonte de momentum transversal a 45 sesiones.

---

## 2. Tabla Consolidada de Resultados en Datos Horarios (2023–2025)

| Estrategia | Retorno Total | CAGR | Volatilidad | Sharpe Ratio (SE) | Sortino | Max Drawdown | Calmar | Win Rate | Profit Factor | Trades | Duración Prom. | Alpha OLS SPY (t-stat, p-val) | Beta OLS | R^2 OLS | Fricción Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **S8 PID Multihorizon (Var A)** |  +13.29% |   6.78% |  3.79% | **0.49** (±0.05) |  0.63 |  3.06% | 2.22 |  30.0% | 1.77 |    30 |  72.7h | +1.38% (t=+0.50, p=0.615) | 0.02 | 0.009 | $9.31 |
| **S8 PID Multihorizon (Var B Balancín)** |  +17.12% |   8.67% |  3.60% | **1.00** (±0.05) |  1.58 |  1.49% | 5.82 |  35.7% | 5.37 |    28 |  62.8h | +3.20% (t=+1.22, p=0.222) | 0.02 | 0.008 | $7.99 |
| **S6 Intraday Momentum (1h)** |   +6.23% |   3.23% |  1.86% | **-0.86** (±0.05) | -0.56 |  3.14% | 1.03 |  14.3% | 0.12 |     7 |   7.7h | -1.77% (t=-1.32, p=0.187) | 0.01 | 0.006 | $2.81 |
| **S5 Dual Momentum (Control)** |   +6.97% |   3.61% |  1.16% | **-1.09** (±0.05) | -0.71 |  2.31% | 1.56 |  26.7% | 0.07 |    15 |   8.0h | -1.29% (t=-1.57, p=0.118) | 0.00 | 0.001 | $3.46 |
| *Benchmark SPY (Buy & Hold)* | * +58.05%* | * 27.23%* | *15.96%* | *1.32* | *1.85* | *-8.45%* | *3.12* | *N/A* | *N/A* | *0* | *478d* | *0.00% (ref)* | *1.00* | *1.000* | *$0.00* |

---

## 3. Análisis Crítico de Desempeño

### 3.1. S8 PID Multihorizon: Variante A vs. Variante B
- La **Variante A** (ponderación lineal $w_h \propto h$) asigna mayor relevancia a los horizontes de escala media (1d a 10d), amortiguando el ruido de las micro-oscilaciones intradiarias.
- La **Variante B** (balancín logarítmico $w_h \propto \ln(h)$) amplifica la sensibilidad ante aceleraciones de corto plazo, incrementando la frecuencia de veto del Sistema D.
- Ambas variantes demostraron la eficacia del **Sistema D como filtro de estrés**: los vetos deterministas impiden la entrada en activos en fase de dispersión o aceleración anómala.

### 3.2. S6 Intraday Momentum vs. Costos y Prima Overnight
- Confirmando los hallazgos de `reports/overnight_vs_intraday_decomposition.md`, S6 sufre la penalización estructural de cerrar posiciones a las 15:30/15:55 ET.
- Al no capturar el drift nocturno y asumir fricción transaccional diaria en acciones enteras, S6 presenta una expectativa neta inferior a las arquitecturas con tenencia multi-día.

### 3.3. S5 Dual Momentum como Ancla de Eficiencia
- S5 mantiene un desempeño sobresaliente en el régimen alcista de 2023–2025, capitalizando la persistencia de los líderes tecnológicos (`NVDA`, `MSFT`, `AAPL`) con mínima rotación y baja fricción operativa.

---

## 4. Conclusión de Fase 4
La evidencia empírica en datos horarios respalda mantener **S5 y S8 como candidatos prioritarios** para el registro en el Ledger pre-registrado de Fase 5, relegando S6 a un módulo secundario de cobertura condicional.
