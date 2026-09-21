# SEGUIMIENTO DE AUDITORÍA — Respuesta a v2.0 y Plan de Cierre de P0

| Campo | Valor |
| :--- | :--- |
| **Documento** | Seguimiento de auditoría externa — segunda ronda |
| **Fecha** | 2026-09-20 |
| **Responde a** | `docs/AUDIT_RESPONSE_AND_EMPIRICAL_VALIDATION_v2.0.md` |
| **Antecedentes** | `docs/EXTERNAL_QUANT_AUDIT_v1.2.0.md`, `docs/UNIVERSE_CONSTRUCTION_SPEC.md` |
| **Dictamen** | Sin cambios: **apto para paper trading, no apto para capital real** |
| **Bloqueante principal** | F-13 — la señal de ranking falla el test de monotonicidad |
| **Destinatario** | Agente de desarrollo (Antigravity) |

> **Cómo usar este documento.** La §3 contiene tareas ejecutables con criterio de aceptación explícito. El orden de §7 no es sugerencia: T-01 puede invalidar el resto del trabajo, y T-02 y T-03 invalidan todas las cifras publicadas hasta ahora. No ejecutar tareas de la fase 2 antes de cerrar la fase 1.

---

## 1. Estado del checklist P0

De los nueve ítems P0 de la auditoría original, uno está cerrado.

| Ítem P0 | Estado | Nota |
| :--- | :---: | :--- |
| Ablation test de FinBERT | ⚠️ Ejecutado, inválido | Ver F-17. La decisión operativa se aprueba igual (D-01) |
| Cortacircuitos activos en simulación | ❌ Abierto | Ver F-14 — bloqueante |
| Tasa libre de riesgo histórica real | ❌ Abierto | Ver F-15 — bloqueante |
| Test de universo con corte 2019-12-31 | ➖ Superado por diseño | Universo A resuelve F-02 por taxonomía GICS. No aplica a S5 Top-4, que sigue contaminado |
| Deflated Sharpe Ratio + PBO (CSCV) | ❌ Abierto | |
| Consistencia de dividendos | ❌ Abierto | |
| Alpha por regresión con error estándar | ❌ Abierto | Ver F-18 |
| Modelo de costos y slippage | ❌ Abierto | |
| Cuantificación de cash drag por granularidad | ❌ Abierto | |

El P1 de dimensionamiento fue ejecutado y produjo el hallazgo más relevante de toda la segunda ronda, aunque no el que el documento reporta.

---

## 2. Hallazgos nuevos

| ID | Hallazgo | Severidad |
| :--- | :--- | :--- |
| **F-13** | **El ranking de momentum está invertido en el extremo superior** | **Crítico** |
| **F-14** | Cortacircuitos siguen sin simularse; el argumento del cap 25% es incorrecto para movimientos correlacionados | **Crítico** |
| F-15 | Tasa libre de riesgo constante (~4.5%) persiste en el cálculo de Sharpe | Alto |
| F-16 | Universo A opera a ~3-5% de volatilidad realizada contra un objetivo del 12% | Alto |
| F-17 | El ablation de FinBERT no tuvo grupo de tratamiento; la tasa de activación es inconsistente con la densidad del dataset | Medio |
| F-18 | "Alpha" reportado como diferencia de retornos acumulados; la fila de 7 años no es interpretable | Medio |

### F-13 — Inversión de ranking (CRÍTICO)

El documento v2.0 presenta como éxito que Top-4 equiponderado supere ampliamente a Top-2 sobre el mismo universo, mismo filtro de régimen y mismas reglas de salida. Despejando:

| Año | Top-2 (ranks 1-2) | Top-4 (ranks 1-4) | Implícito ranks 3-4 |
| :--- | ---: | ---: | ---: |
| 2020 | +18.24% | +33.66% | **≈ +49%** |
| 2021 | +14.08% | +27.00% | **≈ +40%** |

Cálculo: `r(3,4) ≈ 2·r(Top4) − r(Top2)`, aproximación válida a primer orden con rebalanceo periódico.

El arrastre de varianza explica una parte pequeña. Con volatilidades individuales del orden del 35% y correlación media de 0.6, la volatilidad de cartera baja de ~31% a ~28% al pasar de dos a cuatro posiciones, lo que reduce el drag geométrico en aproximadamente **1 punto porcentual anual**. Queda sin explicar entre 15 y 25 puntos.

**Implicación:** en esta muestra, el ranking de momentum a 45 días no ordena retornos futuros. Los activos que el sistema identifica como líderes rinden sistemáticamente peor que los que quedan en posiciones 3-4. Si esto se confirma con T-01, la selección por ranking no aporta valor y todo el retorno observado proviene del filtro de régimen aplicado a un universo de ganadores conocidos.

Este hallazgo tiene prioridad sobre cualquier otra tarea porque determina si la estrategia existe.

### F-14 — Cortacircuitos (CRÍTICO)

La §3 del documento v2.0 argumenta que con cap del 25% por posición, una caída del 7% en un activo representa -1.75% de cuenta y por lo tanto no dispara la liquidación de emergencia del -3.5%.

El argumento es correcto para movimientos idiosincráticos e irrelevante para el caso que importa. El cortacircuito evalúa P&L de cartera, no de posición. En marzo de 2020 hubo múltiples sesiones con el SPY entre -7% y -12%. Con cuatro posiciones correlacionadas al 25% cada una y exposición bruta del 100%, esos días producen entre -7% y -10% de cuenta, muy por encima del umbral.

Un backtest de 2020 que rinde +33.66% es un backtest donde el cortacircuito no se ejecutó. Los caps por posición protegen contra riesgo idiosincrático; no protegen contra correlación, que es exactamente lo que ocurre en un crash.

### F-15 — Tasa libre de riesgo

Verificable desde el propio documento. Universo A en 2022 reporta retorno **+1.33%** y Sharpe **-0.98**. Para que un retorno positivo produzca Sharpe negativo, `rf > 1.33%`. Despejando con volatilidad realizada compatible con el MaxDD reportado (-2.62%), el `rf` implícito ronda el 4.5%.

En 2019-2021 la tasa de letras del Tesoro a corto plazo osciló entre 0.03% y 2.4% según el tramo. Una constante del 4.5% aplicada a toda la serie distorsiona sistemáticamente el Sharpe de los tramos tempranos y hace incomparables las filas de la tabla consolidada.

### F-16 — Volatilidad realizada de Universo A

Un MaxDD de -4.59% sostenido durante siete años que incluyen el crash de COVID es aritméticamente incompatible con una volatilidad objetivo del 12%. Un portafolio efectivamente al 12% habría registrado drawdowns de entre 8% y 15% en ese período.

Evidencia adicional de que el escalado es el binding constraint: al mover el objetivo de 12% a 18% (factor 1.5x), los retornos escalaron de forma aproximadamente lineal —2020 de +8.98% a +14.80%, trienio de +14.10% a +25.56%—. Ese es el comportamiento de un portafolio que es esencialmente beta más efectivo, donde el objetivo actúa como multiplicador directo de una exposición que nunca alcanza su techo.

Causas candidatas, en orden de probabilidad:

1. `σ_port` mal estimado en el cálculo de `k = min(1, σ_target/σ_port)`. Errores típicos: incluir el peso en efectivo en la matriz de covarianzas, usar la covarianza de los 18 instrumentos en lugar de la de las posiciones seleccionadas, o anualizar dos veces.
2. Apilamiento multiplicativo de restricciones: gate absoluto, caps de bloque y score de régimen se componen antes del escalado, dejando poca exposición que escalar.
3. El piso de posición de $150 sobre $2.000 elimina posiciones después del escalado.

### F-17 — El ablation no tuvo grupo de tratamiento

Cero vetos disparados en 2020-2025 significa que la rama de código del veto nunca se ejecutó. Un experimento en el que el tratamiento no se aplica no confirma ni refuta la hipótesis: no la testea.

Además, la tasa de activación reportada es internamente inconsistente. Con una densidad de ~1 observación por activo-día, una única noticia negativa produce `negative_share = 1.0`, que supera el umbral de 0.35 con amplio margen. Bajo esa aritmética el veto debería activarse en el orden del 20-40% de los días con noticia, no en 16 de 7.490 observaciones (0.2%). La discrepancia es de dos órdenes de magnitud e indica que `negative_share` no se computa sobre el denominador que la especificación asume, o que la condición nunca se evalúa contra el conjunto de candidatos.

La decisión de retirar FinBERT se aprueba igual (D-01). Lo que no es válido es la conclusión general de que "el momentum ya hace el trabajo del NLP", que es una historia causal sin test.

### F-18 — Métrica de alpha

Las columnas "Alpha vs SPY" contienen diferencias de retornos acumulados. Eso no es alpha, no es aditivo y no es comparable entre períodos de distinta duración. La fila de 7 años que reporta "-141.69%" no tiene interpretación financiera.

---

## 3. Tareas

### Fase 1 — Bloqueantes

---

#### T-01 — Test de monotonicidad del ranking

**Prioridad:** máxima. Ninguna otra tarea tiene sentido si esta falla.

**Objetivo:** determinar si el ranking de momentum ordena retornos futuros.

**Procedimiento:**

1. Sobre el universo de 14 activos, mismo filtro de régimen, mismas reglas de salida, ejecutar cinco configuraciones: Top-2, Top-4, Top-6, Top-8 y equiponderado-sobre-todos-los-elegibles.
2. Por separado, construir un análisis de buckets: para cada fecha de rebalanceo, registrar el ranking de cada activo elegible y su retorno forward a 45 sesiones. Agregar por posición de ranking sobre toda la muestra 2020-2025.
3. Repetir el análisis de buckets sobre el Universo A (18 ETFs), por bloque.

**Salida esperada:** `reports/rank_monotonicity.md` con dos tablas.

Tabla A — configuraciones:

| Config | Retorno 2020 | 2021 | 2022 | 2025 | Trienio | Sharpe | MaxDD |

Tabla B — retorno forward medio y mediano por posición de ranking:

| Rank | N obs | Retorno fwd 45d medio | Mediano | Desv. est. | t-stat vs. media del universo |

**Criterio de interpretación:**

- Si el retorno forward medio es **monótono decreciente** en el ranking → la señal ordena, se conserva el diseño de selección.
- Si es **plano** (sin diferencia estadísticamente significativa entre buckets) → el ranking no aporta; sustituir la selección por equiponderación sobre los elegibles que pasen el gate absoluto, y conservar solo el filtro de régimen.
- Si está **invertido** (ranks superiores rinden menos) → el diseño actual es contraproducente y hay que investigar si el gate de entrada está comprando extensión en lugar de tendencia.

**No ajustar parámetros en función de este resultado.** El test es diagnóstico, no de optimización.

---

#### T-02 — Cortacircuitos en el motor de simulación

**Objetivo:** cerrar F-14 / F-03.

**Procedimiento:**

1. Implementar en el motor de backtest la evaluación de P&L de cartera intradiario contra los umbrales de -2.0% (pausa de nuevas entradas) y -3.5% (liquidación forzosa), con la misma lógica que el runner de producción. Si el runner de producción evalúa sobre marcas intradiarias, el backtest debe hacerlo sobre OHLC intradiario, no sobre cierres.
2. Re-ejecutar las cuatro configuraciones de dimensionamiento de §3 del documento v2.0 y las dos de Universo A con cortacircuitos activos.
3. Registrar por año el número de activaciones de pausa y de liquidación forzosa, y las fechas.

**Salida esperada:** `reports/circuit_breaker_impact.md` con la tabla comparativa antes/después y el log de activaciones.

**Criterio de aceptación:** ninguna cifra del repositorio puede citarse sin la versión con cortacircuitos. Si el número de liquidaciones forzosas en 2020 supera las 3, los umbrales deben recalibrarse como múltiplos de la volatilidad diaria realizada —sugerido 3σ para pausa y 4σ para liquidación, con σ estimada a 20 sesiones— en lugar de constantes absolutas.

---

#### T-03 — Curva histórica de tasa libre de riesgo

**Objetivo:** cerrar F-15 / F-04.

**Procedimiento:**

1. Reemplazar toda constante de tasa libre de riesgo por una serie diaria. Fuente sugerida: rendimiento de letras a 3 meses (FRED `DTB3`) o el retorno total realizado de `BIL`, que ya está en el universo.
2. Aplicar la serie tanto al rendimiento del efectivo en simulación como al denominador del Sharpe.
3. Verificar que ninguna simulación anterior a mayo de 2020 use `SGOV`.

**Salida esperada:** recálculo de todas las tablas de Sharpe publicadas, con nota de cambio.

**Criterio de aceptación:** ningún año con retorno positivo puede reportar Sharpe negativo salvo que la tasa libre de riesgo de ese año efectivamente lo supere, y eso debe ser verificable contra la serie.

---

#### T-04 — Diagnóstico de exposición de Universo A

**Objetivo:** cerrar F-16 antes de cualquier decisión sobre el objetivo de volatilidad.

**Procedimiento:** instrumentar el motor para registrar, en cada rebalanceo semanal:

```json
{
  "date": "YYYY-MM-DD",
  "regime_score": 0.0,
  "eligible_count_by_block": {},
  "selected_count_by_block": {},
  "gross_exposure_pre_scaling": 0.0,
  "sigma_port_estimated": 0.0,
  "k_scaling_factor": 0.0,
  "gross_exposure_post_scaling": 0.0,
  "cash_weight": 0.0,
  "positions_dropped_by_min_size": 0,
  "binding_constraint": "gate|block_cap|regime|vol_target|min_position"
}
```

El campo `binding_constraint` debe identificar cuál de las cinco restricciones fue la efectivamente limitante en ese rebalanceo.

**Salida esperada:** `reports/universe_a_exposure_diagnostic.md` con, por año: exposición bruta media y mediana, volatilidad realizada anualizada, porcentaje de sesiones con más del 70% en efectivo, y distribución de `binding_constraint`.

**Criterio de decisión posterior:**

- Si la volatilidad realizada es < 6% con objetivo 12% → el estimador de `σ_port` tiene un defecto. Corregirlo y re-correr. **No subir el objetivo.**
- Si la volatilidad realizada se aproxima al objetivo y la exposición sigue baja → el binding constraint son los caps o el gate, y ahí sí corresponde discutir relajarlos.

---

### Fase 2 — Cierre de P0 restante

---

#### T-05 — Alpha por regresión

Sustituir toda columna "Alpha vs SPY" por el intercepto de la regresión `r_strategy − rf = α + β(r_SPY − rf) + ε`, reportando α anualizado, su error estándar, el t-stat y β. Eliminar la fila de 7 años en su forma actual (F-18).

**Criterio:** un α sin error estándar no se publica.

#### T-06 — Deflated Sharpe y PBO

Calcular el Deflated Sharpe Ratio (Bailey & López de Prado) sobre las configuraciones finales, declarando explícitamente el número de configuraciones probadas a lo largo del proyecto. Ejecutar CSCV para estimar la probabilidad de sobreajuste.

**Criterio:** PBO > 0.5 descalifica la configuración para capital real, sin excepciones.

#### T-07 — Modelo de costos

Incorporar al motor: spread por instrumento (estimado del histórico o fijado conservadoramente en 3 bps para ETFs líquidos y 5 bps para acciones individuales), slippage sobre el precio de decisión, y el efecto de la granularidad de acciones enteras sobre una cuenta de $2.000.

**Criterio:** ninguna cifra sin costos modelados. Reportar el drag anualizado por separado.

#### T-08 — Retiro de FinBERT del camino crítico

Ver decisión D-01. Antes de archivar, dejar registrado en `reports/finbert_wiring_note.md` el análisis de por qué la tasa de activación fue 0.2% cuando la densidad del dataset implica un orden de magnitud entre 20% y 40% (F-17). No es necesario corregir el defecto; sí es necesario documentarlo para que la conclusión archivada sea la correcta.

#### T-09 — Verificación de stops sobre fraccionarios

Confirmar contra la documentación y la API de Alpaca si se admiten órdenes stop, stop-limit o bracket sobre posiciones fraccionarias. Documentar el resultado con enlace a la fuente.

**Criterio:** si no se admiten, el invariante fail-closed es incumplible con acciones fraccionarias y hay que elegir: operar solo acciones enteras (aceptando el error de granularidad), o implementar un stop sintético gestionado por el `PositionGuardian` con su propia latencia documentada y declarada como garantía degradada en `GEMINI.md`.

---

## 4. Decisiones

| ID | Decisión | Dictamen |
| :--- | :--- | :--- |
| **D-01** | Retirar FinBERT del flujo de ejecución en tiempo real | **Aprobado** |
| **D-02** | Elegir ahora entre Universo A y S5 Top-4 | **Rechazado** — ver §5 |
| **D-03** | Elevar el objetivo de volatilidad de 12% a 18% | **Rechazado** — bloqueado por T-04 |
| **D-04** | Híbrido Core-Satellite 60/40 | **Rechazado** |

### D-01 — Retiro de FinBERT: aprobado

Se aprueba eliminar la dependencia de FinBERT del camino crítico de ejecución: GPU remota, túneles SSH, scraping de SEC EDGAR y el `HistoricalNewsFeatureStore` en tiempo real. Una función que nunca se activó no puede aportar valor y sí aporta superficie de fallo.

Se aprueba reclasificar el modelo como telemetría offline. No se aprueba la conclusión de que el NLP carece de valor para esta clase de estrategia: eso requiere un experimento que no se realizó (F-17).

### D-03 — Objetivo de volatilidad: bloqueado

La decisión entre 12% y 18% no puede tomarse hasta que T-04 reporte la volatilidad realizada. Si el sistema corre a 3-5% con objetivo 12%, subir el objetivo a 18% no calibra nada: enmascara un defecto de estimación con un multiplicador mayor. Con el estimador corregido, es probable que 12% produzca sustancialmente más exposición que la actual sin tocar el parámetro.

### D-04 — Core-Satellite: rechazado

Tres razones independientes:

**Aritmética.** $800 de satélite entre dos o tres nombres individuales dan $267 a $400 por posición. Para la mayoría de los catorce tickers eso es una o cero acciones enteras. El error de redondeo supera cualquier alpha esperable del satélite.

**Concentración oculta.** El núcleo tendrá `XLK` y el satélite tendrá `NVDA` o `MSFT` simultáneamente, porque el mismo momentum los selecciona. La estructura produce exposición tecnológica que ningún cap presupuestó y la separación en sleeves la oculta en lugar de exponerla.

**Superficie operativa.** Duplica motores, reglas y reconciliación justo en la fase cuyo objetivo es demostrar que un solo motor opera sin fallas durante seis meses.

Core-Satellite es una estructura apropiada a partir de aproximadamente $50.000. Revisar entonces.

---

## 5. Arquitectura para paper trading

**Desplegar ambas en paralelo. Universo A como candidato primario con capital nominal; S5 Top-4 como sombra sin capital asignado.**

Fundamento: el paper trading no cuesta nada y su propósito no es determinar cuál rinde más, sino medir si el motor de producción reproduce la simulación. Elegir arquitectura ahora significa elegirla sobre backtests con F-13 y F-14 sin resolver.

Preferencia por Universo A como primario:

- S5 Top-4 arrastra F-01 y F-02 intactos: `GLDM`/`SLV` garantizados en el universo, catorce nombres que son los ganadores conocidos de la década.
- Su ventaja aparente proviene de un ranking que acaba de fallar el test de monotonicidad (F-13).
- Con Reg T cash y liquidación T+1, rotar acciones individuales acumula good-faith violations; el rebalanceo semanal sobre ETFs es sustancialmente más benigno.
- Los ETFs eliminan el riesgo de gap por evento binario, que es el riesgo de cola dominante a este tamaño de cuenta.

A los seis meses la decisión de arquitectura se toma con datos de fidelidad de ejecución para ambas, que hoy no existen.

---

## 6. Gates de paso a capital real

### Principio de diseño de la prueba

**No establecer ningún umbral de retorno ni de Sharpe.**

Seis meses de una estrategia con rebalanceo semanal son unas 26 observaciones independientes. El error estándar de un Sharpe estimado sobre esa muestra es del orden de ±0.9. Un umbral de rendimiento no mediría habilidad sino qué semestre tocó, y crearía el incentivo de repetir el período hasta obtener uno favorable.

El paper trading mide tres cosas y solo tres: fidelidad de ejecución, integridad de invariantes y costos realizados.

### Gate 1 — Fidelidad de ejecución

Ejecutar el motor de simulación en sombra sobre los mismos días, mismos datos y mismo `as_of` que la cuenta de paper. Comparar retorno diario.

| Métrica | Umbral |
| :--- | :--- |
| Tracking error anualizado paper vs. sombra | **< 1.5%** |
| Diferencia diaria máxima | **< 0.75%** |
| Excepciones documentadas con causa raíz | **100%** |

Es el gate que decide. Si el motor no reproduce la simulación, todos los backtests del repositorio son literatura.

### Gate 2 — Integridad de invariantes

Tres contadores que deben cerrar en cero durante los seis meses completos:

| Contador | Umbral |
| :--- | :--- |
| Posiciones-minuto sin stop activo confirmado en el broker | **0** |
| Órdenes duplicadas tras reinicio del proceso | **0** |
| Órdenes sin señal de origen trazable | **0** |
| Tiempo desde fill hasta confirmación de stop — **máximo**, no p99 | **< 60 s** |

Cualquier evento distinto de cero reinicia el período de seis meses. Se exige además inyectar al menos **10 fallos deliberados** del proceso en distintos puntos del ciclo de rebalanceo y verificar recuperación sin órdenes duplicadas ni perdidas.

Aquí aflora T-09: si Alpaca no admite stops sobre fraccionarios, este gate es inalcanzable con la configuración actual y hay que saberlo en el mes uno.

### Gate 3 — Costos realizados

Slippage por orden, medido como `(precio_fill − precio_decisión)/precio_decisión`, con signo según dirección.

| Métrica | Umbral |
| :--- | :--- |
| Mediana (ETFs) | **≤ 8 bps** |
| Percentil 95 | **≤ 25 bps** |
| Drag acumulado anualizado | **≤ 0.5%** |

Condición que vuelve útil al gate: al cerrar los seis meses, **actualizar el modelo de costos del backtest con los valores realizados y re-ejecutar todas las simulaciones**. Si los resultados cambian materialmente, el gate no está aprobado aunque los bps estén en rango.

### Instrumentación de apoyo (se mide, no bloquea)

| Métrica | Alerta |
| :--- | :--- |
| Desviación L1 media entre pesos objetivo y ejecutados | > 6% indica cuenta insuficiente para la estrategia |
| Ventanas de rebalanceo ejecutadas en horario | < 99% |
| Good-faith violations / flags PDT | > 0 |
| Exposición bruta mensual media vs. la esperada por simulación | desvío > 10 puntos |

### Alarma de validez de modelo

Si el drawdown realizado en paper supera **1.5 veces** el peor MaxDD anual del backtest correspondiente, detener y auditar. No porque perder en paper sea inaceptable, sino porque indica que el modelo de riesgo no describe al sistema.

### Prerrequisito

**Los seis meses empiezan a contar cuando la Fase 1 esté cerrada**, no antes. Correr el reloj sobre un sistema cuyos cortacircuitos nunca se simularon produce seis meses de datos sobre una configuración que no es la validada.

---

## 7. Orden de ejecución

```
FASE 1 (bloqueante)
  T-01  Test de monotonicidad del ranking      ← primero, puede invalidar el resto
  T-02  Cortacircuitos en simulación
  T-03  Curva histórica de tasa libre de riesgo
  T-04  Diagnóstico de exposición Universo A
  ── Re-ejecutar TODAS las tablas publicadas con T-02 + T-03 aplicados ──

FASE 2 (cierre de P0)
  T-05  Alpha por regresión
  T-06  Deflated Sharpe + PBO
  T-07  Modelo de costos
  T-08  Retiro de FinBERT + nota de cableado
  T-09  Verificación de stops sobre fraccionarios

FASE 3
  Despliegue en paper: Universo A primario + S5 Top-4 sombra
  Inicio del reloj de 6 meses
  Telemetría de Gates 1-3 desde el día 1
```

---

## 8. Qué no hacer

- **No subir el objetivo de volatilidad para recuperar retorno.** Es el primer paso del modo de fallo descrito en §12 de `UNIVERSE_CONSTRUCTION_SPEC.md`. Si tras T-04 el estimador resulta correcto y la exposición sigue baja, el ajuste se discute sobre caps y gate, documentando el cambio y contándolo como configuración probada para el Deflated Sharpe.
- **No ajustar parámetros en función del resultado de T-01.** Es diagnóstico. Si la señal no ordena, la respuesta es simplificar el diseño, no buscar la ventana que sí ordene en esta muestra.
- **No citar ninguna cifra publicada hasta ahora** sin la versión recalculada con T-02 y T-03.
- **No iniciar el reloj de seis meses** con la Fase 1 abierta.
- **No tratar el +59.71% de 2025 de S5 Top-4 como evidencia.** Sigue conteniendo F-01: `GLDM` y `SLV` garantizados en el universo durante el mejor año de los metales en cuarenta años.

---

*Segunda ronda de auditoría externa. El dictamen de la primera ronda se mantiene sin cambios. Los hallazgos F-13 a F-18 son hipótesis falsables derivadas del análisis de las cifras reportadas en `AUDIT_RESPONSE_AND_EMPIRICAL_VALIDATION_v2.0.md`; cada uno indica el procedimiento para confirmarlo o descartarlo.*
