"""Estrategia S3: Trend Pullback.

Opera retrocesos saludables hacia la EMA(20) en activos con tendencia alcista activa (EMA20 > EMA50)
durante regímenes de mercado calmos (BULL_CALM).
Evaluación a las 15:45 ET con salida por Take Profit a 2R o Stop a 1.5 ATR.
"""

from __future__ import annotations

from decimal import Decimal

from tbot.indicators.pure import atr, ema, rsi
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


class TrendPullbackStrategy:
    """Implementación de S3 - Trend Pullback."""

    id: str = "trend_pullback"
    version: str = "1.1.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False
    universe: list[str] | None = None  # Opera sobre todo el universo habilitado

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=80,  # Requiere al menos 50 días para EMA50
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        pullback_atr_dist: float = 0.7,
        atr_stop_multiple: float = 1.0,
        reward_risk_ratio: float = 2.0,
        min_rsi: float = 48.0,
        atr_period: int = 14,
        max_holding_days: int = 5,
    ) -> None:
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.pullback_atr_dist = pullback_atr_dist
        self.atr_stop_multiple = atr_stop_multiple
        self.reward_risk_ratio = reward_risk_ratio
        self.min_rsi = min_rsi
        self.atr_period = atr_period
        self.max_holding_days = max_holding_days

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Genera señales cuando el precio retrocede a la EMA20 respetando el mínimo del día previo."""
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
            if len(daily_df) < self.ema_slow_period + 5:
                continue

            df = daily_df.copy()
            close_series = df["close"].astype(float).copy()
            high_series = df["high"].astype(float).copy()
            low_series = df["low"].astype(float).copy()

            cur_price_f = float(current_price)
            if float(close_series.iloc[-1]) != cur_price_f:
                close_series.iloc[-1] = cur_price_f
            high_series.iloc[-1] = max(high_series.iloc[-1], cur_price_f)
            low_series.iloc[-1] = min(low_series.iloc[-1], cur_price_f)

            # 1. Filtro de tendencia: EMA20 > EMA50
            ema_20 = ema(close_series, self.ema_fast_period)
            ema_50 = ema(close_series, self.ema_slow_period)
            val_ema20 = ema_20.dropna().iloc[-1] if not ema_20.dropna().empty else None
            val_ema50 = ema_50.dropna().iloc[-1] if not ema_50.dropna().empty else None

            if val_ema20 is None or val_ema50 is None or val_ema20 <= val_ema50:
                continue

            # 2. Filtro de fuerza relativa (RSI): Evitar entrar en caídas libres
            rsi_series = rsi(close_series, period=self.atr_period)
            val_rsi = rsi_series.dropna().iloc[-1] if not rsi_series.dropna().empty else 0.0
            if val_rsi < self.min_rsi:
                continue

            # 3. Calcular ATR(14)
            atr_series = atr(high_series, low_series, close_series, period=self.atr_period)
            current_atr = atr_series.dropna().iloc[-1] if not atr_series.dropna().empty else 0.0
            if current_atr <= 0:
                continue

            # 3. Condición de retroceso: Distancia a la EMA20 <= pullback_atr_dist * ATR
            dist_to_ema20 = abs(cur_price_f - val_ema20)
            if dist_to_ema20 > (self.pullback_atr_dist * current_atr):
                continue

            # 4. Condición de soporte: Cierre actual > mínimo del día anterior
            prev_low = float(low_series.iloc[-2])
            if cur_price_f <= prev_low:
                continue

            # 5. Cálculo de Stop Loss y Take Profit (2R)
            stop_dist = Decimal(str(round(current_atr * self.atr_stop_multiple, 4)))
            stop_price = current_price - stop_dist
            if stop_price <= 0:
                continue

            risk_amount = current_price - stop_price
            tp_price = current_price + (risk_amount * Decimal(str(self.reward_risk_ratio)))

            # Score: Mejor cuanto más cerca de la EMA20 esté el precio
            score = max(
                0.5,
                min(
                    1.0, 1.0 - (dist_to_ema20 / (self.pullback_atr_dist * current_atr + 1e-6)) * 0.4
                ),
            )

            features = {
                "ema20": round(float(val_ema20), 2),
                "ema50": round(float(val_ema50), 2),
                "dist_to_ema20": round(float(dist_to_ema20), 4),
                "prev_low": round(float(prev_low), 2),
                "atr14": round(float(current_atr), 4),
                "reward_risk": self.reward_risk_ratio,
            }

            sig = Signal.create(
                strategy_id=self.id,
                version=self.version,
                symbol=symbol,
                bar_ts=ctx.now,
                side="buy",
                entry_type="limit",  # o market
                entry_price_ref=current_price,
                stop_price=stop_price,
                take_profit_price=tp_price,
                max_holding=self.max_holding_days,
                exit_at_close=False,
                score=round(score, 4),
                features=features,
            )
            signals.append(sig)

        return signals
