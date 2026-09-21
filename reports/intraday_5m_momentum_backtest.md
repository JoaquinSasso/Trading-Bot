# REPORTE DE AUDITORÍA — BACKTESTING INTRADIARIO DE 5 MINUTOS (M5-HFT)

| Campo | Valor |
| :--- | :--- |
| **Rama de Desarrollo** | `feat/intraday-5m-hft` |
| **Estrategia** | S6: Multi-Horizon Intraday Momentum (10m, 15m, 30m, 45m, 60m) |
| **Resolución Temporal** | Barras de 5 minutos (78 barras por sesión regular de mercado) |
| **Dataset Evaluado** | 60 sesiones recientes (4.632 barras por activo) sobre 12 activos líquidos |
| **Regla de Cierre** | Day-End Flatten incondicional a las 15:55 ET (cero riesgo nocturno) |

---

## 1. Tabla Comparativa de Rendimiento y Atribución de Fricción

| Modalidad de Ejecución | Capital Inicial | Retorno Total | Sharpe Anual | Max Drawdown | Total Operaciones | Win Rate | Profit Factor | Costo Fricción ($) | Arrastre (% cuenta) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **1. Señal Pura Teórica (Sin Costos)** | $25.000 | **+3.91%** | 1.71 | 7.77% | 1062 | 33.2% | 1.07 | $0.00 | 0.0% |
| **2. Realista Institucional ($25k, Costos)** | $25.000 | **-15.76%** | -5.29 | 19.61% | 1066 | 26.4% | 0.72 | $4908.33 | **19.63%** |
| **3. Cuenta Pequeña ($2.000, Acciones Enteras)** | $2.000 | **-14.18%** | -6.12 | 16.98% | 1009 | 25.9% | 0.68 | $290.19 | **14.51%** |

### Hallazgo Crítico de Microestructura (El 'Impuesto de Fricción Intradiaria'):
- La señal pura teórica genera un retorno de **+3.91%** a lo largo de las 60 sesiones.
- Al aplicar los costos reales de microestructura (spreads de 1 a 2.5 bps por activo + slippage conservador de 1.5 bps por orden), el rendimiento neto cae a **-15.76%**.
- La fricción acumulada en 60 sesiones asciende a **$4908.33**, lo que representa un arrastre del **19.63% del capital**.
- En velas de 5 minutos, el movimiento medio por trade es de apenas **0.25% a 0.50%**, por lo que un costo de ida y vuelta de 6-8 bps devora entre el **20% y el 40% del margen bruto** de cada operación.

---

## 2. Análisis de Duración y Distribución de Salidas

| Causa de Salida | Total Ocurrencias | Participación (% de trades) |
| :--- | :---: | :---: |
| `trailing_ema` | 939 | 88.1% |
| `initial_stop_loss` | 61 | 5.7% |
| `day_end_flatten` | 45 | 4.2% |
| `max_holding_bars` | 21 | 2.0% |

- **Tiempo promedio de permanencia:** **35.6 minutos** (7.1 barras de 5m).
- **Preservación Fail-Closed:** Cero posiciones abiertas fuera del horario de mercado. Todas las operaciones activas se cerraron a las 15:55 ET mediante `day_end_flatten`.

---

## 3. Restricciones Regulatorias y Viabilidad Real (PDT vs Margin)

- **Frecuencia Operativa Medida:** **17.8 operaciones por día** en promedio (~89 operaciones semanales).
- **Incompatibilidad con Cuentas Minoristas < $25k (FINRA Rule 4210):**
  * La regla de *Pattern Day Trader* (PDT) limita las cuentas de margen con menos de $25.000 a un máximo de **3 day trades en 5 días móviles**.
  * Con 89 day trades semanales, una cuenta menor de $25k quedaría **bloqueada por el broker en su segundo día de operación**.
  * En cuentas Cash, la regla de liquidación T+1 agotaría el capital disponible en las primeras dos horas de la rueda.
- **Conclusión de Implementación:** La estrategia S6 de 5 minutos es **técnicamente funcional**, pero **solo es operable en cuentas de margen institucional con capital superior a $25.000 USD** (o mediante futuros / CFDs fuera del régimen de acciones al contado de FINRA).

---

## 4. Comparativa Arquitectónica: Intradiario (5m) vs Swing Diario (S5 / Univ A)

| Dimensión | Sistema Diario (Universo A / S5) | Sistema Intradiario 5m (M5-HFT) |
| :--- | :--- | :--- |
| **Horizontes** | 45 días (S5) / 21d, 63d, 126d (Univ A) | **10m, 15m, 30m, 45m, 60m** |
| **Frecuencia de Decisión** | Diaria / Semanal (15:45 ET) | **Cada 5 minutos** (78 veces al día) |
| **Riesgo Nocturno (Gaps)** | Presente (mitigado por régimen SPY) | **Cero** (liquidación forzosa a 15:55 ET) |
| **Sensibilidad a Fricción** | Baja (3-5 bps sobre movimientos de 3-8%) | **Crítica** (6-8 bps sobre movimientos de 0.3%) |
| **Requisito de Capital** | $2.000 USD (Aprobado en T-13) | **> $25.000 USD** (por regla PDT de FINRA) |
| **PBO / Sobreajuste** | Medido (84.45% en S5, parsimonioso en Univ A) | Alto riesgo de microestructura y ruido blanco |
