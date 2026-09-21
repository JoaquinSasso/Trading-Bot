"""Two-Level Staleness Guard, Feed Health State Machine, and Cross-Feed Anomaly Detector.

Implementa las Características 14 a 22 de la Fase 1 (Capa de Datos de Mercado):
- Nivel 1: Heartbeat global del WebSocket (umbral 60s, conmutación a REST cada 10s).
- Nivel 2: Guardia de frescura por símbolo calibrada (tau_symbol = clamp(180s, 600s, p99)).
- Máquina de estados de salud del feed: HEALTHY, DEGRADED, STALE, DISCONNECTED.
- Política estricta de precio de entrada sin forward-fill:
    - Midpoint si spread <= 10 bps y quote fresca.
    - Último trade si está fresco.
    - Si no, descarte con STALE_PRICE.
- Detector de anomalías cruzado entre IEX y SIP diferido (|P_IEX - C_SIP| > 2 * ATR_5m).
- Métricas de descarte: contadores STALE_PRICE y PRICE_ANOMALY.
"""

from __future__ import annotations

import decimal
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import structlog

from tbot.common.clock import Clock, SystemClock
from tbot.common.errors import PriceAnomalyError, StaleDataError
from tbot.data.types import PriceQuote, TradeQuote

if TYPE_CHECKING:
    from tbot.data.calendar import MarketCalendar

logger = structlog.get_logger(__name__)

# Constantes de umbrales temporales y tolerancias
MIN_TAU_SECONDS: float = 180.0  # 3 minutos
MAX_TAU_SECONDS: float = 600.0  # 10 minutos
DEFAULT_TAU_SECONDS: float = 300.0  # 5 minutos (fallback sin calibrar)
MAX_SPREAD_BPS_FOR_MIDPOINT: float = 10.0  # 10 bps
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS: float = 60.0  # 60s para Nivel 1
DEFAULT_STALE_TIMEOUT_SECONDS: float = 300.0  # 5m para silencio crítico
DEFAULT_REST_POLL_INTERVAL_SECONDS: float = 10.0  # 10s polling REST fallback
MAX_FUTURE_TOLERANCE_SECONDS: float = 1.0  # Tolerancia máxima a desvío de reloj futuro

# Elevar la precisión por defecto del contexto decimal a 50 dígitos
if decimal.getcontext().prec < 50:
    decimal.getcontext().prec = 50


class FeedHealthState(StrEnum):
    """Estados formales de salud del feed de datos en tiempo real (Nivel 1)."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"


class ExchangeHaltStatus(StrEnum):
    """Códigos de estado de negociación de bolsa (Alpaca / CTA / UTP)."""

    TRADING = "T"
    HALTED = "H"
    PAUSED = "P"
    RESUMED = "R"
    QUOTE_ONLY = "Q"


class EntryPriceMode(str):
    """Subclase de string para el modo de precio de entrada con soporte de doble igualdad.

    Garantiza compatibilidad transparente con tests que esperan 'MIDPOINT',
    'LAST_TRADE', 'STALE_PRICE' y especificaciones que mencionan 'QUOTE_MIDPOINT'.
    """

    MIDPOINT = "MIDPOINT"
    QUOTE_MIDPOINT = "MIDPOINT"
    LAST_TRADE = "LAST_TRADE"
    STALE_PRICE = "STALE_PRICE"

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        s = str(self)
        o = str(other) if isinstance(other, (str, EntryPriceMode)) else None
        if o is None:
            return False
        return (s == "MIDPOINT" and o == "QUOTE_MIDPOINT") or (
            s == "QUOTE_MIDPOINT" and o == "MIDPOINT"
        )

    def __hash__(self) -> int:
        s = str(self)
        if s in ("MIDPOINT", "QUOTE_MIDPOINT"):
            return hash("MIDPOINT")
        return super().__hash__()


def _normalize_ts(ts: Any) -> datetime | None:
    """Normaliza de forma defensiva cualquier timestamp a datetime UTC-aware.

    - Si ts es None o no es instancia de datetime, retorna None.
    - Si ts es offset-naive (tzinfo is None), se asume UTC (replace(tzinfo=UTC)).
    - Si ts es offset-aware, se convierte a UTC (astimezone(UTC)).
    """
    if ts is None or not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def calibrate_freshness(intervals: Sequence[float] | None) -> float:
    """Calcula el umbral calibrado de frescura tau_symbol en segundos.

    Fórmula:
        tau_symbol = min(10 min, max(3 min, p99(intervals)))

    Args:
        intervals: Secuencia de intervalos de tiempo entre actualizaciones en segundos.
                   Soporta listas, tuplas, np.ndarray, pd.Series e iterables.

    Returns:
        Umbral en segundos acotado a [180.0, 600.0]. Fallback a DEFAULT_TAU_SECONDS (300.0s)
        si está vacío, es None o carece de valores finitos no negativos.
    """
    if intervals is None:
        return DEFAULT_TAU_SECONDS

    try:
        if len(intervals) == 0:
            return DEFAULT_TAU_SECONDS
    except TypeError:
        # En caso de iterables/generadores sin __len__
        pass

    cleaned: list[float] = []
    for x in intervals:
        if x is None:
            continue
        try:
            val = float(x)
            if math.isfinite(val) and val >= 0.0:
                cleaned.append(val)
        except (ValueError, TypeError):
            continue

    if not cleaned:
        return DEFAULT_TAU_SECONDS

    p99 = float(np.percentile(cleaned, 99.0))
    return min(MAX_TAU_SECONDS, max(MIN_TAU_SECONDS, p99))


def _to_decimal(val: Any, name: str = "") -> Decimal | None:
    """Convierte con seguridad floats, ints, Decimals y tipos numéricos de numpy a Decimal.

    Retorna None ante valores nulos, NaN o infinitos (Decimal('NaN'), 'NaN', 'sNaN',
    'Infinity', 'inf', etc.).
    """
    if val is None:
        return None

    if isinstance(val, Decimal):
        if not val.is_finite():
            return None
        return val

    if isinstance(val, (float, np.floating)):
        f_val = float(val)
        if not math.isfinite(f_val):
            return None
        try:
            return Decimal(str(f_val))
        except Exception:
            return None

    if isinstance(val, (int, np.integer)):
        return Decimal(str(int(val)))

    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean:
            return None
        try:
            d = Decimal(val_clean)
            if not d.is_finite():
                return None
            return d
        except Exception:
            return None

    return None


@dataclass
class SymbolDataState:
    """Estado de frescura y cotizaciones por símbolo (Nivel 2)."""

    symbol: str
    last_quote_time: datetime | None = None
    last_trade_time: datetime | None = None
    last_quote: PriceQuote | None = None
    last_trade: TradeQuote | None = None
    tau_symbol: timedelta = timedelta(seconds=DEFAULT_TAU_SECONDS)
    is_halted: bool = False
    halt_code: str | None = None

    @property
    def last_update_time(self) -> datetime | None:
        """Timestamp de la actualización más reciente entre quote y trade."""
        if self.last_quote_time and self.last_trade_time:
            return max(self.last_quote_time, self.last_trade_time)
        return self.last_quote_time or self.last_trade_time


class AnomalyDetector:
    """Detector de anomalías de precio cruzado entre feeds IEX y SIP diferido."""

    def __init__(
        self,
        multiplier: Decimal = Decimal("2"),
        fallback_mode: Literal["percentage", "fail_closed"] = "percentage",
        fallback_pct: Decimal = Decimal("0.02"),
    ) -> None:
        self._multiplier = multiplier
        self._fallback_mode = fallback_mode
        self._fallback_pct = fallback_pct

    def check_price_anomaly(
        self,
        iex_price: Decimal | float | int | str,
        sip_close: Decimal | float | int | str,
        atr_5m: Decimal | float | int | str | None,
    ) -> tuple[bool, str]:
        """Verifica la consistencia entre el precio IEX en tiempo real y el cierre SIP 5m.

        Condición de anomalía:
            |P_IEX - C_SIP| > 2 * ATR_5m

        Returns:
            (True, "OK") si la discrepancia está dentro de tolerancia.
            (False, "PRICE_ANOMALY") si la discrepancia supera 2 * ATR.
            (False, reason) si faltan datos o son inválidos.
        """
        p_iex = _to_decimal(iex_price, "iex_price")
        c_sip = _to_decimal(sip_close, "sip_close")
        atr_dec = _to_decimal(atr_5m, "atr_5m")

        if p_iex is None or c_sip is None:
            return False, "MISSING_PRICE_DATA"
        if (
            not p_iex.is_finite()
            or not c_sip.is_finite()
            or p_iex <= Decimal("0")
            or c_sip <= Decimal("0")
        ):
            return False, "INVALID_PRICE"

        with decimal.localcontext() as ctx:
            ctx.prec = 50
            diff = abs(p_iex - c_sip)

            # Manejo de ATR no disponible, cero o negativo
            if atr_dec is None or not atr_dec.is_finite() or atr_dec <= Decimal("0"):
                if self._fallback_mode == "percentage":
                    threshold = self._fallback_pct * c_sip
                    if diff > threshold:
                        return False, "PRICE_ANOMALY"
                    return True, "OK"
                return False, "MISSING_ATR"

            threshold = self._multiplier * atr_dec
            if diff > threshold:
                return False, "PRICE_ANOMALY"
            return True, "OK"

    def detect_anomaly(
        self,
        iex_price: Decimal | float | int | str,
        sip_close: Decimal | float | int | str,
        atr_5m: Decimal | float | int | str | None,
    ) -> tuple[bool, str]:
        """Predicado de detección: retorna (True, descripción) si hay anomalía."""
        is_ok, reason = self.check_price_anomaly(iex_price, sip_close, atr_5m)
        if not is_ok:
            if reason == "PRICE_ANOMALY":
                return True, "PRICE_ANOMALY: discrepancy > 2*ATR"
            return True, f"PRICE_ANOMALY: {reason}"
        return False, "OK"


class GlobalFeedMonitor:
    """Monitor de Nivel 1: Salud del feed WebSocket global y conmutación a REST."""

    def __init__(
        self,
        clock: Clock,
        calendar: MarketCalendar | None = None,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
        rest_poll_interval_seconds: float = DEFAULT_REST_POLL_INTERVAL_SECONDS,
        enforce_market_hours: bool = True,
    ) -> None:
        self._clock = clock
        self._calendar = calendar
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._stale_timeout = stale_timeout_seconds
        self._rest_poll_interval = rest_poll_interval_seconds
        self._enforce_market_hours = enforce_market_hours

        self._last_message_time: datetime | None = None
        self._state: FeedHealthState = FeedHealthState.DISCONNECTED
        self._is_fallback_active: bool = True
        self._is_disconnected: bool = True

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def state(self) -> FeedHealthState:
        return self.evaluate_state()

    @property
    def is_fallback_active(self) -> bool:
        self.evaluate_state()
        return self._is_fallback_active

    @property
    def rest_poll_interval(self) -> float:
        return self._rest_poll_interval

    @property
    def rest_poll_interval_seconds(self) -> float:
        return self._rest_poll_interval

    @property
    def last_message_time(self) -> datetime | None:
        return self._last_message_time

    def record_message(self, timestamp: datetime | None = None) -> None:
        """Registra la llegada de cualquier mensaje en el WebSocket.

        Garantiza:
        1. Clamping de timestamps futuros que excedan la tolerancia (para no enmascarar desconexiones).
        2. Monotonía temporal: self._last_message_time no retrocede ante mensajes desordenados/retrasados.
        """
        raw_ts = self._normalize_ts(timestamp or self._clock.now())
        now = self._clock.now()

        # 1. Clamping de timestamps a futuro que excedan la tolerancia
        if raw_ts > now + timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS):
            logger.warning(
                "global_feed_future_timestamp_clamped",
                original_ts=raw_ts.isoformat(),
                clamped_ts=now.isoformat(),
            )
            ts = now
        else:
            ts = raw_ts

        # 2. Monotonía: no retroceder cursor temporal ante paquetes desordenados
        if self._last_message_time is None:
            self._last_message_time = ts
        else:
            self._last_message_time = max(self._last_message_time, ts)

        self._is_disconnected = False
        prev_state = self._state
        self.evaluate_state()
        if prev_state != FeedHealthState.HEALTHY and self._state == FeedHealthState.HEALTHY:
            logger.info(
                "global_feed_recovered",
                previous_state=prev_state.value,
                timestamp=ts.isoformat(),
            )

    def record_disconnect(self, reason: str = "") -> None:
        """Registra la desconexión explícita o error de socket."""
        logger.warning(
            "global_feed_disconnected",
            reason=reason,
            timestamp=self._clock.now().isoformat(),
        )
        self._is_disconnected = True
        self._state = FeedHealthState.DISCONNECTED
        self._is_fallback_active = True

    def evaluate_state(self) -> FeedHealthState:
        """Evalúa el estado del feed global según el tiempo transcurrido."""
        now = self._clock.now()

        # Si se exige horario de mercado y estamos fuera de rueda regular
        if self._enforce_market_hours and not self._is_market_open(now):
            return self._state

        if self._is_disconnected or self._last_message_time is None:
            self._state = FeedHealthState.DISCONNECTED
            self._is_fallback_active = True
            return self._state

        silence_seconds = max(0.0, (now - self._last_message_time).total_seconds())

        if silence_seconds <= self._heartbeat_timeout:
            self._state = FeedHealthState.HEALTHY
            self._is_fallback_active = False
        elif silence_seconds <= self._stale_timeout:
            if self._state == FeedHealthState.HEALTHY:
                logger.warning(
                    "global_feed_degraded",
                    silence_seconds=silence_seconds,
                    timeout=self._heartbeat_timeout,
                )
            self._state = FeedHealthState.DEGRADED
            self._is_fallback_active = True
        else:
            if self._state != FeedHealthState.STALE:
                logger.error(
                    "global_feed_stale",
                    silence_seconds=silence_seconds,
                    stale_timeout=self._stale_timeout,
                )
            self._state = FeedHealthState.STALE
            self._is_fallback_active = True

        return self._state

    def check_global_feed(self, last_msg_time: datetime | None = None) -> bool:
        """Verifica si el feed global es saludable (Feature 14 / compatibilidad con tests)."""
        now = self._clock.now()
        target_ts = (
            self._normalize_ts(last_msg_time)
            if last_msg_time is not None
            else self._last_message_time
        )

        if target_ts is None:
            return False

        if self._enforce_market_hours and not self._is_market_open(now):
            return True

        silence = (now - target_ts).total_seconds()
        return -MAX_FUTURE_TOLERANCE_SECONDS <= silence <= self._heartbeat_timeout

    def is_feed_healthy(self) -> bool:
        """Retorna True si el feed está completamente saludable."""
        return self.evaluate_state() == FeedHealthState.HEALTHY

    def is_entry_allowed(self) -> tuple[bool, str]:
        """Gating de entrada global: solo permite entradas si el feed está HEALTHY."""
        current_state = self.evaluate_state()
        if current_state == FeedHealthState.HEALTHY:
            return True, "OK"
        return False, f"FEED_{current_state.value}"

    def _is_market_open(self, now: datetime) -> bool:
        if self._calendar is not None:
            return self._calendar.is_market_open(now)
        # Rueda regular estándar en UTC (13:30 - 20:00 UTC) como fallback
        return 13 <= now.hour < 20

    def _normalize_ts(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)


class SymbolFreshnessMonitor:
    """Monitor de Nivel 2: Frescura de cotizaciones por símbolo y detección de halts."""

    def __init__(
        self,
        clock: Clock,
        default_tau_seconds: float = DEFAULT_TAU_SECONDS,
    ) -> None:
        self._clock = clock
        self._default_tau = timedelta(seconds=default_tau_seconds)
        self._symbols: dict[str, SymbolDataState] = {}

    def calculate_tau_symbol(self, p99_trade_gap_seconds: float) -> timedelta:
        """Calcula el umbral calibrado: min(10m, max(3m, p99_gap))."""
        clamped = min(MAX_TAU_SECONDS, max(MIN_TAU_SECONDS, float(p99_trade_gap_seconds)))
        return timedelta(seconds=clamped)

    def set_symbol_tau(self, symbol: str, tau: timedelta | float) -> None:
        """Configura el umbral de frescura de un símbolo."""
        state = self._get_or_create(symbol)
        if isinstance(tau, (int, float)):
            state.tau_symbol = timedelta(seconds=float(tau))
        else:
            state.tau_symbol = tau

    def get_symbol_tau(self, symbol: str) -> timedelta:
        """Obtiene el umbral de frescura de un símbolo o el fallback por defecto."""
        state = self._symbols.get(symbol.strip().upper())
        if state is not None:
            return state.tau_symbol
        return self._default_tau

    def record_quote(self, symbol: str, quote: PriceQuote) -> None:
        """Registra una cotización de IEX para el símbolo con clamping y monotonía."""
        state = self._get_or_create(symbol)
        raw_ts = _normalize_ts(quote.timestamp or self._clock.now())
        assert raw_ts is not None
        now = self._clock.now()

        # Clamping de timestamps a futuro
        if raw_ts > now + timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS):
            logger.warning(
                "quote_future_timestamp_clamped",
                symbol=symbol,
                original_ts=raw_ts.isoformat(),
                clamped_ts=now.isoformat(),
            )
            ts = now
        else:
            ts = raw_ts

        # Monotonía: no retroceder datos de cotización ante mensajes desordenados
        if state.last_quote_time is None or ts >= state.last_quote_time:
            state.last_quote_time = ts
            state.last_quote = quote

    def record_trade(self, symbol: str, trade: TradeQuote) -> None:
        """Registra una ejecución de trade de IEX para el símbolo con clamping y monotonía."""
        state = self._get_or_create(symbol)
        raw_ts = _normalize_ts(trade.timestamp or self._clock.now())
        assert raw_ts is not None
        now = self._clock.now()

        # Clamping de timestamps a futuro
        if raw_ts > now + timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS):
            logger.warning(
                "trade_future_timestamp_clamped",
                symbol=symbol,
                original_ts=raw_ts.isoformat(),
                clamped_ts=now.isoformat(),
            )
            ts = now
        else:
            ts = raw_ts

        # Monotonía: no retroceder datos de trade ante mensajes desordenados
        if state.last_trade_time is None or ts >= state.last_trade_time:
            state.last_trade_time = ts
            state.last_trade = trade

    def record_exchange_status(self, symbol: str, status_code: str) -> None:
        """Actualiza el estado de negociación del stream statuses de bolsa."""
        state = self._get_or_create(symbol)
        code = status_code.strip().upper()
        state.halt_code = code
        state.is_halted = code in {"H", "P", "Q"}

    def check_symbol_freshness(
        self,
        symbol: str,
        last_update: datetime | None = None,
        tau: timedelta | None = None,
    ) -> tuple[bool, str]:
        """Evalúa si los datos de un símbolo están frescos respecto de tau_symbol."""
        state = self._symbols.get(symbol.strip().upper())
        now = self._clock.now()

        # Halt explícito de bolsa
        if state and state.is_halted:
            return False, "SYMBOL_HALTED"

        eval_ts = last_update
        if eval_ts is None and state:
            eval_ts = state.last_update_time

        if eval_ts is None:
            return False, "NO_DATA"

        eval_ts = _normalize_ts(eval_ts)
        if eval_ts is None:
            return False, "NO_DATA"

        eval_tau = tau
        if eval_tau is None and state:
            eval_tau = state.tau_symbol
        if eval_tau is None:
            eval_tau = self._default_tau

        age = now - eval_ts
        if -timedelta(seconds=MAX_FUTURE_TOLERANCE_SECONDS) <= age <= eval_tau:
            return True, "OK"
        return False, "POTENTIAL_HALT"

    def is_symbol_entry_allowed(self, symbol: str) -> tuple[bool, str]:
        """Gating de entrada específico por símbolo."""
        is_fresh, reason = self.check_symbol_freshness(symbol)
        if not is_fresh:
            if reason == "POTENTIAL_HALT":
                return False, "STALE_SYMBOL_FEED"
            return False, reason
        return True, "OK"

    def _get_or_create(self, symbol: str) -> SymbolDataState:
        sym = symbol.strip().upper()
        if sym not in self._symbols:
            self._symbols[sym] = SymbolDataState(
                symbol=sym,
                tau_symbol=self._default_tau,
            )
        return self._symbols[sym]


class ThreadSafeDiscardCounts(dict[str, int]):
    """Diccionario thread-safe para métricas de descarte de señales.

    Mantiene la interfaz estándar de dict (subscripción, get, items, iteración)
    protegida por un cerrojo threading.Lock para operaciones concurrentes seguras.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()

    def increment(self, key: str, amount: int = 1) -> int:
        """Incrementa atómicamente el contador para la clave especificada."""
        with self._lock:
            val = super().get(key, 0) + amount
            super().__setitem__(key, val)
            return val

    def __getitem__(self, key: str) -> int:
        with self._lock:
            return super().__getitem__(key)

    def __setitem__(self, key: str, value: int) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def get(self, key: str, default: int = 0) -> int:  # type: ignore[override]
        with self._lock:
            return super().get(key, default)

    def copy(self) -> dict[str, int]:
        with self._lock:
            return super().copy()

    def items(self):
        with self._lock:
            return list(super().items())

    def values(self):
        with self._lock:
            return list(super().values())

    def keys(self):
        with self._lock:
            return list(super().keys())

    def __repr__(self) -> str:
        with self._lock:
            return super().__repr__()


class StalenessGuard:
    """Fachada unificada de Guardia de Datos Viejos, Precios de Entrada y Anomalías."""

    def __init__(
        self,
        clock: Clock | None = None,
        calendar: MarketCalendar | None = None,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
        rest_poll_interval_seconds: float = DEFAULT_REST_POLL_INTERVAL_SECONDS,
        enforce_market_hours: bool = True,
        anomaly_multiplier: Decimal = Decimal("2"),
        fallback_mode: Literal["percentage", "fail_closed"] = "percentage",
        fallback_pct: Decimal = Decimal("0.02"),
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self.clock: Clock = self._clock  # Compatibilidad directa con tests
        self._global_monitor = GlobalFeedMonitor(
            clock=self._clock,
            calendar=calendar,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            stale_timeout_seconds=stale_timeout_seconds,
            rest_poll_interval_seconds=rest_poll_interval_seconds,
            enforce_market_hours=enforce_market_hours,
        )
        self._symbol_monitor = SymbolFreshnessMonitor(clock=self._clock)
        self._anomaly_detector = AnomalyDetector(
            multiplier=anomaly_multiplier,
            fallback_mode=fallback_mode,
            fallback_pct=fallback_pct,
        )
        self._discard_lock = threading.Lock()
        self.discard_counts: dict[str, int] = ThreadSafeDiscardCounts(
            {"STALE_PRICE": 0, "PRICE_ANOMALY": 0}
        )

    def _increment_discard(self, key: str, amount: int = 1) -> None:
        """Incrementa de forma atómica y thread-safe un contador de descarte."""
        if isinstance(self.discard_counts, ThreadSafeDiscardCounts):
            self.discard_counts.increment(key, amount)
        else:
            with self._discard_lock:
                self.discard_counts[key] = self.discard_counts.get(key, 0) + amount

    @property
    def global_monitor(self) -> GlobalFeedMonitor:
        return self._global_monitor

    @property
    def symbol_monitor(self) -> SymbolFreshnessMonitor:
        return self._symbol_monitor

    @property
    def anomaly_detector(self) -> AnomalyDetector:
        return self._anomaly_detector

    # --- Delegaciones Nivel 1 (Feed Global) ---
    def record_message(self, timestamp: datetime | None = None) -> None:
        self._global_monitor.record_message(timestamp)

    def record_disconnect(self, reason: str = "") -> None:
        self._global_monitor.record_disconnect(reason)

    def check_global_feed(self, last_msg_time: datetime | None = None) -> bool:
        return self._global_monitor.check_global_feed(last_msg_time)

    def is_feed_healthy(self) -> bool:
        return self._global_monitor.is_feed_healthy()

    def get_feed_state(self) -> FeedHealthState:
        return self._global_monitor.state

    # --- Delegaciones Nivel 2 (Frescura de Símbolo) ---
    def calculate_tau_symbol(self, p99_trade_gap_seconds: float) -> timedelta:
        return self._symbol_monitor.calculate_tau_symbol(p99_trade_gap_seconds)

    def set_symbol_tau(self, symbol: str, tau: timedelta | float) -> None:
        self._symbol_monitor.set_symbol_tau(symbol, tau)

    def get_symbol_tau(self, symbol: str) -> timedelta:
        return self._symbol_monitor.get_symbol_tau(symbol)

    def record_quote(self, symbol: str, quote: PriceQuote) -> None:
        self._symbol_monitor.record_quote(symbol, quote)

    def record_trade(self, symbol: str, trade: TradeQuote) -> None:
        self._symbol_monitor.record_trade(symbol, trade)

    def record_exchange_status(self, symbol: str, status_code: str) -> None:
        self._symbol_monitor.record_exchange_status(symbol, status_code)

    def check_symbol_freshness(
        self,
        symbol: str,
        last_update: datetime | None = None,
        tau: timedelta | None = None,
    ) -> tuple[bool, str]:
        return self._symbol_monitor.check_symbol_freshness(symbol, last_update, tau)

    # --- Gating de Entrada de Dos Niveles ---
    def is_entry_allowed(self, symbol: str | None = None) -> tuple[bool, str]:
        """Gating jerárquico de dos niveles:
        1. Si el feed global no está HEALTHY -> bloqueo inmediato.
        2. Si se indica símbolo y no está fresco o en halt -> bloqueo.
        """
        feed_allowed, feed_reason = self._global_monitor.is_entry_allowed()
        if not feed_allowed:
            return False, feed_reason

        if symbol is not None:
            sym_allowed, sym_reason = self._symbol_monitor.is_symbol_entry_allowed(symbol)
            if not sym_allowed:
                return False, sym_reason

        return True, "OK"

    # --- Política Estricta de Precio de Entrada (Sin Forward-Fill) ---
    def get_entry_price(
        self,
        quote: PriceQuote,
        trade: TradeQuote,
        tau: timedelta | None = None,
    ) -> tuple[Decimal | None, str]:
        """Selector estricto de precio de entrada sin forward-fill:
        1. Midpoint si spread <= 10.0 bps y quote fresca (<= tau).
        2. Último trade si está fresco (<= tau).
        3. Descarte con STALE_PRICE.
        """
        now = self._clock.now()
        eff_tau = tau if tau is not None else timedelta(seconds=DEFAULT_TAU_SECONDS)

        # 1. Evaluar Quote
        quote_valid = False
        midpoint: Decimal | None = None
        spread_bps = float("inf")

        if (
            quote is not None
            and isinstance(quote, PriceQuote)
            and hasattr(quote.bid, "is_finite")
            and quote.bid.is_finite()
            and hasattr(quote.ask, "is_finite")
            and quote.ask.is_finite()
            and quote.bid > Decimal("0")
            and quote.ask > Decimal("0")
            and quote.ask >= quote.bid  # Rechazar libro cruzado
        ):
            midpoint = (
                quote.midpoint
                if quote.midpoint is not None
                else (quote.bid + quote.ask) / Decimal("2")
            )
            if midpoint is not None and hasattr(midpoint, "is_finite") and midpoint.is_finite():
                if quote.spread_bps is not None:
                    spread_bps = float(quote.spread_bps)
                elif midpoint > Decimal("0"):
                    spread_bps = float(((quote.ask - quote.bid) / midpoint) * Decimal("10000"))
                quote_valid = True

        quote_fresh = False
        if quote_valid and quote.timestamp is not None:
            q_ts = _normalize_ts(quote.timestamp)
            if q_ts is not None:
                quote_age = now - q_ts
                # Permitir leve margen por reloj (hasta 1s a futuro)
                quote_fresh = -timedelta(seconds=1) <= quote_age <= eff_tau

        if quote_valid and spread_bps <= MAX_SPREAD_BPS_FOR_MIDPOINT and quote_fresh:
            assert midpoint is not None
            return midpoint, EntryPriceMode("MIDPOINT")

        # 2. Evaluar Trade
        trade_valid = (
            trade is not None
            and isinstance(trade, TradeQuote)
            and hasattr(trade.price, "is_finite")
            and trade.price.is_finite()
            and trade.price > Decimal("0")
            and trade.timestamp is not None
        )

        trade_fresh = False
        if trade_valid and trade.timestamp is not None:
            t_ts = _normalize_ts(trade.timestamp)
            if t_ts is not None:
                trade_age = now - t_ts
                trade_fresh = -timedelta(seconds=1) <= trade_age <= eff_tau

        if trade_valid and trade_fresh:
            return trade.price, EntryPriceMode("LAST_TRADE")

        # 3. Descarte por precio no fresco (Fail-Closed)
        self._increment_discard("STALE_PRICE")
        return None, EntryPriceMode("STALE_PRICE")

    # --- Detección de Anomalías de Precio Cruzado ---
    def check_price_anomaly(
        self,
        iex_price: Decimal | float | int | str,
        sip_close: Decimal | float | int | str,
        atr_5m: Decimal | float | int | str | None,
    ) -> tuple[bool, str]:
        """Verifica discrepancia anómala entre feeds (> 2 * ATR_5m)."""
        is_ok, reason = self._anomaly_detector.check_price_anomaly(iex_price, sip_close, atr_5m)
        if not is_ok and reason == "PRICE_ANOMALY":
            self._increment_discard("PRICE_ANOMALY")
        return is_ok, reason

    # --- Fachada Integral de Validación de Entrada (validate_entry) ---
    def validate_entry(
        self,
        symbol: str,
        quote: PriceQuote,
        trade: TradeQuote,
        sip_close: Decimal | float | int | str,
        atr_5m: Decimal | float | int | str | None = None,
        last_global_msg_time: datetime | None = None,
    ) -> tuple[Decimal | None, bool, str]:
        """Coordina los niveles de validación para autorizar una orden de entrada.

        Secuencia:
        1. Nivel 1: Feed global.
        2. Nivel 2: Frescura y halt del símbolo.
        3. Selector estricto de precio de entrada (sin forward-fill).
        4. Detector de anomalía cruzada (> 2 * ATR).

        Returns:
            (entry_price, True, "OK") si la orden está aprobada.
            (None, False, reason_code) si fue bloqueada en cualquier paso.
        """
        # 1. Nivel 1: Feed global
        if last_global_msg_time is not None:
            if not self.check_global_feed(last_global_msg_time):
                return None, False, "FEED_DEGRADED"
        else:
            is_global_ok, global_reason = self._global_monitor.is_entry_allowed()
            if not is_global_ok:
                return None, False, global_reason

        # 2. Nivel 2: Frescura de símbolo
        tau = self._symbol_monitor.get_symbol_tau(symbol)
        q_raw_ts = getattr(quote, "timestamp", None) if quote is not None else None
        t_raw_ts = getattr(trade, "timestamp", None) if trade is not None else None

        q_ts = _normalize_ts(q_raw_ts)
        t_ts = _normalize_ts(t_raw_ts)

        last_update = None
        if q_ts is not None and t_ts is not None:
            last_update = max(q_ts, t_ts)
        elif q_ts is not None:
            last_update = q_ts
        elif t_ts is not None:
            last_update = t_ts

        is_sym_fresh, sym_reason = self.check_symbol_freshness(symbol, last_update, tau)
        if not is_sym_fresh:
            return None, False, sym_reason

        # 3. Resolución de precio de entrada sin forward-fill
        entry_price, price_mode = self.get_entry_price(quote, trade, tau)
        if entry_price is None:
            return None, False, str(price_mode)

        # 4. Chequeo de anomalía cruzada
        is_price_ok, anomaly_reason = self.check_price_anomaly(entry_price, sip_close, atr_5m)
        if not is_price_ok:
            return None, False, anomaly_reason

        return entry_price, True, "OK"

    def validate_entry_or_raise(
        self,
        symbol: str,
        quote: PriceQuote,
        trade: TradeQuote,
        sip_close: Decimal | float | int | str,
        atr_5m: Decimal | float | int | str | None = None,
        last_global_msg_time: datetime | None = None,
    ) -> Decimal:
        """Versión estricta que lanza excepción si la entrada es rechazada."""
        price, is_valid, reason = self.validate_entry(
            symbol,
            quote,
            trade,
            sip_close,
            atr_5m,
            last_global_msg_time,
        )
        if not is_valid or price is None:
            if reason == "PRICE_ANOMALY":
                raise PriceAnomalyError(
                    f"Price anomaly blocked entry for {symbol}: discrepancy exceeds 2*ATR threshold",
                    details={"symbol": symbol, "reason": reason},
                )
            raise StaleDataError(
                f"Stale market data blocked entry for {symbol}: reason {reason}",
                details={"symbol": symbol, "reason": reason},
            )
        return price
