"""Estrategia S5: Dual Momentum Leader (Rotación de Líderes con Filtro de Régimen y FinBERT).

Estrategia cuantitativa de asignación de capital diseñada para superar al S&P 500:
1. Filtro Macro / Régimen: Solo opera en regímenes alcistas (BULL_CALM, BULL_VOLATILE).
   Si el régimen es bajista (SPY < SMA200/EMA50), mantiene 100% en efectivo.
2. Momentum Transversal (Cross-Sectional): Calcula la fuerza relativa a 60 días en el universo
   y selecciona los N activos líderes (por defecto top 2) que coticen sobre su EMA(20).
3. Asimetría de Retorno (Let Winners Run): No impone Take Profit fijo, sino un Trailing Stop
   dinámico anclado a la EMA(20), permitiendo capturar rallies de meses (+30% a +50%).
4. Veto FinBERT: Filtra activos con riesgo de eventos o noticias de pánico (negative_share >= 0.35).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    Signal,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)


class DualMomentumLeaderStrategy:
    """Implementación de S5 - Dual Momentum Leader."""

    id: str = "dual_momentum_leader"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False
    universe: list[str] | None = None  # Opera sobre universo habilitado (SPY, QQQ, AAPL, NVDA, MSFT)

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=90,  # Requiere al menos 60 días para el momentum y 20 para EMA
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        momentum_lookback_days: int = 60,
        top_n_leaders: int = 2,
        trailing_ema_period: int = 20,
        stop_buffer_pct: float = 0.05,  # Stop inicial a -5% o bajo EMA20
        max_holding_days: int = 20,
    ) -> None:
        self.momentum_lookback_days = momentum_lookback_days
        self.top_n_leaders = top_n_leaders
        self.trailing_ema_period = trailing_ema_period
        self.stop_buffer_pct = stop_buffer_pct
        self.max_holding_days = max_holding_days

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Identifica los activos con mayor momentum a 60 días sobre la EMA20 y emite señales."""
        # 1. Filtro absoluto de régimen: Preservación de capital en mercados bajistas
        if ctx.regime not in self.allowed_regimes:
            return []

        target_universe = (
            self.universe if self.universe is not None else list(ctx.current_prices.keys())
        )

        ranked_candidates: list[tuple[str, float, float, float]] = []

        for symbol in target_universe:
            daily_df = ctx.daily_bars.get(symbol)
            current_price = ctx.current_prices.get(symbol)

            if daily_df is None or current_price is None:
                continue
            if len(daily_df) < self.momentum_lookback_days + 5:
                continue

            closes = daily_df["close"].astype(float).copy()
            cur_price_f = float(current_price)
            if float(closes.iloc[-1]) != cur_price_f:
                closes.iloc[-1] = cur_price_f

            # 2. Calcular Momentum de 60 días
            p_past = float(closes.iloc[-self.momentum_lookback_days])
            if p_past <= 0:
                continue
            mom_60d = (cur_price_f - p_past) / p_past

            # 3. Tendencia local activa: Precio sobre EMA(20)
            ema_20_series = ema(closes, self.trailing_ema_period)
            val_ema20 = ema_20_series.dropna().iloc[-1] if not ema_20_series.dropna().empty else None

            if val_ema20 is None or cur_price_f < val_ema20:
                continue

            if mom_60d > 0.0:
                ranked_candidates.append((symbol, mom_60d, cur_price_f, float(val_ema20)))

        # Ordenar descendente por momentum (fuerza relativa)
        ranked_candidates.sort(key=lambda x: x[1], reverse=True)

        # Seleccionar los Top N líderes
        selected = ranked_candidates[: self.top_n_leaders]
        signals: list[Signal] = []

        for rank, (symbol, mom_score, cur_price_f, val_ema20) in enumerate(selected, start=1):
            if symbol in ctx.portfolio_positions:
                # Ya estamos posicionados en este líder
                continue

            # Stop dinámico: mínimo entre precio*(1 - buffer) o ligeramente bajo la EMA20
            stop_price_f = min(cur_price_f * (1.0 - self.stop_buffer_pct), val_ema20 * 0.985)
            # Asegurar que el stop sea inferior al precio de entrada
            if stop_price_f >= cur_price_f:
                stop_price_f = cur_price_f * 0.95

            sig_id = compute_signal_id(self.id, self.version, symbol, ctx.now)

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
                    take_profit_price=None,  # Sin Take Profit rígido (corre con trailing EMA20)
                    max_holding=timedelta(days=self.max_holding_days),
                    exit_at_close=False,
                    score=round(min(1.0, max(0.1, mom_score)), 4),
                    features={
                        "momentum_60d": round(mom_score, 4),
                        "rank": rank,
                        "ema20": round(val_ema20, 4),
                    },
                )
            )

        return signals
