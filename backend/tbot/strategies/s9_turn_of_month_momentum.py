"""Estrategia S5: Dual Momentum Leader (Rotación de Líderes con Filtro de Régimen y Trailing Stop EMA25).

Estrategia cuantitativa de momentum transversal y preservación de capital:
1. Filtro Macro / Régimen: Solo opera en regímenes alcistas (BULL_CALM, BULL_VOLATILE).
   Si el régimen es bajista (SPY < SMA200/EMA50), mantiene 100% en efectivo remunerado (BIL).
2. Momentum Transversal (Cross-Sectional): Calcula la fuerza relativa a 45 sesiones en el universo
   y selecciona los N activos líderes (por defecto top 4, cap 25%) que coticen sobre su EMA(25).
3. Asimetría de Retorno (Let Winners Run): Trailing Stop dinámico anclado a la EMA(25)
   con buffer del 3.5%, con límite máximo de retención de 30 sesiones de mercado.
4. Nota de Gobernanza (Decisión D-01): El veto FinBERT fue retirado del camino crítico de ejecución
   en producción; la toma de decisiones es 100% determinista basada en precio y volumen.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    Signal,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)


class TurnOfMonthMomentumStrategy:
    """Implementación de S9 - Momentum con filtro estacional Turn of Month."""

    id: str = "turn_of_month_momentum"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False

    # Universo Multi-Sectorial y Metales Preciosos Oficial (Tech, Finanzas, Salud, Energía, Consumo y Metales)
    DEFAULT_UNIVERSE: list[str] = [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
    ]

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=75,  # 45 sesiones de momentum + 25 sesiones de EMA con margen
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        momentum_lookback_days: int = 45,
        top_n_leaders: int = 2,
        trailing_ema_period: int = 25,
        stop_buffer_pct: float = 0.035,  # Buffer unificado al 3.5%
        max_holding_sessions: int = 30,  # 30 sesiones bursátiles de retención máxima
        universe: list[str] | None = None,
        max_holding_days: int | None = None,  # Compatibilidad hacia atrás
    ) -> None:
        self.universe = list(universe) if universe is not None else list(self.DEFAULT_UNIVERSE)
        self.momentum_lookback_days = momentum_lookback_days
        self.top_n_leaders = top_n_leaders
        self.trailing_ema_period = trailing_ema_period
        self.stop_buffer_pct = stop_buffer_pct
        self.max_holding_sessions = (
            max_holding_sessions if max_holding_days is None else max_holding_days
        )
        self.max_holding_days = self.max_holding_sessions

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Identifica los activos con mayor momentum a 45 sesiones sobre la EMA25 y emite señales."""
        # 1. Filtro absoluto de régimen: Preservación de capital en mercados bajistas
        if ctx.regime not in self.allowed_regimes:
            return []

        # 1.5 Filtro Estacional Turn of Month
        import pandas as pd
        from pandas.tseries.holiday import USFederalHolidayCalendar
        
        cur_date = ctx.now.date()
        start_of_month = cur_date.replace(day=1)
        end_of_month = start_of_month + pd.offsets.MonthEnd(0)
        
        bday_us = pd.offsets.CustomBusinessDay(calendar=USFederalHolidayCalendar())
        bdays = pd.date_range(start=start_of_month, end=end_of_month, freq=bday_us)
        
        if len(bdays) >= 6:
            tom_days = list(bdays[:3]) + list(bdays[-3:])
            if pd.Timestamp(cur_date) not in tom_days:
                return []
        else:
            return []

        target_universe = self.universe if self.universe is not None else list(ctx.current_prices.keys())
        ranked_candidates: list[tuple[str, float, float, float]] = []

        for symbol in target_universe:
            daily_df = ctx.daily_bars.get(symbol)
            current_price = ctx.current_prices.get(symbol)

            if daily_df is None or current_price is None:
                continue
            # Requiere lookback + 1 barras para medir exactamente N sesiones de retorno
            if len(daily_df) < self.momentum_lookback_days + 2:
                continue

            closes = daily_df["close"].astype(float).copy()
            cur_price_f = float(current_price)
            if float(closes.iloc[-1]) != cur_price_f:
                closes.iloc[-1] = cur_price_f

            # 2. Calcular Momentum transversal a 45 sesiones: (P_t - P_{t-45}) / P_{t-45}
            # Convención unificada: iloc[-(momentum_lookback_days + 1)] = exactamente 45 sesiones atrás (iloc[-46])
            p_past = float(closes.iloc[-(self.momentum_lookback_days + 1)])
            if p_past <= 0:
                continue
            mom_45d = (cur_price_f - p_past) / p_past

            # 3. Tendencia local activa: Precio sobre EMA(25)
            ema_25_series = ema(closes, self.trailing_ema_period)
            val_ema25 = ema_25_series.dropna().iloc[-1] if not ema_25_series.dropna().empty else None

            if val_ema25 is None or cur_price_f < val_ema25:
                continue

            if mom_45d > 0.0:
                import numpy as np
                log_rets = np.log(closes / closes.shift(1))
                vol_60 = float(log_rets.iloc[-60:].std()) if len(log_rets) >= 60 else float(log_rets.std())
                if np.isnan(vol_60) or vol_60 <= 1e-6:
                    vol_60 = 0.01
                ranked_candidates.append((symbol, mom_45d, cur_price_f, float(val_ema25), vol_60))

        # Ordenar descendente por momentum (fuerza relativa)
        ranked_candidates.sort(key=lambda x: x[1], reverse=True)

        # Seleccionar los Top N líderes
        selected = ranked_candidates[: self.top_n_leaders]
        signals: list[Signal] = []

        for rank, (symbol, mom_score, cur_price_f, val_ema25, vol_60) in enumerate(selected, start=1):
            if symbol in ctx.portfolio_positions:
                continue

            # Stop dinámico inicial: mínimo entre precio*(1 - buffer) o ligeramente bajo la EMA25
            stop_price_f = min(cur_price_f * (1.0 - self.stop_buffer_pct), val_ema25 * (1.0 - self.stop_buffer_pct))
            if stop_price_f >= cur_price_f:
                stop_price_f = cur_price_f * (1.0 - self.stop_buffer_pct)

            sig_id = compute_signal_id(self.id, self.version, symbol, ctx.now)
            
            # Score de paridad de riesgo inverso (1 / volatilidad) mapeado de 0.1 a 1.0
            inv_vol = 1.0 / (vol_60 * 100.0)
            target_score = float(min(1.0, max(0.1, inv_vol)))

            signals.append(
                Signal(
                    signal_id=sig_id,
                    strategy_id=self.id,
                    symbol=symbol,
                    side="buy",
                    entry_type="limit",
                    entry_price_ref=Decimal(str(round(cur_price_f, 4))),
                    stop_price=Decimal(str(round(stop_price_f, 4))),
                    created_at=ctx.now,
                    limit_price=Decimal(str(round(cur_price_f, 4))),
                    take_profit_price=None,  # Trailing Stop dinámico con EMA25
                    max_holding=timedelta(days=self.max_holding_sessions),  # 30 sesiones
                    exit_at_close=False,
                    score=round(target_score, 4),
                    features={
                        "momentum": round(mom_score, 4),
                        "momentum_lookback_days": self.momentum_lookback_days,
                        f"momentum_{self.momentum_lookback_days}d": round(mom_score, 4),
                        "momentum_45d": round(mom_score, 4),
                        "rank": rank,
                        "trailing_ema": round(val_ema25, 4),
                        "trailing_ema_period": self.trailing_ema_period,
                        "ema25": round(val_ema25, 4),
                        "ema20": round(val_ema25, 4),
                    },
                )
            )

        return signals
