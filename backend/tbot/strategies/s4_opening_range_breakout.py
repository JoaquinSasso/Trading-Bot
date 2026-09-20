"""Estrategia S4: Opening Range Breakout (ORB).

Deshabilitada por defecto. Evalúa rupturas alcistas del rango de la primera vela de 5 minutos (9:30-9:35 ET)
con filtro de volumen relativo > 1.5. Salida con stop en el mínimo del rango o TP a 2R.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from tbot.indicators.pure import relative_volume
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


class OpeningRangeBreakoutStrategy:
    """Implementación de S4 - Opening Range Breakout (ORB)."""

    id: str = "opening_range_breakout"
    version: str = "1.0.0"
    schedule: list[str] = ["09:35-11:30/5 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = True
    universe: list[str] | None = ["SPY", "QQQ"]

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=25,
        needs_intraday_bars=True,
        intraday_timeframe="5m",
        intraday_lookback_bars=40,
        requires_sip_delayed=False,  # Requiere datos intradiarios frescos
    )

    def __init__(
        self,
        rel_volume_threshold: float = 1.5,
        reward_risk_ratio: float = 2.0,
    ) -> None:
        self.rel_volume_threshold = rel_volume_threshold
        self.reward_risk_ratio = reward_risk_ratio

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Genera señales de ruptura si la primera vela de 5m fue alcista y el precio supera su máximo con volumen."""
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
            intraday_df = ctx.intraday_bars.get(symbol)

            if current_price is None or intraday_df is None or intraday_df.empty:
                continue

            ts_col = "timestamp" if "timestamp" in intraday_df.columns else "date"
            df_intra = intraday_df.copy()
            if not pd.api.types.is_datetime64_any_dtype(df_intra[ts_col]):
                df_intra[ts_col] = pd.to_datetime(df_intra[ts_col])

            current_date = ctx.now.date()
            today_bars = df_intra[df_intra[ts_col].dt.date == current_date].sort_values(by=ts_col)
            if today_bars.empty:
                continue

            # Primera vela de la rueda (9:30 a 9:35)
            first_bar = today_bars.iloc[0]
            first_open = float(first_bar["open"])
            first_close = float(first_bar["close"])
            first_high = float(first_bar["high"])
            first_low = float(first_bar["low"])

            # 1. La primera vela debe ser alcista
            if first_close <= first_open or first_high <= first_low:
                continue

            # 2. El precio actual debe romper por encima del máximo de la primera vela
            cur_price_f = float(current_price)
            if cur_price_f <= first_high:
                continue

            # 3. Filtro de volumen relativo sobre la barra de ruptura
            vol_series = today_bars["volume"].astype(float)
            rel_vol = 2.0  # default si no hay suficiente historial intradiario
            if len(vol_series) >= 5:
                r_vol = relative_volume(vol_series, window=min(len(vol_series), 20))
                val_rvol = r_vol.dropna().iloc[-1] if not r_vol.dropna().empty else 1.0
                rel_vol = float(val_rvol)

            if rel_vol < self.rel_volume_threshold:
                continue

            # Stop loss en el mínimo de la primera barra
            stop_price = Decimal(str(round(first_low, 4)))
            if stop_price >= current_price:
                continue

            risk = current_price - stop_price
            tp_price = current_price + (risk * Decimal(str(self.reward_risk_ratio)))

            score = min(1.0, max(0.5, 0.5 + (rel_vol / 5.0) * 0.5))

            features = {
                "first_bar_high": first_high,
                "first_bar_low": first_low,
                "rel_volume": round(rel_vol, 2),
                "breakout_pct": round(((cur_price_f - first_high) / first_high) * 100, 2),
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
                take_profit_price=tp_price,
                max_holding=timedelta(hours=6),
                exit_at_close=True,
                score=round(score, 4),
                features=features,
            )
            signals.append(sig)

        return signals
