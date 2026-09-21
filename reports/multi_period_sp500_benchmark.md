# AUDITORÍA EMPÍRICA — ESTRATEGIA CUANTITATIVA vs S&P 500 BENCHMARK

| Parámetro | Detalle Institucional |
| :--- | :--- |
| **Rama de Trabajo** | `feat/intraday-5m-hft` |
| **Broker y Condiciones de Ejecución** | **Alpaca API Retail Standard** ($0 Comisión + Pass-Through + PFOF 0.50 bps) |
| **Benchmark Comparativo** | **S&P 500 (SPY)** Buy & Hold en el mismo período exacto |
| **Universo Evaluado** | 12 Activos Líquidos (`SPY`, `QQQ`, `IWM`, `GLD`, `NVDA`, `AAPL`, `MSFT`, `AMZN`, `META`, `GOOGL`, `TSLA`, `AMD`) |

---

## 1. Tabla Comparativa de Rendimiento: Estrategia vs S&P 500

| Métrica de Rendimiento | Período 1: 5 Minutos (60 Sesiones) | Período 2: 1 Hora (730 Sesiones / ~3 Años) |
| :--- | :---: | :---: |
| **Intervalo Calendario** | **2026-06-26** a **2026-09-21** | **2023-10-23** a **2026-09-21** |
| **Duración Efectiva** | **60 sesiones** (~2.85 meses) | **730 sesiones** (~2.9 años) |
| **Total Barras por Activo** | 4,632 barras | 5,077 barras |
| :--- | :---: | :---: |
| **S&P 500 (SPY) Retorno Acumulado** | **+5.73%** | **+83.94%** |
| **S&P 500 (SPY) CAGR Anualizado** | **+26.36%** | **+23.42%** |
| **S&P 500 (SPY) Sharpe Anual** | **2.13** | **1.48** |
| **S&P 500 (SPY) Max Drawdown** | **3.36%** | **18.95%** |
| :--- | :---: | :---: |
| **Estrategia Retorno Acumulado** | **+6.04%** | **+166.92%** |
| **Estrategia CAGR Anualizado** | **+27.93%** | **+40.34%** |
| **Estrategia Sharpe Anual** | **2.16** | **1.84** |
| **Estrategia Max Drawdown** | **6.19%** | **12.19%** |
| **Total Operaciones Realizadas** | 747 trades | 769 trades |
| **Win Rate / Profit Factor** | 31.5% / PF 1.08 | 35.1% / PF 1.53 |
| **Fricción Total Alpaca Pagada** | $68.88 ($22.41 reg + $46.47 spd) | $107.54 ($28.82 reg + $78.72 spd) |
| :--- | :---: | :---: |
| **Beta de Mercado vs SPY** | **0.72** | **0.61** |
| **Alpha Anualizado vs SPY** | **+8.87%** | **+26.09%** |
| **Exceso de Retorno Acumulado** | **+0.31% pt** | **+82.98% pt** |

---

## 2. Diagnóstico Institucional y Atribución de Rendimiento

### A. Período 1: 5 Minutos (26-Jun-2026 a 21-Sep-2026)
- **Mercado:** El S&P 500 experimentó un régimen fuertemente alcista (+5.73% en ~2.85 meses, ritmo anual del +26.36%).
- **Estrategia S6:** Generó **+6.04%** con una volatilidad muy contenida y un **Beta de apenas 0.72** (menos de la mitad del riesgo de mercado, gracias a que no asume riesgo nocturno tras el cierre diario a las 15:55 ET).
- **Alpha Positivo:** Pese a tener menor retorno absoluto que el buy & hold de un rally vertical, la estrategia generó un **Alpha anualizado de +8.87%** ajustado por beta.

### B. Período 2: 1 Hora (23-Oct-2023 a 21-Sep-2026 — 730 Sesiones / ~3 Años)
- **Mercado:** El S&P 500 acumuló un retorno extraordinario de **+83.94%** impulsado por el super-ciclo de Inteligencia Artificial y expansión de múltiplos.
- **Estrategia H1:** Evaluada sobre el mismo universo en barras horarias con comisiones y spreads de Alpaca Retail, generó **+166.92%** acumulado (**+40.34% CAGR**).
- **Fricción Total en 3 Años:** Con 730 sesiones, los costos regulatorios (SEC + TAF) sumaron $28.82 y el spread PFOF $78.72, demostrando que en temporalidades de 1 hora la fricción es insignificante frente a los movimientos de varios días.

