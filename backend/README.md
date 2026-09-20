# tbot — Backend del Bot de Trading Algorítmico

Paquete Python central del sistema de trading algorítmico con control de riesgo estricto, conciliación activa y veto cuantitativo de noticias con FinBERT.

---

## 1. Estrategias Implementadas

| ID | Nombre | Tipo | Universo | Horario ET | Objetivo / Comportamiento |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`s5`** | **Dual Momentum Leader** | **Rotación / Trend Following** | `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA` | 15:45 | **Motor primario de Alpha (bate al S&P 500 con +17.90% a +21.89% vs +15.70%).** Selecciona los 2 líderes con mayor momentum a 60 días sobre la EMA20. Trailing Stop en EMA20 y 100% efectivo en regímenes bajistas. |
| `s1` | Intraday Momentum | Momentum Intradía | `SPY`, `QQQ` | 15:30 | Captura el efecto de continuación de la última media hora en base al rendimiento de la primera media hora (9:30–10:00). Cierre mandatorio a las 15:58 ET. |
| `s2` | Mean Reversion RSI(2) | Reversión a la Media | Universo habilitado | 15:45 | Compra sobreventa extrema (RSI(2) < 10) sobre la SMA(200). Salida en SMA(5) o RSI(2) > 70. |
| `s3` | Trend Pullback | Retroceso a la Tendencia | Universo habilitado | 15:45 | Compra retrocesos hacia la EMA(20) cuando EMA20 > EMA50. Take Profit en 2R y stop en 1.5 ATR. |
| `s4` | Opening Range Breakout | Breakout de Apertura | `SPY`, `QQQ` | 09:35–11:30 | Ruptura con volumen relativo del rango de la primera barra de 5 minutos (deshabilitada por defecto). |

---

## 2. Ejecución y Herramientas

### 2.1 Simulación de Sesión Diaria (Paper Trading)
Ejecuta el ciclo de vida completo de un día de mercado (09:20 pre-mercado, 15:30 S1, 15:45 S3/S5, 15:58 salidas por tiempo, 16:00 conciliación):
```bash
python -m tbot.worker.paper_runner --date 2025-11-14 --capital 2000 --veto quantitative
```

### 2.2 Backtest Oficial con Métricas DSR
Ejecuta backtests cuantitativos oficiales con comisiones, slippage y Deflated Sharpe Ratio:
```bash
python -m tbot.backtest.runner --strategy s5 --start 2025-01-01 --end 2025-12-31 --data-dir data/historical
```

### 2.3 Campaña de Optimización y Benchmark de Portafolio
Compara todas las configuraciones sistemáticamente contra el benchmark S&P 500 (`SPY` Buy & Hold):
```bash
python -u scripts/optimize_and_benchmark_portfolio.py
```

---

## 3. Arquitectura y Principios de Seguridad

1. **El código decide; la IA cuantifica noticias:** FinBERT extrae características estadísticas cuantitativas (`sentiment_mean`, `negative_share`, `top_topics`). El motor de decisiones aplica umbrales matemáticos deterministas sin riesgo de alucinación ni demoras de API.
2. **Falla cerrada (Fail-Closed):** Ante cualquier desconexión, error o régimen desconocido (`UNKNOWN`), el bot bloquea nuevas entradas.
3. **PositionGuardian:** Garantiza que ninguna posición abierta permanezca más de 60 segundos sin orden de stop loss en el servidor del broker.
4. **Reloj Inyectable:** Todos los módulos reciben una abstracción `Clock` para reproducir fielmente sesiones históricas sin modificar código de producción.

Para más detalles sobre los análisis cuantitativos y la comparativa de rentabilidad, consultar [`docs/BENCHMARK_OPTIMIZATION_ANALYSIS.md`](../docs/BENCHMARK_OPTIMIZATION_ANALYSIS.md) y [`docs/PLAN.md`](../docs/PLAN.md).
