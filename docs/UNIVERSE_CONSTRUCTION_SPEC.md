# ESPECIFICACIÓN DE CONSTRUCCIÓN DE UNIVERSO — Trading-Bot v2.0 (propuesta)

| Campo | Valor |
| :--- | :--- |
| **Documento** | Especificación de diseño, no implementada |
| **Fecha** | 2026-09-20 |
| **Reemplaza** | Universo fijo de 14 activos (v1.2.0) |
| **Motivación** | Hallazgos F-02 (sesgo de selección) y F-07 (riesgo de gap) de `EXTERNAL_QUANT_AUDIT_v1.2.0.md` |
| **Estado** | Requiere validación completa antes de adoptarse |

> **Advertencia previa.** Esta especificación va a producir un backtest peor que el de v1.2.0. Ese es el resultado esperado y correcto: el rendimiento de v1.2.0 provenía de tener `GLD` y `SLV` garantizados en el universo durante el mejor año de los metales en cuarenta años. Si al implementar esto el impulso es ajustar los caps hasta recuperar aquellos números, se está reintroduciendo exactamente el sesgo que el rediseño busca eliminar.

---

## 1. Principio de diseño

Tres reglas gobiernan todo lo que sigue.

**La liquidez es un criterio de elegibilidad, nunca de selección.** Un instrumento entra o no entra al universo según umbrales absolutos fijos. Nunca se rankea por liquidez, porque el volumen es un proxy de atención y la atención se concentra en activos que están atravesando un shock. Un universo ordenado por volumen sobre-muestrea sistemáticamente instrumentos en medio de un evento, que es la peor materia prima posible para una estrategia de momentum a 45 días.

**La composición del universo se define por regla, no por lista.** Toda lista escrita a mano es una hipótesis contaminada por lo que ya ocurrió. La única excepción admisible es un conjunto de instrumentos definido por una taxonomía externa y estable —los once sectores GICS, por ejemplo— porque esa taxonomía no fue elegida mirando los retornos.

**El riesgo se presupuesta por bloque, no por posición.** La arquitectura separa el universo en compartimentos con lógica propia y techo propio. Ningún compartimento puede monopolizar el capital, y el efectivo es el residuo natural cuando ningún compartimento califica.

---

## 2. Por qué ETFs y no acciones individuales

Esta es la decisión más importante del documento y merece justificación explícita.

Con una cuenta de $2.000, las acciones individuales aportan casi todo el riesgo de cola del sistema y muy poco retorno esperado incremental. Un nombre individual puede abrir -25% por un resultado de fase clínica, una investigación contable, una pérdida de contrato o una demanda. Ningún stop loss protege contra eso: en un gap la orden se ejecuta al precio ya deteriorado. El `PositionGuardian` garantiza que exista una orden, no un precio.

Un ETF sectorial diversificado sobre 40-70 compañías no abre -25%. Puede caer fuerte, pero lo hace en movimientos que un stop diario alcanza a gestionar. El riesgo idiosincrático —fraude, litigio, evento binario— queda diluido a niveles irrelevantes.

La contrapartida es que se renuncia a la dispersión transversal entre nombres individuales, que es donde vive una parte del premio de momentum. Es una renuncia real. Pero a este tamaño de cuenta, el premio adicional por operar nombres sueltos es varios órdenes de magnitud menor que el costo esperado del primer gap del 20% en una posición concentrada.

Hay además una razón de validación que resulta decisiva: **un universo de ETFs no tiene sesgo de supervivencia y no requiere datos punto-en-el-tiempo de pago.** Los constituyentes históricos del S&P 500 exigen una suscripción a Norgate, Sharadar o CRSP para backtestear sin sesgo. Los ETFs cotizan continuamente y su historia es pública y gratuita. Con presupuesto cero, el Universo A es el único que se puede validar honestamente.

---

## 3. Universo A — Roster operativo (18 instrumentos)

Es el universo recomendado para la cuenta actual y hasta aproximadamente $25.000 de capital.

### 3.1 Bloque Acciones EE.UU. — Sectores GICS (11)

| Ticker | Sector | Notas |
| :--- | :--- | :--- |
| `XLK` | Tecnología | Precio por acción elevado; verificar granularidad |
| `XLC` | Servicios de comunicación | Inicio: junio 2018 |
| `XLY` | Consumo discrecional | |
| `XLP` | Consumo básico | Defensivo |
| `XLV` | Salud | Defensivo |
| `XLF` | Financiero | |
| `XLI` | Industrial | |
| `XLE` | Energía | Descorrelación en shocks inflacionarios |
| `XLU` | Utilities | Defensivo, sensible a tasas |
| `XLB` | Materiales | |
| `XLRE` | Inmobiliario | Inicio: octubre 2015 |

La taxonomía GICS es el argumento anti-sesgo: los once sectores existen por clasificación industrial, no porque alguien haya mirado sus retornos. El bloque cubre el 100% de la capitalización del S&P 500, de modo que el universo es completo por construcción.

`SPY` y `QQQ` quedan **fuera del ranking**. `SPY` es el benchmark y la señal de régimen; incluirlo como candidato genera doble conteo, ya que es una combinación lineal de los once sectores. `QQQ` es aproximadamente `XLK` más partes de `XLC` y `XLY`.

### 3.2 Bloque Internacional (2)

| Ticker | Exposición |
| :--- | :--- |
| `IEFA` | Desarrollados ex-EE.UU. |
| `IEMG` | Emergentes |

Dos instrumentos bastan. Agregar `EWJ`, `FEZ` o `EWZ` introduce solapamiento con `IEFA`/`IEMG` y multiplica las decisiones sin agregar clases de riesgo nuevas. La diversificación geográfica es real —el ciclo de beneficios europeo y el emergente no coinciden con el estadounidense— pero se captura con dos posiciones.

### 3.3 Bloque Metales Físicos (2)

| Ticker | Metal | Ratio de gastos |
| :--- | :--- | :--- |
| `GLDM` | Oro físico | 0.10% |
| `SLV` | Plata física | 0.50% |

**`GLDM` sustituye a `GLD` como instrumento principal, no como proxy.** Tiene un ratio de gastos de 0.10% frente al 0.40% de `GLD`, y un precio por acción sustancialmente menor, lo que reduce el problema de granularidad en cuentas chicas. Para una cuenta de $2.000 no hay ningún argumento a favor de `GLD`.

La plata requiere tratamiento diferenciado. Su volatilidad histórica es del orden de dos a tres veces la del oro, y su drawdown máximo a cinco años supera el 38% frente al 21% del oro. La ponderación por volatilidad inversa ya la penaliza automáticamente, pero se agrega además un cap duro explícito (ver §5).

### 3.4 Bloque Renta Fija y Defensivo (3)

| Ticker | Exposición | Rol |
| :--- | :--- | :--- |
| `IEF` | Treasuries 7-10 años | Duración intermedia |
| `TIP` | Treasuries indexados a inflación | Tasa real |
| `BIL` / `SGOV` | Letras del Tesoro 0-3 meses | Efectivo remunerado |

`IEF` en lugar de `TLT` de forma deliberada: `TLT` tiene una volatilidad comparable a la de muchas acciones y en 2022 cayó más del 30%. La duración intermedia aporta el beneficio diversificador sin importar riesgo de renta variable disfrazado.

`TIP` cubre el escenario específico que rompió la correlación acciones-bonos en 2022. No es una cobertura perfecta, pero es la única exposición directa a tasa real disponible en formato ETF líquido.

**Para el instrumento de efectivo, usar `BIL` en backtest y `SGOV` en producción.** `SGOV` no comenzó a cotizar hasta mayo de 2020; `BIL` existe desde 2007. Toda simulación anterior a 2020 que use `SGOV` está fabricando datos. Este es el mismo error que el hallazgo F-04.

---

## 4. Arquitectura de bloques y presupuesto de capital

| Bloque | Instrumentos | Cap de capital | Posiciones a seleccionar | Cap por instrumento |
| :--- | :---: | :---: | :---: | :---: |
| Sectores EE.UU. | 11 | **55%** | Top 4 | 20% |
| Internacional | 2 | **15%** | Top 1 | 15% |
| Metales | 2 | **20%** | Top 2 | `GLDM` 15% / `SLV` 8% |
| Renta fija | 2 | **20%** | Top 1 | 20% |
| Efectivo | 1 | **100%** | — | sin cap |

Los caps suman 110%, lo cual es intencional: **son techos, no objetivos.** La exposición bruta real la determina el escalado por volatilidad de §6, que casi nunca permitirá que todos los bloques alcancen su techo simultáneamente. Diseñar los caps para que sumen exactamente 100% obligaría a llenar el portafolio aunque las señales no lo justifiquen, que es precisamente el error que cometía la asignación 50/50 de v1.2.0 al pretender "erradicar el cash drag".

**Cada bloque tiene su propio filtro de momentum absoluto y es independiente del régimen de renta variable.** Esta es la diferencia estructural más importante frente a v1.2.0. En el diseño anterior, régimen BEAR equivalía a 100% efectivo, lo que significa que en 2022 el sistema estuvo fuera del oro durante todo el año, y en 2020 se perdió el rally de metales que acompañó al crash. Bajo esta arquitectura, el bloque de metales y el de renta fija pueden estar invertidos mientras el bloque de acciones está en efectivo. Eso es diversificación real, no rotación binaria.

---

## 5. Regla de selección

### 5.1 Filtro de elegibilidad (aplicado a cada instrumento, cada rebalanceo)

Un instrumento es candidato si cumple **todas** las condiciones:

- Volumen en dólares promedio de 60 días mayor a **$20 millones** (umbral absoluto, no ranking)
- Precio de cierre mayor a **$10**
- Al menos **252 sesiones** de historia cotizada
- Spread promedio menor a **10 puntos básicos**
- Sin apalancamiento, sin exposición inversa, sin estructura de futuros sintéticos
- Operable en Alpaca sin restricciones

Los cinco primeros se calculan estrictamente con datos hasta `t-1`. El umbral de volumen es fijo por diseño: si el mercado entero se seca, el universo se achica y el sistema pasa a efectivo, que es el comportamiento correcto.

### 5.2 Momentum multi-horizonte

Para cada candidato se calcula el retorno en tres ventanas —**21, 63 y 126 sesiones**— excluyendo las **últimas 5 sesiones** de cada cálculo.

La exclusión de la última semana es la convención estándar de la literatura desde Jegadeesh & Titman: el momentum de muy corto plazo presenta reversión, y medirlo hasta el día del ranking introduce sistemáticamente el componente que está a punto de revertir.

Cada ventana produce un ranking dentro del bloque. **Se promedian los rankings, no los retornos.** Promediar retornos deja que la ventana más volátil domine el resultado; promediar posiciones ordinales trata las tres escalas temporales con el mismo peso, que es la intención.

### 5.3 Gate de momentum absoluto

Un instrumento solo puede ser seleccionado si además cumple:

- Retorno a 126 sesiones **superior al de `BIL`** en el mismo período
- Precio de cierre **por encima de su EMA(50)**

El primer filtro es el momentum absoluto de Antonacci: no basta con ser el mejor del bloque, hay que superar al activo libre de riesgo. El segundo confirma tendencia. Si ningún instrumento de un bloque pasa el gate, ese bloque queda en efectivo, sin excepción ni sustituto.

### 5.4 Régimen graduado

El régimen binario de v1.2.0 produce whipsaw cuando el SPY oscila alrededor de la EMA50. Se reemplaza por un score continuo entre 0 y 1, calculado como el promedio de tres condiciones booleanas:

1. `SPY` > `SMA(200)`
2. `SPY` > `EMA(50)`
3. Amplitud: más del 50% de los once sectores por encima de su propia `SMA(200)`

El score multiplica el cap del bloque de acciones EE.UU. Con score 1.0 el cap es 55%; con score 0.33 el cap efectivo es 18%. El bloque internacional usa el mismo score calculado sobre `IEFA`. Los bloques de metales y renta fija **no** se modulan por régimen de renta variable.

La tercera condición —amplitud— es la que aporta valor nuevo. Un índice sostenido por cinco compañías mientras el resto de los sectores se deteriora es un régimen alcista frágil, y el score lo detecta antes que cualquier media móvil sobre el índice.

---

## 6. Ponderación y control de volatilidad

El proceso tiene cuatro pasos en orden estricto.

**Paso 1 — Ponderación por volatilidad inversa dentro del bloque.** Para los instrumentos seleccionados de cada bloque, el peso bruto es proporcional a `1/σᵢ`, donde `σᵢ` es el desvío estándar de retornos diarios de las últimas 60 sesiones, anualizado. Esto equipara la contribución de riesgo de `SLV` y de `XLP`, que es imposible con pesos iguales.

**Paso 2 — Aplicación de caps.** Se truncan los pesos que exceden el cap por instrumento y el cap por bloque. El exceso no se redistribuye: va a efectivo.

**Paso 3 — Escalado a volatilidad objetivo.** Se calcula la volatilidad esperada del portafolio candidato usando la matriz de covarianzas de 90 sesiones con contracción hacia la diagonal (Ledoit-Wolf o, en su defecto, contracción lineal simple con λ = 0.3, porque una covarianza muestral de 18 activos sobre 90 observaciones es numéricamente inestable).

El factor de escala es:

```
k = min(1.0, σ_objetivo / σ_portafolio)
```

con **σ_objetivo = 12% anualizado**. El factor nunca supera 1.0: el sistema no apalanca bajo ninguna circunstancia. El capital no asignado queda en `SGOV`.

**Paso 4 — Redondeo y piso de posición.** Se convierten los pesos a acciones enteras. Toda posición resultante menor a **$150** se descarta y su capital va a efectivo, porque una posición de $60 no mueve la aguja y sí consume slippage y atención operativa.

El control de volatilidad es el mecanismo individual de mayor impacto de toda la propuesta. Un objetivo del 12% frente al ~27% observado en v1.2.0 implica que, en igualdad de condiciones, los drawdowns de 2020 y 2021 se habrían reducido aproximadamente a la mitad. También implica renunciar a la cola derecha: no habrá años de +80%. Esa es la transacción y hay que aceptarla explícitamente.

---

## 7. Rebalanceo y control de rotación

**Cadencia semanal**, ejecutada al cierre del viernes con señales calculadas a las 15:45 ET. El rebalanceo diario multiplica la rotación sin mejorar las señales —un momentum a 126 días no cambia de forma significativa entre lunes y martes— y en cuenta cash con liquidación T+1 aumenta el riesgo de good-faith violations.

**Regla de buffer asimétrico.** Un instrumento necesita estar en el **Top 4** de su bloque para entrar, pero solo necesita mantenerse en el **Top 7** para permanecer. Esta banda de histéresis reduce la rotación típicamente entre un 30% y un 45% sin degradar el retorno, porque elimina las salidas y reentradas causadas por oscilaciones de una o dos posiciones en el ranking.

**Banda de no-negociación en pesos.** Si el peso objetivo de una posición existente difiere del actual en menos de 3 puntos porcentuales, no se opera. Con costos de transacción y spread, rebalancear por diferencias menores destruye valor.

**Salida.** Se mantiene el trailing sobre `EMA(25)` evaluado al cierre, pero el tope duro de tenencia sube de **30 a 90 sesiones**, para que sea coherente con la ventana de señal más larga (126 días). El hallazgo F-09 señalaba que liquidar a los 30 días con señales de 45 amputa la expresión de la propia señal.

---

## 8. Exclusiones explícitas

| Excluido | Ejemplos | Fundamento |
| :--- | :--- | :--- |
| ETFs apalancados | `TQQQ`, `SOXL`, `UPRO` | Arrastre de volatilidad de orden σ²/2 por unidad de tiempo; el rebalanceo diario destruye capital en mercados laterales |
| ETFs inversos | `SQQQ`, `SH`, `SDS` | Mismo arrastre, y en régimen bajista lo correcto es reducir exposición bruta, no comprar exposición negativa |
| Productos de volatilidad | `UVXY`, `VXX` | Contango estructural permanente; pérdida esperada negativa por diseño |
| Commodities de futuros sintéticos | `USO`, `UNG` | Contango y roll decay; `USO` perdió más del 90% en una década con petróleo plano |
| Crédito corporativo | `HYG`, `LQD` | Correlación con renta variable que se dispara justo en los momentos de estrés en que se los necesitaría |
| Criptoactivos | `IBIT` | Volatilidad del 60-80%; el escalado por volatilidad inversa le asignaría un peso marginal de todos modos, a cambio de un riesgo de cola no compensado |
| Acciones individuales | — | Riesgo de gap por evento binario; ver §2 |

**Nota sobre commodities de cesta amplia.** `PDBC` y `DBC` son funcionalmente distintos de `USO`: operan una cesta diversificada con optimización de vencimientos que mitiga sustancialmente el roll negativo. La prohibición actual del repo es correcta para commodities individuales de front-month, pero no se traslada mecánicamente a cestas optimizadas. Quedan fuera de esta especificación por prudencia, y su incorporación requeriría un estudio dedicado que cuantifique el roll yield histórico realizado. No agregarlas sin ese estudio.

---

## 9. Universo B — Escalado a partir de ~$25.000

Cuando el capital lo permita y la cuestión de stops sobre fraccionarios esté resuelta, el bloque de sectores puede reemplazarse por nombres individuales.

| Parámetro | Valor |
| :--- | :--- |
| Universo base | Constituyentes del S&P 500 **punto-en-el-tiempo** |
| Fuente de datos | Norgate, Sharadar o CRSP — obligatoriamente con delistings |
| Posiciones | Top 12 a 15 |
| Cap por nombre | 6% del equity |
| Cap por sector GICS | 25% del equity |
| Resto de bloques | Sin cambios respecto del Universo A |

**El requisito de datos punto-en-el-tiempo no es negociable.** Construir el universo pidiendo la lista actual del S&P 500 y proyectándola hacia atrás reproduce exactamente el hallazgo F-02, con la agravante de parecer metodológicamente riguroso. La lista de 2020 tiene que incluir a las compañías que fueron excluidas después.

El cap del 6% por nombre no es conservador, es aritmético: con 15 posiciones y control de volatilidad, ningún nombre debería superar ese nivel de forma natural. El cap solo existe para el caso patológico en que la volatilidad inversa concentre por un error de estimación.

---

## 10. Restricciones de datos y ventana de validación

Las fechas de inicio de cotización limitan el backtest del Universo A:

| Instrumento | Inicio de cotización |
| :--- | :--- |
| `XLRE` | Octubre 2015 |
| `XLC` | Junio 2018 |
| `GLDM` | Junio 2018 |
| `IEFA` / `IEMG` | Octubre 2012 |
| `SGOV` | Mayo 2020 |

**Ventana de backtest con roster completo: julio de 2018 en adelante.** Sustituyendo `SGOV` por `BIL` y aceptando la ausencia de `XLC` y `GLDM`, la ventana puede extenderse hasta 2013 con un roster reducido, lo cual es recomendable como test de robustez adicional.

La ventana 2018-2026 es defendible como muestra: contiene la corrección del cuarto trimestre de 2018, el crash de COVID de 2020, el mercado alcista de 2021, el bajista severo de 2022, la recuperación de 2023-2024 y el rally de metales de 2025. Son seis regímenes distintos en ocho años. No es una muestra grande, pero es sustancialmente mejor que los cuatro años discontinuos de v1.2.0.

**El sistema debe manejar entradas de instrumentos en el universo.** `XLC` no existe antes de junio de 2018; el motor no puede asumir un roster constante. Esto requiere una tabla de fechas de alta por instrumento y un filtro de elegibilidad que la consulte en cada rebalanceo.

---

## 11. Implementación con $2.000

Con el piso de posición de $150 y control de volatilidad al 12%, la cuenta soportará típicamente entre 4 y 8 posiciones simultáneas, con el resto en `SGOV`. Eso es adecuado: la diversificación efectiva no proviene de la cantidad de posiciones sino de que cada una sea un ETF que ya contiene entre 40 y 1.500 compañías.

El problema real es la granularidad. Un instrumento cuyo precio por acción supere los $150 solo admite asignaciones en múltiplos gruesos, lo que genera desviación respecto del peso objetivo. Hay que **medir ese error, no estimarlo**: registrar en cada rebalanceo la diferencia entre el vector de pesos objetivo y el ejecutado, y reportar el tracking error acumulado. Si supera el 2% anualizado, la granularidad está dominando a la estrategia y hay que reducir el roster o esperar a tener más capital.

`XLK` es el caso más problemático del roster por precio unitario. Si su granularidad resulta inmanejable, el sustituto razonable es `VGT` o `FTEC`, que ofrecen exposición equivalente. No es una decisión de estrategia sino de implementación, y debe documentarse como tal.

---

## 12. Qué esperar de la validación

Predicciones explícitas, registradas antes de correr el backtest para que sirvan de control:

- **El retorno de 2025 caerá drásticamente.** `GLDM` y `SLV` competirán con dieciséis instrumentos más y estarán limitados a un 20% combinado en lugar del 100% del capital. Es la consecuencia directa de eliminar el sesgo, no una falla del diseño.
- **Los drawdowns de 2020 y 2022 se reducirán de forma sustancial**, por efecto del control de volatilidad más que por la diversificación.
- **El Sharpe debería mejorar frente al 0.40 del trienio 2020-2022**, que es la única comparación legítima disponible. Si no mejora, el problema no es el universo sino la señal de momentum.
- **La rotación anual será mayor** que las 21 operaciones de v1.2.0, probablemente en el rango de 40 a 80. Con comisión cero en Alpaca el costo es spread y slippage, pero hay que modelarlo.

**Criterio de aceptación.** Este diseño sustituye al actual si y solo si mejora el Sharpe y el Calmar del período 2018-2026 completo respecto de v1.2.0 corrido sobre la misma ventana y bajo las mismas condiciones realistas —cortacircuitos activos, tasa libre de riesgo histórica, costos modelados. Comparar contra los números publicados de v1.2.0 no es válido, porque esos números no incorporan ninguna de esas correcciones.

---

## 13. Tabla consolidada de parámetros

```yaml
universe:
  mode: A                          # A = ETF-only, B = S&P500 point-in-time
  eligibility:
    min_dollar_volume_60d: 20_000_000
    min_price: 10.0
    min_history_days: 252
    max_spread_bps: 10
    exclude: [leveraged, inverse, volatility, single_commodity_futures]

blocks:
  us_sectors:
    tickers: [XLK, XLC, XLY, XLP, XLV, XLF, XLI, XLE, XLU, XLB, XLRE]
    capital_cap: 0.55              # multiplicado por regime_score
    top_n: 4
    per_instrument_cap: 0.20
  international:
    tickers: [IEFA, IEMG]
    capital_cap: 0.15              # multiplicado por regime_score(IEFA)
    top_n: 1
    per_instrument_cap: 0.15
  metals:
    tickers: [GLDM, SLV]
    capital_cap: 0.20              # NO modulado por régimen de equity
    top_n: 2
    per_instrument_cap: {GLDM: 0.15, SLV: 0.08}
  fixed_income:
    tickers: [IEF, TIP]
    capital_cap: 0.20              # NO modulado por régimen de equity
    top_n: 1
    per_instrument_cap: 0.20
  cash:
    ticker_live: SGOV
    ticker_backtest: BIL

momentum:
  windows: [21, 63, 126]
  skip_recent_days: 5
  aggregation: mean_of_ranks
  absolute_gate:
    outperform_ticker: BIL
    window: 126
    trend_filter: close > EMA(50)

regime:
  type: graduated
  components: [spy_above_sma200, spy_above_ema50, breadth_sectors_above_sma200]
  breadth_threshold: 0.50
  applies_to: [us_sectors, international]

sizing:
  method: inverse_volatility
  vol_lookback_days: 60
  target_portfolio_vol: 0.12
  covariance_lookback_days: 90
  covariance_shrinkage: 0.30
  max_gross_exposure: 1.00         # sin apalancamiento
  min_position_usd: 150

rebalance:
  frequency: weekly
  day: friday
  signal_time_et: "15:45"
  entry_rank_threshold: 4
  exit_rank_threshold: 7           # buffer asimétrico
  no_trade_band: 0.03
  max_holding_days: 90
  trailing_exit: close < EMA(25)

risk:
  daily_pause_pct: -0.02
  daily_liquidation_pct: -0.035    # REQUIERE recalibración: ver nota
```

> **Nota sobre cortacircuitos.** Los umbrales de -2% y -3.5% fueron dimensionados para un portafolio de dos posiciones al 50%. Con volatilidad objetivo del 12% y seis a ocho posiciones diversificadas, un movimiento diario de -3.5% del portafolio pasa a ser un evento genuinamente excepcional en lugar de una ocurrencia semanal. Los umbrales deben recalibrarse como múltiplos de la volatilidad diaria realizada —por ejemplo, 3σ y 4σ móviles— en lugar de constantes fijas. Dejarlos en valores absolutos con la nueva arquitectura los vuelve inoperantes o los mantiene innecesariamente restrictivos según el régimen.

---

*Documento de diseño. Ninguno de los parámetros propuestos fue optimizado sobre datos históricos: son valores iniciales derivados de convenciones de la literatura y de restricciones operativas de la cuenta. Cualquier ajuste posterior debe realizarse mediante walk-forward y documentarse en el registro de cambios, con el número de configuraciones probadas, para poder deflactar el Sharpe resultante.*
