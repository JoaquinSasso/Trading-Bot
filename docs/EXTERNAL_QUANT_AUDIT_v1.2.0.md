# AUDITORÍA CUANTITATIVA EXTERNA — Trading-Bot v1.2.0

| Campo | Valor |
| :--- | :--- |
| **Versión auditada** | S5 Dual Momentum Leader v1.2.0 |
| **Fecha de auditoría** | 2026-09-20 |
| **Documentos base** | `README.md`, `docs/SYSTEM_ARCHITECTURE_AND_AUDIT_DOSSIER.md` |
| **Alcance** | Diseño de estrategia, metodología de backtest, arquitectura de riesgo, pipeline NLP |
| **Fuera de alcance** | Revisión línea a línea del código; no se ejecutó la suite de tests ni se reprodujeron los backtests |
| **Dictamen** | Apto para paper trading continuo. **No apto para capital real.** |

> **Nota de alcance.** Esta auditoría se realizó exclusivamente sobre la documentación provista. Todos los hallazgos son hipótesis falsables sobre la metodología, no verificaciones del código. Cada uno indica cómo confirmarlo o descartarlo.

---

## 1. Resumen ejecutivo

El sistema tiene una base conceptual legítima y una calidad de ingeniería notablemente superior a su calidad de validación. El motor de ejecución —invariantes fail-closed, reloj inyectable, broker simulado, 1.201 tests— está mejor construido que la evidencia que pretende justificarlo.

El problema es que la evidencia no sostiene la conclusión. El resultado de 2025 (+81.85%, Sharpe 2.91, MaxDD -7.57%) no mide la estrategia: mide una decisión de selección de activos tomada después de observar el período. El único tramo genuinamente fuera de muestra, 2020-2022, arroja un Sharpe de 0.40 y un drawdown de 28.97%, y ese tramo está a su vez contaminado por al menos dos problemas metodológicos identificados abajo (F-04 y F-05).

La lectura honesta de los datos disponibles es la siguiente: existe evidencia débil de que un overlay de tendencia con rotación a efectivo protege capital en mercados bajistas severos (2022 es real y es bueno), y no existe evidencia alguna de una capacidad de generación de alpha sostenida. El sistema corre de forma consistente a ~1.7x la volatilidad del S&P 500 sin compensar ese riesgo con retorno ajustado.

---

## 2. Registro de hallazgos

| ID | Hallazgo | Severidad | Verificable mediante |
| :--- | :--- | :--- | :--- |
| F-01 | El período 2025 es in-sample: `GLD`/`SLV` se incorporaron tras observar el año | **Crítico** | Reconstrucción del changelog de universo |
| F-02 | Universo de 14 activos seleccionado ex post (sesgo de supervivencia) | **Crítico** | Test de universo con corte informativo a 2019-12-31 |
| F-03 | Cortacircuitos de -2%/-3.5% probablemente no simulados en el backtest | **Crítico** | Grep del motor de simulación por la lógica de circuit breaker |
| F-04 | Tasa libre de riesgo constante (~4.5%) aplicada a 2020-2022 | **Crítico** | Inspección del parámetro de rendimiento en régimen BEAR |
| F-05 | Procedencia de las 10.962 observaciones históricas de noticias | **Alto** | Trazabilidad de fuente y campo de fecha usado |
| F-06 | Densidad del dataset FinBERT ≈ 1 observación por activo-día | **Alto** | `groupby(symbol, date).count()` sobre el dataset |
| F-07 | Concentración 50% por activo: riesgo de gap no mitigable | **Alto** | — (estructural) |
| F-08 | Fail-closed incompatible con acciones fraccionarias en Alpaca | **Alto** | Documentación de la API de Alpaca |
| F-09 | Horizonte de tenencia (30d) menor que ventana de señal (45d) | **Medio** | — (estructural) |
| F-10 | "Alpha" reportado como diferencia de retornos, no como intercepto de regresión | **Medio** | Recálculo por regresión contra SPY |
| F-11 | Posible inconsistencia de dividendos entre estrategia y benchmark | **Medio** | Inspección de precios ajustados vs. simples |
| F-12 | Granularidad de acciones genera cash drag no contabilizado | **Medio** | Simulación con precios reales y tamaño de cuenta real |

---

## 3. Hallazgos críticos en detalle

### F-01 — El resultado de 2025 es in-sample

SLV rindió aproximadamente 127% y GLD 67% en los doce meses hasta diciembre de 2025. Fue el mejor año del oro en más de cuatro décadas.

El propio README documenta la secuencia: v1.1.0 sin metales rinde +66.54%; v1.2.0 con metales rinde +81.85%, y se la etiqueta "MÁXIMO GANADOR". Ese delta no es una mejora de la estrategia. Es el efecto de haber agregado, con conocimiento del resultado, los dos activos que más subieron en el período de prueba.

El contraste decisivo: un 50/50 de `GLD`+`SLV` comprado en enero de 2025 y mantenido sin ninguna lógica habría superado el +81.85% del sistema. Toda la maquinaria —ranking transversal, filtro de régimen, trailing EMA(25), veto FinBERT— restó rendimiento sobre el beta que el propio diseñador seleccionó a posteriori.

Consecuencia metodológica: la disciplina `timestamp <= as_of` es rigurosa pero se aplica al nivel equivocado. El sesgo de futuro no está en las señales; está en la lista de tickers. Ningún control a nivel de serie temporal puede detectarlo.

### F-02 — Sesgo de selección en el universo

Los 14 instrumentos son el conjunto de ganadores conocidos de la última década. `NVDA`, `LLY`, `COST` y `META` no constituyen diversificación multi-sectorial: son los mejores performers sectoriales identificables únicamente en retrospectiva. Ningún activo de la lista quebró, se estancó durante una década ni fue excluido de su índice.

Backtestear 2020-2022 sobre un universo definido en 2026 es lookahead a nivel de construcción de cartera, y es el sesgo más severo que puede afectar a una estrategia de momentum transversal, porque el ranking opera precisamente sobre el conjunto sesgado.

**Test correctivo:** reconstruir el universo con información disponible al 31/12/2019 —por ejemplo, top-14 por capitalización del S&P 500 a esa fecha, incluyendo necesariamente nombres como `INTC`, `BA`, `PFE`, `GE`— y re-ejecutar 2020-2022. Si el alpha desaparece, era selección ex post.

### F-03 — El backtest y la capa de riesgo no son el mismo sistema

El diseño combina liquidación forzosa de emergencia al -3.5% intradiario con asignación del 50% del capital a un único ticker. `NVDA` registra movimientos diarios de ±4% con regularidad; `SLV` más.

La aritmética es directa: un -7% en uno de los dos líderes equivale a -3.5% de cuenta, es decir liquidación total del portafolio. Con la volatilidad implícita que muestran los propios resultados (~27% anual), ese evento ocurre decenas de veces por año.

Si las simulaciones de 2020-2022 arrojaron drawdowns de 24%, la única explicación coherente es que los cortacircuitos no estaban activos en el motor de simulación. Con ellos operando, la cuenta habría quedado en efectivo a los pocos días de cada corrección y las trayectorias serían completamente distintas —y peores, porque cada liquidación forzada cristaliza la pérdida en el punto de máxima volatilidad y obliga a re-entrar más arriba.

**Implicación:** los números auditados corresponden a una estrategia que no es la que correrá en producción. Hasta resolver esto, toda comparación con SPY es inválida.

### F-04 — Anacronismo en la tasa libre de riesgo

El sistema asume ~4.5% anual en T-Bills durante el régimen BEAR. Esa tasa corresponde al entorno de 2023-2026. En 2020 y 2021 el rendimiento de los T-Bills a corto plazo osciló entre 0.03% y 0.10%. `SGOV` ni siquiera comenzó a cotizar hasta mayo de 2020.

Si la constante se aplicó uniformemente a las simulaciones de 2020-2022, se inyectó retorno inexistente justamente en los meses de febrero a mayo de 2020, que es el tramo sobre el que descansa toda la narrativa de preservación de capital.

### F-05 y F-06 — El pipeline FinBERT

**Densidad.** 7.490 observaciones dividido por (14 activos × ~500 sesiones) da ≈ 1,07 observaciones por activo-día. El dataset histórico presenta la misma proporción: 10.962 ÷ (14 × 756) ≈ 1,04.

Con aproximadamente una noticia por activo por día, `negative_share` no es una feature continua normalizada: es una variable prácticamente binaria. Un umbral de veto en 0.35 equivale operativamente a "la única noticia del día fue negativa". Eso es ruido con nomenclatura técnica.

**Procedencia.** Yahoo Finance RSS no mantiene archivo histórico consultable. El origen de las 10.962 observaciones de 2020-2022 requiere trazabilidad explícita. Si provienen de un backfill que usa fecha de indexación en lugar de fecha de publicación, existe lookahead contaminando todo el tramo declarado como out-of-sample.

**Ventana temporal.** La mayoría de los Form 8-K se presentan después del cierre de mercado. Una ventana que corta a las 15:45 ET es estructuralmente ciega al momento en que la información regulatoria efectivamente aparece.

**Acción requerida:** ablation test de S5 v1.2.0 con y sin veto, sobre datos idénticos. Con 21 operaciones anuales, el veto se activó un puñado de veces; la hipótesis de trabajo es que su contribución es indistinguible de cero.

---

## 4. Análisis del perfil de riesgo

### La volatilidad explica la dispersión del drawdown

Despejando la volatilidad implícita de los pares retorno/Sharpe reportados:

| Año | Retorno | Sharpe | Vol. implícita | MaxDD observado |
| :--- | ---: | ---: | ---: | ---: |
| 2020 | +18.24% | 0.60 | ~28% | -24.10% |
| 2021 | +14.08% | 0.51 | ~26% | -23.41% |
| 2022 | -1.43% | -0.22 | ~16% | -10.44% |
| 2025 | +81.85% | 2.91 | ~27% | **-7.57%** |

El sistema opera de forma consistente en torno al 27% de volatilidad anualizada, aproximadamente 1,7 veces la del S&P 500. Con ese nivel de volatilidad, el drawdown esperado en un año cualquiera se sitúa en el rango 20-30%, que es exactamente lo observado en 2020 y 2021.

El -7.57% de 2025 no es una propiedad del diseño. Es un outlier afortunado de una distribución que no cambió. La menor volatilidad de 2022 se explica sola: la cartera pasó la mayor parte del año en efectivo.

**El dato más revelador del dossier completo:** en 2021, el año más plácido de la década, el SPY registró un drawdown máximo cercano al -5%. El sistema registró -23.41%. El drawdown no proviene del mercado; proviene de asignar el 50% del capital a un único ticker.

### Refutación de la tesis de "captura parcial aceptable"

El dossier plantea el rendimiento de 2021 (+14.08% contra +26.55% del SPY) como el costo estructural aceptable de la protección lograda en 2022. La premisa es incorrecta.

Un overlay defensivo se justifica cuando reduce la volatilidad del portafolio. El de este sistema la aumenta. En 2021 se sacrificaron 12,5 puntos de retorno mientras se corría 26% de volatilidad y un drawdown de 23%. No se pagó menor retorno a cambio de menor riesgo: se pagó riesgo de renta variable concentrada para obtener retorno de renta fija.

### F-07 — Riesgo de gap overnight

Un gap bajista de -20% en uno de los dos líderes produce -10% de cuenta de forma instantánea. El stop loss no protege: en un gap se convierte en orden de mercado ejecutada al precio ya deteriorado. El cortacircuito de -3.5% tampoco, porque solo puede dispararse después del hecho.

Sobre una cuenta de $2.000 la pérdida es sobrevivible en términos absolutos. El problema de auditoría es que la documentación presenta como garantía incondicional algo que matemáticamente no lo es. La "Garantía Incondicional de Stop Loss (<60s)" garantiza la existencia de una orden, no un precio de ejecución, y esa distinción debe figurar explícitamente en `GEMINI.md`.

### F-08 y F-12 — Viabilidad operativa con $2.000

Con `GLD` en torno a los $380, una asignación de $1.000 compra 2 acciones por $760, dejando 24% de la posición en efectivo. Eso contradice directamente el objetivo declarado de "erradicar el cash drag".

La alternativa son acciones fraccionarias, pero debe verificarse si Alpaca admite órdenes stop o bracket sobre posiciones fraccionarias. Si no las admite, el invariante fail-closed no se cumple en producción y el sistema tiene una contradicción de diseño sin resolver: o hay fail-closed, o hay diversificación, pero no ambas con este tamaño de cuenta.

Adicionalmente, en cuenta cash la liquidación T+1 restringe la rotación y expone a good-faith violations si dos rotaciones coinciden en ventana.

---

## 5. Evaluación de la estrategia

### Transición S3 → S5

Conceptualmente correcta y respaldada por la literatura: Jegadeesh & Titman (1993) sobre momentum transversal, Antonacci (2014) sobre dual momentum, Moskowitz-Ooi-Pedersen sobre time-series momentum. No es un ajuste arbitrario sobre datos históricos.

La objeción es de implementación. La literatura aplica momentum transversal sobre universos de 50 a 500 activos por una razón matemática precisa: el ranking cruzado diversifica el componente idiosincrático del retorno. Con 14 activos, de los cuales 8 son megacap tech con correlaciones entre 0.7 y 0.85, el ranking transversal deja de funcionar como tal. Lo que queda es una apuesta concentrada con pasos intermedios.

### Filtro de régimen macro

La arquitectura de momentum absoluto más relativo es la correcta. Las debilidades son dos.

La primera es de latencia: EMA50 y SMA200 sobre SPY reaccionan con uno a dos meses de retraso, de modo que en 2020 el sistema salió después de una caída del 20% y volvió a entrar después de una recuperación del 20%. La segunda es la naturaleza binaria del régimen: con el SPY oscilando alrededor de la EMA50 se produce whipsaw sistemático, entrando y saliendo en los peores puntos del ciclo. Una transición gradual —peso proporcional a la distancia normalizada respecto de las medias— mitiga ambos efectos.

### Regla de salida

El trailing sobre EMA(25) es defendible y preferible a un take-profit fijo por múltiplos de ATR. Preserva la asimetría positiva de la distribución de retornos, que es la única fuente de esperanza matemática en cualquier estrategia de momentum. Un objetivo de beneficio rígido amputa la cola derecha, que es exactamente donde reside la ganancia.

Dos problemas concretos. El primero (F-09): se rankea con una ventana de 45 días y se sostiene un máximo de 30. El horizonte de tenencia es más corto que el horizonte de la señal, de modo que las posiciones se liquidan sistemáticamente antes de que la señal que motivó la entrada termine de expresarse. O el tope se extiende a 60-90 días, o la ventana de ranking se acorta.

El segundo: el stop es de cierre y se evalúa una vez por sesión. La pérdida máxima por posición no está acotada ni intradiaria ni overnight, independientemente de lo que haga el `PositionGuardian`.

### Significancia estadística

Cuatro observaciones anuales no constituyen evidencia. La probabilidad de superar al índice en 3 de 4 años por puro azar es del 31%. Ningún test con n=4 puede distinguir habilidad de suerte.

---

## 6. Dilemas evaluados

**Prohibición de shorting: se respalda.** En cuenta cash bajo Reg T no está siquiera permitido. Con independencia de eso: el short tiene asimetría estructuralmente negativa (pérdida no acotada, ganancia acotada al 100%), costo de préstamo variable y riesgo de squeeze, y los rebotes bajistas son los movimientos más violentos del mercado. La evidencia empírica citada (Sharpe cae a 0.90, drawdown se duplica) es consistente con la teoría. Decisión correcta y bien documentada.

**ETFs inversos: no se recomiendan.** Instrumentos como `SH` o `SDS` se rebalancean diariamente y sufren arrastre de volatilidad del orden de σ²/2 por unidad de tiempo en mercados laterales. Con la volatilidad típica de un mercado bajista, eso es una sangría garantizada. Lo que se necesita en régimen BEAR no es cobertura sino menor exposición bruta, que es lo que el sistema ya hace.

**Metales físicos vs. futuros sintéticos: se respalda.** El argumento de contango es correcto y verificable: `USO` perdió más del 90% en una década durante la cual el petróleo se mantuvo aproximadamente plano. La exclusión de `USO`/`UNG` está bien fundamentada.

**Clases de activos adicionales: no se recomiendan por ahora.** La candidata natural sería duración (`IEF`/`TLT`), pero 2022 demostró que la correlación acciones-bonos puede invertirse precisamente cuando se la necesita. El cuello de botella del sistema no es la falta de activos, es el dimensionamiento de posiciones.

---

## 7. Recomendaciones priorizadas

### P0 — Validación (bloqueante, previo a cualquier otro desarrollo)

- [ ] Re-ejecutar las cuatro simulaciones con los cortacircuitos de -2% / -3.5% activos (F-03)
- [ ] Sustituir la tasa libre de riesgo constante por la curva diaria histórica de T-Bills (F-04)
- [ ] Test de universo reconstruido con corte informativo al 31/12/2019 (F-02)
- [ ] Ablation test del veto FinBERT: con veto vs. sin veto, datos idénticos (F-06)
- [ ] Calcular Deflated Sharpe Ratio y probabilidad de sobreajuste por CSCV (Bailey & López de Prado). Con 5-6 parámetros libres ajustados sobre los mismos datos, la expectativa razonable es que el Sharpe de 2.91 deflactado caiga por debajo de 1
- [ ] Verificar consistencia de dividendos entre estrategia y benchmark (F-11). Si la estrategia usa precios ajustados y SPY usa precio simple, el alpha está inflado en ~1,5 puntos anuales
- [ ] Recalcular el alpha por regresión contra SPY y reportar el intercepto con su error estándar, no la diferencia de retornos (F-10)
- [ ] Incorporar costos de transacción, spread y modelo de slippage al motor de simulación
- [ ] Simular con tamaño de cuenta y precios reales para cuantificar el cash drag por granularidad (F-12)

### P1 — Dimensionamiento por volatilidad inversa

Reemplazar la asignación fija 50/50 por pesos proporcionales a 1/σᵢ, con σ estimado por ATR de 20 días o desvío estándar de retornos de 60 días. A continuación, escalar la exposición bruta de modo que la volatilidad objetivo del portafolio se sitúe entre 12% y 15% anual, dejando efectivo como residuo.

Esta única medida habría reducido aproximadamente a la mitad los drawdowns de 2020 y 2021. Reintroduce cash drag de forma deliberada: ese es el punto. El cash drag es el precio de no destruir la cuenta.

### P2 — Ensemble multi-horizonte y ampliación del Top-N

Promediar *rankings* —no retornos— de ventanas de 21, 45 y 90 días. No mejora significativamente el retorno esperado, pero elimina la fragilidad del parámetro 45 y reduce la rotación.

Elevar el Top-N de 2 a 4-5 posiciones con pesos por volatilidad inversa es la única modificación que ataca la causa raíz de los drawdowns observados.

### P3 — Riesgo de gap overnight

No existe cobertura económicamente viable con $2.000 de capital: las puts protectoras cuestan más que el edge estimado. La única mitigación real es el dimensionamiento: ningún nombre individual por encima del 20-25% del equity. La concentración *es* el riesgo de gap, y no admite parche elegante.

---

## 8. Scorecard institucional

| Pilar | Nota | Fundamento |
| :--- | :---: | :--- |
| Rigor matemático y conceptual de la estrategia | **4/10** | Base teórica sólida; validación circular |
| Arquitectura de riesgo y seguridad operativa | **5/10** | Buenas invariantes de ejecución, pero el sizing contradice los cortacircuitos y el fail-closed choca con fraccionarios |
| Ingesta de datos y procesamiento de sentimiento | **3/10** | ~1 observación por activo-día; procedencia histórica sin justificar; veto sin ablation |
| Robustez frente a condiciones adversas | **4/10** | 2022 es genuinamente bueno; 2020-2021 muestra -24% de DD y la muestra es n=4 |
| Facilidad de implementación y mantenibilidad | **7/10** | 1.201 tests, separación modular limpia, reloj inyectable, broker simulado. Es el activo más valioso del proyecto |

---

## 9. Veredicto final

**Paper trading continuo: aprobado.** No hay riesgo de capital y es el único mecanismo capaz de generar datos que el diseñador no haya observado previamente. Debe iniciarse de inmediato.

**Capital real: no aprobado**, y no por un margen estrecho.

### Requisitos sine qua non

**1. Re-ejecución íntegra del benchmark bajo condiciones realistas.** Cortacircuitos activos, tasa libre de riesgo histórica real, costos y slippage modelados. Si el Sharpe del trienio 2020-2022 se mantiene en torno a 0.40 bajo estas condiciones, el sistema no justifica capital real frente a la alternativa de comprar SPY y no mirar la pantalla.

**2. Test de universo con corte informativo a diciembre de 2019.** Si el alpha de 2020-2022 desaparece cuando la lista de 14 activos deja de ser la lista de ganadores conocidos, lo construido no es una estrategia: es una descripción retrospectiva de la última década.

**3. Seis meses de paper trading continuo con el código de producción**, no con el script de backtest, reconciliando cada fill contra su señal de origen. La métrica relevante de esos seis meses no es el retorno: es el tracking error entre lo que el motor de paper ejecuta y lo que la simulación predijo. Si divergen, todos los puntos anteriores son irrelevantes.

### Requisito operativo adicional

Resolver la compatibilidad entre fail-closed y acciones fraccionarias antes de cualquier despliegue con capital. Con $2.000 y un universo de instrumentos de precio elevado, la configuración actual no puede garantizar simultáneamente stop loss en toda posición y diversificación efectiva.

---

*Documento generado como auditoría externa independiente. Los hallazgos se basan exclusivamente en la documentación provista y deben verificarse contra el código fuente antes de ser tratados como conclusiones firmes.*
