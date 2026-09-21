# ESPECIFICACIÓN — S8: Scorer PID Multi-Horizonte de Doble Sistema

| Campo | Valor |
| :--- | :--- |
| **Documento** | Especificación de diseño, no implementada |
| **Fecha** | 2026-09-21 |
| **Alcance** | Investigación. Reemplazo de la función de scoring y selección |
| **Reemplaza** | `S6_PID_SCORER_SPEC.md` |
| **Cambio respecto de S6** | Se restituyen los ocho horizontes, incluidos los cinco intradiarios |
| **Estado** | Requiere validación. Cuenta como configuración nueva en el ledger (actual: 45) |

> **Nota de contexto regulatorio.** S6 bloqueaba los horizontes intradiarios apoyándose en la regla de Pattern Day Trader. Esa regla fue eliminada: la SEC aprobó el 14 de abril de 2026 la enmienda a FINRA Rule 4210 que suprime el umbral de $25.000, el conteo de operaciones y la designación misma, con efecto desde el 4 de junio de 2026. El mínimo para cuenta de margen queda en $2.000. El bloqueo regulatorio de S6 §6.2 queda sin efecto y los ocho horizontes se restituyen.
>
> Para despliegue futuro conviene confirmar en qué punto del período de implementación está Alpaca: FINRA concedió a las firmas hasta el 20 de octubre de 2027 para adoptar el nuevo marco. Para investigación sin capital, es irrelevante.

---

## 1. Qué es S8

Un PID es un controlador de lazo cerrado: mide el error contra un setpoint y actúa sobre el proceso para anularlo. En trading falta el elemento esencial, porque la salida no afecta a la variable de proceso. No hay lazo.

Lo que queda es un **scorer multi-horizonte** organizado en tres términos: un nivel, una acumulación y una tasa de cambio medida a ocho escalas temporales. La etiqueta PID organiza los términos y aporta dos cosas con contenido real —el tratamiento del windup integral y el filtrado de la derivada— pero no convierte al sistema en un controlador.

S8 reemplaza **únicamente** la función de scoring y selección dentro de la arquitectura existente. Universo, caps de bloque, ponderación por volatilidad inversa, objetivo de volatilidad, cortacircuitos, buffer de rotación y gates de paper trading se mantienen sin modificación y sin re-optimizar. Tocar más de una cosa a la vez impide atribuir cualquier diferencia de resultado.

---

## 2. El problema del signo

En la convención de control, `u = Kp·e + Ki·∫e + Kd·de/dt` empuja la variable **hacia** el setpoint. Si el error es la desviación del precio respecto de su media móvil, el controlador vende cuando el precio está arriba.

**Un PID aplicado literalmente a precios es un sistema de reversión a la media.** Eso es S3, la estrategia que el proyecto abandonó por insuficiente. Para que S8 sea de tendencia, el término proporcional lleva signo positivo: `u = +Kp·e + ...`.

El signo es el único parámetro binario que vale testear, porque separa dos hipótesis económicas opuestas y no es una sintonización. Se ejecutan ambos y se publican ambos.

---

## 3. Definición del error

Para cada instrumento `i` en el instante `t`:

```
e_i(t) = [ ln P_i(t) − ln EMA_45d,i(t) ] / σ_i(t)
```

`EMA_45d` y `σ_i` se calculan sobre **datos diarios**, con `σ_i` la desviación estándar de retornos logarítmicos de las últimas 45 sesiones. `P_i(t)` es el precio en el instante `t`, que puede ser intradiario.

La referencia es diaria y el precio es intradiario a propósito: el error mide cuánto se ha alejado el precio actual de su tendencia establecida, no de una media intradiaria que oscilaría con el ruido que se quiere medir.

La normalización por volatilidad es obligatoria. Sin ella, `SLV` y `XLP` producen errores de magnitudes incomparables y el ranking transversal se convierte en un ranking de volatilidad.

---

## 4. Los tres términos

### 4.1 Proporcional

```
P_i(t) = e_i(t)
```

Mide cuán extendido está el precio respecto de su referencia, en unidades de volatilidad.

### 4.2 Integral, con anti-windup

```
I_i(t) = clip( EWMA_45d[ e_i ](t), −3, +3 )
```

**EWMA, nunca suma acumulada.** Una suma de un error persistentemente positivo crece sin cota. Es el windup clásico de los controladores reales y su traducción a trading es costosa: la integral saturada mantiene la posición larga mucho después de que la tendencia terminó, porque tarda en drenar. Es el mecanismo por el cual los sistemas de tendencia compran techos.

El clip a ±3 acota el aporte: un instrumento que lleva 45 sesiones tres desviaciones sobre su media no es más comprable por estar cuatro arriba.

El integral se computa sobre cierres diarios. No tiene componente intradiario, por construcción: es el término lento del sistema.

### 4.3 Derivativo multi-horizonte

```
D_i(t) = Σ_h  w_h · [ e_i(t) − e_i(t−h) ] / √h
```

sobre los ocho horizontes, con `h` expresado en **minutos de sesión regular** de forma consistente tanto en los pesos como en la normalización.

**La división por √h no es cosmética.** Bajo caminata aleatoria, la dispersión de una diferencia a `h` pasos escala con `√h`. Sin normalizar, los horizontes largos producen diferencias sistemáticamente mayores y los pesos quedan confundidos con un efecto de escala: se creería estar ponderando cuando se está midiendo la raíz del horizonte. Con la normalización, cada término aporta una magnitud comparable y los `w_h` significan lo que se pretende.

### 4.4 Agregación

```
z_P = zscore_transversal( P )    dentro del bloque y la fecha
z_I = zscore_transversal( I )
z_D = zscore_transversal( D )

u_i(t) = s · z_P + z_I + z_D          con s ∈ {+1, −1}
```

Ganancias fijas en 1/1/1. La estandarización transversal previa ya pone los tres términos en la misma escala; ponderarlos por igual es la elección neutral, y cualquier otra es una hipótesis que hay que justificar antes de ver el resultado.

---

## 5. Los ocho horizontes y sus pesos

Horizontes: **10 días, 5 días, 1 día, 2 horas, 1 hora, 30 minutos, 15 minutos, 5 minutos.**

Sesión regular de 390 minutos. En minutos: 3900, 1950, 390, 120, 60, 30, 15, 5.

### 5.1 Variante A — `w_h ∝ h` (configuración de referencia)

Es la regla literal de "mientras más largo el intervalo, más peso".

| Horizonte | `h` (min) | Peso en D | Peso en el score total |
| :--- | ---: | ---: | ---: |
| 10 días | 3900 | 60.28% | 20.09% |
| 5 días | 1950 | 30.14% | 10.05% |
| 1 día | 390 | 6.03% | 2.01% |
| 2 horas | 120 | 1.86% | 0.62% |
| 1 hora | 60 | 0.93% | 0.31% |
| 30 min | 30 | 0.46% | 0.15% |
| 15 min | 15 | 0.23% | 0.08% |
| 5 min | 5 | 0.08% | 0.03% |
| **Subtotal intradiario** | | **3.56%** | **1.19%** |

**Consecuencia aritmética que hay que ver antes de implementar:** bajo esta regla los cinco horizontes intradiarios juntos aportan el 1.19% del score final, y el de 5 minutos aporta 26 milésimas de punto porcentual. Se computan, pero no pueden cambiar un ranking. Se procesarían cerca de 5,6 millones de barras para producir un término que no mueve ninguna decisión.

Esto no es una objeción a la idea: es la interacción entre dos requisitos que se contradicen. O los horizontes largos dominan, o los intradiarios importan. La variante A respeta el primero y sacrifica el segundo.

### 5.2 Variantes que preservan el orden sin anular lo corto

Las tres alternativas mantienen el peso estrictamente creciente con el horizonte —la regla que pediste— y difieren solo en la pendiente.

| Horizonte | A: `w ∝ h` | B: `w ∝ ln h` | C: `w ∝ rango` | D: `w ∝ √h` |
| :--- | ---: | ---: | ---: | ---: |
| 10 días | 60.28% | 21.53% | 22.22% | 39.87% |
| 5 días | 30.14% | 19.72% | 19.44% | 28.19% |
| 1 día | 6.03% | 15.53% | 16.67% | 12.61% |
| 2 horas | 1.86% | 12.46% | 13.89% | 6.99% |
| 1 hora | 0.93% | 10.66% | 11.11% | 4.94% |
| 30 min | 0.46% | 8.85% | 8.33% | 3.50% |
| 15 min | 0.23% | 7.05% | 5.56% | 2.47% |
| 5 min | 0.08% | 4.19% | 2.78% | 1.43% |
| **Intradiario** | **3.56%** | **43.21%** | **41.67%** | **19.33%** |

**Recomendación para la fase de investigación:** ejecutar A y B. A es la especificación literal y sirve de control; B es la que efectivamente testea si la información intradiaria aporta algo. Si B no supera a A, la respuesta sobre los horizontes cortos queda contestada con datos propios en lugar de con un argumento a priori, que es un cierre mucho más sólido.

C y D quedan disponibles como intermedias si B resulta demasiado agresiva.

**Contabilidad:** cada variante suma al ledger de configuraciones. A y B, con dos signos cada una, son cuatro entradas.

---

## 6. Tratamiento del dato intradiario

Esta sección existe porque los errores de implementación intradiaria son silenciosos: producen resultados excelentes y falsos.

### 6.1 Alineación temporal y sesgo de futuro

**La barra de 5 minutos etiquetada `15:45` contiene datos de 15:40 a 15:45 y solo está disponible a partir de las 15:45:00.** Usarla como insumo de una decisión tomada "a las 15:45" es correcto únicamente si la barra está cerrada. El error de un desfase de barra es la forma más común de lookahead intradiario y produce mejoras de rendimiento espectaculares e ilusorias.

Regla: toda barra usada en el cálculo de `e_i(t)` debe tener `timestamp_cierre ≤ t`. El motor debe validar esta condición explícitamente y fallar ruidosamente si se viola, no corregir en silencio.

### 6.2 Límites de sesión

`e_i(t) − e_i(t−30min)` a las 09:45 no es un movimiento de 30 minutos: cruza el cierre anterior y contiene el gap nocturno completo.

Regla: **las diferencias de horizonte intradiario se calculan solo dentro de la misma sesión.** En la primera media hora de cada jornada, los horizontes que no tienen historia suficiente dentro de la sesión quedan indefinidos y su peso se redistribuye proporcionalmente entre los horizontes disponibles.

Alternativa explícitamente rechazada: rellenar con el cierre previo. Eso convierte el término en un detector de gaps disfrazado de derivada de corto plazo.

### 6.3 Sesión regular únicamente

Se usa exclusivamente 09:30–16:00 ET. Las sesiones extendidas tienen volumen tan escaso que las barras de 5 y 15 minutos son en su mayoría artefactos.

### 6.4 Días de sesión corta

Alrededor de quince sesiones por año cierran a las 13:00 (víspera de Navidad, día posterior a Acción de Gracias, entre otras) con 210 minutos en lugar de 390. El conteo de barras cambia y los horizontes de 2 horas pierden parte de su ventana.

Regla: mantener una tabla de calendario con duración real de cada sesión, y aplicar la misma redistribución de pesos de §6.2 cuando un horizonte no cabe.

### 6.5 Ruido de microestructura

A escala de minutos, la autocorrelación de primer orden de los retornos está dominada por el rebote entre puntas, no por información. Una derivada cruda sobre eso mide el spread.

Mitigación de referencia, tomada directamente de la práctica de control: **filtrar la derivada con un pasabajos antes de usarla.** Para los horizontes de 5, 15 y 30 minutos, aplicar una media móvil de 3 barras al precio antes de calcular `e`. Es el equivalente del filtro derivativo de un PID real y no introduce parámetros nuevos si se fija la ventana en 3.

Recomendación adicional para el diagnóstico: reportar la autocorrelación de primer orden de los retornos de 5 minutos por instrumento. Si es fuertemente negativa, el rebote domina y ese horizonte no está midiendo lo que se cree.

### 6.6 Calidad de la fuente

Si las barras provienen del nivel gratuito de Alpaca, son de IEX, que representa una fracción pequeña del volumen consolidado. Para horizontes diarios el efecto es despreciable; para 5 y 15 minutos puede ser dominante, con barras vacías o precios no representativos.

**Requisito de diagnóstico previo:** reportar, por instrumento y año, el porcentaje de barras de 5 minutos con volumen cero o ausentes. Si supera el 10%, ese horizonte se marca como no confiable para ese instrumento y su peso se redistribuye.

---

## 7. Los dos sistemas

El pedido de dos sistemas en competencia es correcto en intuición y peligroso en la implementación obvia.

**Lo que no hay que hacer:** definir el sistema bajista como el alcista con signo invertido. Dos modelos con las mismas entradas y la misma forma funcional con signo opuesto están perfectamente anticorrelacionados. Su competencia se reduce a comparar un número contra su negativo, que es un umbral sobre una sola señal.

**Lo que sí hay que hacer:** construirlos asimétricos, porque el mercado lo es. Las caídas son más rápidas que las subidas, la volatilidad se expande más de lo que se contrae, y las correlaciones convergen a uno en el estrés y se dispersan en la calma.

### 7.1 Sistema U — Persistencia de tendencia

Entradas: el scorer PID completo de §4, sobre los ocho horizontes.

Salida: `u_i(t)`, score transversal dentro del bloque.

Horizonte de reacción: lento. El integral a 45 sesiones domina su comportamiento.

Es el sistema de **selección**: responde qué comprar.

### 7.2 Sistema D — Estrés y deterioro

Entradas deliberadamente distintas. Ninguna es el negativo de una entrada de U, y tres de ellas aprovechan el dato intradiario de un modo que U no puede.

| Característica | Definición | Qué captura |
| :--- | :--- | :--- |
| Expansión de volatilidad | `σ_5d / σ_45d`, con `σ_5d` por varianza realizada sobre barras de 5 min | Régimen de volatilidad en transición |
| Asimetría de semidesvío | `σ⁻_20d / σ⁺_20d` | Cola izquierda engordando |
| Profundidad desde máximo | `(P_t − max P_45d) / σ_45d` | Deterioro estructural, no ruido |
| Convergencia de correlación | `corr_30min(r_i, r_SPY)` sobre las últimas 5 sesiones | Pérdida de diversificación, típica del estrés |
| Aceleración intradiaria | `|Δe_i(30min)| / σ_intradía_20d` | Movimiento anómalo en curso |
| Frecuencia de gaps | Proporción de sesiones a 20d con `|apertura − cierre previo| > 1.5σ` | Discontinuidad de precio |

Salida: `d_i(t)`, score de estrés estandarizado, y `D_agg(t)`, su agregado ponderado por capital sobre la cartera.

Horizonte de reacción: rápido. Sus ventanas van de 30 minutos a 20 sesiones.

Es el sistema de **protección**: responde qué evitar y cuánto exponerse.

Misma disciplina de parámetros: cada característica entra estandarizada y con peso igual.

**Aquí es donde el dato de 5 minutos tiene su mejor uso.** La varianza realizada calculada sobre barras intradiarias es un estimador sustancialmente más preciso que el desvío close-to-close, y 5 minutos es la frecuencia canónica en esa literatura porque equilibra ruido de microestructura contra error de estimación. En el término derivativo de U, la información intradiaria compite contra ocho escalas y pesa entre 0.08% y 4%; en el estimador de volatilidad de D, es el insumo principal.

---

## 8. Arbitraje entre los dos sistemas

La palabra "competencia" sugiere que ambos votan sobre la misma decisión y gana el de mayor score. Ese diseño desperdicia la asimetría: convierte dos modelos distintos en la resta `u_i − λ·d_i` y reintroduce un parámetro libre.

**El arbitraje correcto reparte autoridad sobre decisiones distintas y sobre frecuencias distintas.**

```
Elegibilidad:      elegible_i = (u_i > 0) ∧ (d_i < +1.0) ∧ gate_absoluto_i
Selección:         ranking por u_i dentro del bloque
Dimensionamiento:  peso base por volatilidad inversa, atenuado por d_i
Exposición bruta:  escala global modulada por D_agg
Salida forzada:    d_i > +2.0 liquida, con independencia de u_i
```

Las asimetrías que esto implementa son el punto del diseño:

- **D tiene veto sobre entradas; U no tiene veto sobre salidas.** Un instrumento con buen score de tendencia y alto estrés no se compra. Uno con bajo score de tendencia y bajo estrés simplemente no se selecciona.
- **D actúa en frecuencia intradiaria; U solo en la cadencia de decisión.** Ver §9.
- **D modula la exposición agregada; U no.** El tamaño total del riesgo lo decide el sistema defensivo.
- **D puede forzar salida; U no puede forzar entrada.** Es la dirección conservadora y es coherente con el invariante fail-closed de la arquitectura.

Los umbrales +1.0 y +2.0 están en desviaciones estándar transversales y se fijan a priori. Son convenciones de tres sigmas, no parámetros a sintonizar.

### 8.1 Marcador de competencia

Para investigación, registrar en cada decisión qué sistema la determinó:

```json
{
  "timestamp": "...", "symbol": "...",
  "u_score": 0.0, "d_score": 0.0,
  "decision": "enter|hold|skip|forced_exit",
  "deciding_system": "U|D|both|neither",
  "u_rank_in_block": 0,
  "d_veto_triggered": false,
  "intraday_exit_triggered": false
}
```

Permite responder cuántas decisiones ganó cada sistema, qué fracción del rendimiento proviene de la selección de U contra la protección de D, y si `d_i` alguna vez vetó a un instrumento que después subió. Sin este registro, la competencia no es medible.

### 8.2 Relación con el score de régimen existente

`D_agg` y el score de régimen graduado de `UNIVERSE_CONSTRUCTION_SPEC.md` §5.4 miden riesgo agregado por caminos distintos. **No se suman.** Se testean como alternativas y se conserva uno. Mantenerlos ambos es doble conteo del mismo riesgo.

---

## 9. Cadencia de decisión

Con el PDT eliminado, la frecuencia de decisión pasa a ser una elección de diseño en lugar de una restricción. La elección de S8 es deliberadamente asimétrica.

| Decisión | Frecuencia | Sistema con autoridad |
| :--- | :--- | :--- |
| Selección y entradas | Semanal, viernes al cierre, señales a las 15:45 ET | U |
| Rebalanceo de pesos | Semanal, con banda de no-negociación de 3% | U |
| Veto de entrada | En el momento de la decisión semanal | D |
| Reducción de exposición bruta | Diaria, al cierre | D |
| Salida forzada por estrés | **Intradiaria, evaluada cada 30 minutos** | D |

El razonamiento: la información de alta frecuencia tiene valor económico asimétrico. Detectar una oportunidad de compra treinta minutos antes casi no aporta, porque la tendencia que U busca se despliega en semanas. Detectar un deterioro treinta minutos antes sí aporta, porque las caídas se desarrollan en horas.

Dar autoridad intradiaria solo al sistema defensivo concentra la frecuencia donde la información la justifica, y evita el problema de rotar posiciones intradiariamente persiguiendo señales que el horizonte del integral no respalda.

**Variante de investigación admisible:** una rama con decisión de entrada diaria en lugar de semanal, para medir si la cadencia aporta por sí misma. Suma al ledger.

---

## 10. Presupuesto de parámetros

El PBO actual es de 84.45% y cada parámetro libre eleva el umbral crítico SR\*. La configuración de referencia de S8 está construida para no agregar ninguno.

| Elemento | Tratamiento | Grados de libertad |
| :--- | :--- | :---: |
| Kp, Ki, Kd | Fijos en 1/1/1 tras z-score transversal | **0** |
| Pesos de horizonte | Regla paramétrica declarada, sin exponente libre | **0** (variante declarada) |
| Conjunto de horizontes | Los ocho, fijos | **0** |
| Ventana del integral | 45 sesiones, heredada, no se re-optimiza | **0** |
| Clip de anti-windup | ±3 | **0** |
| Filtro derivativo | 3 barras en horizontes ≤ 30 min | **0** |
| Umbrales de arbitraje | +1.0 y +2.0 sigmas | **0** |
| Signo del término P | Binario, ambos publicados | **1** |
| Variante de pesos | A y B, ambas publicadas | **1** |

**Ledger:** 2 signos × 2 variantes de peso = **4 entradas**. Si se agrega la rama de cadencia diaria, 8. Registrar cada variante ejecutada, incluidas las descartadas.

Si el resultado depende de sintonizar Kp, Ki o Kd, la conclusión es que S8 no funciona. Con ganancias fijas o nada.

---

## 11. Validación

**El benchmark es el ensemble de rankings 21/63/126** de `UNIVERSE_CONSTRUCTION_SPEC.md`, no S5 v1.2.0 ni el multiciclo de 16 años. S8 es más complejo y debe superar a la alternativa simple para justificar esa complejidad.

| Paso | Procedimiento |
| :--- | :--- |
| 1 | Diagnóstico de calidad de barras intradiarias (§6.6) antes de cualquier backtest |
| 2 | Validación de alineación temporal: test unitario que inyecta una barra futura y verifica que el motor falle |
| 3 | Universo punto-en-el-tiempo, una vez cerrado T-11 |
| 4 | Cortacircuitos, curva real de tasa libre de riesgo y modelo de costos activos |
| 5 | Spread long-short `rank(1,2) − rank(6,7)` sobre `u_i`, con errores Newey-West y control no solapado |
| 6 | Atribución multifactorial del resultado: SPY, oro, momentum |
| 7 | Descomposición de aporte: rendimiento de A contra B, y de U solo contra U+D |
| 8 | Re-cálculo de DSR y PBO con el ledger actualizado |

### 11.1 Criterio de muerte

S8 se descarta, sin variantes de rescate, si ocurre cualquiera de estas:

- El spread long-short de `u_i` no es significativo al 5% con errores Newey-West.
- S8 no supera al ensemble 21/63/126 por un margen mayor que el error estándar de la diferencia.
- La variante B no supera a la A por un margen medible: en ese caso los horizontes intradiarios no aportan y se eliminan, quedando S8 reducido a un scorer diario.
- El PBO recalculado empeora respecto del actual.
- El resultado depende de sintonizar las ganancias.

El criterio se escribe antes de correr el test y no se revisa después de verlo.

---

## 12. Requisitos de datos

| Dato | Estado | Uso |
| :--- | :--- | :--- |
| OHLCV diario 2010–2026 | Disponible | `EMA_45`, `σ_45`, integral, horizontes de 1d/5d/10d |
| Barras de 5 minutos | **Disponible (usuario)** | Horizontes intradiarios, varianza realizada, correlación de 30 min |
| Calendario de sesiones con cierres anticipados | **A construir** | §6.4 |
| Serie de `BIL` | Disponible | Gate absoluto, tasa libre de riesgo |
| Constituyentes punto-en-el-tiempo | **Falta** | Paso 3 de §11. Ver T-11 |

Agregar al diagnóstico previo: período exacto cubierto por las barras de 5 minutos y su fuente. Si no cubren todo 2010–2026, el backtest de S8 con horizontes intradiarios queda limitado a la ventana disponible, y la comparación contra el benchmark debe hacerse sobre esa misma ventana para ambos.

---

## 13. Configuración de referencia

```yaml
scorer:
  name: S8
  version: reference

  error:
    reference: EMA
    reference_window_days: 45
    volatility_window_days: 45
    price_source: intraday_last_closed_bar

  proportional:
    sign: +1                  # +1 tendencia, -1 reversión. Ejecutar AMBOS
    gain: 1.0

  integral:
    method: ewma              # NUNCA suma acumulada (windup)
    window_days: 45
    clip: [-3.0, 3.0]
    gain: 1.0
    source: daily_close

  derivative:
    horizons_minutes: [3900, 1950, 390, 120, 60, 30, 15, 5]
    weight_rule: proportional_to_h        # variante A (referencia)
    # weight_rule: proportional_to_log_h  # variante B (comparador)
    scale_normalization: sqrt_h
    horizon_unit: minutes                 # consistente en pesos y normalización
    gain: 1.0
    lowpass_filter:
      applies_to_minutes: [30, 15, 5]
      window_bars: 3

  intraday_rules:
    session: regular_only                 # 09:30-16:00 ET
    cross_session_differences: forbidden
    missing_horizon: redistribute_weights_proportionally
    bar_alignment: close_timestamp <= decision_time
    alignment_violation: fail_loudly

  aggregation: zscore_per_block_per_date

defensive_system:
  name: S8_D
  features:
    - vol_expansion: realized_var_5d_from_5min / sigma_45d
    - semideviation_ratio: sigma_neg_20d / sigma_pos_20d
    - drawdown_depth: (close - max_45d) / sigma_45d
    - correlation_convergence: corr_30min_5sessions(asset, SPY)
    - intraday_acceleration: abs(delta_e_30min) / sigma_intraday_20d
    - gap_frequency: pct_sessions_20d(abs(open - prev_close) > 1.5 * sigma)
  aggregation: equal_weight_zscore

arbitration:
  eligibility:    "u_i > 0 AND d_i < 1.0 AND absolute_gate"
  selection:      "rank by u_i within block"
  sizing:         "inverse_vol, attenuated by d_i"
  gross_exposure: "scaled by D_agg"
  forced_exit:    "d_i > 2.0"
  regime_score:   "evaluate D_agg AS ALTERNATIVE, never additive"
  scoreboard:     enabled

cadence:
  entries_and_rebalance: weekly_friday_1545ET
  entry_veto:            at_weekly_decision
  gross_exposure_update: daily_close
  forced_exit_check:     every_30min_intraday

inherited_unchanged:
  - universe_and_block_caps
  - inverse_volatility_sizing
  - portfolio_vol_target
  - circuit_breakers
  - rotation_buffer
  - cost_model
  - paper_trading_gates

validation:
  benchmark: momentum_rank_ensemble_21_63_126
  requires: [bar_quality_diagnostic, alignment_unit_test, T-11_pit_universe,
             circuit_breakers, real_rf, cost_model]
  ledger_increment: 4
  kill_criteria: see_section_11_1
```

---

## 14. Qué no hacer

- **No sintonizar Kp, Ki ni Kd.** Si 1/1/1 no funciona, S8 no funciona.
- **No usar barras sin cerrar.** El desfase de una barra produce mejoras espectaculares y falsas. El test unitario del paso 2 de §11 es obligatorio antes del primer backtest.
- **No calcular diferencias intradiarias que crucen el cierre.** Convierte la derivada corta en un detector de gaps.
- **No rellenar barras faltantes con el cierre previo.** Redistribuir pesos, como dice §6.2.
- **No definir el sistema bajista como el alcista con signo invertido.**
- **No sumar `D_agg` al score de régimen.** Se elige uno.
- **No comparar S8 contra S5 v1.2.0** ni contra el multiciclo de 16 años. El benchmark es el ensemble 21/63/126 bajo condiciones idénticas.
- **No testear S8 junto con ningún otro cambio.** Una variable por experimento.
- **No revisar el criterio de muerte después de ver el resultado.**

---

*Documento de diseño. Los valores de esta especificación son convenciones derivadas de la literatura de control, de la microestructura de mercado y de la arquitectura existente; ninguno fue optimizado sobre datos históricos. Cada variante ejecutada debe registrarse en el ledger de configuraciones para el cálculo de Deflated Sharpe y PBO.*
