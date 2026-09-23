"""Motor unificado de simulación y replay para backtesting offline (Trading-Bot v2.3).

Soporta:
- Simulación determinista barra a barra sin sesgo de anticipación (No Lookahead).
- Ejecución al cierre de la sesión (default, replica el bot en vivo a las 15:45) o en la
  apertura de la sesión siguiente (`execution_timing="next_open"`).
- Salidas propias de cada estrategia (hook `manage_positions`) y overlay genérico de salidas
  (EMA trailing + máximo de sesiones) solo para estrategias que no gestionan sus posiciones.
- Cortacircuitos (CircuitBreakerManager: -2.0% pausa, -3.5% flatten) evaluados todos los días.
- Guardas de holdout (Regla 0 / assert_not_holdout) y prohibición de futuros sintéticos.
- Arquitectura de bloques de activos (BlockConfig) y modo Strategy estándar.
- Control de volatilidad de cartera con covarianza contraída, alineada por FECHA.
- Fricciones de Alpaca: spread, slippage, SEC fee, FINRA TAF, CAT fee, acciones enteras,
  liquidación T+1 con calendario de sesiones y modelo Reg T de cuenta cash (GFV).
- Rendimiento del efectivo remanente con la serie diaria real de BIL (sin recortes).
- Modo `engine_mode="legacy"` que reproduce exactamente el motor v2.2 (validación de equivalencia).

Rendimiento: los datos diarios se indexan una sola vez (arrays de fechas + búsqueda binaria) y
los indicadores del motor (régimen, EMA, retornos) se precalculan de forma causal. Los valores son
idénticos a recalcularlos sobre cada prefijo porque rolling/EWM son algoritmos causales.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pytz

from tbot.backtest.guards import assert_not_holdout
from tbot.backtest.metrics import (
    BacktestMetrics,
    compute_backtest_metrics,
    compute_portfolio_volatility_and_scale,
    load_risk_free_rate_bil,
)
from tbot.backtest.simulated_broker import (
    LEGACY_ETF_SYMBOLS,
    SimulatedBroker,
    SimulatedTrade,
)
from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.risk.circuit_breakers import CircuitBreakerManager
from tbot.strategies.interfaces import (
    PositionAction,
    PositionSnapshot,
    Signal,
    Strategy,
    StrategyContext,
)

# Invariantes no negociables de GEMINI.md
FORBIDDEN_SYNTHETIC_COMMODITIES: set[str] = {"USO", "UNG", "UCO", "BOIL", "SCO", "KOLD"}
PHYSICAL_COMMODITIES_ALLOWLIST: set[str] = {"GLD", "GLDM", "SLV"}
US_SECTORS: list[str] = ["XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE"]

_D0 = Decimal("0.0")
_INTRADAY_5M = ("5m", "5min", "minute", "intraday_5m", "intraday")
_INTRADAY_1H = ("hourly", "1h", "h", "hour")


def _dec4(x: float) -> Decimal:
    """Convierte un float a Decimal redondeado a 4 decimales (convención del motor)."""
    return Decimal(str(round(float(x), 4)))


@dataclass(frozen=True)
class BlockConfig:
    """Configuración de un bloque de activos dentro del portafolio."""

    name: str
    tickers: list[str]
    capital_cap: float
    top_n: int
    buffer_rank: int
    per_instrument_cap: dict[str, float] = field(default_factory=dict)
    modulate_by_regime: bool = True


@dataclass
class CircuitBreakerEvent:
    """Registro estructurado de activación de cortacircuitos."""

    date: date
    timestamp: datetime
    event_type: str  # "PAUSED_DAILY_LOSS" o "EMERGENCY_FLATTEN"
    intraday_loss_pct: float
    threshold_pct: float
    starting_equity: float
    equity_at_trigger: float
    liquidated_positions: list[str] = field(default_factory=list)


@dataclass
class BacktestConfig:
    """Configuración declarativa unificada para simulación."""

    # Estrategia o Bloques
    strategy: Strategy | None = None
    blocks: dict[str, BlockConfig] | None = None
    # Universo operable. Se aplica a las compras de estrategias que NO declaran su propio universo.
    universe: list[str] | None = None

    # Capital y Broker
    initial_capital: Decimal = Decimal("2000.00")
    account_type: str = "cash"
    settlement_days: int = 1
    # "gfv_aware": cuenta cash Reg T (compra con fondos no liquidados y cuenta GFV).
    # "settled_only": solo fondos liquidados (modelo conservador del motor v2.2).
    cash_account_model: Literal["gfv_aware", "settled_only"] = "gfv_aware"
    max_open_positions: int = 4
    single_position_cap: float = 0.25
    risk_per_trade_pct: float = 0.5

    # Apalancamiento (requiere account_type="margin"; valores por defecto de Alpaca)
    # `leverage`: exposición bruta objetivo / equity. Multiplica target_weight, single_position_cap
    # y los topes de bloque. Límite Reg T overnight: 1 / initial_margin_pct (2x).
    leverage: float = 1.0
    max_gross_leverage: float | None = None  # tope duro del broker; None = `leverage`
    initial_margin_pct: float = 0.50  # Reg T
    maintenance_margin_pct: float = 0.30  # Alpaca, acciones > $6 (tramos de precio en el broker)
    min_margin_equity: float = 2000.0  # Alpaca: con menos equity el poder de compra es 1x
    # Interés sobre saldo deudor: "fixed" (tasa anual fija) o "benchmark_spread"
    # (tasa del efectivo anualizada x252 + spread; sigue el ciclo de tasas).
    margin_rate_mode: Literal["fixed", "benchmark_spread"] = "fixed"
    margin_annual_rate: float = 0.065  # Alpaca no-Elite (sep-2026)
    margin_rate_spread: float = 0.025
    margin_day_count: int = 360  # Alpaca: (debit * tasa) / 360, fines de semana incluidos
    # Tras un margin call (se emite al cierre y se liquida en la apertura siguiente), se venden
    # posiciones (las más grandes primero) hasta que equity / exposición >= este ratio.
    margin_call_restore_ratio: float = 0.50
    # El objetivo de volatilidad del control B-03 se multiplica por `leverage` (si no, el control
    # de volatilidad anularía el apalancamiento).
    vol_target_scales_with_leverage: bool = True
    leveraged_etf_multipliers: dict[str, int] | None = None  # p.ej. {"QLD": 2, "TQQQ": 3}

    # Cortacircuitos
    enable_circuit_breakers: bool = True
    daily_loss_limit_pct: float = 2.0
    emergency_loss_limit_pct: float = 3.5
    weekly_loss_limit_pct: float = 5.0
    max_consecutive_losses: int = 4
    # Sesiones de pausa tras alcanzar la racha máxima de pérdidas (luego se reinicia la racha).
    consecutive_loss_cooldown_sessions: int = 1
    # Con barras diarias: "interpolated" liquida al nivel donde la cartera cruza el umbral
    # (entre apertura y mínimos); "worst_low" liquida todo en los mínimos del día (v2.2).
    cb_flatten_model: Literal["interpolated", "worst_low"] = "interpolated"

    # Control de Volatilidad (B-03)
    enable_vol_control: bool = True
    target_portfolio_vol: float = 0.12  # 12% anual
    vol_shrinkage_lambda: float = 0.3
    vol_lookback_days: int = 90
    min_position_usd: float = 150.0

    # Régimen Graduado
    enable_graduated_regime: bool = True

    # Fricciones Alpaca y Realismo
    integer_shares: bool = True
    apply_retail_costs: bool = True
    etf_half_spread_bps: float = 1.5
    stock_half_spread_bps: float = 2.5
    etf_slippage_bps: float = 2.0
    stock_slippage_bps: float = 5.0
    sec_fee_rate: Decimal = Decimal("0.0000278")
    finra_taf_per_share: Decimal = Decimal("0.000166")
    max_finra_taf_per_order: Decimal = Decimal("8.30")
    cat_fee_per_share: Decimal = Decimal("0.00003")
    etf_symbols: set[str] | None = None  # None = conjunto por defecto del broker

    # Rendimiento de Efectivo Remanente (Cash Yield / BIL)
    enable_cash_yield: bool = True
    rf_series: pd.Series | None = None
    annual_cash_yield_fallback: float = 0.045  # solo si NO hay serie BIL disponible
    # Cómo interpretar rf_series:
    # "daily_rate": ya es la tasa diaria del efectivo (se aplica con piso 0).
    # "bil_returns": son retornos diarios de precio de BIL (ruidosos, con días negativos): la tasa del
    #   efectivo es su media móvil de `cash_rate_smoothing_days` sesiones previas, con piso 0. Evita el
    #   sesgo de recortar a 0 cada día negativo (que inflaba ~1.3%/año el rendimiento 2010-2015).
    # "auto": "bil_returns" si más del 2% de los días son negativos; si no, "daily_rate".
    cash_rate_source: Literal["auto", "daily_rate", "bil_returns"] = "auto"
    cash_rate_smoothing_days: int = 21

    # Ejecución
    # "close": señal y ejecución al cierre de la sesión (bot en vivo a las 15:45).
    # "next_open": señal al cierre, ejecución en la apertura de la sesión siguiente.
    execution_timing: Literal["close", "next_open"] = "close"
    # Qué ve la estrategia de la barra diaria en curso al decidir al cierre:
    # "open_close": solo open y close reales (high/low acotados a open/close, volumen NaN).
    # "full": barra completa (incluye high/low/volumen del día; sesgo leve de anticipación).
    same_bar_visibility: Literal["open_close", "full"] = "open_close"

    # Cadencia de Rebalanceo y Salidas
    rebalance_cadence: Literal["daily", "weekly_friday"] = "daily"
    # Overlay genérico de salidas (EMA trailing + max_holding_sessions):
    # "auto": solo para estrategias SIN manage_positions (con aviso en result.warnings).
    # "always": para todas. "never": nunca.
    exit_overlay: Literal["auto", "always", "never"] = "auto"
    trailing_ema_period: int = 25
    stop_buffer_pct: float = 0.035
    max_holding_sessions: int = 30
    exit_on_bear_regime: bool = False
    eval_time: time = time(15, 45)
    intraday_start_time: time = time(9, 45)
    intraday_end_time: time = time(15, 30)
    flatten_time: time = time(15, 55)

    # Validación Estadística
    num_tested_trials: int = 1
    # Registro persistente de variantes probadas: si se define, el DSR usa
    # max(num_tested_trials, cantidad de configuraciones distintas registradas en la familia).
    trial_ledger_path: str | Path | None = None
    trial_family: str | None = None

    # "corrected" (default) o "legacy" (reproduce el motor v2.2, solo para validar equivalencia).
    engine_mode: Literal["corrected", "legacy"] = "corrected"


@dataclass
class BacktestResult:
    """Resultado unificado de la simulación con compatibilidad de desempacado en tupla."""

    metrics: BacktestMetrics
    trades: list[SimulatedTrade]
    equity_curve: pd.Series
    circuit_breaker_events: list[CircuitBreakerEvent] = field(default_factory=list)
    daily_regime_scores: pd.Series = field(default_factory=pd.Series)
    portfolio_daily_volatility: pd.Series = field(default_factory=pd.Series)
    warnings: list[str] = field(default_factory=list)
    gfv_count: int = 0
    # Margen / apalancamiento
    margin_events: list[CircuitBreakerEvent] = field(default_factory=list)
    margin_interest_paid: float = 0.0
    gross_leverage: pd.Series = field(default_factory=pd.Series)  # exposición bruta / equity al cierre
    day_trades: int = 0

    def __iter__(self):
        return iter((self.metrics, self.trades, self.equity_curve))

    def __getitem__(self, idx: int):
        return (self.metrics, self.trades, self.equity_curve)[idx]

    @property
    def cb_events(self) -> list[CircuitBreakerEvent]:
        return self.circuit_breaker_events


def compute_graduated_regime_score(
    daily_data: dict[str, pd.DataFrame],
    cur_date: date,
) -> float:
    """Calcula el score de régimen graduado (0.0 a 1.0) usando SPY y amplitud sectorial GICS."""
    spy_df = daily_data.get("SPY")
    if spy_df is None or spy_df.empty:
        return 1.0

    if "_parsed_date" not in spy_df.columns:
        d_col = "date" if "date" in spy_df.columns else "timestamp"
        if d_col in spy_df.columns:
            spy_df["_parsed_date"] = pd.to_datetime(spy_df[d_col]).dt.date
        else:
            return 1.0

    past_spy = spy_df[spy_df["_parsed_date"] < cur_date]
    if len(past_spy) < 200:
        return 1.0

    closes_spy = past_spy["close"].astype(float)
    c_last = closes_spy.iloc[-1]

    sma200_spy = float(closes_spy.rolling(200).mean().iloc[-1])
    ema50_spy = float(ema(closes_spy, 50).iloc[-1])

    cond_sma200 = 1.0 if c_last > sma200_spy else 0.0
    cond_ema50 = 1.0 if c_last > ema50_spy else 0.0

    # Amplitud de mercado: % de sectores GICS > SMA(200)
    above_count = 0
    total_valid_sectors = 0
    for s in US_SECTORS:
        s_df = daily_data.get(s)
        if s_df is None or s_df.empty:
            continue
        if "_parsed_date" not in s_df.columns:
            d_col = "date" if "date" in s_df.columns else "timestamp"
            if d_col in s_df.columns:
                s_df["_parsed_date"] = pd.to_datetime(s_df[d_col]).dt.date
            else:
                continue
        past_s = s_df[s_df["_parsed_date"] < cur_date]
        if len(past_s) >= 200:
            total_valid_sectors += 1
            s_closes = past_s["close"].astype(float)
            s_sma200 = float(s_closes.rolling(200).mean().iloc[-1])
            if s_closes.iloc[-1] > s_sma200:
                above_count += 1

    breadth_pct = (above_count / total_valid_sectors) if total_valid_sectors > 0 else 0.50
    cond_breadth = 1.0 if breadth_pct > 0.50 else 0.0

    score = (cond_sma200 + cond_ema50 + cond_breadth) / 3.0
    return score


def compute_multi_horizon_momentum(
    daily_data: dict[str, pd.DataFrame],
    tickers: list[str],
    cur_date: date,
    skip_recent_days: int = 5,
) -> dict[str, float]:
    """Calcula el momentum multi-horizonte (21d, 63d, 126d) promediando rankings ordinales."""
    ret_21: dict[str, float] = {}
    ret_63: dict[str, float] = {}
    ret_126: dict[str, float] = {}

    for sym in tickers:
        df = daily_data.get(sym)
        if df is None or df.empty:
            continue
        if "_parsed_date" not in df.columns:
            d_col = "date" if "date" in df.columns else "timestamp"
            if d_col in df.columns:
                df["_parsed_date"] = pd.to_datetime(df[d_col]).dt.date
            else:
                continue
        past_bars = df[df["_parsed_date"] < cur_date]
        if len(past_bars) < (skip_recent_days + 126):
            continue

        closes = past_bars["close"].astype(float)
        p_ref = float(closes.iloc[-skip_recent_days])

        p_21 = float(closes.iloc[-(skip_recent_days + 21)])
        p_63 = float(closes.iloc[-(skip_recent_days + 63)])
        p_126 = float(closes.iloc[-(skip_recent_days + 126)])

        if p_21 > 0:
            ret_21[sym] = (p_ref - p_21) / p_21
        if p_63 > 0:
            ret_63[sym] = (p_ref - p_63) / p_63
        if p_126 > 0:
            ret_126[sym] = (p_ref - p_126) / p_126

    common_syms = set(ret_21.keys()) & set(ret_63.keys()) & set(ret_126.keys())
    if not common_syms:
        return {}

    sorted_21 = sorted(common_syms, key=lambda s: ret_21[s], reverse=True)
    sorted_63 = sorted(common_syms, key=lambda s: ret_63[s], reverse=True)
    sorted_126 = sorted(common_syms, key=lambda s: ret_126[s], reverse=True)

    rank_21 = {s: i + 1 for i, s in enumerate(sorted_21)}
    rank_63 = {s: i + 1 for i, s in enumerate(sorted_63)}
    rank_126 = {s: i + 1 for i, s in enumerate(sorted_126)}

    avg_ranks = {
        s: (rank_21[s] + rank_63[s] + rank_126[s]) / 3.0
        for s in common_syms
    }
    return avg_ranks


def check_absolute_momentum_gate(
    daily_data: dict[str, pd.DataFrame],
    sym: str,
    cur_date: date,
) -> bool:
    """Verifica gate absoluto: retorno 126d > BIL 126d y cierre >= EMA50."""
    df = daily_data.get(sym)
    bil_df = daily_data.get("BIL")
    if df is None or bil_df is None or df.empty or bil_df.empty:
        return False

    for d in (df, bil_df):
        if "_parsed_date" not in d.columns:
            d_col = "date" if "date" in d.columns else "timestamp"
            if d_col in d.columns:
                d["_parsed_date"] = pd.to_datetime(d[d_col]).dt.date
            else:
                return False

    past = df[df["_parsed_date"] < cur_date]
    past_bil = bil_df[bil_df["_parsed_date"] < cur_date]

    if len(past) < 130 or len(past_bil) < 130:
        return False

    closes = past["close"].astype(float)
    bil_closes = past_bil["close"].astype(float)

    c_now = closes.iloc[-1]
    c_126 = closes.iloc[-126]
    ret_126 = (c_now - c_126) / c_126

    bil_now = bil_closes.iloc[-1]
    bil_126 = bil_closes.iloc[-126]
    bil_ret_126 = (bil_now - bil_126) / bil_126

    if ret_126 <= bil_ret_126:
        return False

    ema50 = float(ema(closes, 50).iloc[-1])
    return c_now >= ema50



# ─────────────────────────────────────────────────────────────────────────────
# Índices y vistas rápidas sobre los datos diarios
# ─────────────────────────────────────────────────────────────────────────────


class _DailyIndex:
    """Índice por fecha de un DataFrame diario ordenado (búsqueda binaria + arrays numpy)."""

    __slots__ = ("df", "dates", "dnp", "n", "open", "high", "low", "close", "_close_dec", "_open_dec", "_ema")

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.dates: list[date] = list(df["_parsed_date"])
        self.dnp = np.array(self.dates, dtype="datetime64[D]")
        self.n = len(df)
        self.open = df["open"].to_numpy(dtype=float) if "open" in df.columns else None
        self.high = df["high"].to_numpy(dtype=float) if "high" in df.columns else None
        self.low = df["low"].to_numpy(dtype=float) if "low" in df.columns else None
        self.close = df["close"].to_numpy(dtype=float)
        self._close_dec: dict[int, Decimal] = {}
        self._open_dec: dict[int, Decimal] = {}
        self._ema: dict[int, np.ndarray] = {}

    def close_dec(self, i: int) -> Decimal:
        v = self._close_dec.get(i)
        if v is None:
            v = _dec4(self.close[i])
            self._close_dec[i] = v
        return v

    def open_dec(self, i: int) -> Decimal:
        v = self._open_dec.get(i)
        if v is None:
            v = _dec4(self.open[i])
            self._open_dec[i] = v
        return v

    def ema_arr(self, period: int) -> np.ndarray:
        """EMA (adjust=False) de toda la serie: el valor en i es idéntico al de ema(close[:i+1])."""
        arr = self._ema.get(period)
        if arr is None:
            arr = ema(pd.Series(self.close), period).to_numpy(dtype=float)
            self._ema[period] = arr
        return arr

    def returns_window(self, k: int, lookback: int) -> tuple[np.ndarray, int]:
        """Retornos simples de las últimas `lookback+1` barras hasta k (inclusive).

        Devuelve (retornos, índice de fila del primer retorno). Equivale a
        close.iloc[-(lookback+1):].pct_change().dropna() sobre el prefijo [0..k].
        """
        start = max(0, k + 1 - (lookback + 1))
        c = self.close[start : k + 1]
        if len(c) < 2:
            return np.empty(0), start + 1
        return c[1:] / c[:-1] - 1.0, start + 1


class _LazyDailyBars(Mapping):
    """Mapping symbol -> DataFrame recortado a la fecha de evaluación, construido bajo demanda.

    Evita copiar ~100 DataFrames por día cuando la estrategia solo consulta algunos.
    Con `mask_today=True`, la fila de la sesión en curso expone solo open/close reales
    (high/low acotados a [min(open, close), max(open, close)] y volumen NaN).
    """

    def __init__(
        self,
        indexes: dict[str, _DailyIndex],
        ends: dict[str, int],
        today: date,
        mask_today: bool,
    ) -> None:
        self._idx = indexes
        self._ends = {s: e for s, e in ends.items() if e > 0}
        self._today = today
        self._mask = mask_today
        self._cache: dict[str, pd.DataFrame] = {}

    def __getitem__(self, sym: str) -> pd.DataFrame:
        cached = self._cache.get(sym)
        if cached is not None:
            return cached
        end = self._ends[sym]  # KeyError si no hay datos
        ix = self._idx[sym]
        view = ix.df.iloc[:end]
        if self._mask and ix.dates[end - 1] == self._today:
            view = view.copy()
            last = end - 1
            o, c = ix.open[last] if ix.open is not None else ix.close[last], ix.close[last]
            cols = view.columns
            if "high" in cols:
                view.iloc[-1, cols.get_loc("high")] = max(o, c)
            if "low" in cols:
                view.iloc[-1, cols.get_loc("low")] = min(o, c)
            if "volume" in cols:
                view["volume"] = view["volume"].astype(float)
                view.iloc[-1, cols.get_loc("volume")] = np.nan
        self._cache[sym] = view
        return view

    def __contains__(self, sym: object) -> bool:
        return sym in self._ends

    def __iter__(self) -> Iterator[str]:
        return iter(self._ends)

    def __len__(self) -> int:
        return len(self._ends)

    def row_index(self, sym: str) -> int:
        """Índice (en el DataFrame original) de la última fila visible del símbolo, o -1."""
        return self._ends.get(sym, 0) - 1


def _strategy_fingerprint(strategy: Any, config: BacktestConfig, start: date, end: date, resolution: str) -> str:
    """Huella estable de una variante (estrategia + parámetros + configuración + período)."""
    s_params: dict[str, Any] = {}
    if strategy is not None:
        for k, v in sorted(vars(strategy).items()):
            if k.startswith("_"):
                continue
            s_params[k] = repr(v)
    cfg_params: dict[str, Any] = {}
    for k, v in sorted(vars(config).items()):
        if k in ("strategy", "rf_series", "trial_ledger_path", "trial_family", "num_tested_trials"):
            continue
        cfg_params[k] = repr(v)
    payload = {
        "strategy_class": type(strategy).__name__ if strategy is not None else None,
        "strategy": s_params,
        "config": cfg_params,
        "period": [str(start), str(end), resolution],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def register_trial(ledger_path: str | Path, family: str, fingerprint: str) -> int:
    """Registra una variante probada en un ledger JSON y devuelve la cantidad de variantes distintas."""
    path = Path(ledger_path)
    data: dict[str, list[str]] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    fams = data.setdefault(family, [])
    if fingerprint not in fams:
        fams.append(fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return len(fams)


class BacktestEngine:
    """Motor unificado de simulación y replay barra a barra."""

    def __init__(
        self,
        strategy: Strategy | BacktestConfig | None = None,
        historical_daily: dict[str, pd.DataFrame] | None = None,
        historical_intraday: dict[str, pd.DataFrame] | None = None,
        initial_capital: Decimal = Decimal("2000.00"),
        risk_per_trade_pct: float = 0.5,
        max_open_positions: int = 4,
        num_tested_trials: int = 1,
        *,
        config: BacktestConfig | None = None,
    ) -> None:
        # 1. Configuración y estrategia
        if isinstance(strategy, BacktestConfig):
            self.config = strategy
            self.strategy = self.config.strategy
        elif config is not None:
            self.config = config
            self.strategy = self.config.strategy or (strategy if isinstance(strategy, Strategy) else None)
        else:
            self.strategy = strategy if isinstance(strategy, Strategy) else None
            self.config = BacktestConfig(
                strategy=self.strategy,
                initial_capital=initial_capital,
                risk_per_trade_pct=risk_per_trade_pct,
                max_open_positions=max_open_positions,
                num_tested_trials=num_tested_trials,
            )

        if self.config.engine_mode not in ("corrected", "legacy"):
            raise ValueError(f"engine_mode inválido: {self.config.engine_mode!r}")
        self._legacy = self.config.engine_mode == "legacy"
        self._warnings: list[str] = []

        # Apalancamiento: validación contra Reg T y tipo de cuenta
        lev = float(self.config.leverage)
        max_gross = float(self.config.max_gross_leverage) if self.config.max_gross_leverage is not None else lev
        if lev <= 0 or max_gross <= 0:
            raise ValueError("leverage y max_gross_leverage deben ser > 0")
        if max(lev, max_gross) > 1.0 + 1e-9:
            if self._legacy:
                raise ValueError("engine_mode='legacy' no soporta apalancamiento")
            if self.config.account_type.lower() != "margin":
                raise ValueError(
                    "El apalancamiento requiere account_type='margin' (una cuenta cash no puede tomar préstamo)."
                )
            reg_t = 1.0 / float(self.config.initial_margin_pct)
            if max(lev, max_gross) > reg_t + 1e-9:
                raise ValueError(
                    f"Apalancamiento {max(lev, max_gross):.2f}x supera el máximo overnight de Reg T "
                    f"({reg_t:.2f}x con margen inicial {self.config.initial_margin_pct:.0%})."
                )
        self._lev = Decimal(str(lev)) if not self._legacy else Decimal("1")
        self._max_gross = max(max_gross, lev)
        self._margin_on = (not self._legacy) and self.config.account_type.lower() == "margin"
        self._margin_call_pending = False
        self._margin_events: list[CircuitBreakerEvent] = []
        self._prev_session_day: date | None = None
        self._gross_lev: dict[date, float] = {}

        self.daily_data = (
            {k.strip().upper(): v.copy() for k, v in historical_daily.items()} if historical_daily else {}
        )
        self.intraday_data = (
            {k.strip().upper(): v.copy() for k, v in historical_intraday.items()} if historical_intraday else {}
        )
        self.initial_capital = self.config.initial_capital
        self.risk_per_trade_pct = self.config.risk_per_trade_pct
        self.max_open_positions = self.config.max_open_positions
        self.num_tested_trials = self.config.num_tested_trials
        self.ny_tz = pytz.timezone("America/New_York")

        # 2. Invariante 2 de GEMINI.md: Prohibición de futuros sintéticos
        all_symbols = {s.strip().upper() for s in (set(self.daily_data.keys()) | set(self.intraday_data.keys()))}
        if self.config.universe:
            all_symbols.update(s.strip().upper() for s in self.config.universe)
        if self.config.blocks:
            for b in self.config.blocks.values():
                all_symbols.update(s.strip().upper() for s in b.tickers)

        for sym in all_symbols:
            if sym.strip().upper() in FORBIDDEN_SYNTHETIC_COMMODITIES:
                raise ValueError(
                    f"Forbidden synthetic commodity future '{sym}' violates GEMINI.md Invariant 2. "
                    f"Only physically backed commodities ({PHYSICAL_COMMODITIES_ALLOWLIST}) are permitted."
                )

        # 3. Pre-parsear fechas (y ordenar cronológicamente si hiciera falta)
        for sym, df in list(self.daily_data.items()):
            if df.empty or ("date" not in df.columns and "timestamp" not in df.columns):
                continue
            d_col = "date" if "date" in df.columns else "timestamp"
            if "_parsed_date" not in df.columns:
                df["_parsed_date"] = pd.to_datetime(df[d_col]).dt.date
            if not self._legacy and not pd.Index(df["_parsed_date"]).is_monotonic_increasing:
                sorted_df = df.sort_values("_parsed_date", kind="mergesort").reset_index(drop=True)
                sorted_df.attrs = dict(df.attrs)
                self.daily_data[sym] = sorted_df

        for df in self.intraday_data.values():
            if df.empty:
                continue
            if "_parsed_ts" not in df.columns:
                if "datetime_et" in df.columns:
                    df["_parsed_ts"] = pd.to_datetime(df["datetime_et"])
                elif "timestamp" in df.columns:
                    if pd.api.types.is_numeric_dtype(df["timestamp"]):
                        ts = pd.to_datetime(df["timestamp"], unit="s")
                        if not self._legacy:
                            # Epoch en UTC -> hora de Nueva York (naive) para comparar con la rueda.
                            ts = ts.dt.tz_localize("UTC").dt.tz_convert("America/New_York").dt.tz_localize(None)
                        df["_parsed_ts"] = ts
                    else:
                        df["_parsed_ts"] = pd.to_datetime(df["timestamp"])
                elif "date" in df.columns:
                    df["_parsed_ts"] = pd.to_datetime(df["date"])
            if "_parsed_date" not in df.columns:
                if "date" in df.columns:
                    df["_parsed_date"] = pd.to_datetime(df["date"]).dt.date
                elif "_parsed_ts" in df.columns:
                    df["_parsed_date"] = df["_parsed_ts"].dt.date

        # 4. Broker simulado con costos Alpaca y acciones enteras
        costs = self.config.apply_retail_costs
        self.broker = SimulatedBroker(
            initial_capital=self.config.initial_capital,
            account_type=self.config.account_type,
            settlement_days=self.config.settlement_days,
            etf_slippage_bps=self.config.etf_slippage_bps if costs else 0.0,
            stock_slippage_bps=self.config.stock_slippage_bps if costs else 0.0,
            etf_half_spread_bps=self.config.etf_half_spread_bps if costs else 0.0,
            stock_half_spread_bps=self.config.stock_half_spread_bps if costs else 0.0,
            sec_fee_rate=self.config.sec_fee_rate if costs else Decimal("0.0"),
            finra_taf_per_share=self.config.finra_taf_per_share if costs else Decimal("0.0"),
            max_finra_taf_per_order=self.config.max_finra_taf_per_order if costs else Decimal("0.0"),
            cat_fee_per_share=self.config.cat_fee_per_share if costs else Decimal("0.0"),
            integer_shares=self.config.integer_shares,
            etf_symbols=LEGACY_ETF_SYMBOLS if self._legacy else self.config.etf_symbols,
            cash_account_model="settled_only" if self._legacy else self.config.cash_account_model,
            legacy=self._legacy,
            initial_margin_pct=Decimal(str(self.config.initial_margin_pct)),
            maintenance_margin_pct=Decimal(str(self.config.maintenance_margin_pct)),
            min_margin_equity=Decimal(str(self.config.min_margin_equity)),
            max_gross_leverage=Decimal("1") if self._legacy else Decimal(str(self._max_gross)),
            leveraged_etf_multipliers=self.config.leveraged_etf_multipliers,
        )

        # 5. Gestor de cortacircuitos (Circuit Breakers)
        if self.config.enable_circuit_breakers:
            self.cb_manager = CircuitBreakerManager(
                daily_loss_limit_pct=self.config.daily_loss_limit_pct,
                emergency_loss_limit_pct=self.config.emergency_loss_limit_pct,
                weekly_loss_limit_pct=self.config.weekly_loss_limit_pct,
                max_consecutive_losses=self.config.max_consecutive_losses,
            )
        else:
            self.cb_manager = None
        self._streak_resume_idx: int | None = None

        # 6. Serie de tasa libre de riesgo BIL
        if self.config.rf_series is not None:
            self.rf_series = self.config.rf_series
        else:
            self.rf_series = load_risk_free_rate_bil()
        self._rf_map: dict[date, float] = {}
        self._cash_rate_series: pd.Series | None = None
        if self.rf_series is not None and not self.rf_series.empty:
            raw = pd.Series(
                self.rf_series.to_numpy(dtype=float),
                index=[
                    k.date() if isinstance(k, datetime) else (k if isinstance(k, date) else pd.Timestamp(k).date())
                    for k in self.rf_series.index
                ],
            ).sort_index()
            raw = raw[~raw.index.duplicated(keep="last")]
            source = self.config.cash_rate_source
            if source == "auto":
                source = "bil_returns" if float((raw < 0).mean()) > 0.02 else "daily_rate"
            n_sm = int(self.config.cash_rate_smoothing_days)
            if source == "bil_returns" and n_sm > 0:
                rate = raw.rolling(n_sm, min_periods=min(5, n_sm)).mean().shift(1).clip(lower=0.0).fillna(0.0)
                self._cash_rate_note = (
                    f"rf_series interpretada como retornos de BIL: tasa del efectivo = media de "
                    f"{n_sm} sesiones previas con piso 0."
                )
            else:
                rate = raw.clip(lower=0.0)
            self._cash_rate_series = rate
            self._rf_map = {k: float(v) for k, v in rate.items()}
        self._rf_missing_days = 0
        if getattr(self, "_cash_rate_note", None) and not self._legacy:
            self._warnings.append(self._cash_rate_note)

        self._index_cache: dict[str, _DailyIndex] = {}

    # ─────────────────────────────────────────────────────────────── utilidades
    def _index(self, sym: str) -> _DailyIndex:
        ix = self._index_cache.get(sym)
        if ix is None or ix.df is not self.daily_data[sym]:
            ix = _DailyIndex(self.daily_data[sym])
            self._index_cache[sym] = ix
        return ix

    def _warn(self, msg: str) -> None:
        if msg not in self._warnings:
            self._warnings.append(msg)

    def _record_trade(self, tr: SimulatedTrade | None, day_idx: int | None = None) -> None:
        """Registra el resultado en el cortacircuitos y arma el enfriamiento de la racha perdedora."""
        if tr is None or self.cb_manager is None:
            return
        self.cb_manager.record_trade(tr.pnl, tr.exit_time)
        if (
            not self._legacy
            and day_idx is not None
            and self._streak_resume_idx is None
            and self.cb_manager.consecutive_losses >= self.config.max_consecutive_losses
        ):
            self._streak_resume_idx = day_idx + max(1, self.config.consecutive_loss_cooldown_sessions)

    def _apply_streak_cooldown(self, day_idx: int) -> None:
        """Al inicio de sesión: si terminó el enfriamiento, reinicia la racha de pérdidas."""
        if self._legacy or self.cb_manager is None:
            return
        if self.cb_manager.consecutive_losses >= self.config.max_consecutive_losses:
            if self._streak_resume_idx is None:
                self._streak_resume_idx = day_idx + max(1, self.config.consecutive_loss_cooldown_sessions) - 1
            if day_idx >= self._streak_resume_idx:
                self.cb_manager.reset_consecutive_losses()
                self._streak_resume_idx = None
        else:
            self._streak_resume_idx = None

    def _apply_cash_yield(self, day: date) -> None:
        """Acredita el rendimiento diario del efectivo liquidado (serie BIL)."""
        if not self.config.enable_cash_yield or self.broker.settled_cash <= _D0:
            return
        if self._legacy:
            if self.rf_series is not None and day in self.rf_series:
                daily_rf = max(0.0, float(self.rf_series[day]))
            else:
                fb = self.config.annual_cash_yield_fallback
                daily_rf = (1.0 + fb) ** (1.0 / 252.0) - 1.0 if fb > 0 else 0.0
            if daily_rf > 0.0:
                self.broker.settled_cash += self.broker.settled_cash * Decimal(str(daily_rf))
            return

        if self._rf_map:
            daily_rf = self._rf_map.get(day)
            if daily_rf is None or math.isnan(daily_rf):
                self._rf_missing_days += 1
                return
        else:
            fb = self.config.annual_cash_yield_fallback
            daily_rf = (1.0 + fb) ** (1.0 / 252.0) - 1.0 if fb > 0 else 0.0
            self._warn(
                f"Sin serie BIL: el efectivo rinde una tasa fija de {fb:.2%} anual (irreal antes de 2022)."
            )
        if daily_rf != 0.0:
            self.broker.settled_cash += self.broker.settled_cash * Decimal(str(daily_rf))

    def _snapshots(self) -> dict[str, PositionSnapshot]:
        return {
            s: PositionSnapshot(
                symbol=p.symbol,
                qty=p.qty,
                entry_price=p.entry_price,
                entry_time=p.entry_time,
                initial_stop=p.initial_stop,
                current_stop=p.current_stop,
                bars_held=p.bars_held,
                strategy_id=p.strategy_id,
            )
            for s, p in self.broker.positions.items()
        }

    def _determine_regime(self, spy_df: pd.DataFrame, current_date: date) -> MarketRegime:
        """Determina el régimen de mercado a partir de barras previas de SPY."""
        if spy_df.empty:
            return MarketRegime.BULL_CALM
        if "_parsed_date" in spy_df.columns:
            history = spy_df[spy_df["_parsed_date"] < current_date]
        else:
            date_col = "date" if "date" in spy_df.columns else "timestamp"
            history = spy_df[pd.to_datetime(spy_df[date_col]).dt.date < current_date]
        if len(history) < 200:
            return MarketRegime.BULL_CALM

        closes = history["close"].astype(float)
        sma200 = float(closes.rolling(200).mean().iloc[-1])
        cur_c = float(closes.iloc[-1])

        if cur_c <= sma200:
            return MarketRegime.BEAR

        rets = closes.pct_change().dropna()
        if len(rets) >= 20:
            vol_20d = float(rets.iloc[-20:].std() * (252.0**0.5))
            if vol_20d > 0.22:
                return MarketRegime.BULL_VOLATILE
        return MarketRegime.BULL_CALM

    def _precompute_regimes(
        self, trading_days: list[date]
    ) -> tuple[list[MarketRegime], list[float]]:
        """Régimen y score graduado por sesión, usando solo barras anteriores (< día).

        Numéricamente idéntico a `_determine_regime` / `compute_graduated_regime_score`
        (rolling y EWM son causales: el valor en i no depende de las filas posteriores).
        """
        n = len(trading_days)
        days_np = np.array(trading_days, dtype="datetime64[D]")
        regimes = [MarketRegime.BULL_CALM] * n
        scores = [1.0] * n

        spy = self.daily_data.get("SPY")
        spy_ok = spy is not None and not spy.empty and "_parsed_date" in spy.columns
        if spy_ok:
            ix = self._index("SPY")
            closes = pd.Series(ix.close)
            sma200 = closes.rolling(200).mean().to_numpy()
            ema50 = ix.ema_arr(50)
            rets = np.full(ix.n, np.nan)
            if ix.n > 1:
                rets[1:] = ix.close[1:] / ix.close[:-1] - 1.0
            lt = np.searchsorted(ix.dnp, days_np, "left") - 1
        sectors = []
        if self.config.enable_graduated_regime:
            for s in US_SECTORS:
                sdf = self.daily_data.get(s)
                if sdf is None or sdf.empty or "_parsed_date" not in sdf.columns:
                    continue
                six = self._index(s)
                s_sma = pd.Series(six.close).rolling(200).mean().to_numpy()
                s_lt = np.searchsorted(six.dnp, days_np, "left") - 1
                sectors.append((six, s_sma, s_lt))

        for i in range(n):
            if not spy_ok:
                continue
            p = int(lt[i])
            if p + 1 < 200:
                continue  # régimen BULL_CALM y score 1.0 por falta de historia
            c_last = float(ix.close[p])
            sma = float(sma200[p])
            # Régimen discreto
            if c_last <= sma:
                regimes[i] = MarketRegime.BEAR
            elif p >= 20:
                vol_20d = float(pd.Series(rets[p - 19 : p + 1]).std() * (252.0**0.5))
                regimes[i] = MarketRegime.BULL_VOLATILE if vol_20d > 0.22 else MarketRegime.BULL_CALM
            # Score graduado
            if self.config.enable_graduated_regime:
                cond_sma200 = 1.0 if c_last > sma else 0.0
                cond_ema50 = 1.0 if c_last > float(ema50[p]) else 0.0
                above = 0
                valid = 0
                for six, s_sma, s_lt in sectors:
                    q = int(s_lt[i])
                    if q + 1 >= 200:
                        valid += 1
                        if float(six.close[q]) > float(s_sma[q]):
                            above += 1
                breadth = (above / valid) if valid > 0 else 0.50
                cond_breadth = 1.0 if breadth > 0.50 else 0.0
                scores[i] = (cond_sma200 + cond_ema50 + cond_breadth) / 3.0
        return regimes, scores

    # ─────────────────────────────────────────────────────────────── run
    def run(
        self,
        start_date: date,
        end_date: date,
        resolution: str = "daily",
    ) -> BacktestResult:
        """Ejecuta el replay cronológico desde start_date hasta end_date."""
        norm_res = resolution.lower().strip()
        frames = list(self.daily_data.values()) + list(self.intraday_data.values())
        if self._legacy:
            is_synthetic = any(getattr(df, "attrs", {}).get("is_synthetic", False) for df in frames)
        else:
            # El bypass solo aplica si TODOS los datos son sintéticos (un solo DataFrame sintético
            # no puede desactivar la guarda sobre datos reales).
            is_synthetic = bool(frames) and all(
                getattr(df, "attrs", {}).get("is_synthetic", False) for df in frames
            )
        if norm_res in _INTRADAY_5M:
            guard_res = "5m"
        elif norm_res in _INTRADAY_1H:
            guard_res = "hourly"
        else:
            guard_res = norm_res if self._legacy else "daily"
        assert_not_holdout(start_date, end_date, resolution=guard_res, allow_synthetic=is_synthetic)

        # Ledger de variantes probadas (DSR)
        if self.config.trial_ledger_path and self.config.trial_family:
            fp = _strategy_fingerprint(self.strategy, self.config, start_date, end_date, norm_res)
            n_trials = register_trial(self.config.trial_ledger_path, self.config.trial_family, fp)
            self.num_tested_trials = max(self.config.num_tested_trials, n_trials)
        elif not self._legacy and self.num_tested_trials <= 1:
            self._warn(
                "num_tested_trials=1: el DSR no corrige por las variantes probadas. "
                "Definí trial_ledger_path + trial_family (o num_tested_trials)."
            )

        if norm_res in _INTRADAY_5M + _INTRADAY_1H:
            return self._run_intraday(start_date, end_date, resolution=norm_res)
        return self._run_daily(start_date, end_date)

    def _run_intraday(
        self,
        start_date: date,
        end_date: date,
        resolution: str = "5m",
    ) -> BacktestResult:
        """Ejecuta la simulación barra a barra en resolución intradiaria (5m o 1h)."""
        if not self.intraday_data:
            raise ValueError(
                f"BacktestEngine en resolución intradiaria ('{resolution}') requiere historical_intraday no vacío."
            )

        # 1. Asegurar parsing de timestamps y fechas
        for df in self.intraday_data.values():
            if df.empty:
                continue
            if "_parsed_ts" not in df.columns:
                if "datetime_et" in df.columns:
                    df["_parsed_ts"] = pd.to_datetime(df["datetime_et"])
                elif "timestamp" in df.columns:
                    if pd.api.types.is_numeric_dtype(df["timestamp"]):
                        df["_parsed_ts"] = pd.to_datetime(df["timestamp"], unit="s")
                    else:
                        df["_parsed_ts"] = pd.to_datetime(df["timestamp"])
                elif "date" in df.columns:
                    df["_parsed_ts"] = pd.to_datetime(df["date"])
            if "_parsed_date" not in df.columns:
                if "date" in df.columns:
                    df["_parsed_date"] = pd.to_datetime(df["date"]).dt.date
                elif "_parsed_ts" in df.columns:
                    df["_parsed_date"] = df["_parsed_ts"].dt.date

        # 2. Timeline cronológico ordenado dentro del rango [start_date, end_date]
        all_ts_set: set[datetime] = set()
        for df in self.intraday_data.values():
            if "_parsed_ts" in df.columns and "_parsed_date" in df.columns:
                valid_ts = df[(df["_parsed_date"] >= start_date) & (df["_parsed_date"] <= end_date)]["_parsed_ts"]
                all_ts_set.update(valid_ts)
        timeline = sorted(all_ts_set)

        # 3. Lookup estructurado de barras por timestamp
        bar_lookup: dict[str, dict[Any, dict[str, Any]]] = {}
        for sym, df in self.intraday_data.items():
            if "_parsed_ts" in df.columns and "_parsed_date" in df.columns:
                sub = df[(df["_parsed_date"] >= start_date) & (df["_parsed_date"] <= end_date)]
                bar_lookup[sym] = sub.set_index("_parsed_ts").to_dict(orient="index")
            else:
                bar_lookup[sym] = {}

        # 4. Asegurar fechas parseadas en datos diarios si existen
        for df in self.daily_data.values():
            if df.empty or "_parsed_date" in df.columns:
                continue
            d_col = "date" if "date" in df.columns else "timestamp"
            df["_parsed_date"] = pd.to_datetime(df[d_col]).dt.date

        prev_date: date | None = None
        daily_equity_records: dict[date, float] = {}
        cb_events: list[CircuitBreakerEvent] = []
        current_prices: dict[str, Decimal] = {}
        opening_equity = self.broker.cash
        halt_session = False
        spy_df = self.daily_data.get("SPY", pd.DataFrame())
        current_regime: MarketRegime = MarketRegime.BULL_CALM
        regime_scores: dict[date, float] = {}

        L = self._legacy
        strategy = self.strategy
        has_hook = strategy is not None and callable(getattr(strategy, "manage_positions", None))
        use_overlay = self.config.trailing_ema_period > 0 and (L or self.config.exit_overlay == "always" or (self.config.exit_overlay == "auto" and not has_hook))
        if not L and use_overlay and strategy is not None and not has_hook:
            self._warn(
                f"La estrategia '{getattr(strategy, 'id', '?')}' no implementa manage_positions: se le aplica "
                f"el overlay genérico de salidas intradía (EMA{self.config.trailing_ema_period})."
            )
        # Última barra de cada sesión (el cierre forzado se ejecuta ahí aunque la barra empiece antes de flatten_time)
        last_ts_of_day: dict[date, Any] = {}
        for ts_ in timeline:
            last_ts_of_day[ts_.date()] = ts_
        # Duración típica de barra para convertir max_holding (timedelta) a cantidad de barras
        bar_td = timedelta(minutes=5)
        if len(timeline) > 1:
            diffs = pd.Series(timeline).diff().dropna()
            if not diffs.empty:
                bar_td = diffs.min() if not L else bar_td
        if self.broker._trading_days == [] and not L:
            self.broker.set_trading_days(sorted({t.date() for t in timeline}))

        flatten_time = getattr(self.config, "flatten_time", time(15, 55))
        start_entry_time = getattr(self.config, "intraday_start_time", time(9, 45))
        end_entry_time = getattr(self.config, "intraday_end_time", time(15, 30))

        curr_intraday_idx: dict[str, int] = {s: -1 for s in self.intraday_data}
        curr_daily_idx: dict[str, int] = {s: -1 for s in self.daily_data} if self.daily_data else {}

        day_counter = 0
        for ts in timeline:
            cur_date = ts.date()
            cur_time = ts.time()

            # Punteros cronológicos estrictos hasta ts
            for s, df in self.intraday_data.items():
                if "_parsed_ts" in df.columns:
                    while (curr_intraday_idx[s] + 1 < len(df)) and (df["_parsed_ts"].iat[curr_intraday_idx[s] + 1] <= ts):
                        curr_intraday_idx[s] += 1
            if self.daily_data:
                for s, df in self.daily_data.items():
                    if "_parsed_date" in df.columns:
                        while (curr_daily_idx[s] + 1 < len(df)) and (df["_parsed_date"].iat[curr_daily_idx[s] + 1] < cur_date):
                            curr_daily_idx[s] += 1

            # A. Detección de inicio de sesión diaria
            if prev_date is None or cur_date != prev_date:
                if prev_date is not None:
                    daily_equity_records[prev_date] = float(self.broker.get_equity(current_prices))
                    if self._margin_on:
                        self._eod_margin_check(prev_date, ts, current_prices)
                self.broker.process_settlement(cur_date)
                if not L:
                    self._charge_margin_interest(cur_date)

                # Rendimiento de efectivo remanente (Cash Yield / BIL)
                self._apply_cash_yield(cur_date)

                for sym in self.intraday_data:
                    if ts in bar_lookup[sym]:
                        current_prices[sym] = Decimal(str(round(float(bar_lookup[sym][ts]["open"]), 4)))
                if not L and self._margin_call_pending:
                    self._liquidate_for_margin(cur_date, ts, current_prices, day_counter, "margin_call_liquidation")
                    self._margin_call_pending = False
                opening_equity = self.broker.get_equity(current_prices)
                if self.cb_manager:
                    self.cb_manager.reset_daily(opening_equity, cur_date)
                    self._apply_streak_cooldown(day_counter)
                halt_session = False
                day_counter += 1
                prev_date = cur_date

                # Régimen dinámico a nivel diario
                current_regime = self._determine_regime(spy_df, cur_date)
                if self.config.enable_graduated_regime and self.daily_data:
                    regime_scores[cur_date] = compute_graduated_regime_score(self.daily_data, cur_date)
                else:
                    regime_scores[cur_date] = 1.0

                # Rotación defensiva ante régimen bajista si está configurada (S5 100% Cash)
                if self.config.exit_on_bear_regime and current_regime == MarketRegime.BEAR and self.broker.positions:
                    for s in list(self.broker.positions.keys()):
                        cur_p = current_prices.get(s, self.broker.positions[s].entry_price)
                        tr = self.broker.close_position(s, cur_p, ts, reason="regime_bear_exit")
                        self._record_trade(tr, day_counter)

            # B. Actualizar precios corrientes con barra actual
            for sym in self.intraday_data:
                if ts in bar_lookup[sym]:
                    current_prices[sym] = Decimal(str(round(float(bar_lookup[sym][ts]["close"]), 4)))

            # C. Monitoreo de cortacircuitos con equity de barra
            if self.cb_manager and not halt_session:
                cur_eq = self.broker.get_equity(current_prices)
                self.cb_manager.update_equity(cur_eq)
                if self.cb_manager.requires_flatten():
                    halt_session = True
                    liquidated = []
                    for s in list(self.broker.positions.keys()):
                        exit_p = current_prices.get(
                            s, Decimal("100.0") if L else self.broker.positions[s].entry_price
                        )
                        tr = self.broker.close_position(
                            s, exit_p, ts, reason="circuit_breaker_emergency_flatten"
                        )
                        if tr:
                            if not L:
                                self._record_trade(tr, day_counter)
                            liquidated.append(s)
                    cb_events.append(
                        CircuitBreakerEvent(
                            date=cur_date,
                            timestamp=ts,
                            event_type="EMERGENCY_FLATTEN",
                            intraday_loss_pct=(
                                float(((opening_equity - cur_eq) / opening_equity) * 100)
                                if opening_equity > 0
                                else 0.0
                            ),
                            threshold_pct=self.config.emergency_loss_limit_pct,
                            starting_equity=float(opening_equity),
                            equity_at_trigger=float(cur_eq),
                            liquidated_positions=liquidated,
                        )
                    )

            # D. Evaluación y salida de posiciones abiertas
            is_flatten = (cur_time >= flatten_time) if L else (cur_time >= flatten_time or ts == last_ts_of_day.get(cur_date))
            for sym in list(self.broker.positions.keys()):
                pos = self.broker.positions.get(sym)
                if pos is None or ts not in bar_lookup[sym]:
                    continue
                b = bar_lookup[sym][ts]
                b_open = Decimal(str(round(float(b["open"]), 4)))
                b_high = Decimal(str(round(float(b["high"]), 4)))
                b_low = Decimal(str(round(float(b["low"]), 4)))
                b_close = Decimal(str(round(float(b["close"]), 4)))

                pos.bars_held += 1

                ema_val = None
                if use_overlay and curr_intraday_idx.get(sym, -1) >= 0:
                    if L:
                        start_pos = max(0, curr_intraday_idx[sym] - 30)
                        recent_c = self.intraday_data[sym]["close"].iloc[start_pos: curr_intraday_idx[sym] + 1].astype(float)
                        if len(recent_c) >= self.config.trailing_ema_period:
                            ema_val = float(ema(recent_c, self.config.trailing_ema_period).iloc[-1])
                    else:
                        ema_series = self._intraday_ema(sym)
                        ema_val = float(ema_series[curr_intraday_idx[sym]])
                        if curr_intraday_idx[sym] + 1 < self.config.trailing_ema_period:
                            ema_val = None

                hit_trailing = False
                if ema_val is not None and L:
                    # v2.2: el stop se subía con el cierre de esta barra y se comparaba con su mínimo.
                    pos.current_stop = max(
                        pos.current_stop,
                        Decimal(str(round(ema_val * (1.0 - self.config.stop_buffer_pct), 4))),
                    )
                if ema_val is not None and pos.bars_held >= 2 and b_close < Decimal(str(round(ema_val, 4))):
                    hit_trailing = True

                # El stop vigente es el de la barra anterior (no se usa información de esta barra).
                hit_stop = (b_low <= pos.current_stop)
                hit_tp = (not L) and pos.take_profit is not None and b_high >= pos.take_profit
                max_bars = pos.max_holding_bars
                hit_max_bars = (
                    isinstance(max_bars, (int, float))
                    and max_bars > 0
                    and pos.bars_held >= max_bars
                )
                day_end_exit = is_flatten and pos.exit_at_close

                exit_p = None
                if hit_stop:
                    exit_p = min(b_open, pos.current_stop)
                    reason = "stop_loss"
                elif hit_tp:
                    exit_p = b_open if b_open >= pos.take_profit else pos.take_profit
                    reason = "take_profit"
                elif hit_trailing:
                    exit_p = b_close
                    reason = "trailing_ema"
                elif day_end_exit:
                    exit_p = b_close
                    reason = "day_end_flatten"
                elif hit_max_bars:
                    exit_p = b_close
                    reason = "max_holding_bars"

                if exit_p is not None:
                    tr = self.broker.close_position(sym, exit_p, ts, reason=reason)
                    self._record_trade(tr, day_counter)
                elif ema_val is not None and not L:
                    # Actualizar el stop con el cierre de esta barra: rige desde la próxima.
                    pos.current_stop = max(
                        pos.current_stop,
                        Decimal(str(round(ema_val * (1.0 - self.config.stop_buffer_pct), 4))),
                    )

            # D.2 Salidas propias de la estrategia
            if has_hook and not L and self.broker.positions:
                ctx_m = StrategyContext(
                    now=ts,
                    regime=current_regime,
                    daily_bars={
                        s: self.daily_data[s].iloc[: curr_daily_idx[s] + 1]
                        for s in self.daily_data if curr_daily_idx.get(s, -1) >= 0
                    },
                    intraday_bars={
                        s: self.intraday_data[s].iloc[: curr_intraday_idx[s] + 1]
                        for s in self.intraday_data if curr_intraday_idx[s] >= 0
                    },
                    current_prices=current_prices,
                    portfolio_positions=set(self.broker.positions.keys()),
                    positions=self._snapshots(),
                )
                for act in strategy.manage_positions(ctx_m) or []:
                    pos_a = self.broker.positions.get(act.symbol)
                    if pos_a is None:
                        continue
                    if act.action == "exit":
                        tr = self.broker.close_position(
                            act.symbol, current_prices.get(act.symbol, pos_a.entry_price), ts,
                            reason=act.reason or "strategy_exit",
                        )
                        self._record_trade(tr, day_counter)
                    elif act.action == "update_stop" and act.new_stop is not None:
                        pos_a.current_stop = max(pos_a.current_stop, Decimal(str(act.new_stop)))
                    elif act.action == "reset_holding":
                        pos_a.bars_held = 0

            # E. Generación de señales y ejecución de compras
            can_buy = (
                (not halt_session)
                and (start_entry_time <= cur_time <= end_entry_time)
                and (self.cb_manager.can_open_new_positions() if self.cb_manager else True)
                and len(self.broker.positions) < self.max_open_positions
            )

            if can_buy and self.strategy is not None:
                sliced_intraday = {
                    s: self.intraday_data[s].iloc[: curr_intraday_idx[s] + 1]
                    for s in self.intraday_data
                    if curr_intraday_idx[s] >= 0
                }
                sliced_daily = {
                    s: self.daily_data[s].iloc[: curr_daily_idx[s] + 1]
                    for s in self.daily_data
                    if curr_daily_idx[s] >= 0
                } if self.daily_data else {}

                ctx = StrategyContext(
                    now=ts,
                    regime=current_regime,
                    daily_bars=sliced_daily,
                    intraday_bars=sliced_intraday,
                    current_prices=current_prices,
                    portfolio_positions=set(self.broker.positions.keys()),
                    positions={} if L else self._snapshots(),
                )
                signals = self.strategy.generate(ctx)

                # Rotación defensiva: suprimir nuevas entradas si exit_on_bear_regime está activo en BEAR
                if self.config.exit_on_bear_regime and current_regime == MarketRegime.BEAR:
                    continue

                for sig in signals:
                    if sig.side == "sell":
                        if not L and sig.symbol in self.broker.positions:
                            tr = self.broker.close_position(
                                sig.symbol, current_prices.get(sig.symbol, self.broker.positions[sig.symbol].entry_price),
                                ts, reason="explicit_sell_signal",
                            )
                            self._record_trade(tr, day_counter)
                        continue
                    if len(self.broker.positions) >= self.max_open_positions:
                        break
                    if sig.symbol in self.broker.positions:
                        continue

                    equity = self.broker.get_equity(current_prices)
                    self.broker.marks = current_prices
                    lev_f = float(self._lev)
                    alloc = min(
                        float(self.broker.buying_power),
                        float(equity) * self.config.single_position_cap * lev_f,
                    )
                    if not L and sig.target_weight is not None:
                        alloc = min(alloc, float(equity) * max(0.0, float(sig.target_weight)) * lev_f)
                    if alloc < self.config.min_position_usd:
                        continue

                    price_f = float(sig.entry_price_ref)
                    if price_f <= 0:
                        continue
                    shares_f = alloc / price_f
                    qty = Decimal(int(shares_f)) if self.config.integer_shares else Decimal(str(round(shares_f, 4)))
                    if qty <= 0:
                        continue
                    new_pos = self.broker.submit_buy(
                        sig, qty, sig.entry_price_ref if L else current_prices.get(sig.symbol, sig.entry_price_ref), ts
                    )
                    if new_pos is not None and not L:
                        mh = sig.max_holding
                        if isinstance(mh, timedelta):
                            new_pos.max_holding_bars = max(1, math.ceil(mh / bar_td)) if mh.total_seconds() > 0 else 0
                        elif isinstance(mh, int):
                            new_pos.max_holding_bars = max(0, mh)

        # Registro del último día
        if prev_date is not None:
            daily_equity_records[prev_date] = float(self.broker.get_equity(current_prices))

        # Cierre final al término de la simulación
        if timeline and self.broker.positions:
            last_ts = timeline[-1]
            for s in list(self.broker.positions.keys()):
                self.broker.close_position(
                    s,
                    current_prices.get(s, Decimal("100.0") if L else self.broker.positions[s].entry_price),
                    last_ts,
                    reason="end_of_period",
                )
            if not L and prev_date is not None:
                daily_equity_records[prev_date] = float(self.broker.get_equity(current_prices))

        return self._finalize(daily_equity_records, cb_events, regime_scores, {}, "intraday_backtest")

    def _intraday_ema(self, sym: str) -> np.ndarray:
        """EMA (adjust=False) de toda la serie intradía del símbolo (causal: valor en i = EMA de [0..i])."""
        cache = getattr(self, "_intra_ema_cache", None)
        if cache is None:
            cache = {}
            self._intra_ema_cache = cache
        arr = cache.get(sym)
        if arr is None:
            arr = ema(self.intraday_data[sym]["close"].astype(float).reset_index(drop=True),
                      self.config.trailing_ema_period).to_numpy(dtype=float)
            cache[sym] = arr
        return arr

    # ─────────────────────────────────────────────────────────────── helpers diarios
    def _vol_control(
        self,
        di: int,
        le: dict[str, np.ndarray],
        weights: dict[str, float],
    ) -> tuple[float, float] | None:
        """Volatilidad de cartera (covarianza contraída) y factor de escala k para `weights`.

        Modo corregido: retornos alineados por FECHA. Modo legacy: alineados por número de
        fila (reproduce el desfase del motor v2.2 entre símbolos con distinta historia).
        """
        lb = self.config.vol_lookback_days
        cols: dict[str, pd.Series] = {}
        for s in weights:
            if s not in self.daily_data:
                continue
            k = int(le[s][di])
            if k + 1 < 2:
                continue
            ix = self._index(s)
            r, first_row = ix.returns_window(k, lb)
            if len(r) == 0:
                continue
            if self._legacy:
                labels = np.arange(first_row, first_row + len(r))
            else:
                labels = ix.dnp[first_row : first_row + len(r)]
            cols[s] = pd.Series(r, index=labels)
        rets = pd.DataFrame(cols).dropna()
        if len(rets) < 20:
            return None
        target_vol = self.config.target_portfolio_vol
        if not self._legacy and self.config.vol_target_scales_with_leverage:
            target_vol *= float(self._lev)
        sigma_p, k_s, _ = compute_portfolio_volatility_and_scale(
            weights=weights,
            returns_matrix=rets,
            target_vol=target_vol,
            shrinkage_lambda=self.config.vol_shrinkage_lambda,
        )
        return sigma_p, k_s

    def _cb_daily_check(
        self,
        day: date,
        di: int,
        eval_dt: datetime,
        opening_equity: Decimal,
        current_prices: dict[str, Decimal],
        opening_prices: dict[str, Decimal],
        today_row: dict[str, int],
        cb_events: list[CircuitBreakerEvent],
    ) -> bool:
        """Cortacircuitos con barras diarias. Devuelve True si hubo liquidación de emergencia."""
        cb = self.cb_manager
        if cb is None:
            return False
        L = self._legacy
        if not self.broker.positions:
            if not L:
                # Sin posiciones igual se evalúan la racha perdedora y el límite semanal.
                cb.update_equity(self.broker.get_equity(current_prices))
                if not cb.can_open_new_positions():
                    self._cb_event(cb_events, day, eval_dt, "PAUSED_DAILY_LOSS", opening_equity,
                                   self.broker.get_equity(current_prices), self.config.daily_loss_limit_pct, [])
            return False

        # Peor caso intradía: cada posición en el mínimo del día.
        worst_pos_val = _D0
        for s, p in self.broker.positions.items():
            r = today_row.get(s, -1)
            if r >= 0:
                worst_pos_val += p.qty * _dec4(self._index(s).low[r])
            else:
                worst_pos_val += p.qty * current_prices.get(s, p.entry_price)
        worst_equity = self.broker.cash + worst_pos_val
        cb.update_equity(worst_equity)

        if cb.requires_flatten():
            liquidated: list[str] = []
            if L or self.config.cb_flatten_model == "worst_low":
                for s in list(self.broker.positions.keys()):
                    r = today_row.get(s, -1)
                    low_p = _dec4(self._index(s).low[r]) if r >= 0 else current_prices.get(s, Decimal("100.0"))
                    tr = self.broker.close_position(
                        s, low_p, eval_dt, reason="circuit_breaker_emergency_flatten", is_gap=True
                    )
                    if tr:
                        self._record_trade(tr, di)
                        liquidated.append(s)
                trigger_eq = worst_equity
            else:
                # Interpolación lineal apertura -> mínimos: se liquida donde la cartera cruza el umbral.
                thr = Decimal(str(self.config.emergency_loss_limit_pct / 100.0))
                target_eq = opening_equity * (Decimal("1") - thr)
                eq_open = self.broker.cash
                for s, p in self.broker.positions.items():
                    eq_open += p.qty * opening_prices.get(s, current_prices.get(s, p.entry_price))
                if eq_open <= target_eq or eq_open <= worst_equity:
                    f = Decimal("0")
                else:
                    f = min(Decimal("1"), max(Decimal("0"), (eq_open - target_eq) / (eq_open - worst_equity)))
                trigger_eq = eq_open - f * (eq_open - worst_equity)
                for s in list(self.broker.positions.keys()):
                    p = self.broker.positions[s]
                    o = opening_prices.get(s, current_prices.get(s, p.entry_price))
                    r = today_row.get(s, -1)
                    lo = _dec4(self._index(s).low[r]) if r >= 0 else o
                    px = o - f * (o - lo) if lo < o else o
                    tr = self.broker.close_position(
                        s, px.quantize(Decimal("0.0001")), eval_dt,
                        reason="circuit_breaker_emergency_flatten", is_gap=(f == 0),
                    )
                    if tr:
                        self._record_trade(tr, di)
                        liquidated.append(s)
            self._cb_event(cb_events, day, eval_dt, "EMERGENCY_FLATTEN", opening_equity, trigger_eq,
                           self.config.emergency_loss_limit_pct, liquidated)
            return True
        if not cb.can_open_new_positions():
            self._cb_event(cb_events, day, eval_dt, "PAUSED_DAILY_LOSS", opening_equity, worst_equity,
                           self.config.daily_loss_limit_pct, [])
        return False

    @staticmethod
    def _cb_event(
        cb_events: list[CircuitBreakerEvent],
        day: date,
        ts: datetime,
        kind: str,
        opening_equity: Decimal,
        eq: Decimal,
        threshold: float,
        liquidated: list[str],
    ) -> None:
        cb_events.append(
            CircuitBreakerEvent(
                date=day,
                timestamp=ts,
                event_type=kind,
                intraday_loss_pct=float(((opening_equity - eq) / opening_equity) * 100) if opening_equity > 0 else 0.0,
                threshold_pct=threshold,
                starting_equity=float(opening_equity),
                equity_at_trigger=float(eq),
                liquidated_positions=liquidated,
            )
        )

    def _max_holding_sessions(self, sig: Signal) -> int:
        """Convierte `Signal.max_holding` a sesiones diarias (int = sesiones, timedelta = días)."""
        mh = sig.max_holding
        if isinstance(mh, timedelta):
            return max(1, mh.days) if mh.total_seconds() > 0 else 0
        if isinstance(mh, int):
            return max(0, mh)
        return 0

    # ─────────────────────────────────────────────────────────────── loop diario
    def _run_daily(
        self,
        start_date: date,
        end_date: date,
    ) -> BacktestResult:
        """Ejecuta el replay cronológico diario desde start_date hasta end_date."""
        cfg = self.config
        L = self._legacy
        broker = self.broker
        strategy = self.strategy

        all_symbols = [s for s, df in self.daily_data.items() if "_parsed_date" in df.columns and not df.empty]
        idx = {s: self._index(s) for s in all_symbols}

        all_dates_set: set[date] = set()
        for s in all_symbols:
            all_dates_set.update(idx[s].dates)
        trading_days = sorted(d for d in all_dates_set if start_date <= d <= end_date)
        if not L:
            broker.set_trading_days(sorted(all_dates_set))

        days_np = np.array(trading_days, dtype="datetime64[D]")
        le: dict[str, np.ndarray] = {}
        first: dict[str, np.ndarray] = {}
        has_today: dict[str, np.ndarray] = {}
        for s in all_symbols:
            ix = idx[s]
            le[s] = np.searchsorted(ix.dnp, days_np, "right") - 1
            f = np.searchsorted(ix.dnp, days_np, "left")
            first[s] = f
            fc = np.minimum(f, max(ix.n - 1, 0))
            has_today[s] = (f < ix.n) & (ix.dnp[fc] == days_np) if ix.n else np.zeros(len(days_np), bool)

        regimes, reg_scores = self._precompute_regimes(trading_days)

        equity_records: dict[date, float] = {}
        cb_events: list[CircuitBreakerEvent] = []
        regime_scores: dict[date, float] = {}
        portfolio_vols: dict[date, float] = {}

        eval_time = cfg.eval_time
        if strategy is not None and getattr(strategy, "id", None) == "intraday_momentum":
            eval_time = time(15, 30)

        has_hook = strategy is not None and callable(getattr(strategy, "manage_positions", None))
        if L:
            overlay_on = bool(cfg.blocks) or cfg.trailing_ema_period > 0
        elif cfg.exit_overlay == "always":
            overlay_on = cfg.trailing_ema_period > 0 or cfg.max_holding_sessions > 0
        elif cfg.exit_overlay == "never":
            overlay_on = False
        else:  # auto
            overlay_on = bool(cfg.blocks) or (strategy is not None and not has_hook)
        if not L and overlay_on and strategy is not None and not has_hook:
            self._warn(
                f"La estrategia '{getattr(strategy, 'id', '?')}' no implementa manage_positions: se le aplica "
                f"el overlay genérico de salidas (EMA{cfg.trailing_ema_period} trailing, "
                f"{cfg.max_holding_sessions} sesiones). Sus salidas propias NO están modeladas."
            )
        enforce_signal_max_hold = (not L) and strategy is not None and not has_hook
        next_open = (not L) and cfg.execution_timing == "next_open"
        mask_today = (not L) and cfg.same_bar_visibility == "open_close" and cfg.execution_timing == "close"

        universe_filter: set[str] | None = None
        if not L and cfg.universe and strategy is not None and getattr(strategy, "universe", None) is None:
            universe_filter = {s.strip().upper() for s in cfg.universe}

        if strategy is not None and callable(getattr(strategy, "prepare", None)) and not L:
            strategy.prepare(self.daily_data)

        pending_buys: list[tuple[Signal, Decimal]] = []  # (señal, asignación USD) para next_open / limit
        pending_exits: list[tuple[str, str]] = []  # (símbolo, motivo) para next_open

        def exec_exit(sym: str, reason: str, price: Decimal, ts: Any, di_: int) -> None:
            if next_open:
                pending_exits.append((sym, reason))
                return
            tr = broker.close_position(sym, price, ts, reason=reason)
            self._record_trade(tr, di_)

        for di, current_day in enumerate(trading_days):
            eval_dt = datetime.combine(current_day, eval_time)
            open_dt = datetime.combine(current_day, time(9, 30))

            # A. Liquidación T+1 al inicio de sesión (+ rendimiento del efectivo de la noche previa
            #    e intereses del saldo deudor por los días calendario transcurridos)
            broker.process_settlement(current_day)
            if not L:
                self._charge_margin_interest(current_day)
                self._apply_cash_yield(current_day)

            # B. Precios de cierre (para valuación/decisión) y de apertura de la sesión
            opening_prices: dict[str, Decimal] = {}
            current_prices: dict[str, Decimal] = {}
            today_row: dict[str, int] = {}
            for sym in all_symbols:
                ix = idx[sym]
                k = int(le[sym][di])
                if k >= 0:
                    current_prices[sym] = ix.close_dec(k)
                if has_today[sym][di]:
                    r0 = int(first[sym][di])
                    today_row[sym] = r0
                    if ix.open is not None:
                        opening_prices[sym] = ix.open_dec(r0)
                    elif k >= 0:
                        opening_prices[sym] = ix.close_dec(r0 - 1) if r0 - 1 >= 0 else current_prices[sym]
                elif k >= 0:
                    opening_prices[sym] = ix.close_dec(k)

            # B.1 Margin call del cierre anterior: liquidación forzada en la apertura
            if not L and self._margin_call_pending:
                self._liquidate_for_margin(current_day, open_dt, opening_prices, di, "margin_call_liquidation")
                self._margin_call_pending = False

            # B.2 Órdenes pendientes ejecutadas en la apertura (next_open / limit del día previo)
            broker.marks = opening_prices
            if not L and (pending_exits or pending_buys):
                for sym, reason in pending_exits:
                    if sym in broker.positions and sym in opening_prices:
                        tr = broker.close_position(sym, opening_prices[sym], open_dt, reason=reason)
                        self._record_trade(tr, di)
                pending_exits.clear()
                carry: list[tuple[Signal, Decimal]] = []
                for sig, alloc in pending_buys:
                    self._fill_pending_buy(sig, alloc, current_day, open_dt, today_row, opening_prices)
                pending_buys = carry

            opening_equity = broker.get_equity(opening_prices)
            if self.cb_manager:
                self.cb_manager.reset_daily(opening_equity, current_day)
                self._apply_streak_cooldown(di)

            # C. Régimen (precalculado con barras < día)
            regime_score = reg_scores[di] if cfg.enable_graduated_regime else 1.0
            regime_scores[current_day] = regime_score
            regime = regimes[di]

            # Rotación defensiva ante régimen bajista si está configurada
            if cfg.exit_on_bear_regime and regime == MarketRegime.BEAR and broker.positions:
                for s in list(broker.positions.keys()):
                    cur_p = current_prices.get(s, broker.positions[s].entry_price)
                    if L:
                        tr = broker.close_position(s, cur_p, eval_dt, reason="regime_bear_exit")
                        self._record_trade(tr, di)
                    else:
                        exec_exit(s, "regime_bear_exit", cur_p, eval_dt, di)

            # D. Stops / take profits / salidas al cierre y cortacircuitos
            halt_session = False
            if self.intraday_data:
                halt_session = self._daily_with_intraday_bars(
                    current_day, di, eval_dt, opening_equity, current_prices, cb_events, enforce_signal_max_hold
                )
            else:
                if L:
                    halt_session = self._cb_daily_check(
                        current_day, di, eval_dt, opening_equity, current_prices, opening_prices, today_row, cb_events
                    ) if broker.positions else False
                self._evaluate_daily_bars(current_day, di, eval_dt, today_row, enforce_signal_max_hold)
                if not L:
                    halt_session = self._cb_daily_check(
                        current_day, di, eval_dt, opening_equity, current_prices, opening_prices, today_row, cb_events
                    )

            # D.2 Guarda de equity negativo (el broker liquida sin aviso si el equity se acerca a 0)
            if self._margin_on and broker.positions:
                self._negative_equity_guard(current_day, di, eval_dt, opening_prices, today_row)
            broker.marks = current_prices

            # E. Overlay genérico de salidas (EMA trailing / sesiones máximas)
            if not halt_session and overlay_on:
                for s in list(broker.positions.keys()):
                    pos = broker.positions.get(s)
                    if pos is None or s not in idx:
                        continue
                    k = int(le[s][di])
                    if k < 0:
                        continue
                    ix = idx[s]
                    cur_close = ix.close_dec(k)
                    ema_val = _dec4(ix.ema_arr(cfg.trailing_ema_period)[k])

                    hit_stop = cur_close < pos.current_stop
                    hit_ema = pos.bars_held >= 3 and cur_close < ema_val
                    hit_max_hold = cfg.max_holding_sessions > 0 and pos.bars_held >= cfg.max_holding_sessions

                    if hit_stop or hit_ema or hit_max_hold:
                        reason = "trailing_ema25" if hit_ema else ("stop_loss" if hit_stop else "max_holding")
                        if L:
                            tr = broker.close_position(s, cur_close, eval_dt, reason=reason)
                            self._record_trade(tr, di)
                        else:
                            exec_exit(s, reason, cur_close, eval_dt, di)
                    else:
                        pos.current_stop = max(
                            pos.current_stop,
                            _dec4(float(ema_val) * (1.0 - cfg.stop_buffer_pct)),
                        )

            # E.2 Salidas propias de la estrategia (hook manage_positions)
            lazy_bars: _LazyDailyBars | None = None
            if strategy is not None and not L:
                ends = {s: int(le[s][di]) + 1 for s in all_symbols}
                lazy_bars = _LazyDailyBars(idx, ends, current_day, mask_today)
            if has_hook and not L and broker.positions:
                ctx_m = StrategyContext(
                    now=eval_dt,
                    regime=regime,
                    daily_bars=lazy_bars,  # type: ignore[arg-type]
                    intraday_bars={},
                    current_prices=current_prices,
                    portfolio_positions=set(broker.positions.keys()),
                    positions=self._snapshots(),
                )
                for act in strategy.manage_positions(ctx_m) or []:
                    self._apply_position_action(act, current_prices, eval_dt, di, exec_exit)

            # F. Generación de señales y ejecución de entradas
            can_buy = (not halt_session) and (self.cb_manager.can_open_new_positions() if self.cb_manager else True)

            if strategy is not None and (can_buy or not L):
                if L:
                    sliced_daily = {s: idx[s].df.iloc[: int(le[s][di]) + 1] for s in all_symbols if le[s][di] >= 0}
                    daily_ctx: Any = sliced_daily
                else:
                    daily_ctx = lazy_bars
                sliced_intraday: dict[str, pd.DataFrame] = {}
                for sym in all_symbols:
                    if sym in self.intraday_data:
                        i_df = self.intraday_data[sym]
                        i_past = i_df[i_df["_parsed_ts"] <= eval_dt]
                        if not i_past.empty:
                            sliced_intraday[sym] = i_past

                ctx = StrategyContext(
                    now=eval_dt,
                    regime=regime,
                    daily_bars=daily_ctx,
                    intraday_bars=sliced_intraday,
                    current_prices=current_prices,
                    portfolio_positions=set(broker.positions.keys()),
                    positions={} if L else self._snapshots(),
                )
                signals = strategy.generate(ctx)

                # Primero las ventas explícitas
                for sig in signals:
                    if sig.side == "sell" and sig.symbol in broker.positions:
                        cur_p = current_prices.get(sig.symbol, broker.positions[sig.symbol].entry_price)
                        if L:
                            tr = broker.close_position(sig.symbol, cur_p, current_day, reason="explicit_sell_signal")
                            self._record_trade(tr, di)
                        else:
                            exec_exit(sig.symbol, "explicit_sell_signal", cur_p, eval_dt, di)

                # Después las compras
                if can_buy:
                    for sig in signals:
                        if sig.side != "buy":
                            continue
                        if len(broker.positions) + (len(pending_buys) if not L else 0) >= self.max_open_positions:
                            break
                        if sig.symbol in broker.positions:
                            continue
                        if universe_filter is not None and sig.symbol not in universe_filter:
                            self._warn("Se descartaron señales fuera de config.universe.")
                            continue
                        self._process_buy_signal(
                            sig, di, current_day, eval_dt, le, current_prices, today_row,
                            portfolio_vols, pending_buys, next_open,
                        )

            elif cfg.blocks and can_buy and strategy is None:
                self._run_blocks_day(
                    current_day, di, eval_dt, trading_days, le, current_prices, regime_score, portfolio_vols
                )

            # G. Rendimiento del efectivo (legacy: al final del día)
            if L:
                self._apply_cash_yield(current_day)

            # H. Registro EOD
            day_equity = float(broker.get_equity(current_prices))
            equity_records[current_day] = day_equity
            if self._margin_on:
                self._eod_margin_check(current_day, eval_dt, current_prices)
            if self.cb_manager:
                self.cb_manager.update_equity(Decimal(str(day_equity)))

        # Teardown: cerrar posiciones remanentes al cierre final
        if trading_days and broker.positions:
            last_day = trading_days[-1]
            last_dt = datetime.combine(last_day, time(16, 0))
            for s in list(broker.positions.keys()):
                r = today_row.get(s, -1) if s in idx else -1
                last_p = idx[s].close_dec(r) if r >= 0 else current_prices.get(s, broker.positions[s].entry_price if not L else Decimal("100.0"))
                tr = broker.close_position(s, last_p, last_dt, reason="end_of_period")
                self._record_trade(tr, len(trading_days) - 1)
            if not L:
                # La curva refleja los costos de la liquidación final.
                equity_records[last_day] = float(broker.get_equity(current_prices))

        return self._finalize(equity_records, cb_events, regime_scores, portfolio_vols, "unified_backtest")

    # ─────────────────────────────────────────────────────────────── piezas del loop diario
    def _evaluate_daily_bars(
        self, day: date, di: int, eval_dt: datetime, today_row: dict[str, int], enforce_max_hold: bool
    ) -> None:
        """Stops, take profits y salidas al cierre con la barra diaria de la sesión."""
        L = self._legacy
        for s in list(self.broker.positions.keys()):
            pos = self.broker.positions.get(s)
            if pos is None:
                continue
            r = today_row.get(s, -1)
            if r < 0:
                continue
            ix = self._index(s)
            tr = self.broker.evaluate_bar(
                symbol=s,
                bar_open=Decimal(str(float(ix.open[r]))),
                bar_high=Decimal(str(float(ix.high[r]))),
                bar_low=Decimal(str(float(ix.low[r]))),
                bar_close=Decimal(str(float(ix.close[r]))),
                timestamp=eval_dt,
                is_market_close=pos.exit_at_close if L else True,
                enforce_max_holding=enforce_max_hold,
            )
            self._record_trade(tr, di)

    def _apply_position_action(
        self,
        act: PositionAction,
        current_prices: dict[str, Decimal],
        eval_dt: datetime,
        di: int,
        exec_exit: Any,
    ) -> None:
        pos = self.broker.positions.get(act.symbol)
        if pos is None:
            return
        if act.action == "exit":
            price = current_prices.get(act.symbol, pos.entry_price)
            exec_exit(act.symbol, act.reason or "strategy_exit", price, eval_dt, di)
        elif act.action == "update_stop" and act.new_stop is not None:
            pos.current_stop = max(pos.current_stop, Decimal(str(act.new_stop)))
        elif act.action == "reset_holding":
            pos.bars_held = 0

    def _process_buy_signal(
        self,
        sig: Signal,
        di: int,
        current_day: date,
        eval_dt: datetime,
        le: dict[str, np.ndarray],
        current_prices: dict[str, Decimal],
        today_row: dict[str, int],
        portfolio_vols: dict[date, float],
        pending_buys: list[tuple[Signal, Decimal]],
        next_open: bool,
    ) -> None:
        cfg = self.config
        L = self._legacy
        broker = self.broker
        equity = broker.get_equity(current_prices)

        if L:
            ref_price = sig.entry_price_ref
        else:
            # Precio de ejecución real: cierre de la sesión (no el precio de referencia de la señal).
            ref_price = current_prices.get(sig.symbol, sig.entry_price_ref)
        if ref_price <= _D0:
            return

        lev = self._lev
        max_alloc = equity * Decimal(str(cfg.single_position_cap)) * lev
        if not L and sig.target_weight is not None:
            raw_alloc = min(equity * Decimal(str(max(0.0, float(sig.target_weight)))) * lev, max_alloc)
        else:
            risk_usd = Decimal(str(self.risk_per_trade_pct / 100.0)) * equity
            risk_per_unit = (sig.entry_price_ref if L else ref_price) - sig.stop_price
            if risk_per_unit <= _D0:
                return
            raw_alloc = min(risk_usd * ((sig.entry_price_ref if L else ref_price) / risk_per_unit), max_alloc)
        if not L and sig.stop_price >= ref_price:
            return  # stop inválido respecto del precio de ejecución

        # B-03 Control de volatilidad de cartera
        vol_scale = 1.0
        if cfg.enable_vol_control:
            target_w: dict[str, float] = {}
            for s, p in broker.positions.items():
                p_val = p.qty * current_prices.get(s, p.entry_price)
                target_w[s] = float(p_val / equity) if equity > 0 else 0.0
            target_w[sig.symbol] = float(raw_alloc / equity) if equity > 0 else 0.0
            res = self._vol_control(di, le, target_w)
            if res is not None:
                portfolio_vols[current_day] = res[0]
                vol_scale = res[1]

        final_alloc = raw_alloc * Decimal(str(vol_scale))
        if final_alloc < Decimal(str(cfg.min_position_usd)):
            return

        if next_open or (
            not L and sig.entry_type == "limit" and sig.limit_price is not None and sig.limit_price < ref_price
        ):
            pending_buys.append((sig, final_alloc))
            return

        qty = final_alloc / ref_price
        if cfg.integer_shares:
            qty = Decimal(math.floor(float(qty)))
        elif not L:
            qty = qty.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if L:
            if qty < Decimal("1"):
                return
        elif qty <= 0:
            return
        pos = broker.submit_buy(sig, qty, ref_price, eval_dt)
        if pos is not None and not L:
            pos.max_holding_bars = self._max_holding_sessions(sig)

    def _fill_pending_buy(
        self,
        sig: Signal,
        alloc: Decimal,
        day: date,
        open_dt: datetime,
        today_row: dict[str, int],
        opening_prices: dict[str, Decimal],
    ) -> None:
        """Ejecuta en la apertura una compra pendiente (next_open o límite del día anterior)."""
        broker = self.broker
        if sig.symbol in broker.positions or len(broker.positions) >= self.max_open_positions:
            return
        r = today_row.get(sig.symbol, -1)
        if r < 0 or sig.symbol not in opening_prices:
            return
        ix = self._index(sig.symbol)
        o = opening_prices[sig.symbol]
        if sig.entry_type == "limit" and sig.limit_price is not None:
            if o <= sig.limit_price:
                px = o
            elif _dec4(ix.low[r]) <= sig.limit_price:
                px = sig.limit_price
            else:
                return  # límite no alcanzado: orden de día cancelada
        else:
            px = o
        if sig.stop_price >= px:
            return  # abrió por debajo del stop: la señal quedó invalidada
        qty = alloc / px
        if self.config.integer_shares:
            qty = Decimal(math.floor(float(qty)))
        else:
            qty = qty.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if qty <= 0:
            return
        pos = broker.submit_buy(sig, qty, px, open_dt)
        if pos is not None:
            pos.max_holding_bars = self._max_holding_sessions(sig)
            # El stop de la barra de hoy se evalúa en el paso D (la posición ya está abierta).

    def _daily_with_intraday_bars(
        self,
        current_day: date,
        di: int,
        eval_dt: datetime,
        opening_equity: Decimal,
        current_prices: dict[str, Decimal],
        cb_events: list[CircuitBreakerEvent],
        enforce_max_hold: bool,
    ) -> bool:
        """Modo diario con barras intradía disponibles: stops barra a barra hasta eval_time."""
        L = self._legacy
        for sym, _pos in list(self.broker.positions.items()):
            if sym not in self.intraday_data:
                continue
            day_intra = self.intraday_data[sym]
            t_col = "timestamp" if "timestamp" in day_intra.columns else "date"
            bars = day_intra[(day_intra["_parsed_date"] == current_day) & (day_intra["_parsed_ts"] <= eval_dt)]
            n_b = len(bars)
            for j, (_, b) in enumerate(bars.iterrows()):
                tr = self.broker.evaluate_bar(
                    symbol=sym,
                    bar_open=Decimal(str(b["open"])),
                    bar_high=Decimal(str(b["high"])),
                    bar_low=Decimal(str(b["low"])),
                    bar_close=Decimal(str(b["close"])),
                    timestamp=b[t_col] if L else b["_parsed_ts"].to_pydatetime(),
                    is_market_close=False if L else (j == n_b - 1),
                    enforce_max_holding=False,
                )
                self._record_trade(tr, di)
                if sym not in self.broker.positions:
                    break

        if not self.cb_manager:
            return False
        current_eq = self.broker.get_equity(current_prices)
        self.cb_manager.update_equity(current_eq)
        if self.cb_manager.requires_flatten():
            liquidated = []
            for s in list(self.broker.positions.keys()):
                px = current_prices.get(s, Decimal("100.0") if L else self.broker.positions[s].entry_price)
                tr = self.broker.close_position(s, px, eval_dt, reason="circuit_breaker_emergency_flatten")
                if tr:
                    self._record_trade(tr, di)
                    liquidated.append(s)
            self._cb_event(cb_events, current_day, eval_dt, "EMERGENCY_FLATTEN", opening_equity, current_eq,
                           self.config.emergency_loss_limit_pct, liquidated)
            return True
        if not self.cb_manager.can_open_new_positions():
            self._cb_event(cb_events, current_day, eval_dt, "PAUSED_DAILY_LOSS", opening_equity, current_eq,
                           self.config.daily_loss_limit_pct, [])
        return False


    # ─────────────────────────────────────────────────────────────── margen / apalancamiento
    def _margin_rate(self, day: date) -> float:
        cfg = self.config
        if cfg.margin_rate_mode == "benchmark_spread":
            return max(0.0, self._rf_map.get(day, 0.0) * 252.0) + cfg.margin_rate_spread
        return cfg.margin_annual_rate

    def _charge_margin_interest(self, day: date) -> None:
        """Intereses del saldo deudor por los días calendario desde la sesión previa (base 360)."""
        if not self._margin_on:
            return
        prev = self._prev_session_day
        self._prev_session_day = day
        if prev is None:
            return
        self.broker.accrue_margin_interest(
            (day - prev).days, self._margin_rate(prev), self.config.margin_day_count
        )

    def _eod_margin_check(self, day: date, ts: datetime, prices: dict[str, Decimal]) -> None:
        """Registra la exposición bruta y emite un margin call si no se cubre el mantenimiento."""
        broker = self.broker
        gross = broker.gross_exposure(prices)
        eq = broker.get_equity(prices)
        self._gross_lev[day] = float(gross / eq) if eq > 0 else float("inf")
        if not broker.positions:
            return
        req = broker.maintenance_requirement(prices)
        if eq < req:
            self._margin_call_pending = True
            self._cb_event(self._margin_events, day, ts, "MARGIN_CALL", eq, eq,
                           float(req / gross * 100) if gross > 0 else 0.0, [])

    def _liquidate_for_margin(
        self, day: date, ts: datetime, prices: dict[str, Decimal], di: int, reason: str
    ) -> None:
        """Vende posiciones completas (la más grande primero) hasta cubrir el mantenimiento y el
        ratio `margin_call_restore_ratio` de equity / exposición bruta."""
        broker = self.broker
        target = Decimal(str(self.config.margin_call_restore_ratio))
        eq0 = broker.get_equity(prices)
        liquidated: list[str] = []
        while broker.positions:
            gross = broker.gross_exposure(prices)
            eq = broker.get_equity(prices)
            if gross <= 0 or (eq / gross >= target and eq >= broker.maintenance_requirement(prices)):
                break
            sym = max(
                broker.positions,
                key=lambda s: broker.positions[s].qty * prices.get(s, broker.positions[s].entry_price),
            )
            px = prices.get(sym, broker.positions[sym].entry_price)
            tr = broker.close_position(sym, px, ts, reason=reason)
            self._record_trade(tr, di)
            liquidated.append(sym)
        if liquidated:
            self._cb_event(self._margin_events, day, ts, "MARGIN_LIQUIDATION", eq0,
                           broker.get_equity(prices), float(target * 100), liquidated)

    def _negative_equity_guard(
        self,
        day: date,
        di: int,
        ts: datetime,
        opening_prices: dict[str, Decimal],
        today_row: dict[str, int],
    ) -> None:
        """Si en el peor caso intradía (todas las posiciones en su mínimo) el equity llega a 0, el
        broker liquida todo en el punto donde el equity se anula (interpolado apertura -> mínimos)."""
        broker = self.broker
        worst = broker.cash
        lows: dict[str, Decimal] = {}
        for s, p in broker.positions.items():
            r = today_row.get(s, -1)
            lows[s] = _dec4(self._index(s).low[r]) if r >= 0 else opening_prices.get(s, p.entry_price)
            worst += p.qty * lows[s]
        if worst > 0:
            return
        eq_open = broker.cash + sum(
            (p.qty * opening_prices.get(s, p.entry_price) for s, p in broker.positions.items()), Decimal("0")
        )
        f = Decimal("0") if eq_open <= 0 else min(Decimal("1"), eq_open / (eq_open - worst))
        liquidated: list[str] = []
        for s in list(broker.positions.keys()):
            p = broker.positions[s]
            o = opening_prices.get(s, p.entry_price)
            px = (o - f * (o - lows[s])) if lows[s] < o else o
            tr = broker.close_position(s, px.quantize(Decimal("0.0001")), ts, reason="negative_equity_liquidation")
            self._record_trade(tr, di)
            liquidated.append(s)
        self._cb_event(self._margin_events, day, ts, "NEGATIVE_EQUITY_LIQUIDATION", eq_open,
                       broker.cash, 0.0, liquidated)

    def _margin_warnings(self, equity_records: dict[date, float]) -> None:
        cfg = self.config
        if not self._margin_on:
            return
        lev = float(self._lev)
        if lev > 1.0:
            gl = pd.Series(self._gross_lev, dtype=float).replace([np.inf], np.nan).dropna()
            calls = sum(1 for e in self._margin_events if e.event_type == "MARGIN_CALL")
            self._warn(
                f"Apalancamiento objetivo {lev:.2f}x: exposición bruta media {gl.mean() if len(gl) else 0:.2f}x "
                f"(máx {gl.max() if len(gl) else 0:.2f}x), intereses de margen ${float(self.broker.margin_interest_paid):,.2f}, "
                f"{calls} margin calls."
            )
            if cfg.enable_vol_control and not cfg.vol_target_scales_with_leverage:
                self._warn(
                    "El control de volatilidad usa el objetivo sin escalar: puede anular el apalancamiento."
                )
            if equity_records and min(equity_records.values()) < cfg.min_margin_equity:
                self._warn(
                    f"El equity cayó por debajo de ${cfg.min_margin_equity:,.0f}: en esos días el poder de "
                    f"compra fue 1x (regla de Alpaca)."
                )
        # Regla PDT (vigente hasta el 2026-06-04): > 3 day trades en 5 sesiones con equity < $25.000
        dts = sorted(d for d in self.broker.day_trade_dates if d < date(2026, 6, 4))
        if dts and equity_records:
            days = sorted(equity_records)
            pos = {d: i for i, d in enumerate(days)}
            idxs = [pos[d] for d in dts if d in pos]
            breaches = 0
            for j in range(len(idxs)):
                window = [i for i in idxs if idxs[j] - 4 <= i <= idxs[j]]
                if len(window) > 3 and equity_records[days[idxs[j]]] < 25000:
                    breaches += 1
            if breaches:
                self._warn(
                    f"{breaches} ocasiones con más de 3 day trades en 5 sesiones y equity < $25.000 "
                    f"(regla PDT vigente hasta jun-2026: el broker habría bloqueado la cuenta)."
                )

    def _finalize(
        self,
        equity_records: dict[date, float],
        cb_events: list[CircuitBreakerEvent],
        regime_scores: dict[date, float],
        portfolio_vols: dict[date, float],
        default_id: str,
    ) -> BacktestResult:
        equity_series = pd.Series(equity_records, dtype=float)
        equity_series.index = pd.to_datetime(equity_series.index)

        strat_id = (
            self.strategy.id
            if self.strategy is not None
            else ("universe_a_blocks" if self.config.blocks else default_id)
        )
        if self._legacy:
            metrics = compute_backtest_metrics(
                strategy_id=strat_id,
                trades=self.broker.closed_trades,
                equity_curve=equity_series,
                initial_capital=float(self.initial_capital),
                num_tested_trials=self.num_tested_trials,
            )
        else:
            if self._rf_missing_days:
                self._warn(f"{self._rf_missing_days} sesiones sin dato de BIL: el efectivo no rindió esos días.")
            if self.broker.gfv_count:
                self._warn(f"{self.broker.gfv_count} Good Faith Violations (ventas antes de liquidar los fondos).")
            self._margin_warnings(equity_records)
            metrics = compute_backtest_metrics(
                strategy_id=strat_id,
                trades=self.broker.closed_trades,
                equity_curve=equity_series,
                initial_capital=float(self.initial_capital),
                num_tested_trials=self.num_tested_trials,
                account_type=self.config.account_type,
                # Misma tasa que rinde el efectivo: el efectivo ocioso tiene exceso de retorno 0.
                rf_series=self._cash_rate_series if self._rf_map else None,
                include_initial_capital=True,
                sortino_mode="downside_deviation",
            )
            metrics.warnings = list(self._warnings)

        return BacktestResult(
            metrics=metrics,
            trades=self.broker.closed_trades,
            equity_curve=equity_series,
            circuit_breaker_events=cb_events,
            daily_regime_scores=pd.Series(regime_scores, dtype=float),
            portfolio_daily_volatility=pd.Series(portfolio_vols, dtype=float),
            warnings=list(self._warnings),
            gfv_count=self.broker.gfv_count,
            margin_events=list(self._margin_events),
            margin_interest_paid=float(self.broker.margin_interest_paid),
            gross_leverage=pd.Series(self._gross_lev, dtype=float),
            day_trades=len(self.broker.day_trade_dates),
        )

    # ─────────────────────────────────────────────────────────────── modo bloques
    def _block_momentum_ranks(self, tickers: list[str], di: int, lt: dict[str, int]) -> dict[str, float]:
        """Equivalente rápido de compute_multi_horizon_momentum (barras < día)."""
        skip = 5
        ret_21: dict[str, float] = {}
        ret_63: dict[str, float] = {}
        ret_126: dict[str, float] = {}
        for sym in tickers:
            if sym not in self.daily_data or self.daily_data[sym].empty:
                continue
            p = lt.get(sym, -1)
            if p + 1 < skip + 126:
                continue
            c = self._index(sym).close
            p_ref = float(c[p + 1 - skip])
            p_21 = float(c[p + 1 - (skip + 21)])
            p_63 = float(c[p + 1 - (skip + 63)])
            p_126 = float(c[p + 1 - (skip + 126)])
            if p_21 > 0:
                ret_21[sym] = (p_ref - p_21) / p_21
            if p_63 > 0:
                ret_63[sym] = (p_ref - p_63) / p_63
            if p_126 > 0:
                ret_126[sym] = (p_ref - p_126) / p_126
        common = set(ret_21.keys()) & set(ret_63.keys()) & set(ret_126.keys())
        if not common:
            return {}
        r21 = {s: i + 1 for i, s in enumerate(sorted(common, key=lambda s: ret_21[s], reverse=True))}
        r63 = {s: i + 1 for i, s in enumerate(sorted(common, key=lambda s: ret_63[s], reverse=True))}
        r126 = {s: i + 1 for i, s in enumerate(sorted(common, key=lambda s: ret_126[s], reverse=True))}
        return {s: (r21[s] + r63[s] + r126[s]) / 3.0 for s in common}

    def _block_abs_gate(self, sym: str, lt: dict[str, int]) -> bool:
        """Equivalente rápido de check_absolute_momentum_gate (barras < día)."""
        if sym not in self.daily_data or "BIL" not in self.daily_data:
            return False
        p = lt.get(sym, -1)
        pb = lt.get("BIL", -1)
        if p + 1 < 130 or pb + 1 < 130:
            return False
        c = self._index(sym).close
        b = self._index("BIL").close
        c_now, c_126 = c[p], c[p + 1 - 126]
        ret_126 = (c_now - c_126) / c_126
        bil_ret_126 = (b[pb] - b[pb + 1 - 126]) / b[pb + 1 - 126]
        if ret_126 <= bil_ret_126:
            return False
        return c_now >= float(self._index(sym).ema_arr(50)[p])

    def _run_blocks_day(
        self,
        current_day: date,
        di: int,
        eval_dt: datetime,
        trading_days: list[date],
        le: dict[str, np.ndarray],
        current_prices: dict[str, Decimal],
        regime_score: float,
        portfolio_vols: dict[date, float],
    ) -> None:
        """Modo BlockConfig (Universe A, asignación multi-activo)."""
        cfg = self.config
        L = self._legacy
        broker = self.broker
        if cfg.rebalance_cadence == "weekly_friday":
            if L:
                is_rebalance_day = (current_day.weekday() == 4) or (current_day == trading_days[-1])
            else:
                # Última sesión de la semana (cubre viernes feriados) y nunca el último día del
                # backtest (se liquidaría inmediatamente en el teardown).
                is_last = di == len(trading_days) - 1
                nxt = trading_days[di + 1] if not is_last else None
                is_rebalance_day = (not is_last) and (
                    nxt.isocalendar()[:2] != current_day.isocalendar()[:2]  # type: ignore[union-attr]
                )
        else:
            is_rebalance_day = True
        if not is_rebalance_day:
            return

        lt: dict[str, int] = {}
        for s in self.daily_data:
            if "_parsed_date" in self.daily_data[s].columns and not self.daily_data[s].empty:
                k = int(le[s][di])
                ix = self._index(s)
                # < día: si la última fila <= día es la de hoy, retroceder una.
                lt[s] = k - 1 if (k >= 0 and ix.dates[k] == current_day) else k

        total_equity = float(broker.get_equity(current_prices))
        for block_key, b_cfg in cfg.blocks.items():  # type: ignore[union-attr]
            effective_block_cap = b_cfg.capital_cap
            if b_cfg.modulate_by_regime:
                effective_block_cap *= regime_score
            if effective_block_cap <= 0.01:
                continue

            avg_ranks = self._block_momentum_ranks(b_cfg.tickers, di, lt)
            if not avg_ranks:
                continue
            qualified = {sym: r for sym, r in avg_ranks.items() if self._block_abs_gate(sym, lt)}
            if not qualified:
                continue

            sorted_qualified = sorted(qualified.keys(), key=lambda s: qualified[s])
            block_open = [s for s in broker.positions if s in b_cfg.tickers]

            for s in list(block_open):
                if s not in qualified or (sorted_qualified.index(s) + 1 > b_cfg.buffer_rank):
                    tr = broker.close_position(s, current_prices[s], eval_dt, reason="buffer_rank_exit")
                    self._record_trade(tr, di)
                    block_open.remove(s)

            available_slots = min(b_cfg.top_n - len(block_open), self.max_open_positions - len(broker.positions))
            if available_slots <= 0:
                continue
            entry_candidates = [s for s in sorted_qualified[: b_cfg.top_n] if s not in broker.positions][:available_slots]
            if not entry_candidates:
                continue

            vols: dict[str, float] = {}
            for s in entry_candidates:
                k = int(le[s][di])
                closes = pd.Series(self._index(s).close[max(0, k + 1 - 60) : k + 1])
                sig_v = float(closes.pct_change().dropna().std() * np.sqrt(252))
                vols[s] = sig_v if (not np.isnan(sig_v) and sig_v > 0.01) else 0.20
            inv_vols = {s: 1.0 / vols[s] for s in entry_candidates}
            sum_inv = sum(inv_vols.values())
            norm_weights = {s: inv_vols[s] / sum_inv for s in entry_candidates}

            curr_block_val = sum(float(broker.positions[s].qty * current_prices[s]) for s in block_open)
            max_block_capital = total_equity * effective_block_cap * float(self._lev)
            available_block_capital = max(0.0, max_block_capital - curr_block_val)

            vol_scale = 1.0
            if cfg.enable_vol_control:
                target_weights: dict[str, float] = {}
                for s in broker.positions:
                    pos_val = float(broker.positions[s].qty * current_prices[s])
                    target_weights[s] = pos_val / total_equity if total_equity > 0 else 0.0
                for s in entry_candidates:
                    cand_val = available_block_capital * norm_weights[s]
                    target_weights[s] = cand_val / total_equity if total_equity > 0 else 0.0
                res = self._vol_control(di, le, target_weights)
                if res is not None:
                    portfolio_vols[current_day] = res[0]
                    vol_scale = res[1]

            for s in entry_candidates:
                if len(broker.positions) >= self.max_open_positions:
                    break
                raw_alloc = available_block_capital * norm_weights[s] * vol_scale
                per_inst_cap = b_cfg.per_instrument_cap.get(
                    s, b_cfg.per_instrument_cap.get("default", cfg.single_position_cap)
                )
                max_inst_alloc = total_equity * min(per_inst_cap, cfg.single_position_cap) * float(self._lev)
                alloc = min(raw_alloc, max_inst_alloc, float(broker.buying_power))
                if alloc < cfg.min_position_usd:
                    continue
                cur_close = current_prices[s]
                shares_f = alloc / float(cur_close)
                shares = Decimal(math.floor(shares_f)) if cfg.integer_shares else Decimal(str(round(shares_f, 4)))
                if shares < Decimal("1") and (L or cfg.integer_shares):
                    continue
                if shares <= 0:
                    continue
                k = int(le[s][di])
                ema25 = float(self._index(s).ema_arr(cfg.trailing_ema_period)[k])
                stop_loss = min(float(cur_close) * 0.95, ema25 * (1.0 - cfg.stop_buffer_pct))
                sig = Signal.create(
                    strategy_id=block_key,
                    version="2.0.0",
                    symbol=s,
                    bar_ts=eval_dt,
                    side="buy",
                    entry_type="market",
                    entry_price_ref=cur_close,
                    stop_price=Decimal(str(round(stop_loss, 4))),
                )
                broker.submit_buy(sig, shares, cur_close, eval_dt)


# Compatibilidad hacia atrás garantizada
ReplayEngine = BacktestEngine
