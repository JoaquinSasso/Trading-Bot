"""Estrategia S6: Intraday 5-Minute Multi-Horizon Momentum (M5-HFT).

Diseñada para la rama 'feat/intraday-5m-hft':
- Evaluación continua cada 5 minutos durante la sesión regular (RTH).
- Medición de momentum transversal multi-horizonte en velas de 5m:
  * 10 minutos (2 barras)
  * 15 minutos (3 barras)
  * 30 minutos (6 barras)
  * 45 minutos (9 barras)
  * 60 minutos (12 barras)
- Ranking ordinal compuesto: Promedio de rankings en los 5 horizontes.
- Gate Absoluto Intradiario: Momentum positivo en horizontes clave y Cierre > EMA(21).
- Gestión de Riesgo Intradiario:
  * Trailing Stop en EMA(9) intradiaria.
  * Stop loss de protección inicial del 0.8%.
  * Límite de tenencia máxima de 18 a 24 barras (90 a 120 minutos).
  * Day-End Flatten incondicional a las 15:55 ET (cero riesgo nocturno / gap).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    Signal,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)

HORIZON_BARS_MAP = {
    10: 2,   # 10 minutos = 2 barras de 5m
    15: 3,   # 15 minutos = 3 barras de 5m
    30: 6,   # 30 minutos = 6 barras de 5m
    45: 9,   # 45 minutos = 9 barras de 5m
    60: 12,  # 60 minutos = 12 barras de 5m
}


@dataclass
class IntradayMomentumMetrics:
    symbol: str
    m10: float
    m15: float
    m30: float
    m45: float
    m60: float
    ema21: float
    ema9: float
    current_price: float
    passes_gate: bool
    composite_rank: float = 999.0


class Intraday5mMultiHorizonStrategy:
    """Estrategia de momentum multi-horizonte en frecuencia de 5 minutos."""

    id: str = "s6_intraday_5m_multi_horizon"
    version: str = "1.0.0"
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }

    universe: list[str] = [
        "SPY", "QQQ", "IWM", "GLD",
        "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "AMD"
    ]

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=False,
        needs_intraday_bars=True,
        intraday_timeframe="5m",
        intraday_lookback_bars=40,  # ~3.3 horas de barras de 5m
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        top_n: int = 3,
        trailing_ema_period: int = 9,
        trend_ema_period: int = 21,
        initial_stop_pct: float = 0.008,  # 0.8% stop loss inicial
        max_holding_bars: int = 24,       # 120 minutos
        start_entry_time: time = time(9, 45),
        end_entry_time: time = time(15, 30),
        flatten_time: time = time(15, 55),
    ) -> None:
        self.top_n = top_n
        self.trailing_ema_period = trailing_ema_period
        self.trend_ema_period = trend_ema_period
        self.initial_stop_pct = initial_stop_pct
        self.max_holding_bars = max_holding_bars
        self.start_entry_time = start_entry_time
        self.end_entry_time = end_entry_time
        self.flatten_time = flatten_time

    @staticmethod
    def calculate_metrics_for_ticker(
        bars_df: pd.DataFrame,
        symbol: str,
        trend_ema_period: int = 21,
        trailing_ema_period: int = 9,
    ) -> IntradayMomentumMetrics | None:
        """Calcula los retornos intradiarios en 10, 15, 30, 45 y 60 min."""
        if len(bars_df) < 15:  # Necesitamos al menos 12 barras + margen
            return None

        closes = bars_df["close"].astype(float).values
        c_now = float(closes[-1])
        if c_now <= 0:
            return None

        # Retornos en cada horizonte
        m10 = (c_now - float(closes[-1 - HORIZON_BARS_MAP[10]])) / float(closes[-1 - HORIZON_BARS_MAP[10]])
        m15 = (c_now - float(closes[-1 - HORIZON_BARS_MAP[15]])) / float(closes[-1 - HORIZON_BARS_MAP[15]])
        m30 = (c_now - float(closes[-1 - HORIZON_BARS_MAP[30]])) / float(closes[-1 - HORIZON_BARS_MAP[30]])
        m45 = (c_now - float(closes[-1 - HORIZON_BARS_MAP[45]])) / float(closes[-1 - HORIZON_BARS_MAP[45]])
        m60 = (c_now - float(closes[-1 - HORIZON_BARS_MAP[60]])) / float(closes[-1 - HORIZON_BARS_MAP[60]])

        # EMAs intradiarias
        closes_s = pd.Series(closes)
        e21 = float(ema(closes_s, trend_ema_period).iloc[-1])
        e9 = float(ema(closes_s, trailing_ema_period).iloc[-1])

        # Gate Absoluto Intradiario:
        # 1. Cierre > EMA21
        # 2. Retorno 60m > 0
        # 3. Retorno 10m > 0
        passes = (c_now > e21) and (m60 > 0.0) and (m10 > 0.0)

        return IntradayMomentumMetrics(
            symbol=symbol,
            m10=m10,
            m15=m15,
            m30=m30,
            m45=m45,
            m60=m60,
            ema21=e21,
            ema9=e9,
            current_price=c_now,
            passes_gate=passes,
        )

    @classmethod
    def rank_universe(
        cls,
        all_bars: dict[str, pd.DataFrame],
        symbols: list[str],
        trend_ema_period: int = 21,
        trailing_ema_period: int = 9,
    ) -> list[IntradayMomentumMetrics]:
        """Calcula el score compuesto intradiario promediando los rankings ordinales."""
        metrics_list: list[IntradayMomentumMetrics] = []

        for sym in symbols:
            df = all_bars.get(sym)
            if df is None or df.empty:
                continue
            m = cls.calculate_metrics_for_ticker(
                bars_df=df,
                symbol=sym,
                trend_ema_period=trend_ema_period,
                trailing_ema_period=trailing_ema_period,
            )
            if m is not None:
                metrics_list.append(m)

        if len(metrics_list) < 2:
            return metrics_list

        # Calcular rankings para cada horizonte
        for h_key in ["m10", "m15", "m30", "m45", "m60"]:
            # Ordenar descendente (mayor retorno = rank 1)
            metrics_list.sort(key=lambda x: getattr(x, h_key), reverse=True)
            for r_idx, item in enumerate(metrics_list, 1):
                setattr(item, f"_rank_{h_key}", r_idx)

        # Promediar rankings ordinales
        for item in metrics_list:
            avg_rank = (
                getattr(item, "_rank_m10")
                + getattr(item, "_rank_m15")
                + getattr(item, "_rank_m30")
                + getattr(item, "_rank_m45")
                + getattr(item, "_rank_m60")
            ) / 5.0
            item.composite_rank = avg_rank

        # Ordenar por mejor composite rank (menor promedio = mejor posición)
        metrics_list.sort(key=lambda x: x.composite_rank)
        return metrics_list

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Evalúa barras de 5m y emite señales intradiarias."""
        cur_dt = ctx.now
        cur_time = cur_dt.time()

        # Si estamos fuera del horario de entrada, no abrir posiciones
        if not (self.start_entry_time <= cur_time <= self.end_entry_time):
            return []

        # Verificar filtro macro de SPY intradiario
        spy_bars = ctx.intraday_bars.get("SPY")
        if spy_bars is not None and len(spy_bars) >= self.trend_ema_period:
            spy_closes = spy_bars["close"].astype(float)
            spy_ema = float(ema(spy_closes, self.trend_ema_period).iloc[-1])
            if float(spy_closes.iloc[-1]) < spy_ema:
                # Régimen intradiario bajista
                return []

        # Evaluar universo
        ranked_metrics = self.rank_universe(
            all_bars=ctx.intraday_bars,
            symbols=self.universe,
            trend_ema_period=self.trend_ema_period,
            trailing_ema_period=self.trailing_ema_period,
        )

        qualified = [m for m in ranked_metrics if m.passes_gate]
        if not qualified:
            return []

        signals: list[Signal] = []
        open_syms = set(ctx.portfolio_positions.keys()) if hasattr(ctx.portfolio_positions, "keys") else set(ctx.portfolio_positions)
        available_slots = self.top_n - len(open_syms)

        for m in qualified:
            if available_slots <= 0:
                break
            if m.symbol in open_syms:
                continue

            entry_p = Decimal(str(round(m.current_price, 4)))
            stop_p = entry_p * Decimal(str(1.0 - self.initial_stop_pct))

            sig = Signal.create(
                strategy_id=self.id,
                version=self.version,
                symbol=m.symbol,
                bar_ts=cur_dt,
                side="buy",
                entry_type="market",
                entry_price_ref=entry_p,
                stop_price=stop_p,
                max_holding=self.max_holding_bars,
                exit_at_close=True,
                score=float(1.0 / m.composite_rank),
                features={
                    "m10": m.m10,
                    "m15": m.m15,
                    "m30": m.m30,
                    "m45": m.m45,
                    "m60": m.m60,
                    "composite_rank": m.composite_rank,
                    "ema9": m.ema9,
                    "ema21": m.ema21,
                },
            )
            signals.append(sig)
            available_slots -= 1

        return signals
