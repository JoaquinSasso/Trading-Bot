# Motor de backtest v2.3: correcciones y optimización

Este documento resume los cambios que surgieron de la auditoría del motor (sep-2026) y cómo usarlos.

## Validación

- **Equivalencia del motor optimizado.** Con `engine_mode="legacy"`, el motor nuevo corre S5 v1.2 y reproduce el motor v2.2 al centavo: los mismos trades y la misma curva de equity. Se verificó con 591 trades en el universo 14 y 542 en el de 93, sobre 2011–2022.
- **Precálculo de S5 v1.3.** El camino rápido (`prepare`) y el general dan trades y equity idénticos. Hay un test que lo cubre: `test_s5_fast_path_matches_general_path_in_engine`.
- **Versiones de pandas.** Los resultados son idénticos con pandas 2.3 y 3.0.

## Rendimiento (S5, 2011–2022, medido en la misma máquina)

| Caso | v2.2 | v2.3 |
|---|---|---|
| Universo 14 | 74 s | 2 s |
| Universo 93 | 361 s | 4.4 s |
| Universo A (ETFs) | 27 s | 0.8 s |

**Por qué es más rápido:** los datos se indexan una sola vez por fecha, con búsqueda binaria, en lugar de filtrar DataFrames por fecha todos los días. El régimen, las EMAs y los retornos se precalculan de forma causal: rolling y EWM dan en la fila *i* el mismo valor que si se recalcularan sobre el prefijo. A la estrategia le llegan vistas perezosas de las barras, que solo se construyen cuando las pide, y S5 precalcula sus indicadores con `prepare()`.

## Correcciones del motor (defaults nuevos)

| Tema | Antes (v2.2) | Ahora (v2.3) | Config |
|---|---|---|---|
| **Salidas por estrategia** | El overlay EMA25 + 30 sesiones se aplicaba a todas las estrategias | Hook `manage_positions`. El overlay solo se aplica a estrategias sin hook, con aviso en `result.warnings` | `exit_overlay` |
| **Tiempo máximo de la señal** | `Signal.max_holding` se ignoraba en modo diario | Se aplica, en sesiones, a estrategias sin hook | — |
| **Visibilidad de la barra del día** | La estrategia veía high/low/volumen del día completo | Solo open y close reales | `same_bar_visibility` |
| **Precio de ejecución** | `entry_price_ref` de la señal | Cierre real de la sesión, o apertura siguiente con `next_open`. Las órdenes límite por debajo del cierre quedan para la sesión siguiente | `execution_timing` |
| **Dimensionamiento** | Solo por riesgo | Si la señal trae `target_weight`, peso objetivo; si no, por riesgo | — |
| **Universo** | `config.universe` no filtraba nada | Filtra las compras de estrategias sin universo propio | — |
| **Rendimiento del efectivo** | BIL diario con piso 0 por día (+~1.3%/año espurio en 2010–2015) | La serie de BIL se trata como retornos: media de 21 sesiones previas, con piso 0. El Sharpe usa la misma tasa | `cash_rate_source`, `cash_rate_smoothing_days` |
| **Momento del rendimiento del efectivo** | Al final del día, sobre el efectivo de después de operar | Al inicio de la sesión, sobre el efectivo de la noche anterior | — |
| **Control de volatilidad** | Retornos alineados por número de fila (desfase entre activos) | Alineados por fecha | — |
| **Orden de stops y cortacircuitos** | Cortacircuitos antes que los stops, con liquidación en el mínimo del día | Primero los stops (órdenes en reposo). Luego el flatten al nivel interpolado donde la cartera cruza el umbral | `cb_flatten_model` |
| **Evaluación de cortacircuitos** | Solo los días con posiciones abiertas | Todos los días. La racha perdedora se enfría tras N sesiones | `consecutive_loss_cooldown_sessions` |
| **Stop vs. salida al cierre** | `exit_at_close` se evaluaba antes que el stop | Primero el stop, después el take profit, después las salidas al cierre | — |
| **Cuenta cash** | Solo fondos liquidados | Modelo Reg T: compra con fondos sin liquidar y cuenta las GFV | `cash_account_model` |
| **Liquidación T+1** | Solo salteaba fines de semana | Usa el calendario de sesiones (saltea feriados) | — |
| **Costos por tipo de activo** | 10 ETFs; los sectores, BIL e IEF pagaban costo de acción | Lista ampliada de ETFs | `etf_symbols` |
| **Curva de equity y métricas** | Sin el retorno del día 1; valor final antes de liquidar; Sortino no estándar | Anclada en el capital inicial; valor final después de liquidar; Sortino con downside deviation | — |
| **DSR** | N=1 salvo que se pasara a mano | Ledger de variantes (`trial_ledger_path` + `trial_family`); aviso si N=1 | — |
| **Guarda de holdout** | Un solo DataFrame sintético la desactivaba | Solo se omite si todos los datos son sintéticos | — |
| **Intradía** | Ver detalle abajo | Ver detalle abajo | — |
| **`runner.py` / `compare.py`** | Importaba S1/S4 (ya no existen) y caía a datos sintéticos; la variante B era igual a la A | Corregido. `compare.py` aborta hasta que se integre el veto | — |

**Detalle de las correcciones intradía:**

- El stop vigente en cada barra es el de la barra anterior.
- El take profit ahora se evalúa.
- `max_holding` se convierte a barras según la duración real de la barra.
- El cierre forzado ocurre en la última barra de la sesión.
- Epoch en UTC → hora de Nueva York.

## S5 v1.3 (Dual Momentum Leader)

- **Régimen bajista:** liquida todo cuando el régimen es BEAR (`exit_on_bear=True`).
- **Rotación por ranking:** vende la posición que cae más allá del puesto `top_n + rotation_buffer` (default 2) o que deja de calificar, siempre que tenga al menos 3 sesiones.
- **Trailing EMA25:** mismas reglas que el overlay anterior, ahora implementadas dentro de S5. Por eso sus parámetros tienen efecto.
- **Máximo de sesiones:** al cumplir `max_holding_sessions`, si el activo sigue en el top N se renueva la posición en lugar de venderla y recomprarla.
- **Tamaño:** peso fijo `1/top_n`, luego el control de volatilidad del motor.

## Limitaciones que el motor no puede resolver

- **Sesgo de supervivencia del universo.** `UNIVERSE_14` y el universo de 93 acciones se eligieron con información de hoy. Hacen falta constituyentes point-in-time para evaluarlos sin ese sesgo. Universo A (ETFs) no tiene este problema.
- **Solapamiento de holdouts.** La ventana de desarrollo horaria (2023-10 → 2025-09) está dentro del holdout diario (2023-01 → 2026-02). Es una decisión de gobernanza, no del motor.
- **Informe de apalancamiento.** `run_leverage_report.py` multiplica retornos diarios: no descuenta el margen y también multiplica el rendimiento del efectivo.

## Reproducir resultados de v2.2

```python
BacktestConfig(..., engine_mode="legacy")
```
