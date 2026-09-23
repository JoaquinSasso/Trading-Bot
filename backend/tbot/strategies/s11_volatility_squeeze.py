"""Estrategia S11: Volatility Squeeze Breakout.

Busca momentos donde la volatilidad se comprime (Bandas de Bollinger dentro de Canales de Keltner)
y luego entra cuando el precio rompe al alza.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import timedelta

import numpy as np
from tbot.indicators.pure import atr, ema, sma
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements, compute_signal_id


class VolatilitySqueezeStrategy:
    """Implementación de S11 - Breakout de Volatilidad (Squeeze)."""

    id: str = "volatility_squeeze"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False
    universe: list[str] | None = None

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=100,
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        kc_period: int = 20,
        kc_atr_mult: float = 1.5,
        max_holding_days: int = 15,
        stop_buffer_pct: float = 0.03,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_period = kc_period
        self.kc_atr_mult = kc_atr_mult
        self.max_holding_days = max_holding_days
        self.stop_buffer_pct = stop_buffer_pct

    def generate(self, ctx: StrategyContext) -> list[Signal]:
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

            if current_price is None or daily_df is None:
                continue
            if len(daily_df) < max(self.bb_period, self.kc_period) + 10:
                continue

            df = daily_df.copy()
            cur_price_f = float(current_price)
            
            close_series = df["close"].astype(float).copy()
            if float(close_series.iloc[-1]) != cur_price_f:
                close_series.iloc[-1] = cur_price_f
                
            high_series = df["high"].astype(float).copy()
            low_series = df["low"].astype(float).copy()
            high_series.iloc[-1] = max(high_series.iloc[-1], cur_price_f)
            low_series.iloc[-1] = min(low_series.iloc[-1], cur_price_f)

            # Bollinger Bands
            bb_sma = close_series.rolling(self.bb_period).mean()
            bb_std = close_series.rolling(self.bb_period).std()
            bb_upper = bb_sma + (self.bb_std * bb_std)
            bb_lower = bb_sma - (self.bb_std * bb_std)

            # Keltner Channels
            kc_ema = ema(close_series, self.kc_period)
            atr_series = atr(high_series, low_series, close_series, self.kc_period)
            kc_upper = kc_ema + (self.kc_atr_mult * atr_series)
            kc_lower = kc_ema - (self.kc_atr_mult * atr_series)
            
            # Squeeze is true when BB is inside KC
            squeeze = ((bb_upper < kc_upper) & (bb_lower > kc_lower)).dropna()
            
            if len(squeeze) < 6:
                continue
            
            # Was there a squeeze in the last 5 days?
            recent_squeeze = squeeze.iloc[-6:-1].any()
            
            # Breakout today?
            breakout = (cur_price_f > float(kc_upper.iloc[-1])) and (float(close_series.iloc[-2]) <= float(kc_upper.iloc[-2]))
            
            if recent_squeeze and breakout:
                # Strong Breakout!
                stop_price = current_price * Decimal(str(1.0 - self.stop_buffer_pct))
                
                sig_id = compute_signal_id(self.id, self.version, symbol, ctx.now)
                sig = Signal(
                    signal_id=sig_id,
                    strategy_id=self.id,
                    symbol=symbol,
                    side="buy",
                    entry_type="market",
                    entry_price_ref=current_price,
                    stop_price=Decimal(str(round(float(stop_price), 4))),
                    created_at=ctx.now,
                    limit_price=None,
                    take_profit_price=None,
                    max_holding=timedelta(days=self.max_holding_days),
                    exit_at_close=False,
                    score=1.0,
                )
                signals.append(sig)

        return signals
