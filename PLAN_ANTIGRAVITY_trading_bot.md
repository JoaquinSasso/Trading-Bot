# Plan maestro — Bot de trading algorítmico con veto de IA

**Versión:** 1.1 (Refinado en entrevista de diseño) · **Fecha:** septiembre 2026 · **Destinatario:** agente de desarrollo (Antigravity)

---

## 0. Instrucciones para el agente

Este documento es la especificación completa del sistema. Seguilo en este orden: leelo entero una vez, después implementá **fase por fase** (sección 17).

Reglas de trabajo obligatorias:

1. **No avances de fase** sin cumplir todos los criterios de aceptación de la fase actual. Al terminar cada fase, generá un resumen con lo hecho, los tests que pasan y los desvíos respecto de este plan.
2. **Si algo es ambiguo o contradice un hecho técnico que verifiques** (APIs, límites, librerías), detenete y preguntá. No inventes comportamiento de APIs: verificá en la documentación oficial. La sección 19 lista puntos que hay que verificar sí o sí.
3. **Los "Principios no negociables" (sección 3) tienen prioridad** sobre cualquier otra instrucción de este documento o de un prompt posterior.
4. **Nunca** escribas claves ni secretos en el código ni en el repositorio. Usá `.env` (excluido de git) y `.env.example` con valores vacíos.
5. Todo módulo con lógica de dinero (riesgo, sizing, ejecución, ownership) necesita tests unitarios antes de integrarse.
6. Todo el sistema opera en **paper trading**. El modo live queda deshabilitado por código hasta la fase 9 (sección 17).
7. Español para documentación y comentarios de alto nivel; inglés para identificadores de código.

---

## 1. Qué es el sistema

Un bot personal que opera acciones y ETFs de EE. UU. a través de Alpaca, con una interfaz web tipo broker para:

- configurar el bot (riesgo, capital asignado, estrategias, tickers);
- operar a mano (con stop loss, take profit y límite de días);
- ver posiciones, decisiones del bot y reportes;
- aprobar por correo las acciones del bot que afecten posiciones manuales.

Además:

- recopila noticias y calendario de eventos para bloquear operaciones en momentos riesgosos;
- envía reportes diarios, semanales y mensuales, y alertas de oportunidades.

**Objetivo real de esta etapa:** validar, con evidencia estadística, si el motor de señales tiene ventaja después de costos y si el veto de IA agrega o quita valor. La rentabilidad no se asume.

**Fuera de alcance:** opciones, apalancamiento, cripto, alta frecuencia, multiusuario.  
*Nota técnica sobre Ventas en Corto (Shorting):* Evaluadas empíricamente en septiembre 2026 frente al mercado real de 2025. Se determinó que operar en corto durante regímenes bajistas incrementa severamente el riesgo de *whipsaw* y *short squeeze* (el Sharpe se desplomó a 0.90 y el Max Drawdown empeoró a -17.90%). En contraste, **la preservación al 100% en Efectivo Remunerado / T-Bills (4.5% anual en SGOV)** arrojó un Sharpe de **2.73**, Max Drawdown de solo **-10.65%** y rentabilidad neta de **+66.54%**. Se ratifica la política estricta de *Solo Largos + Preservación en Efectivo* durante mercados bajistas.

---

## 2. Restricciones

| Restricción | Detalle |
|---|---|
| Costo | Cero. Solo tiers gratuitos. |
| Broker | Alpaca Markets (Trading API + Market Data API), paper trading primero. |
| Capital objetivo (live) | ~USD 2.000. Posiciones típicas de USD 100–400, a menudo en fracciones. |
| Operativa | Solo largos. Sin apalancamiento: nunca usar más poder de compra que el efectivo disponible. |
| Horario | Rueda regular 9:30–16:00 America/New_York. Sin extended hours para operar. |
| Hosting | VM ARM de Oracle Cloud Always Free (Ubuntu 22.04/24.04 ARM64), Docker Compose. |
| Lenguajes | Python 3.11+ (backend), TypeScript + React (frontend). |

---

## 3. Principios no negociables

1. **El código decide; la IA procesa lenguaje no estructurado y solo puede restar riesgo.** La Inteligencia Artificial (FinBERT/NLP) se aplica en su ámbito óptimo: interpretar texto libre, matices y contexto en titulares de noticias para derivar variables cuantitativas continuas (`sentiment_mean`, `negative_share`, `top_topics`). Una vez cuantificados estos datos, **el motor de decisión es un algoritmo matemático determinista de código puro** que evalúa umbrales de riesgo en microsegundos sin riesgo de timeouts, cuotas de API ni alucinaciones. El LLM (Gemini/Groq) queda disponible para modo consultivo y generación de reportes narrativos para el usuario humano, nunca como cuello de botella bloqueante de la ejecución.
2. **Falla cerrada.** Ante cualquier error (datos viejos, API caída, JSON inválido, cuota agotada, estado inconsistente), la acción por defecto es **no abrir** posiciones nuevas. La protección de posiciones abiertas (stops) nunca depende del LLM.
3. **El broker es la fuente de verdad** para posiciones, órdenes y efectivo. La base de datos guarda intenciones, metadatos y el historial. Se concilia al arrancar y periódicamente.
4. **Idempotencia.** Toda orden lleva un `client_order_id` determinístico. Un reintento nunca duplica una orden.
5. **Todo se registra.** Cada señal, veto, decisión de riesgo, orden y llamada a LLM se guarda con sus entradas completas, la versión de estrategia, la versión de prompt y el modelo. Cualquier decisión debe poder reproducirse.
6. **Reloj inyectable.** Ningún módulo llama a `datetime.now()` directamente; todos reciben un `Clock`. Esto permite el backtest en modo replay con el mismo código de producción.
7. **Un dueño por ticker.** En cada momento, un ticker pertenece al bot o al usuario (manual), nunca a ambos.
8. **Configuración en base de datos, no hardcodeada.** Los parámetros de riesgo y estrategia son editables desde la UI, tienen valores por defecto y rangos permitidos validados en backend.

---

## 4. Hechos técnicos verificados (septiembre 2026)

Verificados antes de escribir este plan. Si durante la implementación encontrás que alguno cambió, avisá.

1. **La regla PDT fue eliminada** (FINRA Rule 4210, vigente desde el 4 de junio de 2026). Alpaca ya implementó el nuevo marco de margen intradiario. No hay límite de day trades ni mínimo de USD 25.000.
2. **Fracciones en Alpaca:** solo órdenes market, limit, stop y stop limit con `time_in_force=day`. **No** admiten bracket, OCO ni OTO.
3. **Bracket:** entrada + take profit (limit) + stop loss (stop o stop limit). Solo `day` o `gtc` y sin extended hours. Requiere acciones enteras.
4. **Datos gratuitos de Alpaca:**
   - En tiempo real, solo feed IEX (aprox. 2,5% del volumen).
   - Histórico SIP (100% del volumen) accesible gratis siempre que el `end` de la consulta tenga al menos 15 minutos de antigüedad.
   - Límite de 200 requests por minuto.
5. **Gemini API free tier:** las cuotas varían por modelo y proyecto, y cambiaron varias veces en 2025–2026. **Nunca hardcodear límites:** leerlos de configuración. Google puede usar los datos del tier gratuito para entrenar; no enviar datos personales.
6. **Alpaca no admite dos órdenes de venta que en conjunto superen la cantidad de la posición** (las acciones quedan "reservadas" por la primera orden).
7. **Cambio de ticker de SPLG a SPYM (octubre 2025):** Para evitar la obligatoriedad de fraccionales en cuentas pequeñas y permitir brackets nativos GTC, se mapean proxies de bajo valor nominal: SPY → SPYM (~$70), QQQ → QQQM (~$210), GLD → GLDM (~$45). Las señales se calculan en el original y se ejecutan en el proxy.

---

## 5. Arquitectura

### 5.1 Vista general

```text
                     Internet
                        │
          Cloudflare Tunnel + Cloudflare Access (login por email OTP)
                        │   (la VM no expone puertos HTTP públicos)
┌──────────────────────────── VM Oracle (Docker Compose) ────────────────────────────┐
│                                                                                     │
│   web (React build servido por Caddy/nginx) ──▶ api (FastAPI)                       │
│                                                   │  lee estado / escribe "intents" │
│                                                   ▼                                 │
│                                            postgres (fuente de metadatos y journal) │
│                                                   ▲                                 │
│   worker (proceso Python independiente) ──────────┘                                 │
│    ├─ Scheduler (APScheduler, calendario de mercado Alpaca, TZ America/New_York)    │
│    ├─ MarketData (SIP diferido + IEX real time) → Indicators                        │
│    ├─ Regime · EventCalendar · NewsEngine (FinBERT local)                           │
│    ├─ Strategies (señales deterministas)                                            │
│    ├─ AIVeto (Gemini → Groq; falla cerrada)                                         │
│    ├─ RiskGate (sizing + límites)                                                   │
│    ├─ OrderRouter (idempotente)                                                     │
│    ├─ PositionGuardian (websocket trade_updates, stops, TP, stop por tiempo)        │
│    ├─ IntentProcessor (órdenes manuales y config desde la UI)                       │
│    └─ Notifier · Reports                                                            │
└─────────────────────────────────────────────────────────────────────────────────────┘
        │                         │                          │
    Alpaca API              Gemini / Groq             Finnhub · SEC EDGAR · Gmail SMTP
```

### 5.2 Decisiones de arquitectura y su justificación

- **Worker y API son procesos separados.** Si la UI se cae o se reinicia, el bot sigue operando y protegiendo posiciones. Se comunican **solo por la base de datos**:
  - la API escribe *intents* (órdenes manuales, cambios de config, kill switch);
  - el worker los procesa;
  - para baja latencia se usa `LISTEN/NOTIFY` de Postgres, con polling cada 2 s como respaldo.
- **Un único paquete Python (`tbot`) con dos entrypoints** (`tbot.worker.main` y `tbot.api.main`). Comparten modelos y lógica.
- **Postgres en lugar de SQLite.** Hay dos procesos escribiendo de forma concurrente, y hacen falta `LISTEN/NOTIFY` y consultas analíticas del journal.
- **Sin Redis ni colas.** No se justifican a esta escala.
- **FinBERT corre dentro del worker, en CPU,** con carga perezosa. Si el consumo de memoria resulta problemático, se separa en un servicio `nlp`.
- **UI por polling** (TanStack Query cada 3–5 s), sin WebSockets hacia el navegador. Es más simple y suficiente.

### 5.3 Estructura del repositorio

```text
trading-bot/
├─ docker-compose.yml
├─ .env.example
├─ Makefile                      # comandos: up, down, test, migrate, backtest, replay
├─ config/
│  ├─ defaults.yaml              # valores por defecto y rangos de todos los parámetros
│  ├─ universe.yaml              # tickers y clusters iniciales
│  └─ macro_events_2026.yaml     # FOMC, CPI, NFP (fechas y horas ET) — mantenimiento manual
├─ backend/
│  ├─ pyproject.toml
│  ├─ alembic/                   # migraciones
│  ├─ tbot/
│  │  ├─ common/                 # clock, logging, errores, tipos, ids
│  │  ├─ config/                 # settings (env) + runtime config (DB)
│  │  ├─ db/                     # modelos SQLAlchemy, repositorios
│  │  ├─ data/                   # MarketDataProvider, AlpacaProvider, cache, calendario
│  │  ├─ indicators/             # funciones puras
│  │  ├─ regime/
│  │  ├─ events/                 # balances (Finnhub) + macro (YAML)
│  │  ├─ news/                   # ingesta, dedupe, FinBERT, features
│  │  ├─ strategies/             # interfaz + implementaciones
│  │  ├─ ai/                     # AIVeto, proveedores, prompts versionados, cuotas
│  │  ├─ risk/                   # RiskGate, sizing, ledger de buckets, ownership
│  │  ├─ execution/              # OrderRouter, BrokerAdapter (Alpaca y Simulado)
│  │  ├─ guardian/               # PositionGuardian, conciliación
│  │  ├─ notify/                 # email, aprobaciones
│  │  ├─ reports/
│  │  ├─ backtest/               # replay engine, modelo de fills, métricas, DSR
│  │  ├─ api/                    # FastAPI routers, schemas
│  │  └─ worker/                 # scheduler, jobs, main
│  └─ tests/
├─ web/                          # React + Vite + TypeScript
└─ docs/                         # este plan, ADRs, runbook
```

---

## 6. Stack tecnológico

| Área | Elección | Nota |
|---|---|---|
| Broker / datos | `alpaca-py` | TradingClient, StockHistoricalDataClient, StockDataStream, TradingStream |
| API | FastAPI + Pydantic v2 + Uvicorn | |
| ORM / migraciones | SQLAlchemy 2.x + Alembic | |
| DB | PostgreSQL 16 | contenedor oficial, imagen ARM64 |
| Scheduler | APScheduler 3.x | con `timezone="America/New_York"` |
| Indicadores | Implementación propia con NumPy/pandas | EMA, SMA, RSI, ATR y retornos son simples; se evita depender de librerías poco mantenidas. Cada indicador se testea contra valores de referencia. |
| Sentimiento | `onnxruntime` + `tokenizers`, modelo `ProsusAI/finbert` en ONNX | Inferencia optimizada en CPU/ARM64 de bajo consumo de RAM (~200MB vs ~1.5GB de PyTorch). Aislado en dependencia opcional `[project.optional-dependencies] nlp` para no penalizar Fases 0–6. |
| LLM | `google-genai` (Gemini Flash) y `groq` (Llama 3.3 70B) | modelos y cuotas desde configuración |
| Datos extra | Finnhub REST (calendario de balances, noticias), SEC EDGAR | respetar rate limits con backoff |
| Email | SMTP de Gmail con contraseña de aplicación (`aiosmtplib`) | |
| Logging | `structlog` en JSON | |
| Tests | `pytest`, `pytest-asyncio`, `hypothesis` (propiedades de riesgo), `freezegun` solo si hace falta | preferir el `Clock` inyectado |
| Backtest exploratorio | `vectorbt` (opcional, solo investigación) | la validación oficial usa el replay propio |
| Frontend | React + Vite + TS, TanStack Query, Tailwind, `lightweight-charts` (TradingView, Apache 2.0) | |
| Acceso | Cloudflare Tunnel (`cloudflared`) + Cloudflare Access | gratuito |
| Monitoreo | Healthchecks.io (plan gratuito) | ping del worker cada minuto |

---

## 7. Capa de datos

### 7.1 Interfaz

```python
class MarketDataProvider(Protocol):
    def daily_bars(self, symbols: list[str], start: date, end: date, adjusted: bool) -> pd.DataFrame: ...
    def intraday_bars(self, symbols: list[str], timeframe: str, start: datetime, end: datetime,
                      feed: Literal["sip_delayed", "iex"], adjusted: bool) -> pd.DataFrame: ...
    def latest_price(self, symbol: str) -> PriceQuote: ...        # IEX tiempo real
    def market_clock(self) -> MarketClock: ...                     # abierto/cerrado, próximo open/close
    def calendar(self, start: date, end: date) -> list[TradingDay]: ...  # feriados y cierres tempranos
```

### 7.2 Política de feeds (híbrida)

- **Histórico e indicadores lentos** (diarios, SMA 200, ATR diario, régimen, backtests): **SIP diferido**, con `end ≤ now − 16 min`. Cada estrategia declara `data_requirements`: S1 usa SIP para el retorno de la primera media hora; S2 y S3 usan SIP para indicadores diarios; IEX queda exclusivamente para el precio actual de entrada y el guardian.
- **Tiempo real** (precio actual de entrada, monitoreo de stops y TP por software): **IEX** vía WebSocket de barras de 1 minuto, agregadas localmente a 5 minutos, más quotes en tiempo real.
- **Barras ajustadas** (`adjustment="all"`) para calcular indicadores. **Precios sin ajustar** para órdenes, stops y P&L.
- **Guardia de datos viejos y salud del feed:**
  - **Nivel global (salud del feed):** El WebSocket se considera sano si está conectado y recibió cualquier mensaje en los últimos 60 s durante la rueda. Si no, se bloquean las entradas nuevas en todo el universo y el `PositionGuardian` conmuta automáticamente a polling REST cada 10 s.
  - **Frescura por símbolo con umbral calibrado:** Se toma la actualización más reciente entre último trade y último quote de IEX. El umbral de tolerancia para cada símbolo es $\max(3\text{ min}, \text{p99 del gap entre trades IEX de ese símbolo en esa franja horaria de los últimos 20 días})$, con un tope máximo de 10 min. Se recalcula automáticamente cada noche.
  - **Regla estricta para precios de entrada:** **Sin forward-fill para precios de entrada.** El forward-fill solo se permite para construir barras e indicadores de soporte, marcando esas barras como sintéticas (volumen 0). El precio de referencia de entrada es el midpoint del quote IEX si el spread bid-ask es $\le 10\text{ bps}$; si no, el último trade si está fresco dentro del umbral; si ninguno sirve, la señal se descarta con `reason_code=STALE_PRICE`.
  - **Chequeo cruzado de consistencia con SIP diferido:** Si el precio IEX actual difiere del último cierre SIP disponible en más de $2 \times \text{ATR}(5\text{m})$, se bloquea el símbolo por anomalía y se alerta con `reason_code=PRICE_ANOMALY`.
  - **Halts de mercado:** Suscripción al canal `statuses` del stream si está disponible en el tier gratuito para bloquear el símbolo de inmediato durante un halt. Si no está disponible, cualquier símbolo que supere su umbral calibrado de frescura se trata preventivamente como posible halt.
  - **Métricas en journal:** El journal registra la cantidad de señales descartadas por `STALE_PRICE` o `PRICE_ANOMALY` por símbolo para incluirlo en el reporte semanal y calibrar los umbrales con datos de campo.
- **Cache local** en Postgres de barras diarias (tabla `daily_bars`) y almacenamiento en Parquet para series intradía de backtest.
- **Rate limiter compartido** para Alpaca (≤ 180 req/min, con margen) y Finnhub, con backoff exponencial ante HTTP 429 o 5xx.

### 7.3 Calendario de mercado

Usar siempre el calendario y el reloj de Alpaca (feriados, cierres tempranos). Todos los horarios de jobs se expresan en ET y se recalculan cada día a partir del calendario. En la DB, todo se guarda en UTC.

---

## 8. Motor de decisión (pipeline)

### 8.1 Flujo por evaluación de estrategia

```text
[Job de estrategia disparado por el scheduler]
  1. Precondiciones globales: mercado abierto, kill switch apagado, sin pausa por
     pérdida diaria o semanal, datos frescos. Si falla alguna → registrar y salir.
  2. RegimeFilter.evaluate() → régimen actual (se cachea por día + actualización intradía).
  3. Para cada ticker habilitado y con dueño "bot" o "libre":
       EventFilter.blocked(ticker, now) → si está bloqueado, registrar el motivo y seguir.
  4. Strategy.generate(ctx) → lista de Signal.
  5. Para cada Signal con score ≥ min_signal_score:
       a. AIVeto.review(signal, features)  (según veto_mode)
       b. RiskGate.evaluate(signal, veto_result, portfolio_state) → RiskDecision
       c. si se aprueba → OrderRouter.submit(order_plan)
  6. Persistir todo en el journal (signals, veto_decisions, risk_decisions, orders).
```

### 8.2 Filtro de régimen

Se calcula con barras diarias SIP de SPY.

| Régimen | Condición (valores por defecto, configurables) |
|---|---|
| `BULL_CALM` | cierre de SPY > SMA200 y volatilidad realizada de 20 días < percentil 70 de 2 años |
| `BULL_VOLATILE` | cierre > SMA200 y volatilidad ≥ percentil 70 |
| `BEAR` | cierre ≤ SMA200 |
| `UNKNOWN` | datos insuficientes o error → tratar como bloqueante para entradas nuevas |

Cada estrategia declara los regímenes en los que puede operar.

### 8.3 Filtro de eventos

| Evento | Regla por defecto |
|---|---|
| Macro (FOMC, CPI, NFP) | Sin entradas nuevas desde 30 min antes hasta 30 min después del dato, para todo el universo. Días de FOMC: sin entradas nuevas de estrategias swing ese día. |
| Balance del ticker | Sin entradas nuevas desde 2 días hábiles antes. Las posiciones del bot en ese ticker se cierran al cierre del día hábil previo al reporte. |
| Apertura | Sin entradas de 9:30 a 10:00, salvo las estrategias que declaran `allows_open_window=True`. |
| Cierre | Sin entradas nuevas en los últimos 10 min, salvo las estrategias de cierre (momentum intradía). |
| Datos viejos o halt | Bloqueo del ticker afectado. |
| Ex-dividendo | Informativo: las posiciones swing no se cierran por esto, pero los indicadores usan barras ajustadas. |

**Fuentes de eventos:**

- Balances: Finnhub (`/calendar/earnings`), actualizado una vez por día a las 7:00 ET y cacheado. Si la consulta falla y no hay dato cacheado de menos de 3 días → el ticker queda bloqueado (falla cerrada).
- Macro: `config/macro_events_YYYY.yaml`, cargado a mano desde los calendarios oficiales de la Fed y el BLS. Si faltan eventos para los próximos 30 días, se envía una alerta al usuario.

### 8.4 Estrategias

Interfaz común:

```python
class Strategy(Protocol):
    id: str                        # "intraday_momentum_v1"
    version: str
    schedule: list[CronSpec]       # cuándo se evalúa (hora ET)
    allowed_regimes: set[Regime]
    allows_open_window: bool
    universe: list[str] | None     # None = universo habilitado general
    def generate(self, ctx: StrategyContext) -> list[Signal]: ...

@dataclass(frozen=True)
class Signal:
    signal_id: str                 # hash determinístico(strategy_id, version, symbol, bar_ts)
    strategy_id: str
    symbol: str
    side: Literal["buy"]           # solo largos
    entry_type: Literal["market", "limit"]
    entry_price_ref: Decimal       # precio de referencia al generar la señal
    limit_price: Decimal | None
    stop_price: Decimal
    take_profit_price: Decimal | None
    max_holding: timedelta | int   # minutos (intradía) o días hábiles (swing)
    exit_at_close: bool            # intradía: cerrar antes de las 15:59
    score: float                   # 0–1, definido por la estrategia (no es probabilidad)
    features: dict                 # indicadores usados, para el journal
    created_at: datetime
```

Estrategias iniciales. **Todos los parámetros se validan en backtest (fase 2) antes de habilitarlas en paper.**

#### S1 — `intraday_momentum` (prioridad alta)

- **Universo:** SPY, QQQ.
- **Base empírica:** el retorno de la primera media hora (cierre previo → 10:00) predice el de la última media hora.
- **Evaluación:** 15:30 ET.
- **Señal:** retorno desde el cierre previo hasta las 10:00 > `min_first_half_hour_ret` (por defecto 0) → comprar a las 15:30; en variante opcional, exigir también que el retorno 10:00→15:30 no sea negativo.
- **Salida:** market a las 15:58 ET (`exit_at_close=True`). Stop de protección en 1,5 × ATR(14) de barras de 5 minutos.
- **Parámetros a testear:** umbral de retorno, filtro de volatilidad (el efecto sería más fuerte en días volátiles), hora de entrada (15:20–15:45).
- **Régimen:** todos excepto `UNKNOWN`.

#### S2 — `mean_reversion_rsi2`

- **Universo:** todos los habilitados.
- **Evaluación:** 15:45 ET, con barras diarias SIP + precio IEX actual como cierre aproximado.
- **Señal:** precio > SMA200 diaria y RSI(2) diario < 10.
- **Salida:** cierre > SMA5, o RSI(2) > 70, o máximo 3 días hábiles. Stop en 1,5 × ATR(14) diario.
- **Régimen:** `BULL_CALM`, `BULL_VOLATILE`.

#### S3 — `trend_pullback`

- **Universo:** todos los habilitados.
- **Evaluación:** 15:45 ET.
- **Señal:** EMA20 > EMA50 diarias, el precio retrocede hasta la EMA20 (distancia ≤ 0,5 × ATR diario) y el cierre intradía queda por encima del mínimo del día anterior.
- **Salida:** TP en 2R, stop en 1,5 × ATR diario, máximo 5 días hábiles.
- **Régimen:** `BULL_CALM`.

#### S4 — `opening_range_breakout` (deshabilitada por defecto)

- **Universo:** SPY, QQQ.
- **Evaluación:** cada 5 min entre 9:35 y 11:30. `allows_open_window=True`.
- **Señal:** la primera barra de 5 min cierra alcista y el precio rompe su máximo, con volumen relativo (calculado sobre el histórico del mismo feed) > 1,5.
- **Salida:** stop en el mínimo del rango de apertura, TP en 2R o cierre del día.
- Solo se habilita si pasa la puerta de la fase 2.

#### S5 — `dual_momentum_leader` (Estrategia Principal para Superar al S&P 500)

- **Universo:** `SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA` (universo habilitado general).
- **Evaluación:** 15:45 ET.
- **Base empírica:** En mercados alcistas, la persistencia de fuerza relativa transversal (*cross-sectional momentum*) a 60 días supera significativamente a las estrategias de retroceso a la media, evitando el *cash drag* y capturando las mayores tendencias del mercado.
- **Señal:**
  1. Filtro macro de régimen: `SPY` sobre EMA50/SMA200 (`BULL_CALM`, `BULL_VOLATILE`). En mercado bajista (`BEAR`), mantiene 100% en efectivo para preservar capital.
  2. Tendencia local activa: precio por encima de su EMA(20) diaria.
  3. Ranking de momentum: se calcula el retorno a 60 días para todos los activos y se seleccionan los 2 activos líderes.
  4. Veto FinBERT: se descartan candidatos si `negative_share >= 0.35`.
- **Salida:** Sin Take Profit rígido (asimetría de retornos para dejar correr ganancias). Salida por Trailing Stop dinámico anclado a la EMA(20) o al cruce bajista tras 3 días de retención. Límite de retención de 20 a 40 días.
- **Régimen:** `BULL_CALM`, `BULL_VOLATILE`.
- **Evidencia Empírica (2025):** **+17.90% a +21.89%** vs **+15.70%** del S&P 500 (**+2.20% a +6.19% de Alpha**), Max Drawdown de solo **-6.94%** (inferior a los -9.80% de SPY) y Profit Factor de **2.55**.

**Nota sobre el loop de 5 minutos:** ya no hay un loop único que "consulta todo". Cada estrategia tiene su propio `schedule`. El ciclo de 5 minutos queda para el `PositionGuardian`, la guardia de datos, la ingesta de noticias y S4.

### 8.5 Veto de Riesgo y Noticias (Arquitectura en Dos Capas)

El sistema implementa una estricta separación de responsabilidades entre el procesamiento de lenguaje natural y la toma de decisiones algorítmicas:

```text
[Titulares en Lenguaje Natural] ──▶ [1. IA: FinBERT en ONNX] ──▶ {sentiment_mean, negative_share, top_topics}
                                                                               │
                                                                               ▼
                                                   [2. Motor Cuantitativo Determinista]
                                                                   (Código Puro)
                                                                               │
                                                    ┌──────────────────────────┴──────────────────────────┐
                                                    ▼                                                     ▼
                                             [CONFIRM / REDUCE]                                        [REJECT]
                                                    │                                                     │
                                                    ▼                                                     ▼
                                               [RiskGate]                                            [Descartada]
```

#### Capa 1: Procesamiento de Noticias con IA (FinBERT en ONNX)
- **Ámbito:** La IA se emplea exclusivamente donde aporta una ventaja real sobre reglas rígidas: comprensión de lenguaje natural no estructurado, matices de contexto financiero e ironía en titulares de prensa.
- **Función:** Procesa localmente en CPU cada titular/resumen y genera métricas estadísticas continuas en ventanas de 24h y 72h (`sentiment_mean`, `negative_share`, `top_topics`). Cero texto libre pasa a las etapas posteriores.

#### Capa 2: Motor de Decisión Cuantitativo Determinista (Código Puro)
- **Ámbito:** Una vez cuantificados los datos, **la decisión de riesgo sobre la señal no requiere un LLM**. Se ejecuta mediante un algoritmo determinista directo en microsegundos:
  - Si `negative_share >= 0.35` ──▶ `REJECT` con `reason_code=NEWS_NEGATIVE_CLUSTER`.
  - Si `"litigation"` en `top_topics` y `sentiment_mean < 0.0` ──▶ `REJECT` con `reason_code=EVENT_RISK`.
  - Si `"guidance"` en `top_topics` y `sentiment_mean < -0.15` ──▶ `REDUCE` con `size_multiplier=0.5`.
  - En condiciones normales o positivas ──▶ `CONFIRM` con `size_multiplier=1.0`.
- **Ventajas:** Latencia sub-milisegundo (0.001 ms vs 2,000 ms de una API externa), cero costo de tokens, cero riesgo de desconexión o cuota agotada, determinismo absoluto y testeabilidad completa en backtests históricos sin sesgos de memoria.

#### Capa 3: LLM Opcional y Modo Consultivo (Gemini Flash / Groq)
- **Modos de veto (`veto_mode`, configurable):**
  - `quantitative` (default): evaluación matemática instantánea sobre las métricas de FinBERT.
  - `advisory`: consulta adicional al LLM en paralelo con fines de auditoría y contraste, sin bloquear la ejecución.
  - `required`: delega la decisión final al LLM bajo política estricta de falla cerrada (`UNAVAILABLE` ante caídas).
  - `off`: omite el filtrado de noticias.
- **Generación de Reportes Diarios (16:30 ET):** Redacción en prosa del resumen ejecutivo del día para el usuario humano.

**Entrada para modo LLM (JSON, sin texto crudo de noticias):**

```json
{
  "signal": {"symbol": "QQQ", "strategy_id": "trend_pullback", "entry_ref": 512.3,
             "stop": 505.1, "take_profit": 526.7, "score": 0.71, "max_days": 5},
  "features": {"rsi14": 44.2, "atr14_pct": 1.4, "ema20_vs_50_pct": 1.8,
               "ret_5d_pct": -2.1, "rel_volume": 1.2},
  "regime": "BULL_CALM",
  "events": {"days_to_earnings": null, "macro_today": false},
  "news": {"n_24h": 6, "sentiment_mean": -0.12, "sentiment_min": -0.81,
           "negative_share": 0.33, "sources": ["reuters", "benzinga"],
           "top_topics": ["guidance", "regulation"]}
}
```

**Salida (JSON estricto, validado con Pydantic):**

```json
{
  "analysis": "texto breve (≤ 400 caracteres) con el razonamiento",
  "verdict": "CONFIRM",
  "size_multiplier": 1.0,
  "reason_code": "NEWS_NEGATIVE_CLUSTER"
}
```

- `verdict` ∈ {`CONFIRM`, `REJECT`, `REDUCE`}; `size_multiplier` ∈ [0.25, 1.0]. Con `CONFIRM`, el multiplicador se fuerza a 1.0.
- `reason_code` pertenece a un enum cerrado: `OK`, `NEWS_NEGATIVE_CLUSTER`, `EVENT_RISK`, `REGIME_MISMATCH`, `EXTENDED_MOVE`, `LOW_QUALITY_SETUP`, `OTHER`.
- El campo `analysis` va **antes** que `verdict` en el esquema, para que el modelo razone antes de decidir.

**Reglas para modo LLM:**
- Proveedor primario: Gemini Flash; respaldo: Groq. Timeout de 20 s por intento. Un reintento por proveedor.
- Cualquier error, timeout o esquema inválido → `VetoResult(status="UNAVAILABLE")`. En modo `required` equivale a `REJECT`.
- `QuotaTracker` por proveedor con alerta al superar el 90%.
- `temperature=0` o la menor que admita el modelo.

### 8.6 Noticias (patrón de doble modelo)

1. **Ingesta** cada 5 min durante la rueda y cada 30 min fuera de ella:
   - Alpaca News API y Finnhub `company-news` para los tickers del universo;
   - SEC EDGAR (filings 8-K) una vez por hora.
2. **Deduplicación** por URL canónica y hash de titular.
3. **Clasificación con FinBERT en ONNX Runtime** del titular (y resumen si existe) → `{positive, negative, neutral}` con score, utilizando inferencia optimizada en CPU/ARM64.
4. **Extracción determinista de `top_topics`:** Mediante taxonomía de palabras clave y reglas heurísticas sobre los titulares más las categorías provistas por Finnhub/Alpaca. Se mapean rápidamente a tags predefinidos (`earnings`, `guidance`, `litigation`, `m&a`, `fda_reg`, `macro`) sin invocar LLMs ni generar riesgo de inyección.
5. **Agregación** por ticker en ventanas de 24 h y 72 h (tabla `news_features`).
6. **El LLM de veto recibe solo `news_features`**, nunca texto libre de noticias. El texto crudo lo procesa únicamente FinBERT/ONNX en local sin acceso a herramientas externas.
7. **Lista blanca de fuentes** configurable; lo que viene de fuentes fuera de la lista se guarda pero no pondera.

### 8.7 RiskGate

**Modo de cuenta (`account_mode`):**
- `margin_no_leverage` (por defecto en paper y live): Permite rotación y reuso intradiario del capital sin esperar liquidación T+1, garantizando por software que el bot nunca use apalancamiento ni margen prestado (compra $\le$ efectivo real).
- `cash`: Opera exclusivamente con efectivo liquidado, con seguimiento riguroso del ciclo T+1 para evitar Good Faith Violations.
- Configurable en runtime vía DB y UI.

Evalúa en este orden y devuelve la primera falla, con su `reason_code`:

| # | Chequeo | Default |
|---|---|---|
| 1 | Kill switch apagado | — |
| 2 | Bot no pausado (pérdida diaria/semanal, rachas) | — |
| 3 | Ticker con dueño `bot` o libre (nunca `manual`) | — |
| 4 | Sin posición ni orden abierta del bot en el ticker | — |
| 5 | Posiciones del bot abiertas < `max_open_positions` | 4 |
| 6 | Posiciones en el mismo cluster < `max_positions_per_cluster` | 2 |
| 7 | Distancia al stop válida: 0,3 × ATR ≤ (entrada − stop) ≤ 3 × ATR | — |
| 8 | Relación TP/riesgo ≥ `min_reward_risk` (si hay TP) | 1.5 |
| 9 | Sizing calculable y ≥ monto mínimo (`min_order_notional`) | USD 10 |
| 10 | Exposición del bot + nueva orden ≤ capital asignado al bot | — |
| 11 | Nueva orden ≤ efectivo disponible de la cuenta (según `account_mode`, sin apalancamiento) | — |

**Sizing y asignación de cantidad:**

```text
capital_bot       = bot_allocation_pct × equity_de_la_cuenta (tomado a las 9:25 ET)
riesgo_usd        = risk_per_trade_pct × capital_bot × veto.size_multiplier
qty_teorica       = riesgo_usd / (entry_ref − stop_price)
notional          = min(qty_teorica × entry_ref, max_position_notional, disponible_bot)
qty               = notional / entry_ref

Regla de cantidad para órdenes del bot:
1. Si floor(qty) ≥ 1:
   → Usar floor(qty) acciones enteras con bracket nativo (camino "whole": GTC para swing, DAY para intradía).
2. Si floor(qty) == 0 y el riesgo de 1 acción entera ≤ max_risk_per_trade_pct_hard (default 1.0% del capital del bot):
   → Comprar 1 acción entera con bracket nativo (camino "whole").
3. Si no y allow_fractional_bot es true (default true, editable en UI):
   → Camino "fractional" como fallback (stop DAY diario + TP por software), registrado con path=fractional_fallback en el journal.
4. Si allow_fractional_bot es false:
   → Descartar la señal con reason_code=FRACTIONAL_NOT_ALLOWED.
```

**Cortacircuitos (evaluados sobre el P&L atribuible exclusivamente al bot):**

- **Pérdida diaria (-2% por defecto):** Si la pérdida diaria realizada + no realizada del bot alcanza `-daily_loss_limit_pct` (default 2% del capital del bot), se activa la pausa de nuevas entradas para el resto de la sesión. Las posiciones abiertas se mantienen gestionadas por sus stops y TPs normales.
- **Umbral de emergencia intradiario (-3.5%):** Si la pérdida del bot continúa empeorando y alcanza `-emergency_loss_limit_pct` (default 3.5%), se dispara un cierre forzado inmediato a mercado (market flatten) de todas las posiciones abiertas del bot.
- **Pérdida semanal (-5% por defecto):** Pausa hasta reactivación manual explícita desde la UI.
- **N pérdidas consecutivas (default 4):** Pausa de 1 día hábil.

### 8.8 Ejecución (OrderRouter)

**Mapeo de proxies en el universo:**
Para evitar que ETFs caros fuercen el camino fraccional, el universo define `signal_symbol` y `execution_symbol`:
- SPY → SPYM (~$70)
- QQQ → QQQM (~$210)
- GLD → GLDM (~$45)
- Acciones individuales: símbolo idéntico (ej. AAPL → AAPL).
Las señales e indicadores se calculan con el símbolo original; la orden se envía en el proxy. Los precios de stop y TP se trasladan proporcionalmente en términos porcentuales o en múltiplos de ATR del proxy. Ambos símbolos quedan registrados en el journal.

**`client_order_id`:** `{owner}-{signal_id[:24]}-{leg}`, con `owner` ∈ {`bot`, `man`} y `leg` ∈ {`entry`, `stop`, `tp`, `exit`, `stopd{YYYYMMDD}`}. Es determinístico: reenviar la misma orden no la duplica, porque Alpaca rechaza IDs repetidos y ese rechazo se trata como éxito idempotente.

**Camino "whole" (acciones enteras - máxima resiliencia):**

- Swing: bracket nativo en Alpaca con entrada market o limit, TP limit y stop, `time_in_force=gtc`.
- Intradía: bracket nativo con `time_in_force=day` y cierre forzado por el guardian a las 15:58 ET.

**Camino "fractional" (fallback y órdenes manuales):**

1. Entrada: market (o limit) `day`, con cantidad fraccional.
2. Al recibir el fill (evento `fill` del stream `trade_updates`): enviar orden **stop** `day` por la cantidad total al `stop_price`.
3. El take profit lo gestiona el `PositionGuardian` por software (evitando la restricción de acciones reservadas de Alpaca).
4. Cada día hábil a las 9:20 ET, el guardian reenvía los stops `day` de todas las posiciones fraccionales abiertas.
5. Para ejecutar un TP o salida por tiempo: cancelar el stop → confirmar cancelación → enviar venta market. Si la cancelación falla, alertar; nunca dejar la posición sin stop > 60 s.

**`BrokerAdapter`:** interfaz con dos implementaciones:

- `AlpacaBroker` (paper/live): el endpoint live solo se habilita si `LIVE_TRADING_ENABLED=true` **y** hay una confirmación explícita en la DB.
- `SimulatedBroker`: para replay y variantes sombra.

### 8.9 PositionGuardian

Corre de forma continua en el worker:

- Se suscribe a `trade_updates` (fills, cancelaciones, rechazos) y actualiza `orders` y `position_meta`.
- Cada 30 s durante la rueda:
  - revisa los TP por software de las posiciones fraccionales;
  - revisa los stops de respaldo (si el precio IEX está por debajo del stop y no existe orden stop activa → venta market inmediata y alerta);
  - revisa las salidas por tiempo y las de `exit_at_close`.
- 15:58 ET: cierra las posiciones intradía.
- Día previo a un balance: cierra las posiciones del bot en ese ticker.
- **Conciliación activa de tres capas con Alpaca REST (fuente de verdad):**
  1. *Capa inmediata (WebSocket):* Eventos en vivo vía stream `trade_updates` (fills, cancelaciones, rechazos) que actualizan de inmediato `orders` y `positions`.
  2. *Capa periódica (auditoría REST cada 60 s):* Bucle continuo durante la rueda comparando posiciones y órdenes activas contra la API REST de Alpaca para capturar eventos perdidos por desconexión o desincronización.
  3. *Capa de apertura y cierre (conciliación forzada):* Al arrancar el worker, a las 9:25 ET (pre-market antes de la apertura para resolver cualquier divergencia nocturna o de asignación) y a las 16:05 ET (post-market).
  - Toda posición en Alpaca sin metadatos en la DB se marca `owner=manual` y `unmanaged=True`, se alerta y **nunca** se toca automáticamente por el bot.
  - Toda discrepancia en cantidad o estado de órdenes se actualiza tomando a Alpaca como verdad absoluta y registrando la incidencia en `audit_log`.
- Si el WebSocket se desconecta: reconexión automática con backoff; mientras tanto, las consultas REST de órdenes pasan a cada 10 s.

### 8.10 Optimización de Rentabilidad y Benchmark contra el S&P 500 (+15.70% en 2025)

A fin de garantizar que el bot supere la rentabilidad de una inversión pasiva en el S&P 500 (+15.70% en 2025), se ejecutó una campaña de optimización cuantitativa sistemática sobre todo el histórico disponible (`SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`) y las 4.280 noticias procesadas con FinBERT.

#### Diagnóstico del Rendimiento de S3
- **Rendimiento:** +4.17% a +5.16% (frente a +15.70% de SPY y +37.13% de NVDA).
- **Causas Raíz:**
  1. *Cash Drag:* Riesgo de 0.5% con max 4 posiciones dejó 65%–75% en efectivo al 0%.
  2. *Corte Prematuro:* Take Profit rígido de 2.0R liquidaba ganadores tras solo +2.5% a +3.5%.
  3. *Antipatrón de Retroceso:* En megatendencias, los líderes cotizan permanentemente sobre la EMA(20) sin retroceder.

#### Opciones Evaluadas
1. **S3 Optimizada (Sizing 1.0% + Trailing EMA20 + FinBERT):** Obtuvo de -4.17% a +0.94%, insuficiente para batir al índice.
2. **Camino 2: Core-Satellite Híbrido (70% S5 / 30% S3):** Obtuvo **+12.05%** (Alpha negativo de **-3.65%**), ya que el satélite del 30% diluye la rentabilidad.
3. **Camino 1: S5 Dual Momentum Leader Puro + FinBERT:** Obtuvo **+17.90% a +21.89%** (**Alpha de +2.20% a +6.19%** vs SPY), con un Max Drawdown de solo **-6.94%** (vs -9.80% de SPY) y Profit Factor de **2.55**.

#### Decisión
Se adoptó **Camino 1 (S5)** como la estrategia primaria de Alpha del sistema. Documentación detallada en [`docs/BENCHMARK_OPTIMIZATION_ANALYSIS.md`](file:///d:/Github%20Repositories/Trading-Bot/docs/BENCHMARK_OPTIMIZATION_ANALYSIS.md).

---

## 9. Operación manual, buckets y aprobaciones

### 9.1 Buckets de capital

- `bot_allocation_pct` (default 50%) define el capital máximo del bot. El resto es manual.
- **Exposición del bot** = valor de mercado de sus posiciones + nocional de sus órdenes de compra abiertas.
- **Operación manual:** si supera su bucket, la UI muestra una advertencia pero permite continuar tras una confirmación. La cuenta es del usuario.
- **El bot, en cambio, nunca supera su bucket** (RiskGate, chequeo 10).

### 9.2 Ownership por ticker

- Tabla `ticker_ownership(symbol, owner ∈ {bot, manual, free}, since, reason)`.
- Una orden manual sobre un ticker libre lo pasa a `manual`. Una posición del bot lo pasa a `bot`. Al cerrarse la posición y las órdenes, vuelve a `free`.
- **Orden manual sobre un ticker del bot:** la UI avisa y ofrece dos opciones: (a) cancelar, o (b) "tomar control", que transfiere la posición del bot a manual (se cancelan las órdenes del bot y el usuario define su propio stop).
- **El bot quiere operar un ticker manual:**
  - si `manual_override_approval=off` (default), la señal se descarta con `reason_code=OWNED_BY_MANUAL`;
  - si está en `on`, se crea una solicitud de aprobación por email (9.4).

### 9.3 Ticket de orden manual (UI → intent)

Campos:

- símbolo;
- cantidad **o** monto en USD;
- tipo: market o limit (con precio límite);
- stop loss (precio o %);
- take profit (precio o %);
- máximo de días de tenencia (opcional);
- "permitir que el bot proponga acciones sobre esta posición" (sí/no).

La API valida y crea un `intent` de tipo `MANUAL_ORDER`; el worker lo ejecuta con el mismo OrderRouter (whole o fractional), con `owner=man`. El PositionGuardian aplica a las posiciones manuales el stop, el TP y el límite de días que definió el usuario.

### 9.4 Aprobación por email

1. Se crea `approval_request` con: acción propuesta, motivo, precio de referencia, expiración (default 15 min) y un token HMAC-SHA256 de un solo uso (secreto en `.env`).
2. Email con un resumen y un enlace a `https://<dominio>/approvals/{id}?t={token}`. El enlace pasa igual por Cloudflare Access, así que requiere login.
3. La UI muestra el detalle con los botones **Aprobar** y **Rechazar**. Al aprobar, se vuelve a correr el RiskGate con el estado actual, y si el precio se movió más de 1 ATR desde la solicitud, la aprobación se invalida.
4. **Sin respuesta antes de la expiración → se rechaza.** Nunca se ejecuta por silencio.

---

## 10. Modelo de datos (tablas principales)

Todas incluyen `id`, `created_at` y `updated_at` (UTC). Los montos van en `NUMERIC(18,6)`, nunca en float.

| Tabla | Campos clave |
|---|---|
| `runtime_config` | key, value (JSONB), version, updated_by, valid_range (JSONB) |
| `config_history` | key, old_value, new_value, changed_at, source |
| `universe` | symbol, enabled, cluster, fractionable, notes |
| `ticker_ownership` | symbol (PK), owner, since, reason |
| `daily_bars` | symbol, date, o, h, l, c, v, adjusted (bool), feed |
| `regime_snapshots` | date/ts, regime, inputs (JSONB) |
| `events` | type (earnings/macro/exdiv), symbol (nullable), ts_et, source, fetched_at |
| `news_items` | id, symbol, source, url, headline, summary, published_at, finbert_label, finbert_score, whitelisted |
| `news_features` | symbol, window, ts, n, sentiment_mean, sentiment_min, negative_share, sources (JSONB) |
| `signals` | signal_id (unique), strategy_id, strategy_version, symbol, payload (JSONB), score, variant |
| `llm_calls` | provider, model, prompt_version, request (JSONB), response_raw, latency_ms, tokens_in/out, status |
| `veto_decisions` | signal_id, variant, mode, verdict, size_multiplier, reason_code, llm_call_id |
| `risk_decisions` | signal_id, variant, approved, reason_code, sizing (JSONB), portfolio_state (JSONB) |
| `orders` | client_order_id (unique), broker_order_id, owner, signal_id, signal_symbol, execution_symbol, leg, type, tif, qty, notional, limit/stop, status, submitted_at, filled_qty, avg_fill_price |
| `position_meta` | symbol, execution_symbol, owner, strategy_id, entry_price, stop_price, tp_price, max_holding, opened_at, path (whole/fractional_fallback), exit_at_close, unmanaged |
| `trades` | posición cerrada: owner, strategy, entrada, salida, qty, pnl, pnl_r, fees_est, slippage_est, exit_reason |
| `equity_snapshots` | ts, equity, cash, bot_exposure, manual_exposure, pnl_day_bot, pnl_day_manual |
| `intents` | type, payload, status (pending/processing/done/failed), result, created_by |
| `approvals` | request (JSONB), token_hash, expires_at, status, decided_at |
| `alerts` | type, severity, message, sent_at, channel |
| `reports` | period (daily/weekly/monthly), period_start, content_md, metrics (JSONB), sent_at |
| `quota_usage` | provider, date, minute_bucket, count |
| `audit_log` | actor (user/bot/system), action, details (JSONB) |

---

## 11. Parámetros configurables (defaults y rangos)

Se definen en `config/defaults.yaml` y se copian a `runtime_config` en la primera ejecución. La UI solo permite valores dentro del rango.

| Parámetro | Default | Rango | Descripción |
|---|---|---|---|
| `account_mode` | margin_no_leverage | margin_no_leverage / cash | modo de cuenta Alpaca y liquidación |
| `bot_allocation_pct` | 50 | 0–100 | % del equity para el bot |
| `risk_per_trade_pct` | 0.5 | 0.25–1.0 | % del capital del bot arriesgado por operación |
| `max_risk_per_trade_pct_hard` | 1.0 | 0.5–2.0 | riesgo máximo tolerado para redondear a 1 acción entera |
| `allow_fractional_bot` | true | true/false | habilitar fallback a fraccionales para el bot |
| `max_position_notional` | 400 | 20–2000 | tope USD por posición |
| `min_order_notional` | 10 | 1–100 | mínimo USD por orden |
| `max_open_positions` | 4 | 1–8 | posiciones simultáneas del bot |
| `max_positions_per_cluster` | 2 | 1–3 | por cluster de correlación |
| `min_reward_risk` | 1.5 | 1.0–4.0 | TP mínimo en múltiplos de R |
| `atr_stop_multiple` | 1.5 | 1.0–2.5 | por defecto, sobreescribible por estrategia |
| `min_signal_score` | 0.5 | 0–1 | **"nivel de exigencia"** del bot sobre sus propias señales |
| `veto_mode` | required | off/advisory/required | uso del veto de IA |
| `daily_loss_limit_pct` | 2 | 0.5–5 | pausa de nuevas entradas por pérdida diaria |
| `emergency_loss_limit_pct` | 3.5 | 2.5–7.0 | salida de emergencia (market flatten de todas las posiciones del bot) |
| `weekly_loss_limit_pct` | 5 | 2–10 | pausa semanal |
| `max_consecutive_losses` | 4 | 2–10 | enfriamiento |
| `macro_block_minutes` | 30 | 0–120 | ventana alrededor de datos macro |
| `earnings_block_days` | 2 | 0–5 | días hábiles antes de un balance |
| `manual_override_approval` | off | on/off | permitir que el bot pida operar tickers manuales |
| `approval_expiry_minutes` | 15 | 5–60 | |
| `opportunity_alert_min_score` | 0.8 | 0.5–1 | umbral para alertar por email |
| `max_alerts_per_day` | 3 | 0–10 | |
| `llm.gemini.model` / `llm.groq.model` | configurable | — | nombres de modelo |
| `llm.*.rpm` / `llm.*.rpd` | configurable | — | cuotas vigentes (leer en AI Studio / Groq) |

**Presets de "nivel de seguridad" en la UI** (atajos que setean varios parámetros a la vez):

- **Conservador:** riesgo 0,25%, máximo 2 posiciones, `min_signal_score` 0,7, veto `required`.
- **Normal:** los defaults.
- **Agresivo (limitado):** riesgo 1%, máximo 6 posiciones, `min_signal_score` 0,4. Nunca supera los rangos.

La "confianza" del LLM **no** se usa como umbral, porque no está calibrada. El nivel de exigencia se controla con `min_signal_score` (determinista) y con `veto_mode`.

---

## 12. Universo inicial

`config/universe.yaml`:

| Símbolo señal | Símbolo ejecución | Cluster | Estado | Notas |
|---|---|---|---|---|
| SPY | SPYM | `index_broad` | habilitado | Proxy de bajo coste (~$70) para acciones enteras |
| QQQ | QQQM | `tech` | habilitado | Proxy de bajo coste (~$210) para acciones enteras |
| GLD | GLDM | `gold` | habilitado (diversificación) | Proxy de bajo coste (~$45) |
| AAPL | AAPL | `tech` | habilitado | |
| GOOGL | GOOGL | `tech` | habilitado | |
| NVDA | NVDA | `semis` | habilitado | |
| MU | MU | `semis` | habilitado | |
| LRCX | LRCX | `semis` | habilitado | |
| IWM | IWM | `index_smallcap` | habilitado (diversificación) | |
| XLE | XLE | `energy` | habilitado (diversificación) | |
| XLV | XLV | `healthcare` | habilitado (diversificación) | |
| TLT | TLT | `bonds` | habilitado (diversificación) | |
| ABAT | — | — | **excluido** | baja liquidez, volatilidad extrema |

Los clusters se revisan cada mes con la correlación rodante de 60 días de retornos diarios. El reporte mensual sugiere cambios de cluster si la correlación con otro cluster supera 0,7, pero **no** los aplica solo.

---

## 13. Interfaz web

Pantallas:

1. **Dashboard:** equity, P&L del día y de la semana (bot, manual, total), buckets (uso vs límite), estado del bot (activo, pausado y motivo, kill switch), régimen actual, próximos eventos (48 h), últimas alertas.
2. **Posiciones:** tabla con dueño, estrategia, entrada, stop, TP, días abiertos, P&L, camino (whole/fractional) y estado de protección (stop activo sí/no). Acciones: cerrar, editar stop/TP (solo manuales), tomar control.
3. **Ticker:** gráfico `lightweight-charts` (velas diarias y de 5 min) con líneas de entrada, stop y TP, marcas de señales y noticias recientes con su sentimiento.
4. **Operar:** ticket de orden manual (9.3), con vista previa del riesgo en USD antes de confirmar.
5. **Órdenes e historial:** órdenes abiertas y cerradas, trades con P&L en R.
6. **Decisiones (journal):** cada señal con su cadena señal → veto → riesgo → orden, con los `reason_code`. Filtros por estrategia, variante y resultado.
7. **Configuración:** presets de seguridad, parámetros de la sección 11, universo y clusters, estrategias on/off, email, modo de veto.
8. **Aprobaciones:** pendientes e historial.
9. **Reportes:** diarios, semanales y mensuales (render de Markdown), métricas por estrategia y variante.
10. **Sistema:** salud de servicios, último ping, uso de cuotas LLM, errores recientes, **kill switch** (con confirmación) y botón "cerrar todas las posiciones del bot".

Toda acción que envía órdenes o cambia el riesgo pide confirmación explícita y queda en `audit_log`.

---

## 14. Notificaciones y reportes

- **Diario** (16:30 ET): P&L, trades, señales generadas/vetadas/rechazadas con motivos, estado de posiciones, eventos del día siguiente, resumen de sentimiento por ticker y titulares destacados (con enlace; se muestran a la persona, no al modelo que decide).
- **Semanal** (sábado 10:00 ET) y **mensual** (primer sábado del mes): métricas por estrategia y por variante (sección 15.3) contra las referencias, drawdown, uso de cuotas y sugerencias de clusters.
- **La narrativa del reporte** puede generarla el LLM a partir de las métricas estructuradas. Si no hay cuota, se usa una plantilla. El LLM de reportes no tiene ninguna capacidad de operar.
- **Alertas inmediatas por email:**
  - oportunidad: señal con score ≥ `opportunity_alert_min_score`, régimen favorable y sentimiento no negativo, con un tope diario;
  - kill switch o pausa activada;
  - posición sin stop más de 60 s;
  - error de conciliación;
  - worker caído (vía Healthchecks.io);
  - cuota de LLM agotada;
  - faltan eventos macro cargados.

---

## 15. Validación: backtest, replay y variantes

### 15.1 Motor de replay (oficial)

- Reutiliza el `Strategy`, el `RiskGate` y el `OrderRouter` de producción, con un `Clock` simulado y el `SimulatedBroker`.
- **Datos:** barras SIP (diarias y de 5 min), desde 2018 o lo más atrás que permita la API, incluyendo obligatoriamente 2020 y 2022.
  - Ingestor primario: API de Alpaca SIP con rate limiting y cache local en Parquet/Postgres.
  - Fallback: Loader para importar archivos CSV externos si la API gratuita de Alpaca limita la profundidad de barras intradía de 5 minutos previas a 2020.
- **Modelo de fills:**
  - market = apertura de la barra siguiente + slippage;
  - stop = el peor entre el precio stop y la apertura (gaps);
  - limit = solo si el precio lo cruza.
- **Costos:**
  - slippage base: 2 bps en SPY/QQQ/SPYM/QQQM, 5 bps en acciones, configurable;
  - medio spread estimado;
  - tarifas regulatorias en las ventas (SEC fee y FINRA TAF, parametrizadas porque cambian cada año fiscal).
- **Veto de IA:** no se backtestea (sesgo de anticipación). En replay, el veto está en `off`.
- **Walk-forward:** ventanas de optimización de 2 años y de prueba de 6 meses, móviles. Se reporta solo el resultado fuera de muestra.
- Se registra la **cantidad de combinaciones de parámetros probadas** por estrategia, que es necesaria para el DSR.
- **Puerta de aprobación independiente por estrategia:** Cada estrategia se evalúa y aprueba de forma independiente. Si una estrategia no supera el DSR ≥ 0,90 o el MaxDD ≤ 15%, se descarta o congela sin bloquear el avance a paper de las estrategias que sí hayan aprobado.

### 15.2 Métricas

Operaciones, tasa de acierto, expectativa por operación (en R y en USD), profit factor, retorno anualizado, volatilidad, Sharpe, **Deflated Sharpe Ratio** (Bailey y López de Prado), drawdown máximo y duración, exposición promedio, slippage estimado vs real (en paper), y comparación con SPY buy & hold y con cash.

### 15.3 Variantes en paper trading

- **A1 — `engine_only`:** motor puramente cuantitativo (S1/S3) + riesgo, veto de noticias `off`.
- **A2 — `engine_plus_quant_news`:** motor cuantitativo + IA FinBERT para procesamiento de noticias + Veto cuantitativo determinista en código puro (`veto_mode=quantitative`).
- **B — `engine_plus_llm_veto`:** motor cuantitativo + veto de decisión delegado a LLM (`veto_mode=required`).
- **C — `spy_buy_hold`:** referencia pasiva calculada, no se opera.

Implementación:

- Si Alpaca permite más de una cuenta paper por usuario, las variantes operan en cuentas separadas.
- Si no, la variante principal opera en la cuenta paper y las demás corren en sombra con el `SimulatedBroker`, alimentadas con los mismos datos en vivo.
- Todas las variantes consumen **exactamente las mismas señales** (mismo `signal_id`) y se registran con el campo `variant`.

---

## 16. Seguridad y operación

**Acceso:**

- Cloudflare Tunnel + Cloudflare Access con política "solo el email del usuario" (OTP).
- La API valida el JWT de Access (header `Cf-Access-Jwt-Assertion`) para que no se pueda saltear el túnel. En desarrollo local, se provee un modo bypass para localhost.
- Puertos públicos de la VM: solo SSH con clave (sin contraseña), con `fail2ban` y actualizaciones automáticas de seguridad.

**Secretos:**

- Van en `.env` con permisos 600: claves de Alpaca paper y live separadas, Gemini, Groq, Finnhub, SMTP, `APPROVAL_HMAC_SECRET`, credenciales de Postgres.
- `LIVE_TRADING_ENABLED=false` por defecto.

**Operación de la base de datos y los logs:**

- Postgres no expone puertos fuera de la red de Docker.
- Backups diarios con `pg_dump` a Oracle Object Storage (tier gratuito), con retención de 30 días. Probar la restauración una vez por mes.
- Logs JSON con rotación (driver de Docker con `max-size`).

**Monitoreo:**

- El worker hace ping a Healthchecks.io cada minuto.
- Si no hay ping durante 5 min en horario de mercado → email.
- `restart: unless-stopped` en todos los servicios.

**Riesgo de Oracle Always Free:** Oracle puede recuperar instancias con muy poco uso. Mitigación a verificar (sección 19): pasar la cuenta a Pay As You Go sin salir de los límites gratuitos. Además, mantener un script de redespliegue (`make deploy`) que levante todo desde cero en menos de 30 min en otra VM.

**Horario:** los jobs se definen en ET y dependen del calendario de Alpaca. El servidor corre en UTC.

---

## 17. Fases de implementación

### Fase 0 — Esqueleto e infraestructura local
**Tareas:**
- Repo Git, `pyproject.toml` (dependencias base ligeras, extras `[nlp]` aisladas), Docker Compose (postgres, api, worker, web), Alembic, `structlog`, `Clock` inyectable, settings tipados desde `.env`, carga de `defaults.yaml` a `runtime_config`, Makefile, CI local con `pytest` y `ruff`.

**Aceptación:**
- `make up` levanta todo; `/health` responde.
- La migración inicial crea todas las tablas de la sección 10 (incluyendo `signal_symbol`, `execution_symbol` y nuevos parámetros).
- Existe un test que verifica que ningún módulo usa `datetime.now()` directo (búsqueda en el código).

### Fase 1 — Datos, indicadores, calendario y régimen
**Tareas:**
- `AlpacaProvider` con política híbrida de feeds, guardia de datos viejos de 2 niveles (heartbeat 60s y gap p99 por símbolo), cache de barras diarias en Postgres, rate limiter, calendario y reloj.
- Indicadores: EMA, SMA, RSI (Wilder), ATR (Wilder), retornos, volatilidad realizada, volumen relativo. Barras sintéticas con forward-fill explícito y volumen 0.
- `RegimeFilter`, `EventCalendar` (Finnhub + YAML macro).

**Aceptación:**
- Los indicadores coinciden con valores de referencia (tolerancia 1e-6) en fixtures.
- Las consultas SIP con `end` reciente se recortan solas a `now − 16 min`.
- La guardia de datos viejos y detección de anomalía de precio (> 2 ATR contra SIP) funcionan con reloj simulado.
- Un feriado y un cierre temprano del calendario se manejan bien en los tests.

### Fase 2 — Estrategias y replay (puerta de decisión)
**Tareas:**
- Interfaz `Strategy` con declaración de `data_requirements`, S1–S4, `SimulatedBroker`, `ReplayEngine`, ingestor SIP con cache en Parquet y loader fallback para CSV externos, modelo de costos, métricas, DSR, walk-forward y el comando `make backtest STRATEGY=...` que genera un reporte en Markdown.

**Aceptación:**
- El replay es determinista: dos corridas dan el mismo resultado.
- Hay un test de "sin mirar al futuro": una estrategia nunca accede a barras con timestamp > reloj.
- Existe un reporte por estrategia con métricas fuera de muestra.

**Puerta y Resolución:** Evaluación **independiente por estrategia**. La estrategia S3 (Trend Pullback) arrojó rendimientos insuficientes (-1.52% a +0.94% vs +15.70% del S&P 500) debido a *cash drag* estructural y compra en retrocesos en mercados tendenciales. Se diseñó, implementó y validó la **Estrategia S5 v1.1.0 (Dual Momentum Leader Multi-Sectorial)** con rotación sobre 12 activos (`SPY`, `QQQ`, `AAPL`, `MSFT`, `NVDA`, `AMZN`, `META`, `GOOGL`, `JPM`, `LLY`, `XOM`, `COST`), logrando **+66.54% de retorno anual (Alpha +50.84% sobre SPY)**, **Sharpe 2.73**, **Max Drawdown de -10.65%** y **Profit Factor de 3.82**. S5 v1.1.0 superó la puerta de decisión con los mejores estándares cuantitativos y avanzó como motor primario de Alpha a la Fase 3 (Paper Trading).

### Fase 3 — Riesgo, ejecución y guardian (paper)
**Tareas:**
- `RiskGate` completo (`account_mode: margin_no_leverage | cash`), sizing con regla de proxies (SPYM/QQQM/GLDM) y acciones enteras prioritarias, cortacircuitos escalonados (-2% pausa, -3.5% flatten de emergencia), ownership, ledger de buckets.
- `OrderRouter` (camino whole con brackets nativos GTC/DAY y camino fractional_fallback condicionado a `allow_fractional_bot`), `AlpacaBroker`.
- `PositionGuardian`: stream `trade_updates`, TP por software, reenvío diario de stops a las 9:20 ET, conciliación activa de tres capas (stream inmediato, auditoría periódica cada 60 s, conciliación forzada al arranque y 9:25 ET), kill switch por CLI y API.

**Aceptación:**
- Tests de propiedades (`hypothesis`): el sizing nunca supera el bucket, el tope nocional ni el efectivo; el riesgo en USD nunca supera el configurado.
- Tests con broker simulado: reenviar una orden no la duplica; ninguna posición queda sin stop más de 60 s (simulando fallos de cancelación y desconexión).
- **Prueba manual en paper** documentada en `docs/paper_checklist.md`:
  - bracket nativo de acciones enteras en proxy (SPYM/QQQM);
  - entrada fraccional + stop diario reenviado a la mañana siguiente;
  - TP por software;
  - salida por tiempo;
  - conciliación activa: una posición creada a mano desde la web de Alpaca se marca como `owner=manual` y `unmanaged=True` sin ser tocada.

### Fase 4 — Scheduler de producción y modo sombra
**Tareas:**
- Scheduler con jobs por estrategia, guardian, ingesta y reportes mínimos.
- Modo `shadow`: pipeline completo en vivo sin enviar órdenes.

**Aceptación:**
- 5 días hábiles seguidos en sombra sin excepciones no manejadas.
- El journal está completo para cada evaluación (incluyendo path y descartes por `STALE_PRICE`/`PRICE_ANOMALY`).
- El ping de Healthchecks funciona.

### Fase 5 — API y UI (lectura + configuración + kill switch)
**Tareas:**
- Endpoints de solo lectura, configuración con validación de rangos (incluyendo `account_mode`, `allow_fractional_bot`, `max_risk_per_trade_pct_hard`), presets, kill switch y "cerrar todo del bot".
- Pantallas 1, 2, 3, 5, 6, 7 y 10.

**Aceptación:**
- Un cambio de configuración en la UI se refleja en el worker en ≤ 5 s y queda en `config_history`.
- Un valor fuera de rango se rechaza con error claro.
- El kill switch bloquea entradas nuevas en ≤ 5 s.

### Fase 6 — Operación manual, ownership y aprobaciones
**Tareas:**
- Ticket manual, intents, reglas de ownership, "tomar control", pantalla 4, aprobaciones exclusivamente por email con tokens HMAC (sección 9.4), pantalla 8.

**Aceptación:**
- Una orden manual con stop, TP y límite de días se ejecuta y el guardian la gestiona.
- El bot nunca envía órdenes sobre un ticker `manual` sin una aprobación válida.
- Una aprobación vencida o con token inválido se rechaza.
- Una aprobación con precio movido más de 1 ATR se invalida.

### Fase 7 — Noticias, FinBERT (ONNX) y veto de IA
**Tareas:**
- Ingesta de noticias, dedupe, clasificación de titulares con `ProsusAI/finbert` en ONNX Runtime (`onnxruntime` en extras `[nlp]`), extracción determinista de `top_topics` por heurísticas de palabras clave y categorías, tabla `news_features`.
- `AIVeto` con Gemini Flash (primario) y Groq (respaldo), `QuotaTracker`, prompts versionados, modos `off`/`advisory`/`required`.
- Variantes A y B (sección 15.3).

**Aceptación:**
- Ningún texto de noticia llega al prompt de veto (test que inspecciona el JSON serializado).
- Con los proveedores caídos (mock), el resultado es `UNAVAILABLE` y la señal se rechaza en modo `required`.
- Un JSON inválido del LLM nunca produce una orden.
- El consumo diario de LLM queda dentro de las cuotas configuradas en una simulación de un día típico.

### Fase 8 — Reportes y alertas
**Tareas:**
- Reportes diario (16:30 ET), semanal y mensual por Gmail SMTP; alertas críticas inmediatas; pantalla 9.
- El reporte semanal detalla el % de operaciones por camino (`whole` vs `fractional_fallback`) y los descartes por frescura.

**Aceptación:**
- Los reportes se generan con datos reales de paper, se envían por email y se ven en la UI.
- El tope diario de alertas se respeta.

### Fase 9 — Endurecimiento y preparación para live
**Tareas:**
- Despliegue en Oracle con Cloudflare Tunnel y Access, backups y restauración probada, runbook (`docs/runbook.md`: cómo reiniciar, cómo pausar, cómo restaurar, qué hacer si una posición queda sin stop), revisión de seguridad.

**Aceptación:**
- Un redespliegue desde cero funciona siguiendo el runbook.
- La restauración de backup está probada.
- Los criterios de paso a live (sección 18) se evalúan en un reporte automático.

---

## 18. Criterios para pasar a dinero real

Todos deben cumplirse. Los evalúa un reporte y los confirma el usuario.

1. ≥ 150 operaciones cerradas en paper por la variante que se quiere llevar a live, o ≥ 3 meses de paper (lo que ocurra último).
2. Expectativa neta positiva después de costos y DSR ≥ 0,90 sobre el período de paper.
3. Drawdown máximo en paper ≤ 15% del capital del bot.
4. La variante B supera a la A en retorno ajustado por riesgo; si no, se lleva la A a live y se descarta el veto.
5. Cero incidentes de posición sin stop no resueltos y cero órdenes duplicadas en el período.
6. **Inicio en live:** `risk_per_trade_pct` al 50% del valor usado en paper durante el primer mes.

---

## 19. Puntos de verificación y estado tras la entrevista de diseño

1. **Tipo de cuenta en Alpaca:**
   - **Resuelto en diseño:** Se implementan ambos modos (`account_mode: margin_no_leverage | cash`) con un switch configurable en runtime. El modo por defecto para paper trading es `margin_no_leverage` (evita bloqueos T+1 y el software garantiza 0 apalancamiento). En modo cash se rastrea estrictamente la liquidación T+1.
2. **Órdenes stop fraccionales time_in_force=day antes de las 9:30 ET:**
   - Verificar en Fase 3 en paper que las órdenes stop fraccionales enviadas a las 9:20 ET queden correctamente activas al abrir la rueda.
3. **Múltiples cuentas paper en Alpaca:**
   - Si no es posible tener dos cuentas, la variante B opera en paper y la variante A en sombra con `SimulatedBroker`.
4. **Endpoints de Finnhub gratuitos:**
   - Verificar en Fase 1 `/calendar/earnings` y `/company-news` y límites de 60 req/min.
5. **Cuotas de Gemini Flash y Groq:**
   - Configurar dinámicamente en `runtime_config` en Fase 7 leyendo cuotas actuales del dashboard.
6. **Profundidad histórica de barras SIP de 5 min en plan gratuito:**
   - **Resuelto en diseño:** El ingestor utiliza Alpaca SIP con cache en Parquet/Postgres y soporta un loader fallback para importar archivos CSV externos si la API gratuita no cubre hasta 2018.
7. **Sentimiento y huella en ARM64 / VM:**
   - **Resuelto en diseño:** Se utiliza `onnxruntime` + `tokenizers` en vez de PyTorch, reduciendo el consumo de RAM de ~1.5GB a ~200MB. Empaquetado en dependencias opcionales `[nlp]` para no ralentizar Fases 0–6.
8. **Política de Oracle Always Free:**
   - Mitigación planificada: pasar a Pay As You Go manteniendo recursos dentro del tier gratuito y mantener `make deploy` para recuperación en < 30 min.
9. **Tarifas regulatorias SEC fee y FINRA TAF:**
   - Parametrizadas en configuración para actualizar anualmente.
10. **Proxies de bajo coste unitario:**
   - **Resuelto en diseño:** Confirmado cambio de SPLG a SPYM (oct 2025). El universo mapea `signal_symbol` (SPY, QQQ, GLD) a `execution_symbol` (SPYM, QQQM, GLDM) para permitir acciones enteras con brackets nativos GTC.

---

## 20. Glosario rápido

- **ATR:** rango verdadero promedio; mide la volatilidad típica de una barra.
- **R:** riesgo inicial de una operación (entrada − stop). "2R" = ganar el doble de lo arriesgado.
- **Bracket:** orden de entrada con take profit y stop adjuntos en el broker.
- **SIP / IEX:** feed consolidado de todas las bolsas / feed de una sola bolsa (IEX).
- **DSR (Deflated Sharpe Ratio):** Sharpe corregido por la cantidad de pruebas realizadas y por la no normalidad de los retornos.
- **Walk-forward:** optimizar en una ventana del pasado y evaluar en la siguiente, avanzando en el tiempo.
- **Modo sombra:** el sistema decide en vivo pero no envía órdenes.
- **Falla cerrada:** ante la duda o un error, no operar.
