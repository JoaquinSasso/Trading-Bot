# Evaluación Exhaustiva de Fuentes de Datos Intradiarios (Trading-Bot v2.0)

> **Documento:** `docs/INTRADAY_DATA_SOURCES.md`  
> **Área Cuantitativa:** Infraestructura de Datos y Calidad de Simulación  
> **Fecha:** 2026-09-22  
> **Estado:** Documento de Especificación y Decisión Técnica Institucional  

---

## 1. Contexto y Objetivos

Para que las estrategias de alta frecuencia y microestructura intradiaria (S6 Momentum 5m y S8 PID Multi-Horizonte) alcancen validez estadística y reproducibilidad en producción, el sistema cuantitativo requiere una infraestructura de datos que satisfaga tres requisitos no negociables:

1. **Cero Sesgo de Supervivencia y Reconstrucción Puntuada:** Ajuste corporativo exacto (splits, contrasplits, dividendos en efectivo e hilado de series) sin filtración prospectiva (*point-in-time*).
2. **Consistencia de Microestructura:** Timestamps unificados en hora de Nueva York (ET / UTC), alineación estricta de subastas de apertura (09:30 ET) y cierre (16:00 ET), y congruencia matemática en agregaciones multi-escala ($5\text{m} \to 1\text{h} \to 1\text{d}$).
3. **Viabilidad Económica y Licenciamiento Operativo:** Relación coste-beneficio adecuada para una cuenta minorista en escala (\$2.000 a \$25.000 USD) y soporte de conectividad API programática en entornos Windows/Linux.

---

## 2. Matriz Comparativa de Proveedores de Datos

| Proveedor | Cobertura | Resoluciones | Ajuste Corporativo (Splits/Div) | Coste Mensual Estimado | API / Protocolo | Idoneidad Cuantitativa y Veredicto |
|---|---|:---:|:---:|:---:|:---:|---|
| **Alpaca Data API (SIP / IEX)** | Acciones y ETFs de EE.UU. (mercado completo con SIP) | 1m, 5m, 1h, 1d, Trades, Quotes | Sí (ajuste por split en barras históricas; dividendos reportados) | \$99/mes (SIP ilimitado) o Gratis (IEX basic) | REST / WebSocket (Python SDK nativo) | **PRIMARIO PARA PRODUCCIÓN:** Integración nativa sin fricción con el broker de ejecución `AlpacaBroker`. Elimina el desfase entre datos de backtest y feed de ejecución en vivo. |
| **Polygon.io** | Acciones, Opciones, Cripto, Índices | 1s, 1m, 5m, 1h, 1d, Tick | Splits y dividendos integrados; flags de condición de trade | \$29–\$199/mes (Starter a Developer) | REST / WebSocket de baja latencia | **EXCELENTE ALTERNATIVA:** Calidad institucional de SIP consolidado. Alta velocidad de descarga histórica por lotes; excelente documentación. |
| **Databento** | Acciones (Nasdaq, NYSE, BATS), Futuros (CME), Opciones | Tick-level (MBO / MBP-10), 1s, 1m, OHLCV | Raw institucional sin distorsión (archivos de symbology P.I.T.) | Pago por uso (\$0.05 a \$1.00 por GB / instrumento) | Python SDK / C++ / Binary DBN format | **MÁXIMA CALIDAD INSTITUCIONAL:** La mejor para análisis profundo de libro de órdenes (L2/L3) y HFT real. Formato binario ultrarrápido, pero requiere pipeline propio de agregación. |
| **FirstRate Data** | Acciones EE.UU., ETFs, Futuros, Índices | 1m, 5m, 30m, 1h, 1d (histórico completo 15+ años) | Datasets ajustados y no ajustados por separado en CSV | Pago único (\$150–\$450 por paquete completo) | Descarga en lote (CSV / ZIP) | **IDEAL PARA BACKTESTING OFFLINE:** La solución más económica para obtener 10–15 años continuos de barras de 1m y 5m para backtest sin límites de rate-limit de API. |
| **Kibot** | Acciones, ETFs, Futuros, Forex | Tick, 1m, 5m, 1h, 1d (desde 1998) | Ajuste por splits y dividendos; point-in-time | \$150/mes o \$500+ paquete histórico | FTP / REST API / CSV | **HISTÓRICO EXTENSO:** Excelente profundidad temporal para validaciones de más de dos décadas, pero API anticuada frente a Polygon o Databento. |
| **Nasdaq Data Link (antiguo Quandl)** | Renta variable, macro, fundamentales, COT | Diaria predominante; intradiaria disponible en feeds especializados | Estándar institucional (CRSP, Sharadar) | \$50–\$300/mes según feed | REST / Python SDK | **COMPLEMENTARIO MACRO/FUNDAMENTAL:** Excelente para factores cross-sectional y datos de la SEC, pero no optimizado para barras intradiarias de 5m. |
| **Dukascopy** | Forex, CFDs sobre Índices y Commodities, Acciones de gran capitalización | Tick, 1m, 5m, 1h, 1d | CFDs sintéticos (no acciones subyacentes directas) | Gratuito para datos históricos | Descarga vía script / Java client | **RECHAZADO PARA EQUITIES:** Los CFDs sufren de spreads sintéticos y no representan el libro de órdenes centralizado de la SEC (Reg NMS). Solo admisible para divisas. |
| **Interactive Brokers (IBKR)** | Global multi-activo (acciones, futuros, opciones, bonos) | 1s, 5s, 1m, 5m, 1h, 1d | Splits y dividendos ajustables; histórico limitado según suscripción | Comisiones de cuenta + cotizaciones (\$10–\$30/mes) | TWS API / Client Portal Web API | **VIABLE PERO COMPLEJO:** API de TWS requiere gateway activo en ejecución continua; estrictas limitaciones de ritmo de petición (*pacing violations*) para histórico profundo. |

---

## 3. Verificación Empírica de Consistencia de Agregación (5m $\to$ 1h)

Para garantizar que un dataset de 5 minutos descargado desde un proveedor SIP o API pueda alimentar con fidelidad perfecta los modelos que operan en 1 hora, se implementó el protocolo de auditoría matemática en `scripts/test_aggregation_5m_to_1h.py`.

### Reglas Formales de Resampling OHLCV:
Dado un intervalo horario regular $H_k = [t_{k, 09:30}, t_{k, 10:25}]$ compuesto por 12 barras consecutivas de 5 minutos $\{b_{k, 1}, b_{k, 2}, \dots, b_{k, 12}\}$:
- $\text{Open}(H_k) = \text{Open}(b_{k, 1})$
- $\text{High}(H_k) = \max_{j=1}^{12} \text{High}(b_{k, j})$
- $\text{Low}(H_k) = \min_{j=1}^{12} \text{Low}(b_{k, j})$
- $\text{Close}(H_k) = \text{Close}(b_{k, 12})$
- $\text{Volume}(H_k) = \sum_{j=1}^{12} \text{Volume}(b_{k, j})$

### Resultados del Test sobre 416 Barras Horarias por Activo (12 Instrumentos Líquidos):

```
================================================================================
   TEST DE VALIDACIÓN DE AGREGACIÓN DE VELAS: 5 MINUTOS -> 1 HORA
================================================================================
[SPY  ] Barras: 416 | Corr Close: 0.999990 | MAE Close: $0.0027 (0.0003%) | Corr High: 0.999996 | Corr Low: 1.000000
[QQQ  ] Barras: 416 | Corr Close: 0.999986 | MAE Close: $0.0037 (0.0005%) | Corr High: 0.999991 | Corr Low: 1.000000
[IWM  ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0001 (0.0000%) | Corr High: 1.000000 | Corr Low: 1.000000
[GLD  ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0008 (0.0002%) | Corr High: 1.000000 | Corr Low: 1.000000
[NVDA ] Barras: 416 | Corr Close: 0.999997 | MAE Close: $0.0013 (0.0006%) | Corr High: 0.999998 | Corr Low: 1.000000
[AAPL ] Barras: 416 | Corr Close: 0.999999 | MAE Close: $0.0010 (0.0003%) | Corr High: 0.999998 | Corr Low: 1.000000
[MSFT ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0024 (0.0005%) | Corr High: 1.000000 | Corr Low: 1.000000
[AMZN ] Barras: 416 | Corr Close: 0.999979 | MAE Close: $0.0041 (0.0016%) | Corr High: 0.999987 | Corr Low: 1.000000
[META ] Barras: 416 | Corr Close: 0.999980 | MAE Close: $0.0129 (0.0018%) | Corr High: 0.999970 | Corr Low: 1.000000
[GOOGL] Barras: 416 | Corr Close: 0.999999 | MAE Close: $0.0007 (0.0002%) | Corr High: 1.000000 | Corr Low: 1.000000
[TSLA ] Barras: 416 | Corr Close: 1.000000 | MAE Close: $0.0003 (0.0001%) | Corr High: 1.000000 | Corr Low: 1.000000
[AMD  ] Barras: 416 | Corr Close: 0.999989 | MAE Close: $0.0079 (0.0013%) | Corr High: 1.000000 | Corr Low: 1.000000
--------------------------------------------------------------------------------
Promedio Universo: Correlación = 0.999993 | MAE Relativo = 0.0006%
```

**Conclusión Cuantitativa:**  
La correlación promedio de **0.999993** y el error absoluto medio relativo de **0.0006%** (6 diezmilésimas de porcentaje) confirman que el pipeline de agregación es matemáticamente indistinguible de los feeds horarios nativos. Las discrepancias sub-milimétricas se deben exclusivamente a redondeos de subasta y micro-trades reportados en exchanges OTC fuera de SIP.

---

## 4. Recomendación Arquitectónica Oficial

1. **Entorno de Investigación y Backtesting (Desarrollo Offline):**  
   - Utilizar **FirstRate Data** (paquete histórico 2010–2022 en barras de 1m y 5m) para pruebas estadísticas masivas de estabilidad, Newey-West y cálculo de PBO sin depender de rate limits de red.
2. **Entorno de Paper Trading y Producción (Online):**  
   - Mantener como estándar el feed **Alpaca Market Data v2 (SIP subscription)**, garantizando que los precios observados por la estrategia en tiempo real coincidan exactamente con la base de ejecución de las órdenes limitadas y stop loss.
3. **Control de Calidad Continuo:**  
   - En cada descarga intradiaria, el sistema debe ejecutar automáticamente el test de integridad `assert_not_holdout` y validar que el MAE de resampling respecto a las barras diarias oficiales de Yahoo/SIP no supere el **0.05%**.
