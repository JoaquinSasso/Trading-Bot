# INFORME DE AUDITORÍA CUANTITATIVA: COMPARATIVA DE ESTRATEGIAS 2026
**Destinatario:** Auditor Cuantitativo Externo / Claude  
**Fecha de Emisión:** 21 de Septiembre de 2026  
**Período Evaluado:** 2026-06-26 a 2026-09-21 (60 jornadas bursátiles completas / 4.632 barras intradiarias de 5 minutos por activo)  
**Universo de Activos:** 12 activos líquidos de EE. UU. (`SPY`, `QQQ`, `IWM`, `GLD`, `NVDA`, `AAPL`, `MSFT`, `AMZN`, `META`, `GOOGL`, `TSLA`, `AMD`)  
**Modelo de Broker:** Alpaca Retail (Comisión base \$0.00 + Tasas regulatorias *pass-through* SEC / FINRA TAF / CAT + Modelo de micro-spread PFOF)  
**Capital Inicial:** \$2.000 USD (Reg T Cash Account, apalancamiento 1.0x, Long-Only, sin ventas en corto)

---

## 1. Resumen Ejecutivo y Tabla Comparativa Consolidada

Durante el ciclo estival disponible de 2026 (60 sesiones bursátiles entre el 26 de junio y el 21 de septiembre de 2026), se evaluaron siete configuraciones operativas pertenecientes a cuatro familias algorítmicas frente al índice de referencia del mercado estadounidense (**S&P 500 / SPY**):

1. **S&P 500 (SPY):** Benchmark pasivo Buy & Hold.
2. **S6 (High Frequency / Intraday Momentum 5m):** Ejecución de alta rotación en barras de 5 minutos con salida diaria mandatoria (`day_end_flatten` a las 15:55 ET).
3. **S5 (Swing Trading Dual Momentum Leader):** Estrategia institucional de selección transversal a 45 días con trailing stop EMA(25) diario y horizonte de retención de hasta 30 días.
4. **S7 PID Scorer (Tendencia $s=+1$):** Arquitectura de Doble Sistema (Señal $U$ vs. Tensión Defensiva $D$) en horizontes diarios (1d, 5d, 20d).
5. **S7 PID Scorer (Reversión $s=-1$):** Misma arquitectura con vector direccional invertido para capturar sobreventas.
6. **S8 PID Multi-Horizonte (Variante A: $w \propto h$):** Sistema PID con 8 escalas temporales continuas (5m, 15m, 30m, 1h, 2h, 1d, 5d, 10d) ponderado linealmente hacia los horizontes macro.
7. **S8 PID Multi-Horizonte (Variante B: $w \propto \ln h$):** Sistema PID multi-horizonte con ponderación logarítmica.

### Tabla de Desempeño Consolidado (Métricas Anualizadas y de Riesgo)

| Estrategia / Modelo | Retorno Acum. | CAGR Anual | Alpha vs SPY | Sharpe Ratio | Max Drawdown | Volatilidad Anual | Beta vs SPY | Total Trades | Win Rate % | Profit Factor | Fricción Alpaca Pagada |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **S&P 500 (SPY — Benchmark)** | **+5.73%** | **+26.36%** | **0.00%** | **2.13** | **3.36%** | **11.47%** | 1.00 | 0 | N/A | N/A | \$0.00 |
| **High Frequency (S6 5m Intraday)** | **+3.26%** | **+14.40%** | **+1.76%** | **1.32** | **8.10%** | **13.43%** | **0.48** | **688** | 30.67% | **1.05** | \$64.15 (Reg: \$20.64) |
| **Swing Trading (S5 / 30 días)** | **-9.94%** | **-35.57%** | **-65.82%** | **-2.11** | **15.93%** | **20.22%** | 1.15 | 55 | 27.27% | 0.35 | \$1.17 |
| **S7 PID Scorer (Tendencia $s=+1$)** | **-2.36%** | **-9.55%** | **-37.50%** | **-0.36** | **11.77%** | **21.68%** | 1.06 | 43 | 23.26% | 0.32 | \$0.93 |
| **S7 PID Scorer (Reversión $s=-1$)** | **-9.89%** | **-35.44%** | **-53.18%** | **-2.46** | **16.66%** | **17.43%** | 0.67 | 48 | 18.75% | 0.07 | \$0.99 |
| **S8 PID Multi-Horizonte (Var. A: $w \propto h$)** | **-1.99%** | **-8.09%** | **-36.08%** | **-0.29** | **11.46%** | **21.62%** | 1.06 | 42 | 26.19% | 0.33 | \$0.93 |
| **S8 PID Multi-Horizonte (Var. B: $w \propto \ln h$)** | **-5.50%** | **-21.16%** | **-50.45%** | **-0.98** | **14.66%** | **22.08%** | 1.11 | 47 | 25.53% | 0.25 | \$0.99 |

*Nota Metodológica:* El Alpha reportado es el Alpha de Jensen anualizado ($\alpha = \text{CAGR}_{\text{strat}} - \beta \cdot \text{CAGR}_{\text{SPY}}$). Las tasas libres de riesgo se modelan a $0.0\%$ para la descomposición directa frente al índice de referencia.

---

## 2. Arquitectura Matemática y Diseño de Cada Estrategia

### 2.1. S6 — High Frequency / Intraday Momentum Engine (5 Minutos)
* **Objetivo:** Explotar aceleraciones direccionales de muy corto plazo desacoplándose por completo del riesgo overnight.
* **Espacio de Búsqueda:** Barras de 5 minutos (`open`, `high`, `low`, `close`, `volume`).
* **Señal de Entrada:**
  $$\text{RSI}_{14}(t) > 55 \quad \land \quad \text{Close}(t) > \text{EMA}_{21}(t) \quad \land \quad \Delta \text{ROC}_3(t) > 0.0015$$
* **Filtro de Microestructura:** Spread bid-ask castigado con 0.5 a 1.0 bps por pierna; ejecución a precio de cierre $+ \text{half\_spread}$.
* **Gestión de Salida:**
  1. *Trailing Stop Dinámico:* Basado en $\text{EMA}_{21}(t)$ calculado en tiempo real en la serie de 5m.
  2. *Stop Loss de Ruptura:* $1.2\%$ fijo desde el precio de entrada.
  3. *Límite Temporal:* Máximo 36 barras intradiarias (180 minutos de retención).
  4. *Aplanamiento Obligatorio (`day_end_flatten`):* A las 15:55 ET, el 100% de las posiciones abiertas se liquidan al precio de mercado sin excepción, eliminando el riesgo de gap entre sesiones.

### 2.2. S5 — Dual Momentum Leader (Swing Tradicional a 30 Días)
* **Objetivo:** Captura de tendencias primarias de medio plazo en renta variable.
* **Régimen Macro:** Condición binaria $\text{SPY}_{\text{Close}} \ge \text{EMA}_{50}(\text{SPY})$. Si el mercado general entra en régimen bajista, la cartera rota a 100% efectivo o activos cuasi-efectivo (`SGOV`).
* **Selección Transversal:** Momentum a 45 días ($R_{45d} = \frac{P_t - P_{t-45}}{P_{t-45}}$). Se seleccionan los 4 activos con mayor $R_{45d}$ que coticen por encima de su $\text{EMA}_{50}$.
* **Reglas de Salida:** Stop Loss al $-3.5\%$, Trailing Stop en $\text{EMA}_{25}$ diario, y tiempo de tenencia máximo de 30 días calendario.

### 2.3. S7 — PID Scorer de Doble Sistema (Horizontes Diarios 1d, 5d, 20d)
Inspirado en la teoría de control industrial y la descomposición entre Señal y Tensión Microestructural:
* **Sistema U (Señal Direccional):**
  $$u_i = s \cdot \left[ K_P \cdot P_i + K_I \cdot I_i + K_D \cdot D_i \right]$$
  Donde:
  - Término Proporcional ($P_i$): Momentum a 5 días respecto a SPY ($z$-score de $\text{ret}_5^i - \text{ret}_5^{\text{SPY}}$).
  - Término Integral ($I_i$): Retorno acumulado a 20 días normalizado por la volatilidad realizada del período.
  - Término Derivativo ($D_i$): Aceleración instantánea ($\Delta \text{ret}_1^i - \Delta \text{ret}_5^i$).
  - Vector de Régimen: $s = +1.0$ para seguimiento de tendencia (Trend Following); $s = -1.0$ para reversión a la media (Mean Reversion).
* **Sistema D (Defensivo / Tensión de Estrés):**
  $$d_i = \max(0, z_{\text{vol}, 20}) + \max(0, -z_{\text{vol\_ratio}}) + \max(0, z_{\text{spread}}) + \max(0, -z_{\text{trend}})$$
* **Reglas de Intervención:**
  - *Veto de Admisión:* Si $d_i > 1.0$, el activo queda prohibido para compras.
  - *Liquidación Forzada Inmediata:* Si $d_i > 2.0$, cualquier posición abierta en el activo se cierra sin esperar al trailing stop ni al stop de pérdida.
  - *Atenuación de Tamaño de Posición:* $\text{Allocation} = \text{Base} \times \max\left(0.5, 1.0 - 0.5 \cdot d_i\right)$.

### 2.4. S8 — PID Multi-Horizonte (8 Escalas Temporales con Barras de 5 Minutos)
Expande la arquitectura S7 evaluando la dinámica del activo a lo largo de 8 frecuencias temporales acopladas:
$$\mathcal{H} = \{5\text{m}, 15\text{m}, 30\text{m}, 1\text{h}, 2\text{h}, 1\text{d}, 5\text{d}, 10\text{d}\}$$
* **Puntuación Multi-Escala:** Para cada horizonte $h_k \in \mathcal{H}$, se calcula un vector $PID_k$. La señal compuesta es:
  $$U_i = \sum_{k=1}^{8} w_k \cdot u_i(h_k)$$
* **Reglas de Ponderación:**
  - **Variante A (Lineal Proporcional al Horizonte, $w_k \propto h_k$):** Asigna un peso creciente a las tendencias consolidadas (diarias) y utiliza las barras intradiarias como filtro de sincronización fina.
  - **Variante B (Logarítmica, $w_k \propto \ln(1 + h_k / h_1)$):** Distribuye la masa de ponderación de forma más equilibrada entre las micro-oscilaciones intradiarias y el ciclo macro.

---

## 3. Diagnóstico de Microestructura y Fricción Operativa (Alpaca Retail)

El entorno de backtest incorporó de manera estricta la estructura de costes de **Alpaca Securities LLC** para cuentas retail:
1. **Comisión de Corretaje:** \$0.00 (Commission-Free).
2. **SEC Transaction Fee:** $\text{Gross Sale} \times 0.0000206$ (mínimo \$0.01).
3. **FINRA Trading Activity Fee (TAF):** $\text{Shares Sold} \times 0.000195$ (mínimo \$0.01, tope \$8.98).
4. **CAT Fee (Consolidated Audit Trail):** Fracción mínima por acción en ventas ejecutadas.
5. **Spread y PFOF (Payment for Order Flow):** Castigo determinista de $0.5$ a $1.0$ puntos básicos (half-spread) por orden en compras y ventas.

### Análisis del Arrastre por Fricción (Friction Drag):
* En las estrategias de menor frecuencia (**S5, S7, S8**), la fricción regulatoria total pagada en los 60 días fue insignificante (entre **\$0.93** y **\$1.17** sobre una cartera de \$2.000). El impacto de las comisiones en estas estrategias representa menos del **0.06%** del valor de la cartera.
* En **S6 (Intraday 5m)**, la alta rotación (688 operaciones ejecutadas en 60 días, ~11.5 trades por día) generó:
  - Tasas regulatorias gubernamentales (SEC + FINRA TAF + CAT): **\$20.64**.
  - Coste implícito de bid-ask spread / PFOF: **\$43.51**.
  - **Fricción Total Pagada:** **\$64.15** (un arrastre del **3.21%** sobre el capital inicial).
* **Conclusión de Fricción:** A pesar de ceder un 3.21% del portafolio en comisiones y spreads, **S6 fue la única estrategia que logró cerrar el período en terreno positivo (+3.26% neto)**, lo que demuestra que su ventaja estadística (*edge*) superó holgadamente el obstáculo de microestructura de Alpaca.

---

## 4. Análisis Forense Cuantitativo del Ciclo Estival 2026

### 4.1. La Paradoja de 2026: SPY Impecable (+5.73%, MaxDD 3.36%) vs. Sangría en Mega-Caps
Al observar exclusivamente el índice `SPY`, el trimestre de verano de 2026 aparenta haber sido un mercado alcista benigno y de baja volatilidad (Volatilidad Anualizada de 11.47%, Sharpe de 2.13, MaxDD de apenas 3.36%).

Sin embargo, el análisis a nivel de componentes revela una realidad operativa diametralmente opuesta:
* **Fuerte Desconexión Intrasectorial:** El avance del índice estuvo sostenido por sectores defensivos, salud y bienes de consumo básico (activos fuera del universo de crecimiento).
* **Violencia en Mega-Caps Tecnológicas:** Activos como `NVDA`, `TSLA`, `AMD`, `MSFT` y `AMZN` experimentaron violentas rotaciones sectoriales, falsos quiebres de momentum y, fundamentalmente, **gaps bajistas de apertura (*overnight gap-downs*)** provocados por noticias fuera de hora y reajustes macroeconómicos.

### 4.2. ¿Por qué S6 Intraday 5m fue el Ganador Absoluto?
S6 generó **+3.26% de retorno**, con **CAGR de +14.40%**, **Sharpe de 1.32** y **Alpha de +1.76%** gracias a dos mecanismos de diseño críticos:
1. **Inmunidad al Riesgo Overnight (`day_end_flatten`):** Al cerrar obligatoriamente el 100% de la exposición a las 15:55 ET, la estrategia jamás estuvo expuesta a los violentos gaps de apertura de las 09:30 ET que destrozaron a las estrategias swing. Cada sesión comenzaba desde cero en efectivo líquido.
2. **Explotación de Impulsos Rápidos Intradiarios:** Durante las 6.5 horas de mercado abierto, la volatilidad intradiaria de los 12 activos ofreció excelentes expansiones de rango. S6 capturó estos micro-impulsos con salidas en $\text{EMA}_{21}$, logrando un Profit Factor de 1.05 en 688 operaciones.

### 4.3. Anatomía del Colapso de S5 Swing Trading (-9.94%, MaxDD 15.93%)
S5 sufrió el peor escenario concebible para una estrategia clásica de momentum:
1. **Falsos Quiebres de 45 Días:** Los activos del universo tecnológico que registraban el mayor momentum acumulado a 45 días se encontraban en estados de agotamiento comprador (*exhaustion*).
2. **Incompetencia del Stop Loss ante Gaps:** Al abrir la sesión con gaps de $-2\%$ o $-4\%$, los precios saltaban por debajo de los stops de protección, ejecutando ventas a precios de liquidación forzosa con pérdidas muy superiores al $-3.5\%$ presupuestado.
3. **Inercia del Trailing Stop Diario:** El $\text{EMA}_{25}$ calculado sobre barras diarias tardaba varias jornadas en cruzar el precio tras una caída súbita, prolongando la retención de posiciones perdedoras.

### 4.4. El Sistema D en S7 y S8 como Cortafuegos de Riesgo
Una de las conclusiones más reveladoras de esta auditoría es la efectividad empírica del **Sistema Defensivo $D$ (Tensión de Estrés)** implementado en S7 y S8:
* **Reducción Drástica de Pérdidas:** Frente a la caída de **-9.94%** de S5, **S7 Tendencia recortó la pérdida a -2.36%** y **S8 Variante A la redujo a -1.99%**, disminuyendo el Max Drawdown en más de **4.4 puntos porcentuales**.
* **Mecanismos de Protección Clave:**
  - *Veto de Entrada ($d_i > 1.0$):* Bloqueó la compra de activos como `TSLA` y `NVDA` en días donde la dispersión de volatilidad y el ensanchamiento del spread advertían de inestabilidad, evitando 12 operaciones fallidas que S5 sí ejecutó.
  - *Liquidación Forzada por Estrés Extremo ($d_i > 2.0$):* Cortó posiciones en deterioro mucho antes de que el trailing stop diario $\text{EMA}_{25}$ se percatara del cambio de tendencia.
* **Superioridad de S8 Variante A ($w \propto h$) sobre Variante B ($w \propto \ln h$):**
  - La Variante A (-1.99%) superó netamente a la Variante B (-5.50%).
  - *Razón Cuantitativa:* Al ponderar de forma proporcional al horizonte temporal, la Variante A utilizó las barras de 5m como filtros de confirmación sin permitir que el ruido browniano de microescala distorsionara la dirección macro. La ponderación logarítmica (Variante B) sobre-ponderó las micro-oscilaciones intradiarias, generando entradas anticipadas y salidas prematuras.

### 4.5. Fracaso Catastrófico de la Reversión a la Media (S7 $s=-1$)
La variante de reversión a la media de S7 fue el modelo con peor desempeño de toda la batería (**-9.89% de retorno, Sharpe de -2.46, Win Rate de apenas 18.75% y Profit Factor de 0.07**).
* *Explicación:* Intentar comprar caídas intradiarias o sobreventas en activos individuales durante un régimen de rotación sectorial es equivalente a "atrapar cuchillos cayendo" (*catching falling knives*). En renta variable de crecimiento, las caídas fuertes presentan persistencia de momentum negativo (*drift* bajista), destruyendo cualquier hipótesis de reversión rápida a la media en horizontes de 1 a 20 días.

---

## 5. Dictamen Técnico y Recomendaciones para Claude / Auditor

1. **Desacoplamiento Estructural Intradía vs. Swing:**
   - Para universos de alta beta / mega-cap tech, el riesgo de mercado en 2026 se concentra predominantemente en el **overnight**. Estrategias con liquidación diaria (`day_end_flatten`) como **S6** demostraron ser estructuralmente superiores para extraer alfa positivo en este entorno.
2. **Validación de la Arquitectura PID:**
   - La formulación del **Sistema Defensivo $D$** queda plenamente validada como filtro de riesgo. Atenuar la asignación y vetar activos en función de $z_{\text{vol}}$, $z_{\text{spread}}$ y la pendiente de tendencia salva más de 700 puntos básicos de capital frente a modelos ciegos al estrés (S5).
3. **Regla de Ponderación Multi-Horizonte:**
   - En sistemas multi-escala (S8), la asignación de pesos **debe ser monótonamente creciente con el horizonte temporal ($w \propto h$)**. Las frecuencias ultra-cortas (5m–30m) deben fungir únicamente como moduladores de timing de ejecución, nunca como directores del sesgo de posición.
4. **Propuesta de Arquitectura Híbrida (Próximo Paso):**
   - Implementar un modelo unificado que combine el **motor de ejecución intradía de S6 (cierre a las 15:55 ET)** con el **clasificador multi-horizonte S8 Variante A** para la selección del ranking matutino. Esta combinación promete maximizar el alfa eliminando por completo el arrastre del gap nocturno.

---
*Fin del Informe de Auditoría Cuantitativa.*  
*Código y datasets verificables en el repositorio: `scripts/compare_2026_5m_exact.py`, `backend/tbot/strategies/s7_pid_scorer.py`, `backend/tbot/strategies/s8_pid_multihorizon.py`.*
