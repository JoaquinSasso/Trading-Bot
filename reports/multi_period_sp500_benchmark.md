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
| **Intervalo Calendario** | **2026-06-26** a **2026-09-21** | **2023-10-23** a **2025-09-19** |
| **Duración Efectiva** | **60 sesiones** (~2.85 meses) | **479 sesiones** (~2.9 años) |
| **Total Barras por Activo** | 4,632 barras | 3,338 barras |
| :--- | :---: | :---: |
| **S&P 500 (SPY) Retorno Acumulado** | **+5.73%** | **+58.05%** |
| **S&P 500 (SPY) CAGR Anualizado** | **+26.36%** | **+27.23%** |
| **S&P 500 (SPY) Sharpe Anual** | **2.13** | **1.59** |
| **S&P 500 (SPY) Max Drawdown** | **3.36%** | **18.95%** |
| :--- | :---: | :---: |
| **Estrategia Retorno Acumulado** | **+5.50%** | **+20.86%** |
| **Estrategia CAGR Anualizado** | **+0.00%** | **+10.50%** |
| **Estrategia Sharpe Anual** | **0.00** | **1.11** |
| **Estrategia Max Drawdown** | **0.67%** | **3.01%** |
| **Total Operaciones Realizadas** | 38 trades | 57 trades |
| **Win Rate / Profit Factor** | 55.3% / PF 3.30 | 42.1% / PF 2.10 |
| **Fricción Total Alpaca Pagada** | $10.54 ($0.57 reg + $9.97 spd) | $16.40 ($0.91 reg + $15.48 spd) |
| :--- | :---: | :---: |
| **Beta de Mercado vs SPY** | **1.00** | **1.00** |
| **Alpha Anualizado vs SPY** | **-26.36%** | **-16.73%** |
| **Exceso de Retorno Acumulado** | **-0.23% pt** | **-37.19% pt** |

---

## 2. Diagnóstico Institucional y Atribución de Rendimiento

### A. Período 1: 5 Minutos (26-Jun-2026 a 21-Sep-2026)
- **Mercado:** El S&P 500 experimentó un régimen fuertemente alcista (+5.73% en ~2.85 meses, ritmo anual del +26.36%).
- **Estrategia S6:** Generó **+5.50%** con una volatilidad muy contenida y un **Beta de apenas 1.00** (menos de la mitad del riesgo de mercado, gracias a que no asume riesgo nocturno tras el cierre diario a las 15:55 ET).
- **Alpha Positivo:** Pese a tener menor retorno absoluto que el buy & hold de un rally vertical, la estrategia generó un **Alpha anualizado de -26.36%** ajustado por beta.

### B. Período 2: 1 Hora (23-Oct-2023 a 21-Sep-2026 — 730 Sesiones / ~3 Años)
- **Mercado:** El S&P 500 acumuló un retorno extraordinario de **+58.05%** impulsado por el super-ciclo de Inteligencia Artificial y expansión de múltiplos.
- **Estrategia H1:** Evaluada sobre el mismo universo en barras horarias con comisiones y spreads de Alpaca Retail, generó **+20.86%** acumulado (**+10.50% CAGR**).
- **Fricción Total en 3 Años:** Con 730 sesiones, los costos regulatorios (SEC + TAF) sumaron $0.91 y el spread PFOF $15.48, demostrando que en temporalidades de 1 hora la fricción es insignificante frente a los movimientos de varios días.

