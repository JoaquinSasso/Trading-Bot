# SEGUIMIENTO DE AUDITORÍA v2.2 — Revisión del Consolidado y Autorización de Fase 3

| Campo | Valor |
| :--- | :--- |
| **Documento** | Seguimiento de auditoría externa — tercera ronda |
| **Fecha** | 2026-09-21 |
| **Responde a** | `docs/AUDIT_RESPONSE_v2.2_CONSOLIDATED.md` |
| **Antecedentes** | `EXTERNAL_QUANT_AUDIT_v1.2.0.md`, `UNIVERSE_CONSTRUCTION_SPEC.md`, `AUDIT_FOLLOWUP_v2.0.md` |
| **Dictamen de Fase 3** | **Reloj de paper trading AUTORIZADO** — ver §5 |
| **Dictamen de capital real** | **No apto.** PBO = 84.45% es descalificante y no lo revierte el paper trading |
| **Hallazgos nuevos** | F-19 a F-23, más una corrección propia (F-24) |
| **Destinatario** | Agente de desarrollo (Antigravity) |

> **Lectura rápida para el agente.** El reloj de seis meses puede arrancar ya. Las tareas T-10 a T-16 corren en paralelo y ninguna lo bloquea, pero todas deben cerrar antes de cualquier discusión sobre capital real. La §6 corrige un umbral que este auditor fijó mal en la ronda anterior.

---

## 1. Cierres aceptados

Los siguientes ítems quedan cerrados sin observaciones.

| Ítem | Evidencia | Comentario |
| :--- | :--- | :--- |
| **F-15 / T-03** — Tasa libre de riesgo | `data/risk_free_rate_bil_2010_2026.csv` | Serie diaria de `BIL` sustituye la constante. Cerrado sin reservas |
| **F-14 / T-02** — Cortacircuitos (parcial) | `reports/circuit_breaker_impact.md` | La caída de Top-2 de +18.7% a **-10.42%** en 2020 con 14 liquidaciones confirma el hallazgo y desmiente una cifra que el propio equipo había defendido. Queda una inconsistencia abierta: ver F-21 |
| **T-06** — Deflated Sharpe y PBO | `reports/deflated_sharpe_and_pbo.md` | PBO = 84.45% publicado con su dictamen de descalificación en lugar de enterrado. Es la pieza de mayor valor del consolidado |
| **T-09** — Stops fraccionarios | `reports/fractional_stops_verification.md` | Verificación concreta con códigos de error. La resolución —acciones enteras con brackets GTC nativos en el candidato primario— preserva el invariante fail-closed sin degradarlo a stop sintético |
| **D-01 / T-08** — Desacople de FinBERT | `paper_runner.py` con `veto_mode="off"` | Ejecutado. La nota forense explica correctamente la tasa de activación del 0.2% por el 87.8% de filas con `n_items = 0` |
| **F-16 / T-04** — Exposición de Universo A | `reports/universe_a_exposure_diagnostic.md` | Volatilidad realizada de 5.2% contra objetivo de 12%, con el gate absoluto como restricción dominante en el 40.4% de las semanas. Diagnóstico correcto y decisión D-03 bien ratificada |

**Hallazgo colateral aprovechable.** El test de monotonicidad sobre Universo A dio plano (~+2.0% en todos los ranks). Si la selección sectorial no ordena, el ranking es parametrización sin función. Ver T-16.

---

## 2. Hallazgos nuevos

| ID | Hallazgo | Severidad |
| :--- | :--- | :--- |
| **F-19** | **El backtest multiciclo 2010–2026 corre sobre el universo sesgado; extiende F-02 en lugar de resolverlo** | **Crítico** |
| **F-20** | **T-01 usa observaciones solapadas y reporta niveles en lugar de spreads; F-13 sigue abierto** | **Alto** |
| F-21 | Conteo de liquidaciones incoherente entre Top-2 y Top-4 con exposición bruta idéntica | Alto |
| F-22 | Alpha CAPM mal especificado: un solo factor sobre una cartera con hasta 50% en metales | Alto |
| F-23 | El arrastre de granularidad de Universo A está extrapolado desde S5 y contradice la medición propia | Medio |
| F-24 | Umbral de Gate 3 mal calibrado por este auditor | Corrección propia |

---

### F-19 — El multiciclo extiende el sesgo (CRÍTICO)

El período 2010–2026 se presenta como la validación más fuerte del sistema. Es la más débil.

F-02 nunca se cerró para S5. La tabla de estado del consolidado lo marca como superado por diseño, pero esa resolución aplica únicamente a Universo A, que elimina el sesgo por taxonomía GICS. S5 sigue operando sobre catorce nombres seleccionados en 2026.

Extender la ventana a dieciséis años **agrava** el problema en lugar de mitigarlo, porque aumenta la distancia de retrospección. En enero de 2010, ninguna regla razonable habría seleccionado `NVDA`, entonces una compañía de gráficos de mediana capitalización, ni `LLY` en pleno precipicio de patentes. El propio consolidado anota en la fila de 2012 que `META` salió a bolsa ese mayo: la simulación incorpora una compañía que durante los primeros 29 meses no cotizaba, y está ahí porque hoy sabemos cómo le fue.

En consecuencia:

- El **+1.085,4%** acumulado, el CAGR de 16.57% y el Jensen's Alpha de **+11.15% ± 3.46%** (t = 3.22) no son resultados fuera de muestra.
- La §4.3, "Comportamiento en las 5 Grandes Crisis Históricas", describe cómo se comportó una cartera de ganadores conocidos durante esas crisis. Es una narrativa, no una prueba de robustez.
- El resultado de 2011 (+9.74% apoyado en rotación a metales) es especialmente sensible al sesgo: `GLD` y `SLV` están en el universo porque conocemos el ciclo de metales completo hasta 2025.

**Acción:** T-11. Hasta que cierre, la §4 debe llevar en el repositorio una advertencia explícita de que el universo no es punto-en-el-tiempo y que sus métricas no constituyen validación fuera de muestra.

---

### F-20 — F-13 sigue abierto (ALTO)

El resultado reportado es monótono decreciente y aparenta significancia: rank 1 **+7.11%** (t = 2.27, p = 0.024), rank 2 +7.09%, rank 3 +5.37%, rank 4 +4.35%, rank 7 +3.28% (t = −2.25, p = 0.025). Hay dos defectos que invalidan los estadísticos.

**Defecto 1 — Solapamiento.** Retornos forward a 45 sesiones muestreados semanalmente se solapan nueve veces. Las 3.800 observaciones no son independientes: el tamaño efectivo está en el orden de 420. Corrigiendo con Newey-West para una estructura MA(8), el error estándar se infla entre √3 y 3, y el t de 2.27 cae a un rango de **0.76 a 1.31**. En ningún extremo el resultado es significativo al 5%.

**Defecto 2 — Niveles en lugar de spreads.** Un retorno forward de +7.11% a 45 días es en su mayor parte retorno de mercado. La hipótesis a testear no es si el rank 1 sube, sino si le gana al rank 7. El estadístico pertinente es el retorno de una cartera long-short `rank(1,2) − rank(6,7)`, que cancela el factor de mercado y aísla la capacidad de ordenamiento.

**Sobre la "trampa de movilidad de capital":** la explicación es plausible y bien razonada, pero note lo que implica. Si Top-4 supera a Top-2 porque rota más rápido y no porque seleccione mejor, la ventaja proviene de la movilidad del capital, no del ranking. Es directamente testeable (T-15) y la respuesta cambia qué componente del diseño hay que conservar.

---

### F-21 — Incoherencia en el conteo de liquidaciones (ALTO)

Top-2 registra **14 liquidaciones forzosas** en 2020 y cierra en −10.42%. Top-4 "absorbe la volatilidad normal" y cierra en +22.63% con cortacircuitos fijos.

Ambas configuraciones comparten universo, filtro de régimen y exposición bruta: 2 × 50% y 4 × 25% suman 100% en los dos casos. El cortacircuito evalúa P&L de cartera, no de posición. En una sesión con el SPY en −9%, ambas carteras pierden aproximadamente lo mismo a nivel de cuenta, y el cap por posición no cambia eso, porque protege contra riesgo idiosincrático y marzo de 2020 fue correlación pura.

Con exposición bruta idéntica, el conteo de liquidaciones debería ser del mismo orden. Que una sufra 14 y la otra prácticamente ninguna indica que las configuraciones difieren en exposición bruta de un modo no declarado, o que la lógica del cortacircuito no se aplica de forma idéntica en ambas ramas.

**Acción:** T-14.

---

### F-22 — Alpha CAPM mal especificado (ALTO)

La regresión de un solo factor contra SPY, aplicada a una cartera que puede tener hasta el 50% en `GLD` y `SLV`, atribuye al intercepto todo el retorno de los metales.

Los propios estadísticos lo delatan: β = 0.31 y **R² = 0.15**. El modelo explica el 15% de la varianza, de modo que el 85% restante cae en el residuo y el intercepto por construcción. Un alpha de +15.13% con R² de 0.15 no es evidencia de habilidad; es evidencia de que el modelo no describe la cartera.

Agregar que se probaron 45 configuraciones vuelve irrelevante el p-value de 0.019: con corrección de Bonferroni el umbral sería 0.0011. El propio cálculo de DSR ya lo captura por otra vía (SR* = 1.22 > SR = 1.11).

**Acción:** T-12. Predicción registrada antes del test: la mayor parte del alpha se traslada a cargas sobre el factor oro y el factor momentum, y el intercepto queda cerca de cero sin significancia.

---

### F-23 — Granularidad de Universo A no medida (MEDIO)

La §7 del consolidado estima el arrastre por redondeo de Universo A en **~15 bps anuales**, basándose en ETFs de $30 a $100. Dos problemas.

Primero, el roster real no encaja en ese rango: `XLK`, `XLY` y `XLV` cotizan muy por encima. Con una cuenta de $2.000 y posiciones objetivo del orden de $200 a $300, una sola acción de `XLK` puede representar el 15% de la cuenta en un salto indivisible.

Segundo, la cifra contradice la medición propia: la §5 mide **~1.4% anual** de arrastre exclusivamente por granularidad en S5, casi diez veces más. La estimación de Universo A no se midió, se extrapoló a la baja.

**Acción:** T-13.

---

### F-24 — Corrección del umbral de Gate 3 (error propio)

El umbral que fijé en `AUDIT_FOLLOWUP_v2.0.md` —drag acumulado ≤ 0.5% anualizado— está mal calibrado, y los datos del consolidado lo demuestran. Lo definí pensando en Universo A con rebalanceo semanal sobre ETFs y lo apliqué implícitamente a toda la arquitectura. Con la rotación medida de S5, unas 70 operaciones anuales sobre $2.000, la fricción estructural es de 2.5% a 4% anual y ningún ajuste de ejecución cierra esa brecha.

La corrección está en §6. El umbral pasa de ser una constante a ser relativo al modelo de costos de cada estrategia.

---

## 3. Tareas

Ninguna de estas tareas bloquea el arranque del reloj. Todas bloquean la discusión de capital real.

---

#### T-10 — Re-cómputo del test de monotonicidad

**Cierra:** F-20 / F-13.

**Procedimiento:**

1. Construir la serie de retornos de una cartera long-short: larga en `rank(1,2)`, corta en `rank(6,7)`, rebalanceada semanalmente, sobre el mismo universo y período que el test original.
2. Calcular el retorno medio, su error estándar con **Newey-West, lag = 8** (45 sesiones / 5 de muestreo − 1), t-stat y p-value.
3. Como control cruzado, repetir con muestreo **no solapado**: una observación cada 45 sesiones. Reportar ambos.
4. Repetir el procedimiento completo sobre Universo A por bloque.

**Salida:** actualización de `reports/rank_monotonicity.md` con sección "v2 — corrección por solapamiento".

**Criterio:** F-13 se cierra únicamente si el spread long-short es positivo y significativo al 5% bajo errores Newey-West. Si no lo es, el diseño de selección por ranking debe simplificarse a equiponderación sobre los elegibles que pasen el gate absoluto.

---

#### T-11 — Universo punto-en-el-tiempo para S5

**Cierra:** F-19 / F-02 para S5.

**Procedimiento:**

1. Definir una **regla** de construcción del universo, no una lista. Sugerido: las N compañías de mayor capitalización del S&P 500 a la fecha de corte, más los ETF de metales y renta fija que ya existían en esa fecha.
2. Aplicar la regla con corte informativo al **2010-01-01** y re-ejecutar 2010–2019 completo.
3. Repetir con cortes móviles anuales (reconstitución cada 1 de enero con información disponible a esa fecha), que es la versión metodológicamente correcta.
4. Documentar qué nombres entran y salen en cada reconstitución.

**Salida:** `reports/pit_universe_backtest_2010_2019.md`.

**Criterio:** si el alpha desaparece o pierde significancia con universo punto-en-el-tiempo, el resultado multiciclo se retira del repositorio como evidencia y se reclasifica como estudio de caso. Mientras T-11 no exista, la §4 del consolidado lleva advertencia explícita.

**Nota de presupuesto:** esto requiere constituyentes históricos con delistings. Si no hay acceso a Norgate, Sharadar o CRSP, el sustituto aceptable es una regla mecánica sobre datos públicos —por ejemplo, componentes del Dow Jones Industrial Average, cuya composición histórica está documentada y es de acceso libre— declarando la limitación. Lo que no es aceptable es proyectar hacia atrás la lista actual.

---

#### T-12 — Atribución multifactorial del alpha

**Cierra:** F-22.

**Procedimiento:** estimar

```
r_p − r_f = α + β₁(r_SPY − r_f) + β₂·r_GLD + β₃·r_MOM + ε
```

donde `r_MOM` es el factor de momentum (serie UMD de la librería de Kenneth French, o `MTUM` como proxy investible). Reportar α anualizado con error estándar, t-stat, cada β con su significancia, y R² del modelo completo contra el de un factor.

**Salida:** `reports/multifactor_attribution.md`.

**Criterio:** si el R² sube sustancialmente y el α pierde significancia, el retorno del sistema es exposición factorial y no habilidad. Eso no invalida la estrategia —replicar factores con disciplina tiene valor— pero cambia por completo lo que el repositorio puede afirmar.

---

#### T-13 — Granularidad medida sobre Universo A

**Cierra:** F-23.

**Procedimiento:** ejecutar sobre el roster real de 18 ETF, con precios reales y cuenta de $2.000, la misma comparación que la §5 hizo para S5: retorno con acciones fraccionarias ideales sin fricción contra retorno con acciones enteras (`floor`) y costos. Separar el componente de redondeo del componente de spread/slippage.

**Salida:** actualización de `reports/capm_alpha_and_costs.md`.

**Criterio:** si el arrastre por redondeo supera el 1.0% anual, evaluar reemplazar los ETF de precio unitario alto por equivalentes de menor precio —por ejemplo `VGT` o `FTEC` en lugar de `XLK`— documentándolo como decisión de implementación, no de estrategia.

---

#### T-14 — Log de liquidaciones de Top-4 en 2020

**Cierra:** F-21.

**Procedimiento:** exportar, para Top-2 y Top-4 en 2020, el registro de evaluación diaria del cortacircuito: fecha, exposición bruta al inicio de la sesión, P&L intradiario mínimo de cartera, umbral aplicable y acción tomada. Comparar las dos series en las sesiones de marzo de 2020 con SPY por debajo de −5%.

**Salida:** `reports/circuit_breaker_event_log_2020.md`.

**Criterio:** debe quedar explicado por qué dos carteras con exposición bruta del 100% sobre el mismo universo producen conteos de liquidación de orden distinto. Si la explicación es que Top-4 opera con exposición bruta menor, eso debe declararse en la especificación, porque entonces la comparación Top-2 contra Top-4 no es sobre dimensionamiento sino sobre exposición.

---

#### T-15 — Test de movilidad de capital

**Cierra:** la hipótesis explicativa de F-20.

**Procedimiento:** comparar cuatro configuraciones con costos y cortacircuitos activos:

1. Top-2, `max_holding_days = 30` (baseline actual)
2. Top-2, `max_holding_days = 10`
3. Top-2 con reevaluación semanal y reemplazo si cae del Top-4
4. Top-4, `max_holding_days = 30` (configuración propuesta)

**Salida:** `reports/capital_mobility_test.md`.

**Criterio:** si (2) o (3) igualan a (4), la ventaja proviene de la rotación y no de la amplitud, y el diseño debe conservar el mecanismo de movilidad en lugar del número de posiciones. Si solo (4) funciona, la amplitud es el factor y la hipótesis de movilidad queda descartada.

---

#### T-16 — Simplificación de Universo A

**Motivación:** el propio test mostró que el ranking sectorial es plano (~+2.0% en todos los ranks). Un parámetro que no discrimina es superficie de sobreajuste sin contrapartida, y el PBO de 84.45% indica que sobra exactamente eso.

**Procedimiento:** implementar una variante de Universo A que elimine el ranking y el `top_n` por bloque, y equipondere por volatilidad inversa **todos** los instrumentos del bloque que pasen el gate absoluto, conservando caps de bloque, régimen graduado y objetivo de volatilidad. Comparar contra la versión con ranking.

**Salida:** `reports/universe_a_simplified.md`.

**Criterio:** si el desempeño es equivalente, adoptar la variante simplificada como candidato primario. Menos parámetros libres reducen directamente el PBO en la próxima medición, que es el obstáculo real para capital.

---

## 4. Orden de ejecución

```
PARALELO AL RELOJ (no bloquean Fase 3)
  T-10  Monotonicidad con Newey-West y spread long-short   ← mayor prioridad
  T-14  Log de liquidaciones Top-4 2020                    ← rápido, cierra F-21
  T-13  Granularidad medida sobre Universo A               ← rápido
  T-12  Atribución multifactorial
  T-15  Test de movilidad de capital
  T-16  Variante simplificada de Universo A
  T-11  Universo punto-en-el-tiempo 2010                   ← el más costoso

BLOQUEAN CAPITAL REAL
  Las siete anteriores, más una nueva medición de PBO sobre la
  configuración final y evidencia forward de los 6 meses.
```

---

## 5. Autorización de Fase 3

**El reloj de seis meses queda autorizado**, en los términos que propone la §8 del consolidado:

- **Candidato primario con capital paper nominal:** Universo A (18 ETF, objetivo de volatilidad 12%), rebalanceo semanal, **acciones enteras con brackets GTC nativos**.
- **Candidato sombra sin capital nominal:** S5 Top-4 con cortacircuitos adaptativos.
- Telemetría de Gates 1 a 3 desde el día 1.

**Fundamento:** el reloj mide fidelidad de ejecución, no validez de la estrategia. Para eso la simulación ya está lista: tiene cortacircuitos intradiarios, curva real de tasa libre de riesgo y modelo de costos. Los hallazgos F-19 a F-23 son de investigación y no afectan la capacidad de comparar el motor de producción contra su simulación.

**Condición explícita que debe quedar registrada en `GEMINI.md`:**

> Con PBO = 84.45% y Sharpe observado de 1.11 por debajo del umbral crítico SR* = 1.22, **seis meses impecables de paper trading no habilitan capital real.** El paper trading demuestra que el motor funciona, que es condición necesaria y no suficiente. La existencia de una ventaja estadística sigue sin demostrarse y se responde con T-10 a T-12 más evidencia forward, no con ingeniería de ejecución.

---

## 6. Gate 3 corregido

Reemplaza íntegramente la definición de Gate 3 de `AUDIT_FOLLOWUP_v2.0.md`.

**Principio:** la fricción realizada se juzga contra el modelo de costos de la estrategia, no contra una constante universal. Un sistema con 70 operaciones anuales sobre $2.000 tiene una fricción estructural que ninguna calidad de ejecución elimina; lo auditable es si el backtest la modeló bien.

| Métrica | Umbral |
| :--- | :--- |
| Fricción total realizada vs. modelada en el backtest | **≤ 1.25×** el valor modelado |
| Slippage mediano por orden (ETF) | **≤ 8 bps** |
| Slippage percentil 95 | **≤ 25 bps** |
| Desvío del arrastre por granularidad vs. el medido en T-13 | **≤ 1.25×** |

**Condición de cierre, sin cambios:** al terminar los seis meses, actualizar el modelo de costos con los valores realizados y **re-ejecutar todas las simulaciones**. Si los resultados cambian materialmente, el gate no está aprobado aunque cada métrica individual esté en rango.

Gates 1 y 2 se mantienen exactamente como están definidos en `AUDIT_FOLLOWUP_v2.0.md` §6.

---

## 7. Qué no hacer

- **No citar la §4 del consolidado como validación fuera de muestra** hasta que T-11 esté cerrado. El multiciclo de 16 años mide el desempeño de una cartera de ganadores conocidos.
- **No tratar F-13 como cerrado.** El resultado puede ser correcto en dirección, pero hoy no está medido con estadísticos válidos.
- **No agregar configuraciones nuevas sin registrarlas.** El contador va en 45 y cada variante adicional eleva el umbral SR* y empeora el PBO. T-16 es una simplificación y va en la dirección correcta; cualquier variante que agregue parámetros va en la contraria.
- **No usar el resultado de los seis meses como argumento de rentabilidad.** Son unas 26 observaciones independientes y el error estándar del Sharpe estimado sobre esa muestra ronda ±0.9.
- **No promover S5 Top-4 de sombra a primario durante el período**, cualquiera sea su desempeño relativo. La decisión de arquitectura se toma al cierre, con los datos de fidelidad de ambos y con T-11 resuelto.

---

*Tercera ronda de auditoría externa. Los hallazgos F-19 a F-23 son hipótesis falsables derivadas del análisis de las cifras del consolidado v2.2; cada uno indica el procedimiento para confirmarlo o descartarlo. F-24 es una corrección de un umbral mal calibrado por este auditor en la ronda anterior.*
