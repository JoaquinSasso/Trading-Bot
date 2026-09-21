# ESPECIFICACIÓN — Scorer PID de Doble Sistema (S6)

| Campo | Valor |
| :--- | :--- |
| **Documento** | Especificación de diseño, no implementada |
| **Fecha** | 2026-09-21 |
| **Alcance** | Reemplazo de la función de scoring/selección. No toca universo, caps, control de volatilidad ni cortacircuitos |
| **Restricción dominante** | PBO = 84.45% (`reports/deflated_sharpe_and_pbo.md`). El presupuesto de parámetros libres es el criterio de diseño, no el rendimiento |
| **Estado** | Requiere validación completa. Cuenta como configuración nueva en el ledger (actual: 45) |

> **Regla que gobierna todo el documento.** Cada parámetro libre que se agregue eleva el umbral crítico SR\* y empeora el PBO, que ya descalifica el sistema para capital real. Esta especificación está construida para que el scorer PID tenga **cero parámetros ajustables en su versión de referencia**. Si solo funciona con ganancias sintonizadas, no funciona.

---

## 1. Qué es esto y qué no es

Un PID es un controlador de lazo cerrado: mide el error entre un setpoint y una variable de proceso, y actúa sobre el proceso para llevar ese error a cero. Aplicado a precios de acciones falta el elemento esencial. **La salida del controlador no afecta a la variable de proceso.** Una cuenta de $2.000 comprando `XLK` no mueve a `XLK`. No hay lazo, no hay realimentación, y no hay setpoint objetivo.

Lo que queda al quitar el lazo es una combinación lineal de tres términos: un nivel, una acumulación y una tasa de cambio. Eso es un **scorer multi-horizonte**, no un controlador. La etiqueta PID es una analogía útil para organizar los términos, y nada más.

Esto no invalida la idea. El scoring multi-horizonte con pesos decrecientes por horizonte ya está especificado en `UNIVERSE_CONSTRUCTION_SPEC.md` §5.2 y es una mejora legítima sobre la ventana única de 45 días. Lo que esta especificación aporta sobre aquella es la estructura P/I/D, la asimetría entre sistemas y el tratamiento del windup. Lo que no aporta es una clase nueva de información.

**Consecuencia práctica:** S6 no es una arquitectura nueva. Es un reemplazo de la función de scoring dentro de la arquitectura existente. Universo, caps de bloque, ponderación por volatilidad inversa, objetivo de volatilidad, cortacircuitos y gates se mantienen sin modificación.

---

## 2. El problema del signo

En un controlador estándar, `u(t) = Kp·e + Ki·∫e + Kd·de/dt`, y el actuador empuja la variable de proceso **hacia** el setpoint. Si se define el error como la desviación del precio respecto de su media móvil, el controlador vende cuando el precio está por encima y compra cuando está por debajo.

**Un PID aplicado literalmente a precios es un sistema de reversión a la media.** No es trend-following: es S3, la estrategia que el proyecto abandonó por insuficiente.

Para que el scorer sea de tendencia hay que invertir el signo respecto de la convención de control: `u = +K·e` en lugar de `−K·e`. Eso lo saca formalmente del marco PID, pero conserva la estructura de términos, que es lo útil.

**El signo es el único parámetro binario que vale la pena testear**, porque distingue dos hipótesis económicas opuestas y no es una sintonización. Se testean ambos, se reportan ambos, y el ledger de configuraciones suma dos, no una.

---

## 3. Presupuesto de parámetros

Esta es la sección que decide si S6 vive o muere.

La propuesta original implica: tres ganancias (Kp, Ki, Kd) más ocho pesos de horizonte, unos once grados de libertad. Sobre un sistema cuyo PBO ya es de 84.45%, agregar once parámetros ajustables garantiza que la próxima medición empeore y que cualquier resultado favorable sea indistinguible de selección sobre la muestra.

**Diseño de referencia — cero parámetros ajustables:**

| Elemento | Tratamiento | Grados de libertad |
| :--- | :--- | :---: |
| Kp, Ki, Kd | Fijos en 1/1/1 tras estandarizar cada término en la sección transversal | **0** |
| Pesos de horizonte | Paramétricos: `w_h ∝ h`, proporcional a la longitud del horizonte | **0** |
| Ventana del integral | 45 sesiones, heredada del diseño actual, no se re-optimiza | **0** |
| Clip de anti-windup | Fijo en ±3σ | **0** |
| Signo del término P | Binario, se reportan ambos | **1** |

La estandarización transversal de cada término antes de sumarlos es lo que hace innecesarias las ganancias: si P, I y D se convierten a z-scores dentro de cada bloque y fecha, ya están en la misma escala y ponderarlos por igual es la elección neutral. Cualquier otra ponderación es una hipótesis que hay que justificar antes de testear, no después de ver el resultado.

`w_h ∝ h` implementa exactamente el pedido de "mientras más largo el intervalo, más peso" sin introducir un exponente libre. Si más adelante se quiere explorar `w_h ∝ h^γ`, el grid admisible es γ ∈ {0, 0.5, 1} con los tres resultados publicados, nunca el mejor de los tres.

---

## 4. Definición del error

Para cada instrumento `i` y sesión `t`:

```
e_i(t) = [ ln P_i(t) − ln EMA_45,i(t) ] / σ_i(t)
```

donde `σ_i(t)` es la desviación estándar de retornos logarítmicos diarios de las últimas 45 sesiones.

La normalización por volatilidad es obligatoria, no opcional. Sin ella, `SLV` y `XLP` producen errores de magnitudes incomparables y el ranking transversal se convierte en un ranking de volatilidad. Es el mismo principio que justifica la ponderación por volatilidad inversa en el sizing.

El resultado es adimensional y comparable entre instrumentos y entre épocas.

---

## 5. Los tres términos

### 5.1 Proporcional

```
P_i(t) = e_i(t)
```

Mide cuán extendido está el precio respecto de su referencia, en unidades de volatilidad. Es el término que más directamente carga sobre la hipótesis de tendencia contra reversión.

### 5.2 Integral, con anti-windup

```
I_i(t) = clip( EWMA_45[ e_i ](t), −3, +3 )
```

**Se usa EWMA, no suma acumulada.** Una suma acumulada de un error persistentemente positivo crece sin cota: es el windup clásico de los controladores reales, y en trading tiene una traducción directa y costosa. El controlador queda saturado en posición larga mucho después de que la tendencia terminó, porque la integral acumulada tarda en drenar. Ese es el mecanismo exacto por el cual los sistemas de tendencia compran extensión en los techos.

El clip a ±3 es la protección adicional: un instrumento que lleva 45 sesiones tres desviaciones por encima de su media no es "más comprable" por estar cuatro desviaciones arriba. Acota el aporte del término y elimina la sensibilidad a outliers.

### 5.3 Derivativo multi-horizonte

```
D_i(t) = Σ_h  w_h · [ e_i(t) − e_i(t−h) ] / √h
```

con `w_h ∝ h`, normalizado a `Σ w_h = 1`.

**La división por √h no es cosmética.** Bajo caminata aleatoria, la dispersión de una diferencia a `h` pasos escala con `√h`. Sin normalizar, los horizontes largos producen diferencias sistemáticamente mayores y los pesos `w_h` quedan confundidos con un efecto de escala: se creería estar ponderando cuando en realidad se está midiendo la raíz del horizonte. Con la normalización, los `w_h` significan lo que se pretende que signifiquen.

### 5.4 Agregación

```
z_P = zscore_transversal( P )     dentro del bloque y la fecha
z_I = zscore_transversal( I )
z_D = zscore_transversal( D )

u_i(t) = s · z_P + z_I + z_D          con s ∈ {+1, −1}
```

---

## 6. Horizontes: cuáles entran y cuáles no

### 6.1 Admitidos en la versión de referencia

**10 sesiones, 5 sesiones, 1 sesión**, más el integral a 45 sesiones.

Con `w_h ∝ h`: w(10) = 0.625, w(5) = 0.3125, w(1) = 0.0625.

### 6.2 Excluidos: los horizontes intradiarios

Los intervalos de 2h, 1h, 30m, 15m y 5m quedan fuera, y no por preferencia metodológica sino porque no existe un camino de ejecución que los pueda usar.

**Bloqueo regulatorio.** Con $2.000 de patrimonio, una cuenta de margen queda sujeta a la regla de Pattern Day Trader: más de tres operaciones intradiarias en cinco días hábiles restringe la cuenta, y el mínimo para operar sin esa restricción es de $25.000. Una cuenta cash evita el PDT pero queda sujeta a liquidación T+1, que inmoviliza el capital. En ambos casos, una señal generada a las 11:30 no puede accionarse el mismo día de forma sostenible.

**Bloqueo de frecuencia de decisión.** El candidato primario rebalancea semanalmente. Una derivada de 5 minutos sobre una decisión semanal muestrea el proceso a unas dos mil veces la frecuencia de acción. La información se promedia hasta desaparecer antes de poder usarse.

**Bloqueo de calidad de datos.** El acceso gratuito de Alpaca entrega datos de IEX, que representa una fracción pequeña del volumen consolidado. Las barras de 5 y 15 minutos construidas sobre ese flujo tienen huecos y precios no representativos. Para horizontes diarios el efecto es despreciable; para intradiarios es dominante.

**Bloqueo estadístico.** A horizontes de minutos, la varianza de los retornos está dominada por microestructura —rebote entre puntas, discretización del tick— y no por información. Es además el caso de libro de texto de amplificación de ruido en el término derivativo: la razón por la cual todo controlador real filtra la derivada con un pasabajos antes de usarla. La propuesta original pide exactamente lo que la literatura de control recomienda evitar.

**Bloqueo de coherencia interna.** El pedido es que los horizontes largos pesen más. Con `w_h ∝ h`, el peso de la barra de 5 minutos respecto de la de 10 días es del orden de 1 a 780. Aunque fuera gratis computarla, no movería el score.

### 6.3 Extensión condicional

Los horizontes intradiarios se reconsideran únicamente si se cumplen las tres condiciones a la vez: patrimonio por encima de $25.000, suscripción a datos consolidados (SIP), y frecuencia de decisión diaria o superior. Mientras alguna falte, no se implementan ni se investigan: son trabajo que no puede convertirse en decisión.

---

## 7. Los dos sistemas

El pedido de dos sistemas en competencia es correcto en intuición y peligroso en la implementación obvia.

**Lo que no hay que hacer:** definir el sistema bajista como el alcista con el signo invertido. Dos modelos con las mismas entradas y la misma forma funcional con signo opuesto están perfectamente anticorrelacionados. Su "competencia" se reduce a comparar un número contra su negativo, que es un umbral sobre una sola señal. No se gana información y se duplica el código.

**Lo que sí hay que hacer:** construirlos asimétricos por diseño, porque el mercado lo es. Las caídas son más rápidas que las subidas, la volatilidad se expande más de lo que se contrae, y las correlaciones convergen a uno en el estrés y se dispersan en la calma. Un modelo que describe bien la subida no describe bien la bajada invertida.

### 7.1 Sistema U — Persistencia de tendencia

Entradas: el scorer PID de §5 sobre el error precio-EMA.

Salida: `u_i(t)`, score transversal dentro del bloque.

Es el sistema de **selección**. Responde a la pregunta de qué comprar.

### 7.2 Sistema D — Estrés y deterioro

Entradas deliberadamente distintas, ninguna de las cuales es el negativo de una entrada de U:

| Característica | Definición | Qué captura |
| :--- | :--- | :--- |
| Expansión de volatilidad | `σ_5d / σ_45d` | Régimen de volatilidad en transición |
| Asimetría de semidesvío | `σ⁻_20d / σ⁺_20d` | Cola izquierda engordando |
| Profundidad desde máximo | `(P_t − max P_45d) / σ_45d` | Deterioro estructural, no ruido |
| Convergencia de correlación | `corr_20d(r_i, r_SPY)` | Pérdida de diversificación, típica del estrés |
| Frecuencia de gaps | Proporción de sesiones a 20d con `|apertura − cierre previo| > 1.5σ` | Discontinuidad de precio |

Salida: `d_i(t)`, score de estrés estandarizado, y `D_agg(t)`, su agregado ponderado por capital sobre la cartera.

Es el sistema de **protección**. Responde a la pregunta de qué evitar y cuánto exponerse.

Aplica la misma disciplina de parámetros: cada característica entra estandarizada y con peso igual. Sin ponderaciones sintonizadas.

---

## 8. Capa de arbitraje

La palabra "competencia" sugiere que ambos sistemas votan sobre la misma decisión y gana el de mayor score. Ese diseño desperdicia la asimetría: convierte dos modelos distintos en una resta, `u_i − λ·d_i`, y reintroduce un parámetro libre `λ`.

**El arbitraje correcto reparte autoridad sobre decisiones distintas.**

```
Elegibilidad:     elegible_i = (u_i > 0) ∧ (d_i < +1.0) ∧ gate_absoluto_i
Selección:        ranking por u_i dentro del bloque
Dimensionamiento: peso base por volatilidad inversa, atenuado por d_i
Exposición bruta: escala global modulada por D_agg
Salida forzada:   d_i > +2.0 fuerza liquidación, independientemente de u_i
```

Las asimetrías que esto implementa, y que son el punto del diseño:

- **D tiene veto sobre las entradas; U no tiene veto sobre las salidas.** Un instrumento con buen score de tendencia y alto estrés no se compra. Un instrumento con bajo score de tendencia y bajo estrés simplemente no se selecciona, sin drama.
- **D actúa más rápido.** Sus ventanas son de 5 a 20 sesiones contra las 45 del integral de U. Eso refleja que los deterioros se desarrollan más rápido que las tendencias.
- **D modula exposición agregada; U no.** El tamaño total del riesgo lo decide el sistema defensivo.
- **D puede forzar salida; U no puede forzar entrada.** Es la dirección conservadora y es coherente con el invariante fail-closed que ya gobierna la arquitectura.

Los umbrales +1.0 y +2.0 están en unidades de desviación estándar transversal y se fijan a priori, no se sintonizan. Son el equivalente de una regla de tres sigmas: convenciones, no parámetros.

### 8.1 Relación con el score de régimen existente

`D_agg` y el score de régimen graduado de `UNIVERSE_CONSTRUCTION_SPEC.md` §5.4 miden cosas parecidas por caminos distintos: uno desde la microestructura de los instrumentos, el otro desde medias móviles del índice y amplitud sectorial.

**No se suman.** Se testean como alternativas y se conserva uno. Mantener los dos es doble conteo del mismo riesgo y agrega parámetros sin agregar información.

---

## 9. Integración

S6 reemplaza **únicamente** la función de scoring y selección. Todo lo demás permanece idéntico y no se re-optimiza:

| Componente | Estado |
| :--- | :--- |
| Universo (A o B) | Sin cambios |
| Caps de bloque y por instrumento | Sin cambios |
| Ponderación por volatilidad inversa | Sin cambios |
| Objetivo de volatilidad de cartera | Sin cambios |
| Cortacircuitos | Sin cambios |
| Modelo de costos y granularidad | Sin cambios |
| Gates 1–3 de paper trading | Sin cambios |
| Buffer asimétrico de rotación | Sin cambios |
| Score de régimen | En evaluación contra `D_agg` (§8.1) |

Tocar más de una cosa a la vez impide atribuir cualquier diferencia de resultado. Si S6 se testea junto con un cambio de universo o de caps, el experimento no responde nada.

---

## 10. Plan de validación

**Comparación cabeza a cabeza contra el ensemble de rankings 21/63/126**, que es el competidor honesto. El benchmark no es S5 v1.2.0 ni el multiciclo de 16 años: es la alternativa simple que ya está especificada y que S6 debe superar para justificar su complejidad.

| Paso | Procedimiento |
| :--- | :--- |
| 1 | Universo punto-en-el-tiempo (requiere T-11 cerrado). Sin esto, la comparación arrastra F-02 |
| 2 | Cortacircuitos, curva real de tasa libre de riesgo y costos activos en ambas ramas |
| 3 | Test de spread long-short `rank(1,2) − rank(6,7)` sobre el score de S6, con errores Newey-West y control no solapado (mismo protocolo que T-10) |
| 4 | Atribución multifactorial del resultado (SPY, oro, momentum), mismo protocolo que T-12 |
| 5 | Re-cálculo de DSR y PBO con el ledger actualizado |

**Contabilidad del ledger:** el ledger está en 45 configuraciones. S6 en su versión de referencia agrega **2** (signo positivo y negativo). Si se explora el grid de γ, agrega 6. Si además se sintonizan Kp/Ki/Kd, el conteo se dispara y el umbral SR\* con él. Registrar cada variante ejecutada, incluidas las descartadas.

### 10.1 Criterio de muerte

S6 se descarta, sin reintentos ni variantes de rescate, si ocurre cualquiera de estas:

- El spread long-short del score S6 no es significativo al 5% con errores Newey-West.
- S6 no supera al ensemble 21/63/126 por un margen mayor que el error estándar de la diferencia.
- El PBO recalculado con S6 incluido empeora respecto del actual.
- El resultado depende de sintonizar Kp/Ki/Kd: si con ganancias 1/1/1 no funciona y con ganancias ajustadas sí, la conclusión es que no funciona.

El criterio de muerte se escribe antes de correr el test y no se revisa después de verlo. Es la única defensa práctica contra el mecanismo que llevó el PBO a 84.45%.

---

## 11. Requisitos de datos

| Dato | Estado | Nota |
| :--- | :--- | :--- |
| OHLCV diario, universo completo | Disponible | Ya indexado 2010–2026 |
| Retornos de `SPY` para correlación rodante | Disponible | |
| Serie de `BIL` | Disponible | `data/risk_free_rate_bil_2010_2026.csv` |
| Constituyentes punto-en-el-tiempo | **Falta** | Bloquea el paso 1 de §10. Ver T-11 |
| Barras intradiarias | No requerido | Ver §6.2 |

S6 no agrega ninguna dependencia de datos nueva. Es deliberado: una propuesta que requiriese datos intradiarios de pago sería indefendible antes de haber demostrado una ventaja con los datos que ya existen.

---

## 12. Configuración de referencia

```yaml
scorer:
  name: S6_PID
  version: reference          # cero parámetros ajustables

  error:
    reference: EMA
    reference_window: 45
    normalization: realized_vol_45d

  proportional:
    sign: +1                  # +1 tendencia, -1 reversión. Testear AMBOS
    gain: 1.0                 # fijo tras z-score transversal

  integral:
    method: ewma              # NUNCA suma acumulada (windup)
    window: 45
    clip: [-3.0, 3.0]
    gain: 1.0

  derivative:
    horizons_days: [10, 5, 1]
    weight_rule: proportional_to_horizon    # w_h ∝ h
    scale_normalization: sqrt_h             # obligatorio
    gain: 1.0
    intraday_horizons: []                   # bloqueado: ver §6.2

  aggregation: zscore_per_block_per_date

defensive_system:
  name: S6_D
  features:
    - vol_expansion: sigma_5d / sigma_45d
    - semideviation_ratio: sigma_neg_20d / sigma_pos_20d
    - drawdown_depth: (close - max_45d) / sigma_45d
    - correlation_convergence: corr_20d(asset, SPY)
    - gap_frequency: pct_sessions_20d(abs(open - prev_close) > 1.5 * sigma)
  aggregation: equal_weight_zscore

arbitration:
  eligibility:    "u_i > 0 AND d_i < 1.0 AND absolute_gate"
  selection:      "rank by u_i within block"
  sizing:         "inverse_vol, attenuated by d_i"
  gross_exposure: "scaled by D_agg"
  forced_exit:    "d_i > 2.0"
  regime_score:   "evaluate D_agg AS ALTERNATIVE, never additive"

validation:
  benchmark: momentum_rank_ensemble_21_63_126
  requires: [T-11_pit_universe, circuit_breakers, real_rf, cost_model]
  ledger_increment: 2
  kill_criteria: see_section_10_1
```

---

## 13. Qué no hacer

- **No sintonizar Kp, Ki ni Kd.** Si la versión 1/1/1 no funciona, S6 no funciona. Sintonizar convierte un test en una búsqueda y el PBO ya dice qué pasa cuando se busca.
- **No implementar los horizontes intradiarios** antes de que se cumplan las tres condiciones de §6.3. No hay camino de ejecución para esa información.
- **No definir el sistema bajista como el alcista con signo invertido.** Sin entradas distintas no hay competencia, hay un umbral.
- **No sumar `D_agg` al score de régimen.** Se elige uno.
- **No comparar S6 contra S5 v1.2.0** ni contra el multiciclo de 16 años. El benchmark es el ensemble 21/63/126 bajo condiciones idénticas.
- **No testear S6 junto con ningún otro cambio.** Una variable por experimento.
- **No revisar el criterio de muerte después de ver el resultado.**

---

*Documento de diseño. Los valores de esta especificación son convenciones derivadas de la literatura de control y de restricciones operativas de la cuenta; ninguno fue optimizado sobre datos históricos. Cualquier variante debe registrarse en el ledger de configuraciones para el cálculo de Deflated Sharpe y PBO.*
