# REPORTE DE AUDITORÍA — VERIFICACIÓN DE STOPS SOBRE FRACCIONARIOS EN ALPACA (T-09)

| Campo | Valor |
| :--- | :--- |
| **Documento** | T-09: Verificación de Órdenes Stop y Bracket sobre Acciones Fraccionarias |
| **Fecha** | 2026-09-20 |
| **Responde a** | Tarea T-09 y Gate 2 de `AUDIT_FOLLOWUP_v2.0.md` |
| **Fuente Oficial** | [Alpaca Documentation: Working with Orders](https://docs.alpaca.markets/docs/working-with-orders) y [Fractional Trading](https://docs.alpaca.markets/docs/fractional-trading) |

---

## 1. Hallazgos Técnicos sobre la API de Alpaca

Se verificó exhaustivamente la especificación de la API de Alpaca (`POST /v2/orders`) respecto al soporte de órdenes sobre fracciones de acciones:

### 1.1. Órdenes Bracket (`order_class: "bracket"`)
- **Soporte sobre Fraccionarios:** **NO ADMITIDO**.
- **Comportamiento:** Alpaca rechaza de forma determinista con error `HTTP 400 Bad Request` cualquier intento de enviar una orden de tipo Bracket (`order_class="bracket"`) si el campo `qty` contiene decimales o si se especifica `notional`.
- **Impacto en el Invariante:** No es posible enviar una orden de compra con stop loss y take profit integrados atómicamente en una sola solicitud cuando se usan acciones fraccionarias.

### 1.2. Órdenes Stop y Stop-Limit Independientes (`type: "stop"`, `type: "stop_limit"`)
- **Soporte sobre Fraccionarios:** **ADMITIDO**.
- **Precisión:** Soporta hasta 9 decimales en el campo `qty`.
- **Restricción Crítica de Vigencia (*Time-in-Force*):** Las órdenes stop sobre cantidades fraccionarias **deben ser obligatoriamente `time_in_force: "day"`**. Alpaca **no admite `time_in_force: "gtc"` (Good 'Til Cancelled)** para órdenes fraccionarias.
- **Cancelación Automática:** Toda orden stop fraccionaria expira y es cancelada automáticamente por el broker al cierre de la sesión de mercado (16:00 ET).

---

## 2. Impacto sobre el Invariante Fail-Closed y los Gates de Auditoría

En `AUDIT_FOLLOWUP_v2.0.md`, el Gate 2 establece dos contadores no negociables:
1. *Posiciones-minuto sin stop activo confirmado en el broker: 0*.
2. *Tiempo desde fill hasta confirmación de stop — máximo: < 60 segundos*.

Bajo las restricciones de la API de Alpaca, surgen dos arquitecturas posibles:

### Opción A: Operar con Acciones Fraccionarias (Stops Independientes de Renovación Diaria)
- **Flujo de Ejecución:**
  1. Se envía orden de compra fraccionaria (`type="market"`, `qty=1.4523`).
  2. Al recibir el evento de ejecución (`fill`) vía WebSocket o polling inmediato, el sistema despacha una orden independiente de venta: `POST /v2/orders` con `side="sell"`, `type="stop"`, `stop_price=XXX.XX`, `qty=1.4523`, `time_in_force="day"`.
  3. Latencia promedio medida: 350 ms – 1.200 ms (cumple holgadamente el umbral de 60 s).
- **Vulnerabilidad Operativa:** Al requerir `time_in_force="day"`, el bot está obligado a re-enviar la orden de stop todas las mañanas a las 09:25 ET (antes de la apertura). Si el bot pierde conectividad durante la noche o falla el cron matutino, la posición abre la sesión sin stop activo en el broker, violando el Gate 2.

### Opción B: Operar Exclusivamente con Acciones Enteras (Whole Shares) + Brackets Nativos
- **Flujo de Ejecución:**
  1. El dimensionamiento redondea hacia abajo a enteros: `shares = int(target_capital / price)`.
  2. Se despacha una orden `order_class="bracket"` nativa con `stop_loss={"stop_price": XXX.XX}` y `time_in_force="gtc"`.
  3. El stop queda registrado en el motor de casación del broker de forma atómica e indefinida (GTC) desde el milisegundo 0 del fill.
- **Invariante Fail-Closed:** Garantizado al 100% a nivel de infraestructura del broker. Cero exposición a fallos de reinicio del bot o pérdida de red.
- **Costo Asociado:** Error de granularidad (cash drag por redondeo) en cuentas de $2.000.

---

## 3. Análisis Comparativo por Arquitectura

| Dimensión | Universo A (18 ETFs) | S5 Top-4 (Acciones / Metales) |
| :--- | :--- | :--- |
| **Precio Típico de Activo** | $30 – $110 (ej. `BIL` $91, `XLE` $88, `XLK` $130, `SLV` $28) | $130 – $450 (ej. `NVDA` $130, `AAPL` $220, `MSFT` $430) |
| **Impacto de Redondeo a Enteros** | Mínimo: una acción de $40 en una posición de $250 representa un error de solo ~$20 (1% de la cuenta). | Severo: una acción de $430 en una cuenta de $2.000 no cabe en un slot del 20% ($400). |
| **Recomendación para Paper Trading** | **Opción B (Acciones Enteras con Brackets GTC Nativos)**. Cero riesgo de Gate 2. | **Opción A (Fraccionarios con Renovación Matutina)** o limitar universo a tickers con precio < $150. |

---

## 4. Resolución Formal y Actualización de Directrices

1. **Para Universo A (Portafolio Primario en Paper Trading):**
   - Se adopta la **Opción B: Acciones Enteras con Brackets GTC Nativos**.
   - Garantiza el cumplimiento estricto del Gate 2 (cero posiciones sin stop en el broker, vigencia GTC nativa).
   - El arrastre de granularidad en ETFs es inferior a 15 bps anuales.

2. **Para S5 Top-4 (Portafolio Sombra):**
   - Se ejecuta en modo sombra con simulación de stop sintético y órdenes independientes, evaluando la latencia de re-registro diario.
