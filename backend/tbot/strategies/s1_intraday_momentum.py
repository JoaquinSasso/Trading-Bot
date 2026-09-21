"""Estrategia S1: Intraday Momentum.

Base empírica: El retorno de la primera media hora de la rueda (cierre previo -> 10:00 ET)
predice el retorno de la última media hora (15:30 -> 15:58 ET).
Se evalúa a las 15:30 ET en SPY y QQQ.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from tbot.indicators.pure import atr
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


class IntradayMomentumStrategy:
    """Implementación determinista de S1 - Intraday Momentum."""

    id: str = "intraday_momentum"
    version: str = "1.0.0"
    schedule: list[str] = ["15:30 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
        MarketRegime.BEAR,
    }
    allows_open_window: bool = False
    universe: list[str] | None = ["SPY", "QQQ"]

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=5,
        needs_intraday_bars=True,
        intraday_timeframe="5m",
        intraday_lookback_bars=80,  # ~6.5 horas de barras de 5 minutos
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        min_first_half_hour_ret: float = 0.0,
        require_midday_non_negative: bool = False,
        atr_stop_multiple: float = 1.5,
        atr_period: int = 14,
    ) -> None:
        self.min_first_half_hour_ret = min_first_half_hour_ret
        self.require_midday_non_negative = require_midday_non_negative
        self.atr_stop_multiple = atr_stop_multiple
        self.atr_period = atr_period

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Evalúa las barras del día y genera señales si se cumple la condición de momentum."""
        if ctx.regime not in self.allowed_regimes:
            return []

        signals: list[Signal] = []
        target_universe = (
            self.universe if self.universe is not None else list(ctx.current_prices.keys())
        )

        for symbol in target_universe:
            if symbol in ctx.portfolio_positions:
                continue

            current_price = ctx.current_prices.get(symbol)
            daily_df = ctx.daily_bars.get(symbol)
            intraday_df = ctx.intraday_bars.get(symbol)

            if current_price is None or daily_df is None or intraday_df is None:
                continue
            if len(daily_df) < 2 or len(intraday_df) < self.atr_period + 5:
                continue

            # 1. Obtener cierre del día previo
            prev_close = float(daily_df["close"].iloc[-2])
            if prev_close <= 0:
                continue

            # 2. Filtrar barras del día actual
            # Las barras intradía deben tener columna timestamp o date
            ts_col = "timestamp" if "timestamp" in intraday_df.columns else "date"
            df_intra = intraday_df.copy()
            if not pd.api.types.is_datetime64_any_dtype(df_intra[ts_col]):
                df_intra[ts_col] = pd.to_datetime(df_intra[ts_col])

            current_date = ctx.now.date()
            today_bars = df_intra[df_intra[ts_col].dt.date == current_date]
            if today_bars.empty:
                continue

            # 3. Buscar precio a las 10:00 ET (o la barra más cercana previa o igual a 10:00)
            # Asumimos que los timestamps de las barras están normalizados o con timezone
            bars_until_1000 = today_bars[
                (today_bars[ts_col].dt.hour < 10)
                | ((today_bars[ts_col].dt.hour == 10) & (today_bars[ts_col].dt.minute == 0))
            ]

            if bars_until_1000.empty:
                continue

            price_1000 = float(bars_until_1000["close"].iloc[-1])
            first_half_hour_ret = (price_1000 - prev_close) / prev_close

            # Condición principal: retorno primer media hora > umbral
            if first_half_hour_ret <= self.min_first_half_hour_ret:
                continue

            # Filtro opcional: retorno 10:00 -> 15:30 no negativo
            cur_price_f = float(current_price)
            if self.require_midday_non_negative:
                midday_ret = (cur_price_f - price_1000) / price_1000
                if midday_ret < 0:
                    continue

            # 4. Calcular ATR(14) sobre las barras intradía de 5 minutos
            atr_series = atr(
                high=today_bars["high"],
                low=today_bars["low"],
                close=today_bars["close"],
                period=self.atr_period,
            )
            current_atr = atr_series.dropna().iloc[-1] if not atr_series.dropna().empty else 0.0
            if current_atr <= 0:
                continue

            # Stop loss = current_price - (atr_stop_multiple * ATR)
            stop_distance = Decimal(str(round(current_atr * self.atr_stop_multiple, 4)))
            stop_price = current_price - stop_distance
            if stop_price <= 0:
                continue

            # Score relativo al retorno de la mañana (normalizado de 0 a 1)
            score = min(1.0, max(0.5, 0.5 + first_half_hour_ret * 20.0))

            features = {
                "first_half_hour_ret": round(first_half_hour_ret, 6),
                "price_1000": price_1000,
                "prev_close": prev_close,
                "atr_5m": round(float(current_atr), 4),
                "stop_distance": float(stop_distance),
            }

            sig = Signal.create(
                strategy_id=self.id,
                version=self.version,
                symbol=symbol,
                bar_ts=ctx.now,
                side="buy",
                entry_type="market",
                entry_price_ref=current_price,
                stop_price=stop_price,
                take_profit_price=None,  # Salida pura a mercado a las 15:58 ET
                max_holding=timedelta(minutes=28),
                exit_at_close=True,
                score=round(score, 4),
                features=features,
            )
            signals.append(sig)

        return signals
