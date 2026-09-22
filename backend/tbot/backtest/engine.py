"""Motor unificado de simulación y replay para backtesting offline (Trading-Bot v2.0).

Soporta:
- Simulación determinista barra a barra sin sesgo de anticipación (No Lookahead).
- Cableado directo del gestor de cortacircuitos (CircuitBreakerManager: -2.0% pausa, -3.5% flatten).
- Enforzamiento estricto de guardas de holdout (Regla 0 / assert_not_holdout).
- Invariante 2 de GEMINI.md: Prohibición de futuros sintéticos (USO, UNG) y shorting.
- Arquitectura de bloques de activos (BlockConfig) y modo Strategy estándar.
- Régimen de mercado graduado (SPY vs SMA200/EMA50 + amplitud de 11 sectores GICS).
- Control de volatilidad de cartera con covarianza contraída (shrunk covariance, lambda=0.3, 90d).
- Fricciones de Alpaca: spread, slippage, SEC fee, FINRA TAF, CAT fee, acciones enteras.
- Rendimiento de efectivo remanente (Cash Yield / BIL real diario).
- Compatibilidad 100% hacia atrás con ReplayEngine y desempaquetado en tupla.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
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
from tbot.backtest.simulated_broker import SimulatedBroker, SimulatedTrade
from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.risk.circuit_breakers import CircuitBreakerManager
from tbot.strategies.interfaces import Signal, Strategy, StrategyContext

# Invariantes no negociables de GEMINI.md
FORBIDDEN_SYNTHETIC_COMMODITIES: set[str] = {"USO", "UNG", "UCO", "BOIL", "SCO", "KOLD"}
PHYSICAL_COMMODITIES_ALLOWLIST: set[str] = {"GLD", "GLDM", "SLV"}
US_SECTORS: list[str] = ["XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE"]


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
    universe: list[str] | None = None

    # Capital y Broker
    initial_capital: Decimal = Decimal("2000.00")
    account_type: str = "cash"
    settlement_days: int = 1
    max_open_positions: int = 4
    single_position_cap: float = 0.25
    risk_per_trade_pct: float = 0.5

    # Cortacircuitos
    enable_circuit_breakers: bool = True
    daily_loss_limit_pct: float = 2.0
    emergency_loss_limit_pct: float = 3.5
    weekly_loss_limit_pct: float = 5.0
    max_consecutive_losses: int = 4

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

    # Rendimiento de Efectivo Remanente (Cash Yield / BIL)
    enable_cash_yield: bool = True
    rf_series: pd.Series | None = None
    annual_cash_yield_fallback: float = 0.045

    # Cadencia de Rebalanceo y Salidas
    rebalance_cadence: Literal["daily", "weekly_friday"] = "daily"
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


@dataclass
class BacktestResult:
    """Resultado unificado de la simulación con compatibilidad de desempacado en tupla."""

    metrics: BacktestMetrics
    trades: list[SimulatedTrade]
    equity_curve: pd.Series
    circuit_breaker_events: list[CircuitBreakerEvent] = field(default_factory=list)
    daily_regime_scores: pd.Series = field(default_factory=pd.Series)
    portfolio_daily_volatility: pd.Series = field(default_factory=pd.Series)

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

        # 3. Pre-parsear fechas para rendimiento
        for df in self.daily_data.values():
            if df.empty or ("date" not in df.columns and "timestamp" not in df.columns):
                continue
            d_col = "date" if "date" in df.columns else "timestamp"
            if "_parsed_date" not in df.columns:
                df["_parsed_date"] = pd.to_datetime(df[d_col]).dt.date

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

        # 4. Broker simulado con costos Alpaca y acciones enteras
        self.broker = SimulatedBroker(
            initial_capital=self.config.initial_capital,
            account_type=self.config.account_type,
            settlement_days=self.config.settlement_days,
            etf_slippage_bps=self.config.etf_slippage_bps if self.config.apply_retail_costs else 0.0,
            stock_slippage_bps=self.config.stock_slippage_bps if self.config.apply_retail_costs else 0.0,
            etf_half_spread_bps=self.config.etf_half_spread_bps if self.config.apply_retail_costs else 0.0,
            stock_half_spread_bps=self.config.stock_half_spread_bps if self.config.apply_retail_costs else 0.0,
            sec_fee_rate=self.config.sec_fee_rate if self.config.apply_retail_costs else Decimal("0.0"),
            finra_taf_per_share=self.config.finra_taf_per_share if self.config.apply_retail_costs else Decimal("0.0"),
            max_finra_taf_per_order=self.config.max_finra_taf_per_order if self.config.apply_retail_costs else Decimal("0.0"),
            cat_fee_per_share=self.config.cat_fee_per_share if self.config.apply_retail_costs else Decimal("0.0"),
            integer_shares=self.config.integer_shares,
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

        # 6. Serie de tasa libre de riesgo BIL
        if self.config.rf_series is not None:
            self.rf_series = self.config.rf_series
        else:
            self.rf_series = load_risk_free_rate_bil()

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

    def run(
        self,
        start_date: date,
        end_date: date,
        resolution: str = "daily",
    ) -> BacktestResult:
        """Ejecuta el replay cronológico desde start_date hasta end_date."""
        # Line 1: Enforzamiento estricto de Rule 0 Holdout Guard
        is_synthetic = any(
            getattr(df, "attrs", {}).get("is_synthetic", False) for df in self.daily_data.values()
        )
        if not is_synthetic and self.intraday_data:
            is_synthetic = any(
                getattr(df, "attrs", {}).get("is_synthetic", False) for df in self.intraday_data.values()
            )
        assert_not_holdout(start_date, end_date, resolution=resolution, allow_synthetic=is_synthetic)

        norm_res = resolution.lower().strip()
        if norm_res in ("5m", "5min", "minute", "intraday_5m", "hourly", "1h", "h", "hour", "intraday"):
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

        flatten_time = getattr(self.config, "flatten_time", time(15, 55))
        start_entry_time = getattr(self.config, "intraday_start_time", time(9, 45))
        end_entry_time = getattr(self.config, "intraday_end_time", time(15, 30))

        curr_intraday_idx: dict[str, int] = {s: -1 for s in self.intraday_data}
        curr_daily_idx: dict[str, int] = {s: -1 for s in self.daily_data} if self.daily_data else {}

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
                self.broker.process_settlement(cur_date)

                # Rendimiento de efectivo remanente (Cash Yield / BIL)
                if self.config.enable_cash_yield and self.broker.settled_cash > Decimal("0"):
                    if self.rf_series is not None and cur_date in self.rf_series:
                        daily_rf = max(0.0, float(self.rf_series[cur_date]))
                    else:
                        fallback = self.config.annual_cash_yield_fallback
                        daily_rf = (1.0 + fallback) ** (1.0 / 252.0) - 1.0 if fallback > 0 else 0.0
                    if daily_rf > 0.0:
                        yield_amount = self.broker.settled_cash * Decimal(str(daily_rf))
                        self.broker.settled_cash += yield_amount

                for sym in self.intraday_data:
                    if ts in bar_lookup[sym]:
                        current_prices[sym] = Decimal(str(round(float(bar_lookup[sym][ts]["open"]), 4)))
                opening_equity = self.broker.get_equity(current_prices)
                if self.cb_manager:
                    self.cb_manager.reset_daily(opening_equity, cur_date)
                halt_session = False
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
                        if tr and self.cb_manager:
                            self.cb_manager.record_trade(tr.pnl, tr.exit_time)

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
                        exit_p = current_prices.get(s, Decimal("100.0"))
                        tr = self.broker.close_position(
                            s, exit_p, ts, reason="circuit_breaker_emergency_flatten"
                        )
                        if tr:
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
            is_flatten = (cur_time >= flatten_time)
            for sym in list(self.broker.positions.keys()):
                pos = self.broker.positions.get(sym)
                if pos is None or ts not in bar_lookup[sym]:
                    continue
                b = bar_lookup[sym][ts]
                b_open = Decimal(str(round(float(b["open"]), 4)))
                b_low = Decimal(str(round(float(b["low"]), 4)))
                b_close = Decimal(str(round(float(b["close"]), 4)))

                pos.bars_held += 1

                # Trailing stop dinámico si aplica
                hit_trailing = False
                if self.config.trailing_ema_period > 0 and curr_intraday_idx.get(sym, -1) >= 0:
                    start_pos = max(0, curr_intraday_idx[sym] - 30)
                    recent_c = self.intraday_data[sym]["close"].iloc[start_pos: curr_intraday_idx[sym] + 1].astype(float)
                    if len(recent_c) >= self.config.trailing_ema_period:
                        ema_val = float(ema(recent_c, self.config.trailing_ema_period).iloc[-1])
                        new_stop = max(
                            pos.current_stop,
                            Decimal(str(round(ema_val * (1.0 - self.config.stop_buffer_pct), 4))),
                        )
                        pos.current_stop = new_stop
                        if pos.bars_held >= 2 and b_close < Decimal(str(round(ema_val, 4))):
                            hit_trailing = True

                hit_stop = (b_low <= pos.current_stop)
                hit_max_bars = (
                    isinstance(pos.max_holding_bars, (int, float))
                    and pos.max_holding_bars > 0
                    and pos.bars_held >= pos.max_holding_bars
                )
                day_end_exit = is_flatten and pos.exit_at_close

                if hit_stop or hit_trailing or hit_max_bars or day_end_exit:
                    if hit_stop:
                        exit_p = min(b_open, pos.current_stop)
                        reason = "stop_loss"
                    elif hit_trailing:
                        exit_p = b_close
                        reason = "trailing_ema"
                    elif day_end_exit:
                        exit_p = b_close
                        reason = "day_end_flatten"
                    else:
                        exit_p = b_close
                        reason = "max_holding_bars"

                    tr = self.broker.close_position(sym, exit_p, ts, reason=reason)
                    if tr and self.cb_manager:
                        self.cb_manager.record_trade(tr.pnl, tr.exit_time)

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
                )
                signals = self.strategy.generate(ctx)

                # Rotación defensiva: suprimir nuevas entradas si exit_on_bear_regime está activo en BEAR
                if self.config.exit_on_bear_regime and current_regime == MarketRegime.BEAR:
                    continue

                for sig in signals:
                    if len(self.broker.positions) >= self.max_open_positions:
                        break
                    if sig.symbol in self.broker.positions:
                        continue

                    equity = self.broker.get_equity(current_prices)
                    alloc = min(
                        float(self.broker.buying_power),
                        float(equity) * self.config.single_position_cap,
                    )
                    if alloc < self.config.min_position_usd:
                        continue

                    price_f = float(sig.entry_price_ref)
                    if price_f <= 0:
                        continue
                    shares_f = alloc / price_f
                    qty = Decimal(int(shares_f)) if self.config.integer_shares else Decimal(str(round(shares_f, 4)))
                    if qty <= 0:
                        continue
                    self.broker.submit_buy(sig, qty, sig.entry_price_ref, ts)

        # Registro del último día
        if prev_date is not None:
            daily_equity_records[prev_date] = float(self.broker.get_equity(current_prices))

        # Cierre final al término de la simulación
        if timeline and self.broker.positions:
            last_ts = timeline[-1]
            for s in list(self.broker.positions.keys()):
                self.broker.close_position(
                    s, current_prices.get(s, Decimal("100.0")), last_ts, reason="end_of_period"
                )

        equity_series = pd.Series(daily_equity_records)
        equity_series.index = pd.to_datetime(equity_series.index)

        strat_id = (
            self.strategy.id
            if self.strategy is not None
            else ("universe_a_blocks" if self.config.blocks else "intraday_backtest")
        )

        metrics = compute_backtest_metrics(
            strategy_id=strat_id,
            trades=self.broker.closed_trades,
            equity_curve=equity_series,
            initial_capital=float(self.initial_capital),
            num_tested_trials=self.num_tested_trials,
        )

        return BacktestResult(
            metrics=metrics,
            trades=self.broker.closed_trades,
            equity_curve=equity_series,
            circuit_breaker_events=cb_events,
            daily_regime_scores=pd.Series(regime_scores),
        )

    def _run_daily(
        self,
        start_date: date,
        end_date: date,
    ) -> BacktestResult:
        """Ejecuta el replay cronológico diario desde start_date hasta end_date."""

        all_symbols = list(self.daily_data.keys())
        spy_df = self.daily_data.get("SPY", pd.DataFrame())

        all_dates_set: set[date] = set()
        for df in self.daily_data.values():
            all_dates_set.update(df["_parsed_date"])

        trading_days = sorted([d for d in all_dates_set if start_date <= d <= end_date])
        equity_records: dict[date, float] = {}
        cb_events: list[CircuitBreakerEvent] = []
        regime_scores: dict[date, float] = {}
        portfolio_vols: dict[date, float] = {}

        eval_time = self.config.eval_time
        if self.strategy is not None and getattr(self.strategy, "id", None) == "intraday_momentum":
            eval_time = time(15, 30)

        for current_day in trading_days:
            eval_dt = datetime.combine(current_day, eval_time)

            # A. Liquidación T+1 al inicio de sesión
            self.broker.process_settlement(current_day)

            # B. Precios de apertura de sesión (09:30 ET) y baseline de equity
            opening_prices: dict[str, Decimal] = {}
            current_prices: dict[str, Decimal] = {}
            for sym in all_symbols:
                d_df = self.daily_data[sym]
                d_past = d_df[d_df["_parsed_date"] <= current_day]
                if not d_past.empty:
                    cur_p = Decimal(str(round(float(d_past["close"].iloc[-1]), 4)))
                    current_prices[sym] = cur_p

                d_today = d_df[d_df["_parsed_date"] == current_day]
                if not d_today.empty and "open" in d_today.columns:
                    opening_prices[sym] = Decimal(str(round(float(d_today["open"].iloc[0]), 4)))
                elif not d_past.empty:
                    d_prior = d_df[d_df["_parsed_date"] < current_day]
                    if not d_prior.empty:
                        opening_prices[sym] = Decimal(str(round(float(d_prior["close"].iloc[-1]), 4)))
                    else:
                        opening_prices[sym] = current_prices.get(sym, Decimal("100.0"))

            opening_equity = self.broker.get_equity(opening_prices)
            if self.cb_manager:
                self.cb_manager.reset_daily(opening_equity, current_day)

            # C. Régimen graduado
            if self.config.enable_graduated_regime:
                regime_score = compute_graduated_regime_score(self.daily_data, current_day)
            else:
                regime_score = 1.0
            regime_scores[current_day] = regime_score
            regime = self._determine_regime(spy_df, current_day)

            # Rotación defensiva ante régimen bajista si está configurada (S5 100% Cash)
            if self.config.exit_on_bear_regime and regime == MarketRegime.BEAR and self.broker.positions:
                for s in list(self.broker.positions.keys()):
                    cur_p = current_prices.get(s, self.broker.positions[s].entry_price)
                    tr = self.broker.close_position(s, cur_p, eval_dt, reason="regime_bear_exit")
                    if tr and self.cb_manager:
                        self.cb_manager.record_trade(tr.pnl, tr.exit_time)

            # D. Evaluación intradiaria y cortacircuitos
            halt_session = False

            if self.intraday_data:
                # Evaluación cronológica tick/barra intradiaria
                for sym, _pos in list(self.broker.positions.items()):
                    if sym in self.intraday_data:
                        day_intra = self.intraday_data[sym]
                        t_col = "timestamp" if "timestamp" in day_intra.columns else "date"
                        bars = day_intra[
                            (day_intra["_parsed_date"] == current_day)
                            & (day_intra["_parsed_ts"] <= eval_dt)
                        ]
                        for _, b in bars.iterrows():
                            tr = self.broker.evaluate_bar(
                                symbol=sym,
                                bar_open=Decimal(str(b["open"])),
                                bar_high=Decimal(str(b["high"])),
                                bar_low=Decimal(str(b["low"])),
                                bar_close=Decimal(str(b["close"])),
                                timestamp=b[t_col],
                                is_market_close=False,
                            )
                            if tr and self.cb_manager:
                                self.cb_manager.record_trade(tr.pnl, tr.exit_time)
                            if sym not in self.broker.positions:
                                break

                # Monitoreo de cortacircuitos con equity a eval_time
                if self.cb_manager:
                    current_eq = self.broker.get_equity(current_prices)
                    self.cb_manager.update_equity(current_eq)
                    if self.cb_manager.requires_flatten():
                        halt_session = True
                        liquidated = []
                        for s in list(self.broker.positions.keys()):
                            tr = self.broker.close_position(
                                s, current_prices.get(s, Decimal("100.0")), eval_dt, reason="circuit_breaker_emergency_flatten"
                            )
                            if tr:
                                self.cb_manager.record_trade(tr.pnl, tr.exit_time)
                                liquidated.append(s)
                        cb_events.append(
                            CircuitBreakerEvent(
                                date=current_day,
                                timestamp=eval_dt,
                                event_type="EMERGENCY_FLATTEN",
                                intraday_loss_pct=float(((opening_equity - current_eq) / opening_equity) * 100) if opening_equity > 0 else 0.0,
                                threshold_pct=self.config.emergency_loss_limit_pct,
                                starting_equity=float(opening_equity),
                                equity_at_trigger=float(current_eq),
                                liquidated_positions=liquidated,
                            )
                        )
                    elif not self.cb_manager.can_open_new_positions():
                        cb_events.append(
                            CircuitBreakerEvent(
                                date=current_day,
                                timestamp=eval_dt,
                                event_type="PAUSED_DAILY_LOSS",
                                intraday_loss_pct=float(((opening_equity - current_eq) / opening_equity) * 100) if opening_equity > 0 else 0.0,
                                threshold_pct=self.config.daily_loss_limit_pct,
                                starting_equity=float(opening_equity),
                                equity_at_trigger=float(current_eq),
                                liquidated_positions=[],
                            )
                        )
            else:
                # Evaluación en barras diarias: worst-case intraday low
                if self.broker.positions:
                    worst_pos_val = Decimal("0.0")
                    for s, p in self.broker.positions.items():
                        d_row = self.daily_data[s][self.daily_data[s]["_parsed_date"] == current_day]
                        if not d_row.empty:
                            low_p = Decimal(str(round(float(d_row["low"].iloc[0]), 4)))
                            worst_pos_val += p.qty * low_p
                        else:
                            worst_pos_val += p.qty * current_prices.get(s, p.entry_price)
                    worst_equity = self.broker.cash + worst_pos_val

                    if self.cb_manager:
                        self.cb_manager.update_equity(worst_equity)
                        if self.cb_manager.requires_flatten():
                            halt_session = True
                            liquidated = []
                            for s in list(self.broker.positions.keys()):
                                d_row = self.daily_data[s][self.daily_data[s]["_parsed_date"] == current_day]
                                low_p = (
                                    Decimal(str(round(float(d_row["low"].iloc[0]), 4)))
                                    if not d_row.empty
                                    else current_prices.get(s, Decimal("100.0"))
                                )
                                tr = self.broker.close_position(
                                    s, low_p, eval_dt, reason="circuit_breaker_emergency_flatten", is_gap=True
                                )
                                if tr:
                                    self.cb_manager.record_trade(tr.pnl, tr.exit_time)
                                    liquidated.append(s)
                            cb_events.append(
                                CircuitBreakerEvent(
                                    date=current_day,
                                    timestamp=eval_dt,
                                    event_type="EMERGENCY_FLATTEN",
                                    intraday_loss_pct=float(((opening_equity - worst_equity) / opening_equity) * 100) if opening_equity > 0 else 0.0,
                                    threshold_pct=self.config.emergency_loss_limit_pct,
                                    starting_equity=float(opening_equity),
                                    equity_at_trigger=float(worst_equity),
                                    liquidated_positions=liquidated,
                                )
                            )
                        elif not self.cb_manager.can_open_new_positions():
                            cb_events.append(
                                CircuitBreakerEvent(
                                    date=current_day,
                                    timestamp=eval_dt,
                                    event_type="PAUSED_DAILY_LOSS",
                                    intraday_loss_pct=float(((opening_equity - worst_equity) / opening_equity) * 100) if opening_equity > 0 else 0.0,
                                    threshold_pct=self.config.daily_loss_limit_pct,
                                    starting_equity=float(opening_equity),
                                    equity_at_trigger=float(worst_equity),
                                    liquidated_positions=[],
                                )
                            )

                # Evaluación de stops y salidas en datos diarios
                for s in list(self.broker.positions.keys()):
                    pos = self.broker.positions.get(s)
                    if pos is None:
                        continue
                    d_row = self.daily_data[s][self.daily_data[s]["_parsed_date"] == current_day]
                    if not d_row.empty:
                        r = d_row.iloc[0]
                        tr = self.broker.evaluate_bar(
                            symbol=s,
                            bar_open=Decimal(str(r["open"])),
                            bar_high=Decimal(str(r["high"])),
                            bar_low=Decimal(str(r["low"])),
                            bar_close=Decimal(str(r["close"])),
                            timestamp=eval_dt,
                            is_market_close=pos.exit_at_close,
                        )
                        if tr and self.cb_manager:
                            self.cb_manager.record_trade(tr.pnl, tr.exit_time)

            # E. Gestión de salidas específicas (trailing EMA / holding sessions)
            if not halt_session and (self.config.blocks or self.config.trailing_ema_period > 0):
                for s in list(self.broker.positions.keys()):
                    pos = self.broker.positions.get(s)
                    if pos is None:
                        continue
                    d_df = self.daily_data[s]
                    d_past = d_df[d_df["_parsed_date"] <= current_day]
                    if d_past.empty:
                        continue
                    cur_close = Decimal(str(round(float(d_past["close"].iloc[-1]), 4)))
                    past_closes = d_past["close"].astype(float)
                    ema_val = Decimal(str(round(float(ema(past_closes, self.config.trailing_ema_period).iloc[-1]), 4)))

                    hit_stop = cur_close < pos.current_stop
                    hit_ema = (pos.bars_held >= 3 and cur_close < ema_val)
                    hit_max_hold = (
                        self.config.max_holding_sessions > 0 and pos.bars_held >= self.config.max_holding_sessions
                    )

                    if hit_stop or hit_ema or hit_max_hold:
                        reason = "trailing_ema25" if hit_ema else ("stop_loss" if hit_stop else "max_holding")
                        tr = self.broker.close_position(s, cur_close, eval_dt, reason=reason)
                        if tr and self.cb_manager:
                            self.cb_manager.record_trade(tr.pnl, tr.exit_time)
                    else:
                        new_stop = max(
                            pos.current_stop,
                            Decimal(str(round(float(ema_val) * (1.0 - self.config.stop_buffer_pct), 4))),
                        )
                        pos.current_stop = new_stop

            # F. Generación de señales y ejecución de entradas
            can_buy = (not halt_session) and (self.cb_manager.can_open_new_positions() if self.cb_manager else True)

            if can_buy and self.strategy is not None:
                # Modo Strategy (S1, S2, S3, S5, S6, S8, etc.)
                sliced_daily: dict[str, pd.DataFrame] = {}
                sliced_intraday: dict[str, pd.DataFrame] = {}

                for sym in all_symbols:
                    d_df = self.daily_data[sym]
                    d_past = d_df[d_df["_parsed_date"] <= current_day]
                    if not d_past.empty:
                        sliced_daily[sym] = d_past

                    if sym in self.intraday_data:
                        i_df = self.intraday_data[sym]
                        i_past = i_df[i_df["_parsed_ts"] <= eval_dt]
                        if not i_past.empty:
                            sliced_intraday[sym] = i_past

                ctx = StrategyContext(
                    now=eval_dt,
                    regime=regime,
                    daily_bars=sliced_daily,
                    intraday_bars=sliced_intraday,
                    current_prices=current_prices,
                    portfolio_positions=set(self.broker.positions.keys()),
                )

                signals = self.strategy.generate(ctx)
                for sig in signals:
                    if len(self.broker.positions) >= self.max_open_positions:
                        break
                    if sig.symbol in self.broker.positions:
                        continue

                    equity = self.broker.get_equity(current_prices)
                    risk_usd = Decimal(str(self.risk_per_trade_pct / 100.0)) * equity
                    risk_per_unit = sig.entry_price_ref - sig.stop_price
                    if risk_per_unit <= Decimal("0.0"):
                        continue

                    # Single-position cap enforcement
                    max_alloc = equity * Decimal(str(self.config.single_position_cap))
                    raw_alloc = min(risk_usd * (sig.entry_price_ref / risk_per_unit), max_alloc)

                    # B-03 Shrunk covariance portfolio vol control
                    vol_scale = 1.0
                    if self.config.enable_vol_control:
                        active_syms = list(self.broker.positions.keys()) + [sig.symbol]
                        rets_dict = {}
                        for s in active_syms:
                            p_df = self.daily_data[s][self.daily_data[s]["_parsed_date"] <= current_day]["close"].astype(float)
                            if len(p_df) >= 2:
                                rets_dict[s] = p_df.iloc[- (self.config.vol_lookback_days + 1):].pct_change().dropna()
                        active_rets = pd.DataFrame(rets_dict).dropna()
                        if len(active_rets) >= 20:
                            target_w = {}
                            for s in self.broker.positions:
                                p_val = self.broker.positions[s].qty * current_prices.get(s, self.broker.positions[s].entry_price)
                                target_w[s] = float(p_val / equity) if equity > 0 else 0.0
                            target_w[sig.symbol] = float(raw_alloc / equity) if equity > 0 else 0.0
                            sigma_p, k_s, _ = compute_portfolio_volatility_and_scale(
                                weights=target_w,
                                returns_matrix=active_rets,
                                target_vol=self.config.target_portfolio_vol,
                                shrinkage_lambda=self.config.vol_shrinkage_lambda,
                            )
                            vol_scale = k_s
                            portfolio_vols[current_day] = sigma_p

                    final_alloc = raw_alloc * Decimal(str(vol_scale))
                    if final_alloc < Decimal(str(self.config.min_position_usd)):
                        continue

                    qty = final_alloc / sig.entry_price_ref
                    if self.config.integer_shares:
                        qty = Decimal(math.floor(float(qty)))
                    if qty < Decimal("1"):
                        continue

                    self.broker.submit_buy(sig, qty, sig.entry_price_ref, eval_dt)

            elif can_buy and self.config.blocks:
                # Modo BlockConfig (Universe A, multi-asset allocation)
                is_friday = (current_day.weekday() == 4) or (current_day == trading_days[-1])
                is_rebalance_day = is_friday if self.config.rebalance_cadence == "weekly_friday" else True

                if is_rebalance_day:
                    total_equity = float(self.broker.get_equity(current_prices))

                    for block_key, b_cfg in self.config.blocks.items():
                        effective_block_cap = b_cfg.capital_cap
                        if b_cfg.modulate_by_regime:
                            effective_block_cap *= regime_score

                        if effective_block_cap <= 0.01:
                            continue

                        avg_ranks = compute_multi_horizon_momentum(self.daily_data, b_cfg.tickers, current_day)
                        if not avg_ranks:
                            continue

                        qualified = {
                            sym: r_val
                            for sym, r_val in avg_ranks.items()
                            if check_absolute_momentum_gate(self.daily_data, sym, current_day)
                        }
                        if not qualified:
                            continue

                        sorted_qualified = sorted(qualified.keys(), key=lambda s: qualified[s])
                        block_open = [s for s in self.broker.positions if s in b_cfg.tickers]

                        # Buffer rank exits
                        for s in list(block_open):
                            if s not in qualified or (sorted_qualified.index(s) + 1 > b_cfg.buffer_rank):
                                tr = self.broker.close_position(
                                    s, current_prices[s], eval_dt, reason="buffer_rank_exit"
                                )
                                if tr and self.cb_manager:
                                    self.cb_manager.record_trade(tr.pnl, tr.exit_time)
                                block_open.remove(s)

                        available_slots = min(
                            b_cfg.top_n - len(block_open),
                            self.max_open_positions - len(self.broker.positions),
                        )
                        if available_slots <= 0:
                            continue

                        entry_candidates = [
                            s for s in sorted_qualified[: b_cfg.top_n] if s not in self.broker.positions
                        ][:available_slots]
                        if not entry_candidates:
                            continue

                        # Inverse volatility weighting (60d)
                        vols = {}
                        for s in entry_candidates:
                            p_closes = self.daily_data[s][self.daily_data[s]["_parsed_date"] <= current_day]["close"].astype(float)
                            ret_s = p_closes.iloc[-60:].pct_change().dropna()
                            sig = float(ret_s.std() * np.sqrt(252))
                            vols[s] = sig if (not np.isnan(sig) and sig > 0.01) else 0.20

                        inv_vols = {s: 1.0 / vols[s] for s in entry_candidates}
                        sum_inv = sum(inv_vols.values())
                        norm_weights = {s: inv_vols[s] / sum_inv for s in entry_candidates}

                        curr_block_val = sum(
                            float(self.broker.positions[s].qty * current_prices[s]) for s in block_open
                        )
                        max_block_capital = total_equity * effective_block_cap
                        available_block_capital = max(0.0, max_block_capital - curr_block_val)

                        # B-03 Shrunk covariance portfolio vol control
                        vol_scale = 1.0
                        if self.config.enable_vol_control:
                            active_symbols = list(self.broker.positions.keys()) + [
                                s for s in entry_candidates if s not in self.broker.positions
                            ]
                            rets_dict = {}
                            for s in active_symbols:
                                p_df = self.daily_data[s][self.daily_data[s]["_parsed_date"] <= current_day]["close"].astype(float)
                                if len(p_df) >= 2:
                                    rets_dict[s] = p_df.iloc[- (self.config.vol_lookback_days + 1):].pct_change().dropna()
                            active_returns_df = pd.DataFrame(rets_dict).dropna()
                            if len(active_returns_df) >= 20 and active_symbols:
                                target_weights = {}
                                for s in self.broker.positions:
                                    pos_val = float(self.broker.positions[s].qty * current_prices[s])
                                    target_weights[s] = pos_val / total_equity if total_equity > 0 else 0.0
                                for s in entry_candidates:
                                    cand_val = available_block_capital * norm_weights[s]
                                    target_weights[s] = cand_val / total_equity if total_equity > 0 else 0.0

                                sigma_p, k_scale, _ = compute_portfolio_volatility_and_scale(
                                    weights=target_weights,
                                    returns_matrix=active_returns_df,
                                    target_vol=self.config.target_portfolio_vol,
                                    shrinkage_lambda=self.config.vol_shrinkage_lambda,
                                )
                                vol_scale = k_scale
                                portfolio_vols[current_day] = sigma_p

                        for s in entry_candidates:
                            if len(self.broker.positions) >= self.max_open_positions:
                                break
                            raw_alloc = available_block_capital * norm_weights[s] * vol_scale
                            per_inst_cap = b_cfg.per_instrument_cap.get(
                                s, b_cfg.per_instrument_cap.get("default", self.config.single_position_cap)
                            )
                            max_inst_alloc = total_equity * min(per_inst_cap, self.config.single_position_cap)
                            avail_buying_power = float(self.broker.buying_power)
                            alloc = min(raw_alloc, max_inst_alloc, avail_buying_power)

                            if alloc >= self.config.min_position_usd:
                                cur_close = current_prices[s]
                                shares_f = alloc / float(cur_close)
                                shares = Decimal(math.floor(shares_f)) if self.config.integer_shares else Decimal(str(round(shares_f, 4)))
                                if shares < Decimal("1"):
                                    continue

                                past_closes = self.daily_data[s][self.daily_data[s]["_parsed_date"] <= current_day]["close"].astype(float)
                                ema25 = float(ema(past_closes, self.config.trailing_ema_period).iloc[-1])
                                stop_loss = min(float(cur_close) * 0.95, ema25 * (1.0 - self.config.stop_buffer_pct))

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
                                self.broker.submit_buy(sig, shares, cur_close, eval_dt)

            # G. Rendimiento de efectivo remanente (Cash Yield / BIL)
            if self.config.enable_cash_yield and self.broker.settled_cash > Decimal("0"):
                if self.rf_series is not None and current_day in self.rf_series:
                    daily_rf = max(0.0, float(self.rf_series[current_day]))
                else:
                    fallback = self.config.annual_cash_yield_fallback
                    daily_rf = (1.0 + fallback) ** (1.0 / 252.0) - 1.0 if fallback > 0 else 0.0
                if daily_rf > 0.0:
                    yield_amount = self.broker.settled_cash * Decimal(str(daily_rf))
                    self.broker.settled_cash += yield_amount

            # H. Registro EOD
            day_equity = float(self.broker.get_equity(current_prices))
            equity_records[current_day] = day_equity
            if self.cb_manager:
                self.cb_manager.update_equity(Decimal(str(day_equity)))

        # Teardown: cerrar posiciones remanentes al cierre final
        if trading_days and self.broker.positions:
            last_day = trading_days[-1]
            last_dt = datetime.combine(last_day, time(16, 0))
            for s in list(self.broker.positions.keys()):
                d_row = self.daily_data[s][self.daily_data[s]["_parsed_date"] == last_day]
                last_p = (
                    Decimal(str(round(float(d_row["close"].iloc[-1]), 4)))
                    if not d_row.empty
                    else current_prices.get(s, Decimal("100.0"))
                )
                tr = self.broker.close_position(s, last_p, last_dt, reason="end_of_period")
                if tr and self.cb_manager:
                    self.cb_manager.record_trade(tr.pnl, tr.exit_time)

        equity_series = pd.Series(equity_records)
        equity_series.index = pd.to_datetime(equity_series.index)

        strat_id = (
            self.strategy.id
            if self.strategy is not None
            else ("universe_a_blocks" if self.config.blocks else "unified_backtest")
        )

        metrics = compute_backtest_metrics(
            strategy_id=strat_id,
            trades=self.broker.closed_trades,
            equity_curve=equity_series,
            initial_capital=float(self.initial_capital),
            num_tested_trials=self.num_tested_trials,
        )

        return BacktestResult(
            metrics=metrics,
            trades=self.broker.closed_trades,
            equity_curve=equity_series,
            circuit_breaker_events=cb_events,
            daily_regime_scores=pd.Series(regime_scores),
            portfolio_daily_volatility=pd.Series(portfolio_vols),
        )


# Compatibilidad hacia atrás garantizada
ReplayEngine = BacktestEngine
