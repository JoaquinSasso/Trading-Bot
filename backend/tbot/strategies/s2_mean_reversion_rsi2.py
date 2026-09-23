"""Estrategia S2: Mean Reversion RSI(2).

Busca oportunidades de sobreventa extrema en activos dentro de una tendencia alcista estructural.
Evaluación a las 15:45 ET utilizando barras diarias SIP + precio actual como aproximación de cierre.
"""

from __future__ import annotations

from decimal import Decimal

from tbot.indicators.pure import atr, rsi, sma
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, StrategyContext, StrategyDataRequirements


class MeanReversionRSI2Strategy:
    """Implementación de S2 - Mean Reversion RSI(2) en tendencia alcista."""

    id: str = "mean_reversion_rsi2"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False
    universe: list[str] | None = None  # Opera sobre todo el universo habilitado

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=250,  # Requiere al menos 200 días para SMA200
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        rsi_threshold: float = 10.0,
        sma_trend_period: int = 200,
        sma_exit_period: int = 5,
        rsi_exit_threshold: float = 70.0,
        atr_stop_multiple: float = 1.5,
        atr_period: int = 14,
        max_holding_days: int = 3,
    ) -> None:
        self.rsi_threshold = rsi_threshold
        self.sma_trend_period = sma_trend_period
        self.sma_exit_period = sma_exit_period
        self.rsi_exit_threshold = rsi_exit_threshold
        self.atr_stop_multiple = atr_stop_multiple
        self.atr_period = atr_period
        self.max_holding_days = max_holding_days

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Evalúa las barras diarias y genera señales para activos con RSI(2) < 10 y precio > SMA200."""
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
            if len(daily_df) < self.sma_trend_period + 5:
                continue

            # Construir serie de precios incluyendo el precio actual como última barra estimada
            df = daily_df.copy()
            close_series = df["close"].astype(float).copy()

            # Actualizar el último valor con current_price si corresponde a la sesión de hoy
            cur_price_f = float(current_price)
            if float(close_series.iloc[-1]) != cur_price_f:
                close_series.iloc[-1] = cur_price_f

            # Calcular SMA(200)
            sma_200 = sma(close_series, self.sma_trend_period)
            val_sma200 = sma_200.dropna().iloc[-1] if not sma_200.dropna().empty else None

            if val_sma200 is None or cur_price_f <= val_sma200:
                continue  # Debe estar por encima de la media de 200 días

            # Calcular RSI(2)
            rsi_2 = rsi(close_series, period=2)
            val_rsi2 = rsi_2.dropna().iloc[-1] if not rsi_2.dropna().empty else None

            if val_rsi2 is None or val_rsi2 >= self.rsi_threshold:
                continue  # Debe estar en sobreventa extrema (RSI(2) < 10)

            # Filtro de volumen: confirmar pánico
            # El volumen del día de la caída debe ser > 1.5x el promedio de los últimos 20 días
            vol_series = df["volume"].astype(float)
            if len(vol_series) >= 20:
                avg_vol_20 = vol_series.rolling(20).mean().iloc[-1]
                if vol_series.iloc[-1] <= (avg_vol_20 * 1.5):
                    continue
            else:
                continue

            # Calcular ATR(14) para el stop loss
            high_series = df["high"].astype(float).copy()
            low_series = df["low"].astype(float).copy()
            high_series.iloc[-1] = max(high_series.iloc[-1], cur_price_f)
            low_series.iloc[-1] = min(low_series.iloc[-1], cur_price_f)

            atr_series = atr(high_series, low_series, close_series, period=self.atr_period)
            current_atr = atr_series.dropna().iloc[-1] if not atr_series.dropna().empty else 0.0

            if current_atr <= 0:
                continue

            stop_dist = Decimal(str(round(current_atr * self.atr_stop_multiple, 4)))
            stop_price = current_price - stop_dist
            if stop_price <= 0:
                continue

            # Score: Más alto cuanto menor sea el RSI(2)
            score = max(0.5, min(1.0, 1.0 - (val_rsi2 / 20.0)))

            features = {
                "rsi2": round(float(val_rsi2), 2),
                "sma200": round(float(val_sma200), 2),
                "price_vs_sma200_pct": round(((cur_price_f - val_sma200) / val_sma200) * 100, 2),
                "atr14": round(float(current_atr), 4),
                "stop_distance": float(stop_dist),
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
                take_profit_price=None,  # Salida dinámica por SMA(5) o RSI(2) > 70
                max_holding=self.max_holding_days,
                exit_at_close=False,
                score=round(score, 4),
                features=features,
            )
            signals.append(sig)

        return signals
