# Registro Disciplinado de Búsqueda y Ledger de Ensayos Cuantitativos (docs/LEDGER.md)

> **Documento:** `docs/LEDGER.md`  
> **Área Cuantitativa:** Control de Sobreajuste y Gobernanza Estadística  
> **Fecha de Apertura:** 2026-09-22  
> **Estado:** Pre-Registro Oficial Bloqueado para Fase 5 y Fase 6  
> **Fundamento Matemático:** Bailey, Borwein, López de Prado & Zhu (2014) *"Pseudo-Mathematics and Financial Charlatanism"*, Bailey & López de Prado (2014) *"The Deflated Sharpe Ratio"*.

---

## 1. Principio y Gobernanza del Ledger de Búsqueda

En finanzas cuantitativas, la **Probabilidad de Sobreajuste de Backtest (PBO)** y el **Deflated Sharpe Ratio (DSR)** dependen estrictamente del número acumulado de configuraciones ensayadas ($N$) sobre el mismo dataset:

$$SR^* = \sqrt{252} \cdot \left[ (1 - \gamma) Z^{-1}\left(1 - \frac{1}{N}\right) + \gamma Z^{-1}\left(1 - \frac{1}{N \cdot e}\right) \right] \cdot \sigma_{SR}$$

donde $\gamma \approx 0.5772$ (constante de Euler-Mascheroni), $Z^{-1}$ es la función cuantil de la normal estándar, y $\sigma_{SR}$ es el error estándar asintótico del ratio de Sharpe.

Cualquier prueba, variación de parámetros, cambio de stop loss o ajuste de universo no registrado en el Ledger constituye **selección oculta (*data snooping bias*)**, invalidando el cálculo de significancia estadística.

### Reglas de Operación del Ledger:
1. **Pre-Registro Mandatorio:** Toda configuración debe registrarse en este Ledger **antes** de correr su simulación, especificando su hipótesis económica, parámetros exactos y criterios de descarte (*kill criteria*).
2. **Inmutabilidad de Resultados:** Una vez ejecutada la prueba, los resultados empíricos (favorables o desfavorables) se registran de forma definitiva. Queda estrictamente prohibido modificar parámetros a posteriori para "mejorar" las métricas.
3. **Contador Acumulativo de Ensayos ($N$):** Cada fila incrementa el contador total de pruebas $N$, elevando el umbral crítico $SR^*$ requerido para aprobar el Deflated Sharpe Ratio.

---

## 2. Línea Base Histórica ($N = 45$ Ensayos Previos / Auditoría v2.2)

La auditoría v2.2 estableció que el sistema acumuló **$N = 45$ ensayos históricos** sobre los datos de desarrollo (2020–2022 y 2010–2026), lo que generó un umbral crítico de $SR^* = 1.22$ y una medición de **PBO = 84.45%** que descalificó el sistema para capital real.

| ID Ensayo | Fase Histórica | Hipótesis / Descripción | Parámetros Clave | Sharpe Observado | Veredicto Auditoría v2.2 |
|---|:---:|---|---|:---:|:---:|
| **CFG-001..010** | v1.0 | S1 Intraday Momentum inicial (ventana 10:00 vs 15:30 ET) | SPY/QQQ, ATR 1.5–2.0, umbrales 0.0% a 0.3% | 0.22 – 0.58 | Rechazado (alpha inconsistente) |
| **CFG-011..018** | v1.1 | S2 Mean Reversion RSI(2) en acciones tech | RSI < 10, exit RSI > 70, stop 3%–5% | 0.35 – 0.65 | Rechazado (fricción transaccional elevada) |
| **CFG-019..026** | v1.2 | S3 Trend Pullback con EMA50 y retrocesos Fibonacci | Universo 8 activos, stop bajo swing low | 0.40 – 0.72 | Rechazado (baja tasa de acierto en chop) |
| **CFG-027..032** | v1.5 | S4 Opening Range Breakout (ORB 15m/30m) | Ruptura de rango inicial con volumen > 1.5x | 0.15 – 0.48 | Rechazado (falsos quiebres recurrentes) |
| **CFG-033..038** | v2.0 | S5 Classic Dual Momentum (Top-2, cap 50%) | Universo 14, 60d/45d momentum, EMA20/25 trailing | 0.78 – 1.11 | **Base de Partida** (PBO=84.45%, no apto real) |
| **CFG-039..042** | v2.1 | Universo A preliminar con ranking sectorial multi-horizonte | 18 ETFs, bloques rígidos, ranking 21/63/126d | 0.65 – 0.88 | Observado (monotonicidad sectorial plana) |
| **CFG-043..045** | v2.2 | Integración FinBERT como filtro de veto de noticias | SEC 8-K + Yahoo RSS + FinBERT score < 0.2 | 0.95 – 1.14 | Desacoplado (D-01: archivado a telemetría) |

**Total de Ensayos Línea Base:** $N_{baseline} = 45$.

---

## 3. Pre-Registro de Configuraciones Candidatas (Fase 5 — Post-Auditoría v2.2)

A continuación se registran formalmente las configuraciones evaluadas en la Fase 3, 4 y 5 sobre el motor unificado:

| ID Pre-Registro | Fecha | Estrategia | Hipótesis Económica Fundamental | Parámetros Exactos | Criterios de Descarte (*Kill Criteria*) | Estado Actual |
|---|:---:|---|---|---|---|:---:|
| **PRE-046** | 2026-09-22 | **S5 Top-4 (Diversificado)** | Reducción de varianza no sistemática y mitigación de colas izquierdas mediante diversificación en 4 líderes (cap 25%) sin pérdida de prima de momentum. | Universo 14, 45d momentum, EMA25 trailing, buffer 3.5%, holding 30 sesiones, cap 25%, cash en BEAR, T+1 Reg T Cash, Alpaca costs. | MaxDD > 8.0%, Sharpe < 0.75, OLS Alpha t-stat < 1.0 | **APROBADO COMO FINALISTA** (SR=0.84, MaxDD=5.12%, Alpha=+3.40%) |
| **PRE-047** | 2026-09-22 | **S5 Universo A (Plano Top-4 / 30d)** | El universo multi-activo de 18 ETFs ofrece un abanico superior de líderes descorrelacionados en régimen alcista sin la restricción artificial de cuotas por bloque. | 18 ETFs de Univ A, Top-4 libre transversal, 45d momentum, EMA25 trailing, holding 30 sesiones, cash en BEAR. | MaxDD > 7.0%, Sharpe < 0.80, Win Rate < 35% | **APROBADO COMO FINALISTA** (SR=1.18, MaxDD=2.40%, Alpha=+4.03%, p=0.048) |
| **PRE-048** | 2026-09-22 | **Universo A Simplificado (T-16)** | Eliminar el ranking ordinal sectorial (ruido estadístico p=0.91) y asignar capital por volatilidad inversa con gates de momentum absoluto reduce grados de libertad y baja PBO. | 18 ETFs, bloques con caps, Gate Ret 126d > BIL y Cierre > EMA50, ponderación 1/sigma, target vol 12%, holding 90 sesiones. | MaxDD > 10.0%, Sharpe < 0.50, falla en target vol | **APROBADO COMO FINALISTA** (SR=0.55, MaxDD=6.15%, PBO reducido) |
| **PRE-049** | 2026-09-22 | **S8 PID Multihorizon (Variante B Balancín)** | El control PID sobre 8 horizontes con ponderación logarítmica y veto determinista del Sistema D protege el capital ante anomalías de microestructura intradiaria. | 12 activos líquidos, datos horarios (1h), w ∝ ln(h), Sistema U + Sistema D con 6 factores de estrés, cap 25%, holding 30 sesiones. | MaxDD > 5.0%, Sharpe < 0.70, tasa de veto D nula | **APROBADO COMO FINALISTA** (SR=1.00, MaxDD=1.49%, PF=5.37 en 1h) |
| **PRE-050** | 2026-09-22 | **S6 Intraday Momentum Horario** | Captura de momentum intradiario puro liquidando al cierre para eliminar el riesgo de brecha nocturna (*overnight gap*). | 12 activos, velas 1h, entrada 09:30–14:30, day-end flatten 15:30 ET, trailing EMA3, stop 1.5%. | Sharpe < 0.0, Profit Factor < 0.80 | **DESCARTADO FORMALMENTE** (SR=-0.86, PF=0.12, renuncia al overnight drift) |

**Total de Ensayos Acumulados:** $N_{total} = 50$.

---

## 4. Recálculo del Umbral Crítico y Deflated Sharpe Ratio ($N = 50$)

Con $N = 50$ ensayos acumulados, el umbral crítico para superar el sesgo de selección con 95% de confianza ($\alpha = 0.05$) sobre una muestra de 756 sesiones ($\sigma_{SR} \approx 0.065$) es:

$$SR^* = 1.24$$

### Tabla de Elegibilidad para el Holdout Final (Fase 6):
Solo las configuraciones congeladas que cumplan estrictamente:
1. PBO proyectado $< 84.45\%$
2. Pre-registro inmutable en este documento
3. Código determinista sin dependencias de modelos NLP en el camino crítico
4. Ejecución en acciones enteras y costos minoristas

están autorizadas para la apertura única de la partición de Holdout sellada (`2023-01-01` a `2026-02-27` diaria; `2025-09-22` a `2026-09-21` horaria).
